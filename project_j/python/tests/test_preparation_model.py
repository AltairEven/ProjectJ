import asyncio
import logging

import pytest
from fastapi.testclient import TestClient
from support import LEARNER, next_course, wait_jobs
from test_learning_planning import model_changes_goals, taught

from project_j.application.configuration import Settings
from project_j.application.progress import advance, next_step, retry
from project_j.contracts import RevisionRef
from project_j.interface.api import create_app
from project_j.learning.fixtures import initialize
from project_j.platform.storage import Conflict
from project_j.preparation.drafting import PreparationUnavailable

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


def typed(task: dict) -> dict:
    """A draft task with the per-type fields a task without typed content leaves empty."""
    content = dict.fromkeys(("choice", "order", "match", "fill", "open"))
    minutes = 10 if task["purpose"] == "offscreen" else None
    return {"minutes": minutes, "correct_text": None} | content | task


def science_draft(context, evidence="He fills it with water."):
    """A science lesson on a model-written story: one English report, a point and an offscreen."""
    draft = {
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
                "stage": "question",
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
    draft["tasks"] = [typed(task) for task in draft["tasks"]]
    return draft


def model_prepares(application, monkeypatch, *drafts):
    """Each preparation call takes the next draft builder; the last one repeats."""
    adapter = application.models.adapters["simulated"]
    original = adapter.respond
    queue = list(drafts)

    def respond(request):
        if request.purpose != "preparation" or "kind" in request.context:  # Not a course draft.
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
    result = asyncio.run(next_course(application, "next1", LEARNER, activity))
    package = result["next_package"]
    assert package["preparation"] == "drafted"
    course = application.store.read(ref(package["course_ref"]), LEARNER)
    assert course.body["primary_goal"]["dimension_id"] == SCIENCE
    assert course.body["budget"]["offscreen"] == 10  # The offscreen task's own minutes.
    material = application.store.read(ref(course.body["material_refs"][0]), LEARNER)
    assert material.body["origin"] == "model" and material.body["source"].startswith("model:")
    assert application.files.read_asset(material.body["asset"]).startswith("# Sam's Cup\n")
    preview = application.store.read(ref(package["preview_ref"]), LEARNER)
    assert preview.body["draft"]["title"] == "Sam's Cup"
    assert preview.body["resources"][0]["state"] == "planned"
    assert preview.body["discussion"] == [] and preview.body["confirmation"] is None
    check = application.store.read(ref(package["check_ref"]), LEARNER)
    assert check.body["status"] == "checked"
    assert "preparation drafted stage=prepare attempt=1 material=model tasks=3" in caplog.text
    assert "Sam" not in caplog.text  # No story or question text in the logs.


def test_one_repair_round_fixes_a_rejected_draft(application, monkeypatch, caplog):
    activity = taught(application)
    model_changes_goals(application, monkeypatch)
    bad = lambda context: science_draft(context, evidence="He fills it with milk.")  # noqa: E731
    model_prepares(application, monkeypatch, bad, science_draft)
    caplog.set_level(logging.INFO, logger="project_j")
    before = calls(application)
    result = asyncio.run(next_course(application, "next1", LEARNER, activity))
    assert result["next_package"]["preparation"] == "drafted"
    assert calls(application) - before == 2
    assert "preparation refused stage=prepare attempt=1 checks=evidence" in caplog.text


def test_rejected_twice_saves_nothing_and_reports_not_ready(application, monkeypatch):
    activity = taught(application)
    model_changes_goals(application, monkeypatch)
    model_prepares(application, monkeypatch, lambda c: science_draft(c, evidence="Not there."))
    before = counts(application)
    result = asyncio.run(next_course(application, "next1", LEARNER, activity))
    package = result["next_package"]
    assert package["status"] == "not_ready"
    assert package["reason"].startswith("课程未取得（规则核对未通过：cup-fact的依据不在故事原文中")
    assert counts(application) == before
    with pytest.raises(PreparationUnavailable, match="^课程未取得（规则核对未通过"):
        asyncio.run(application.prepare("p2", LEARNER))


def test_preparation_failure_keeps_demand_and_retries(application, caplog):
    demand = application.current(LEARNER, "demand")
    application.models.adapters["simulated"].fail_next = True
    caplog.set_level(logging.INFO, logger="project_j")

    async def scenario():
        name = advance(application, LEARNER)
        await wait_jobs(application)
        reason = application.job_failures[name]
        assert reason == "课程未取得（模型暂不可用）。下节需求已保存，可以重试。"
        assert application.current(LEARNER, "demand").ref == demand.ref
        retry(application, LEARNER, name)
        await wait_jobs(application)
        assert next_step(application, LEARNER)["kind"] == "course"

    asyncio.run(scenario())
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
    result = asyncio.run(next_course(application, "next1", LEARNER, activity))
    return ref(result["next_package"]["course_ref"])


def start(application, course_ref, request_id="start2"):
    return application.start(request_id, LEARNER, course_ref, materials_ready=True)


def test_new_goals_are_prepared_confirmed_and_started(application, monkeypatch, caplog):
    course_ref = science_lesson(application, monkeypatch)
    caplog.set_level(logging.INFO, logger="project_j")
    with pytest.raises(Conflict, match="^课程未确认"):
        start(application, course_ref, "s2")
    started = asyncio.run(application.confirm_and_start("s3", LEARNER, course_ref, True))
    assert started["decision"] == "allow_simulation"
    assert any("Sam has a red cup." in text for text in application.material_cache.values())
    application.core.control("stop-s3", "stop")
    application.seal()
    preview = next(
        doc
        for doc in application.store.list_current(LEARNER, "preview")
        if doc.body["course_ref"] == course_ref.model_dump()
    )
    assert preview.body["confirmation"]["course_ref"] == course_ref.model_dump()
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
        parent = {"Authorization": f"Bearer {application.token}"}
        failed = client.post(
            "/api/course/discuss",
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


def reading_draft(context, skip=()):
    """A reading lesson on a model-written story; skip leaves those requirements unmapped."""
    answer = {
        "task_id": "cup-fact",
        "dimension_id": READING,
        "purpose": "answer",
        "prompt": "What does Sam put in the cup?",
        "evidence": "He fills it with water.",
        "acceptable_answers": ["water", "He fills it with water."],
        "known_incorrect": ["milk"],
        "supports": [
            support("找到fills这个词，看它后面是什么。", (READING, "light_prompt")),
            support("用句框回答：He fills it with ___.", (REPORT, "scaffold")),
        ],
        "exit_text": "可以先跳过这题。",
        "optional": False,
    }
    report = answer | {
        "task_id": "cup-pour",
        "dimension_id": REPORT,
        "prompt": "What did Sam do with the water? Answer in a short sentence.",
        "evidence": "Sam pours the water into a bowl.",
        "acceptable_answers": ["He poured the water into a bowl."],
        "known_incorrect": ["He pour the water into a bowl."],
        "supports": [
            support("看pours这一句。", (REPORT, "light_prompt")),
            support("用过去式说：He ___ the water into a bowl.", (REPORT, "scaffold")),
        ],
    }
    # A short observation: a practice lesson would need three task types and 20 minutes.
    draft = science_draft(context) | {
        "lesson_type": "observation",
        "tasks": [typed(answer), typed(report)],
        "resources": [],
    }
    draft["demand_mapping"] = [
        {"index": item["index"], "implementation": "本课落实。", "task_ids": ["cup-pour"]}
        for item in context["requirements"]
        if item["field"] not in skip
    ]
    return draft


def test_study_preparation_allows_open_after_the_reference_set_passed(tmp_path):
    """Real classroom judgment passed reference set 1.1.0 (QT-3, run qt3-reference-b)."""
    from conftest import ROOT

    from project_j.application.coordinator import Coordinator
    from project_j.platform.storage import SimulationStore

    store = SimulationStore(tmp_path / "private/study", "a1", realm="study")
    app = Coordinator(store, ROOT, verification={"result": "pass"}, onboarding=False)
    assert app.open_allowed()


def test_study_preparation_writes_by_interests_and_maps_the_chosen_skill(
    tmp_path, monkeypatch, caplog
):
    from conftest import ROOT

    from project_j.application.coordinator import Coordinator
    from project_j.application.documents import document
    from project_j.platform.storage import Publication, SaveKey, SimulationStore

    store = SimulationStore(tmp_path / "private/study", "a1", realm="study")
    app = Coordinator(store, ROOT, verification={"result": "pass"}, onboarding=False)
    initialize(app)
    learner = app.learner_id
    demand = app.current(learner, "demand")
    chosen = document(
        "demand",
        demand.object_id,
        "focus-op",
        learner,
        demand.body | {"skill_focus": ["past_simple"]},
        refs=demand.dependency_refs,
        revision=demand.revision + 1,
        occurred_at=demand.occurred_at,
    )
    store.save(
        SaveKey(operation_id="focus-op", step_id="demand", source_revision=1),
        learner,
        [chosen],
        (Publication(ref=chosen.ref, expected_revision=demand.revision, reason="测试"),),
    )
    seen = []

    def unmapped(context):
        seen.append(context)
        return reading_draft(context, skip=("skill_focus",))

    model_prepares(app, monkeypatch, unmapped, reading_draft)
    monkeypatch.setattr(app.models.adapters["simulated"], "simulated", False)  # a real model
    caplog.set_level(logging.INFO, logger="project_j")
    asyncio.run(app.prepare("p1", learner))
    context = seen[0]
    assert context["library"] == [] and context["candidate"] is None and context["template"] is None
    assert context["recent_titles"] == []
    assert context["demand"]["skill_focus"][0]["label"] == "一般过去时"
    assert context["demand"]["topic"] == "The Paper Bridge"
    assert ("topic", "The Paper Bridge") in [
        (item["field"], item["requirement"]) for item in context["requirements"]
    ]
    assert any(item["field"] == "skill_focus" for item in context["requirements"])
    assert calls(app) == 2  # the unmapped skill was sent back for repair
    assert "attempt=2 material=model tasks=2 read_aloud=0 resources=0 focus=1" in caplog.text
