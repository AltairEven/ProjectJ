import asyncio
import logging

import pytest
from fastapi.testclient import TestClient
from support import CONDITIONS, LEARNER
from test_learning_planning import model_changes_goals, taught

from project_j.application.configuration import Settings
from project_j.contracts import RevisionRef
from project_j.interface.api import create_app
from project_j.platform.storage import Conflict

READING, REPORT = "english.reading.facts", "english.speaking.report"
SCIENCE = "science.observation.report"
NEW_STORY = (
    "Sam has a red cup. He fills it with water. The cup is heavy now. "
    "Sam pours the water into a bowl. The cup is light again. "
    "Sam smiles and puts the cup on the table."
)
KINDS = ("course", "material", "brief", "preview", "content_check")


def support(text, *levels):
    return {"text": text, "help": [{"dimension_id": d, "level": lv} for d, lv in levels]}


def science_draft(context, evidence="He fills it with water."):
    """A science lesson on a model-written story: one English report, a point and an offscreen."""
    return {
        "material_id": None,
        "title": "Sam's Cup",
        "story": NEW_STORY,
        "lesson_type": "observation",
        "tasks": [
            {
                "task_id": "cup-fact",
                "dimension_id": REPORT,
                "purpose": "answer",
                "prompt": "What does Sam put in the cup?",
                "evidence": evidence,
                "acceptable_answers": ["water", "He fills it with water.", "水"],
                "known_incorrect": ["milk"],
                "supports": [
                    support("找到fills这个词，看它后面是什么。", (REPORT, "light_prompt")),
                    support("用句框回答：He fills it with ___.", (REPORT, "scaffold")),
                ],
                "exit_text": "可以先跳过这题。",
                "optional": False,
            },
            {
                "task_id": "point-cup",
                "dimension_id": SCIENCE,
                "purpose": "point",
                "prompt": "指一指你用的杯子，说说它是空的还是满的。",
                "evidence": "只有实际报告可用。",
                "acceptable_answers": [],
                "known_incorrect": [],
                "supports": [
                    support("先说你看到了什么。", (SCIENCE, "light_prompt")),
                    support("分开说杯子和里面的东西。", (SCIENCE, "scaffold")),
                ],
                "exit_text": "没有杯子也可以如实说。",
                "optional": False,
            },
            {
                "task_id": "cup-experiment",
                "dimension_id": SCIENCE,
                "purpose": "offscreen",
                "prompt": "离屏比较空杯和装水的杯子哪个重。回来报告做了什么、结果怎样。",
                "evidence": "只有实际报告可用。",
                "acceptable_answers": [],
                "known_incorrect": [],
                "supports": [
                    support("先用中文说你做了什么。", (SCIENCE, "light_prompt")),
                    support("分开报告材料、改变和结果。", (SCIENCE, "scaffold")),
                ],
                "exit_text": "没有做也可以如实报告。",
                "optional": True,
            },
        ],
        "read_aloud": False,
        "budget": [
            {"part": "opening", "minutes": 3},
            {"part": "reading", "minutes": 5},
            {"part": "expression", "minutes": 4},
            {"part": "offscreen", "minutes": 15},
            {"part": "closing", "minutes": 3},
        ],
        "demand_mapping": [
            {"index": item["index"], "implementation": "本课按离屏观察落实。", "task_ids": []}
            for item in context["requirements"]
        ],
        "resources": [
            {
                "resource_id": "cup-picture",
                "kind": "image",
                "purpose": "开场展示两个杯子",
                "content": "一个空杯和一个装满水的杯子并排放着。",
                "task_ids": ["cup-experiment"],
                "audience": "student",
                "required": False,
                "fallback": "家长拿出实物杯子。",
            }
        ],
        "preview": {
            "story": "Sam's Cup（自编新材料）",
            "why": "按需求先取实际观察报告。",
            "materials": "两个一样的杯子和水。",
            "help": "先中文报告，再用英语句框。",
            "exit": "30分钟内可跳过、暂停或停止。",
        },
        "brief": {
            "history": "没有儿童历史，走无历史分支。",
            "adjustments": "挑战只替换活动，可降阶、跳过、暂停或停止。",
            "closing": "只总结实际报告；停止时简短结束。",
        },
        "note": "这节先用杯子做实际观察。",
        "limitations": [],
    }


def model_prepares(application, monkeypatch, *drafts):
    """Each preparation call takes the next draft builder; the last one repeats."""
    adapter = application.models.adapters["simulated"]
    original = adapter.respond
    queue = list(drafts)

    def respond(request):
        if request.purpose != "preparation":
            return original(request)
        build = queue.pop(0) if len(queue) > 1 else queue[0]
        return build(request.context)

    monkeypatch.setattr(adapter, "respond", respond)


def calls(application):
    return sum(call["purpose"] == "preparation" for call in application.models.calls)


def counts(application):
    return {kind: len(application.store.list_current(LEARNER, kind)) for kind in KINDS}


def ref(value):
    return RevisionRef.model_validate(value)


def test_new_story_is_saved_as_model_material(application, monkeypatch, caplog):
    activity = taught(application)
    model_changes_goals(application, monkeypatch)
    model_prepares(application, monkeypatch, science_draft)
    caplog.set_level(logging.INFO, logger="project_j")
    result = asyncio.run(application.next_course("next1", LEARNER, activity))
    package = result["next_package"]
    assert package["preparation"] == "drafted"
    course = application.store.read(ref(package["course_ref"]), LEARNER)
    assert course.body["primary_goal"]["dimension_id"] == SCIENCE
    assert course.body["budget"]["offscreen"] == 15
    material = application.store.read(ref(course.body["material_refs"][0]), LEARNER)
    assert material.body["origin"] == "model" and material.body["source"].startswith("model:")
    assert application.files.read_asset(material.body["asset"]).startswith("# Sam's Cup\n")
    preview = application.store.read(ref(package["preview_ref"]), LEARNER)
    assert preview.body["draft"]["title"] == "Sam's Cup"
    assert preview.body["resources"][0]["state"] == "planned"
    assert preview.body["discussion"] == [] and preview.body["confirmation"] is None
    check = application.store.read(ref(package["check_ref"]), LEARNER)
    assert check.body["status"] == "checked"
    report = application.store.read(ref(result["report_ref"]), LEARNER)
    assert report.body["course"] == "drafted"
    assert "preparation drafted stage=prepare attempt=1 material=model tasks=3" in caplog.text
    assert "Sam" not in caplog.text  # No story or question text in the logs.


def test_one_repair_round_fixes_a_rejected_draft(application, monkeypatch, caplog):
    activity = taught(application)
    model_changes_goals(application, monkeypatch)
    bad = lambda context: science_draft(context, evidence="He fills it with milk.")  # noqa: E731
    model_prepares(application, monkeypatch, bad, science_draft)
    caplog.set_level(logging.INFO, logger="project_j")
    before = calls(application)
    result = asyncio.run(application.next_course("next1", LEARNER, activity))
    assert result["next_package"]["preparation"] == "drafted"
    assert calls(application) - before == 2
    assert "preparation refused stage=prepare attempt=1 checks=evidence" in caplog.text


def test_rejected_twice_saves_nothing_and_reports_not_ready(application, monkeypatch):
    activity = taught(application)
    model_changes_goals(application, monkeypatch)
    model_prepares(application, monkeypatch, lambda c: science_draft(c, evidence="Not there."))
    before = counts(application)
    result = asyncio.run(application.next_course("next1", LEARNER, activity))
    package = result["next_package"]
    assert package["status"] == "not_ready"
    assert package["reason"].startswith("课程未取得（规则核对未通过：cup-fact的依据不在故事原文中")
    assert counts(application) == before
    with TestClient(create_app(Settings(), application)) as client:
        parent = {"Authorization": f"Bearer {application.parent_token}"}
        refused = client.post(
            "/api/parent/prepare", headers=parent, json={"request_id": "p2", "learner_id": LEARNER}
        )
    assert refused.status_code == 503
    assert refused.json()["message"].startswith("课程未取得（规则核对未通过")


def test_preparation_failure_keeps_demand_and_retries(application, caplog):
    demand = application.current(LEARNER, "demand")
    application.models.adapters["simulated"].fail_next = True
    caplog.set_level(logging.INFO, logger="project_j")
    with TestClient(create_app(Settings(), application)) as client:
        parent = {"Authorization": f"Bearer {application.parent_token}"}
        body = {"request_id": "p1", "learner_id": LEARNER}
        failed = client.post("/api/parent/prepare", headers=parent, json=body)
        assert failed.status_code == 503
        assert failed.json() == {
            "message": "课程未取得（模型暂不可用）。下节需求已保存，可以重试。",
            "preparation": "failed",
        }
        assert application.current(LEARNER, "demand").ref == demand.ref
        assert client.post("/api/parent/prepare", headers=parent, json=body).status_code == 200
    assert "preparation failure stage=prepare failure=unavailable" in caplog.text


def test_saved_package_is_not_drafted_again(application):
    prepared = asyncio.run(application.prepare("prepare1", LEARNER))
    assert calls(application) == 1
    again = asyncio.run(application.prepare("prepare1", LEARNER))
    assert again == prepared and calls(application) == 1
    course = application.store.read(ref(prepared["course_ref"]), LEARNER)
    material = application.store.read(ref(course.body["material_refs"][0]), LEARNER)
    assert material.body["origin"] == "library" and material.operation_id == course.operation_id


FEEDBACK = "离屏实验改成两个纸杯，第一题再简单一点。"


def revised(context):
    """The model's discussion reply: an easier first question, everything else kept."""
    draft = science_draft(context)
    if "feedback" in context:
        draft["tasks"][0]["prompt"] = "What is in the cup?"
        draft["note"] = "第一题改得更直接；离屏实验原本就用杯子，未改。"
    return draft


def science_lesson(application, monkeypatch, *drafts):
    activity = taught(application)
    model_changes_goals(application, monkeypatch)
    model_prepares(application, monkeypatch, *(drafts or (revised,)))
    result = asyncio.run(application.next_course("next1", LEARNER, activity))
    return ref(result["next_package"]["course_ref"])


def start(application, course_ref, request_id="start2"):
    return application.start(request_id, LEARNER, course_ref, CONDITIONS)


def test_new_goals_are_prepared_confirmed_and_started(application, monkeypatch, caplog):
    course_ref = science_lesson(application, monkeypatch)
    caplog.set_level(logging.INFO, logger="project_j")
    with TestClient(create_app(Settings(), application)) as client:
        parent = {"Authorization": f"Bearer {application.parent_token}"}
        body = {
            "request_id": "s2",
            "learner_id": LEARNER,
            "course_ref": course_ref.model_dump(),
            "conditions": CONDITIONS,
        }
        refused = client.post("/api/parent/start", headers=parent, json=body)
        assert refused.status_code == 409 and refused.json()["message"].startswith("课程未确认")
        confirmed = client.post(
            "/api/parent/confirm",
            headers=parent,
            json={"request_id": "c1", "learner_id": LEARNER, "course_ref": course_ref.model_dump()},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["resources"] == "awaiting_generation"
        started = client.post("/api/parent/start", headers=parent, json=body | {"request_id": "s3"})
        assert started.status_code == 200 and started.json()["decision"] == "allow_simulation"
    assert any("Sam has a red cup." in text for text in application.material_cache.values())
    preview = application.store.read(ref(confirmed.json()["preview_ref"]), LEARNER)
    assert preview.body["confirmation"]["course_ref"] == course_ref.model_dump()
    assert preview.body["resources"][0]["state"] == "awaiting_generation"
    assert "start refused reason=unconfirmed" in caplog.text
    assert f"preparation confirmed course={course_ref.object_id} revision=1" in caplog.text


def test_discussion_creates_a_new_version(application, monkeypatch, caplog):
    course_ref = science_lesson(application, monkeypatch)
    demand = application.current(LEARNER, "demand").ref
    caplog.set_level(logging.INFO, logger="project_j")
    result = asyncio.run(application.discuss("d1", LEARNER, course_ref, FEEDBACK))
    assert result["preparation"] == "drafted"
    course = application.store.read(ref(result["course_ref"]), LEARNER)
    assert course.revision == 2 and course.object_id == course_ref.object_id
    assert course.body["tasks"][0]["prompt"] == "What is in the cup?"
    assert application.store.read(course_ref, LEARNER).body["tasks"][0]["prompt"] != (
        "What is in the cup?"
    )
    preview = application.store.read(ref(result["preview_ref"]), LEARNER)
    assert preview.body["discussion"] == [
        {"feedback": FEEDBACK, "note": "第一题改得更直接；离屏实验原本就用杯子，未改。"}
    ]
    assert preview.body["confirmation"] is None
    assert application.current(LEARNER, "demand").ref == demand
    assert f"preparation discussed course={course_ref.object_id} revision=2" in caplog.text
    assert "纸杯" not in caplog.text


def test_discussion_requires_a_new_confirmation(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch)
    application.confirm("c1", LEARNER, course_ref)
    result = asyncio.run(application.discuss("d1", LEARNER, course_ref, FEEDBACK))
    with pytest.raises(Conflict, match="课程已有更新版本"):
        start(application, course_ref)
    newer = ref(result["course_ref"])
    with pytest.raises(Conflict, match="课程未确认"):
        start(application, newer, "start3")
    application.confirm("c2", LEARNER, newer)
    assert start(application, newer, "start4")["decision"] == "allow_simulation"


def test_discussion_on_an_old_version_is_refused(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch)
    asyncio.run(application.discuss("d1", LEARNER, course_ref, FEEDBACK))
    before = calls(application)
    with pytest.raises(Conflict, match="课程已有更新版本"):
        asyncio.run(application.discuss("d2", LEARNER, course_ref, "再改一次。"))
    assert calls(application) == before


def test_failed_discussion_keeps_the_version(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch)
    application.confirm("c1", LEARNER, course_ref)
    application.models.adapters["simulated"].fail_next = True
    with TestClient(create_app(Settings(), application)) as client:
        parent = {"Authorization": f"Bearer {application.parent_token}"}
        failed = client.post(
            "/api/parent/discuss",
            headers=parent,
            json={
                "request_id": "d1",
                "learner_id": LEARNER,
                "course_ref": course_ref.model_dump(),
                "feedback": FEEDBACK,
            },
        )
    assert failed.status_code == 503
    assert failed.json()["message"] == "修改未取得（模型暂不可用）。原课程版本不变，可以重试。"
    assert application.store.current(course_ref.object_id, LEARNER).ref == course_ref
    assert start(application, course_ref)["decision"] == "allow_simulation"


def test_next_material_after_a_model_story():
    from project_j.preparation.packages import FIRST, TRANSFER, next_material

    model = "model:abc"
    assert next_material(model, {model})["source"] == FIRST
    assert next_material(model, {model, FIRST})["source"] == TRANSFER
    both = next_material(model, {model, FIRST, TRANSFER})
    assert both["source"] == FIRST and both["familiar"]


def test_discussion_saved_before_a_crash_resumes_without_the_model(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch)
    finish = application.ledger.finish

    def crash(*args, **kwargs):
        raise RuntimeError("process lost after the revision was saved")

    monkeypatch.setattr(application.ledger, "finish", crash)
    with pytest.raises(RuntimeError):
        asyncio.run(application.discuss("d1", LEARNER, course_ref, FEEDBACK))
    monkeypatch.setattr(application.ledger, "finish", finish)
    before = calls(application)
    result = asyncio.run(application.discuss("d1", LEARNER, course_ref, FEEDBACK))
    assert ref(result["course_ref"]).revision == 2 and calls(application) == before
