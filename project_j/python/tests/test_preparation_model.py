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
        "reading_focus": "As you read, notice how the cup changes.",
        "lesson_type": "observation",
        "tasks": [
            {
                "task_id": "cup-fact",
                "dimension_id": REPORT,
                "purpose": "answer",
                "prompt": "What does Sam put in the cup?",
                "evidence": evidence,
                "acceptable_answers": ["water", "He fills it with water."],
                "known_incorrect": ["milk"],
                "supports": [
                    support(
                        'Find the word "fills". What comes after it?', (REPORT, "light_prompt")
                    ),
                    support("Answer with: He fills it with ___.", (REPORT, "scaffold")),
                ],
                "exit_text": "Let's skip this one for now.",
                "optional": False,
            },
            {
                "task_id": "point-cup",
                "dimension_id": SCIENCE,
                "purpose": "point",
                "prompt": "Point to the cup you used and say if it is empty or full.",
                "evidence": "Only the real report counts.",
                "acceptable_answers": [],
                "known_incorrect": [],
                "supports": [
                    support("First say what you saw.", (SCIENCE, "light_prompt")),
                    support("Tell the cup and what is in it one by one.", (SCIENCE, "scaffold")),
                ],
                "exit_text": "If you have no cup, just say so.",
                "optional": False,
            },
            {
                "task_id": "cup-experiment",
                "dimension_id": SCIENCE,
                "purpose": "offscreen",
                "prompt": "Away from the screen, find out which is heavier: an empty cup or a cup "
                "of water. Come back and tell what you did and what happened.",
                "evidence": "Only the real report counts.",
                "acceptable_answers": [],
                "known_incorrect": [],
                "supports": [
                    support("First say what you did.", (SCIENCE, "light_prompt")),
                    support("Tell the things, the change and the result.", (SCIENCE, "scaffold")),
                ],
                "exit_text": "If you did not do it, just say so.",
                "optional": True,
            },
        ],
        "read_aloud": False,
        "demand_mapping": [
            {
                "index": item["index"],
                "implementation": "Done by the offscreen observation.",
                "task_ids": [],
            }
            for item in context["requirements"]
        ],
        "resources": [
            {
                "resource_id": "cup-picture",
                "kind": "image",
                "purpose": "Show two cups at the start",
                "content": "An empty cup next to a cup full of water.",
                "task_ids": ["cup-experiment"],
                "audience": "student",
                "required": False,
                "fallback": "Use two real cups.",
                "stage": "question",
            }
        ],
        "preview": {
            "story": "Sam's Cup (new story)",
            "why": "The need asks for a real observation report first.",
            "materials": "Two same cups and water.",
            "help": "Where to look first, then an English sentence frame.",
            "exit": "You can skip, pause or stop.",
        },
        "brief": {
            "history": "No history yet.",
            "adjustments": "A challenge only replaces an activity; easier, skip, pause or stop.",
            "closing": "Sum up only the real reports; end briefly on stop.",
        },
        "note": "This lesson starts with a real cup observation.",
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
    assert package["reason"].startswith(
        "The lesson could not be made (the rules found problems: cup-fact: the evidence"
    )
    assert counts(application) == before
    with pytest.raises(
        PreparationUnavailable, match=r"^The lesson could not be made \(the rules found problems"
    ):
        asyncio.run(application.prepare("p2", LEARNER))


def test_preparation_failure_keeps_demand_and_retries(application, caplog):
    demand = application.current(LEARNER, "demand")
    application.models.adapters["simulated"].fail_next = True
    caplog.set_level(logging.INFO, logger="project_j")

    async def scenario():
        name = advance(application, LEARNER)
        await wait_jobs(application)
        reason = application.job_failures[name]
        assert reason == (
            "The lesson could not be made (the model is not available now). "
            "The next lesson's plan is saved; you can try again."
        )
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
        draft["note"] = (
            "The first question is more direct now; the offscreen test already used cups."
        )
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
    with pytest.raises(Conflict, match="^Confirm the lesson first"):
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
        {
            "feedback": FEEDBACK,
            "note": "The first question is more direct now; the offscreen test already used cups.",
        }
    ]
    assert preview.body["confirmation"] is None
    assert application.current(LEARNER, "demand").ref == demand
    assert f"preparation discussed course={course_ref.object_id} revision=2" in caplog.text
    assert "纸杯" not in caplog.text


def test_discussion_requires_a_new_confirmation(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch)
    application.confirm("c1", LEARNER, course_ref)
    result = asyncio.run(application.discuss("d1", LEARNER, course_ref, FEEDBACK))
    with pytest.raises(Conflict, match="has a newer version"):
        start(application, course_ref)
    newer = ref(result["course_ref"])
    with pytest.raises(Conflict, match="Confirm the lesson first"):
        start(application, newer, "start3")
    application.confirm("c2", LEARNER, newer)
    assert start(application, newer, "start4")["decision"] == "allow_simulation"


def test_discussion_on_an_old_version_is_refused(application, monkeypatch):
    course_ref = science_lesson(application, monkeypatch)
    asyncio.run(application.discuss("d1", LEARNER, course_ref, FEEDBACK))
    before = calls(application)
    with pytest.raises(Conflict, match="has a newer version"):
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
    assert failed.json()["message"] == (
        "The change could not be made (the model is not available now). "
        "The earlier lesson version stays; you can try again."
    )
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
            support('Find the word "fills". What comes after it?', (READING, "light_prompt")),
            support("Answer with: He fills it with ___.", (REPORT, "scaffold")),
        ],
        "exit_text": "Let's skip this one for now.",
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
            support('Look at the sentence with "pours".', (REPORT, "light_prompt")),
            support("Say it in the past: He ___ the water into a bowl.", (REPORT, "scaffold")),
        ],
    }
    # A short observation: a practice lesson would need three task types and 20 minutes.
    draft = science_draft(context) | {
        "lesson_type": "observation",
        "tasks": [typed(answer), typed(report)],
        "resources": [],
    }
    draft["demand_mapping"] = [
        {"index": item["index"], "implementation": "Done in this lesson.", "task_ids": ["cup-pour"]}
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
    assert context["demand"]["skill_focus"][0]["label"] == "Past simple"
    assert context["demand"]["topic"] == "The Paper Bridge"
    assert ("topic", "The Paper Bridge") in [
        (item["field"], item["requirement"]) for item in context["requirements"]
    ]
    assert any(item["field"] == "skill_focus" for item in context["requirements"])
    assert calls(app) == 2  # the unmapped skill was sent back for repair
    assert "attempt=2 material=model tasks=2 read_aloud=0 resources=0 focus=1" in caplog.text
