import asyncio
import json
import shutil
from pathlib import Path

import pytest
from support import answer, deliver_all, start

import project_j.application.reference as reference_module
from project_j.application.reference import (
    REFERENCE,
    PronunciationFixture,
    grade,
    load_reference,
    main,
    pronunciation_for,
    reference_request,
    reference_tasks,
    run_reference,
    validate,
)
from project_j.classroom.harness import is_help_request
from project_j.classroom.judgment import ClassroomJudgment
from project_j.learning.fixtures import AUXILIARY_GOAL, PRIMARY_GOAL
from project_j.platform.models.contracts import AdapterReply
from project_j.platform.models.service import ModelService
from project_j.preparation.packages import FIRST, course_tasks, story_text

ROOT = Path(__file__).resolve().parents[3]


def test_course_tasks_match_the_prepared_course(application):
    core = asyncio.run(start(application))
    story = story_text((ROOT / FIRST).read_text())
    built = course_tasks(
        FIRST,
        story,
        core.course.tasks[0].material_ref,
        PRIMARY_GOAL.dimension_id,
        AUXILIARY_GOAL.dimension_id,
    )
    assert built == core.course.tasks
    assert (core.course.primary_goal, core.course.auxiliary_goal) == (
        PRIMARY_GOAL,
        AUXILIARY_GOAL,
    )


@pytest.mark.parametrize(
    ("text", "intent", "purpose", "expected"),
    [
        ("a car", "answer", "answer", False),
        ("帮我一下", "answer", "answer", True),
        ("还是不懂", "answer", "answer", True),
        ("I need help", "answer", "answer", True),
        ("我不知道", "answer", "answer", False),
        ("", "help", "answer", True),
        ("帮我", "answer", "offscreen", True),
        ("帮我先用中文说，纸折了", "answer", "offscreen", False),
    ],
)
def test_is_help_request(application, text, intent, purpose, expected):
    core = asyncio.run(start(application))
    task_id = "reading-fact" if purpose == "answer" else "paper-experiment"
    task = next(t for t in core.course.tasks if t.task_id == task_id)
    assert is_help_request(task, text, intent) is expected


def test_reference_set_is_valid_against_the_course():
    reference, digest = load_reference(ROOT)
    assert validate(reference, reference_tasks(ROOT)) == []
    assert len(digest) == 64 and len(reference.cases) == 50
    assert reference.version == "1.1.0"


def test_the_open_cases_were_added_without_changing_the_first_37():
    reference, _ = load_reference(ROOT)
    tasks = reference_tasks(ROOT)
    added = reference.cases[37:]
    assert {case.case_id for case in added} == {
        *(f"O1-{n}" for n in ("correct", "variant", "chinese", "mixed", "incorrect", "short")),
        *(f"O1-{n}" for n in ("unclear", "help", "after-help")),
        *(f"O2-{n}" for n in ("correct", "chinese", "incorrect", "offtopic")),
    }
    assert all(tasks[case.source, case.task_id].purpose == "open" for case in added)
    assert all(tasks[case.source, case.task_id].purpose != "open" for case in reference.cases[:37])


def test_reference_set_covers_the_required_classes():
    reference, _ = load_reference(ROOT)
    categories = {case.category for case in reference.cases}
    required = {"correct", "variant", "incorrect", "unclear", "help", "after_help", "read_aloud"}
    assert required <= categories
    assert {"chinese", "mixed"} & categories
    helps = {(c.task_id, c.help_count) for c in reference.cases if c.category == "help"}
    assert {("reading-fact", 0), ("reading-fact", 1)} <= helps  # two help requests on one task
    tasks = reference_tasks(ROOT)
    answered = [c for c in reference.cases if c.answer]
    listed = [
        c
        for c in answered
        if c.answer
        in tasks[c.source, c.task_id].acceptable_simulated
        + tasks[c.source, c.task_id].incorrect_simulated
    ]
    assert len(listed) * 2 <= len(answered)  # most answers are not copied from the package lists


def test_validate_reports_broken_cases():
    reference, _ = load_reference(ROOT)
    first = reference.cases[0]
    broken = reference.model_copy(
        update={
            "cases": (
                first.model_copy(update={"task_id": "missing"}),
                first.model_copy(update={"case_id": "x1", "answer": "帮我一下"}),  # help rule
                first.model_copy(update={"case_id": "x2", "forbidden": ("bridge",)}),  # own answer
                first.model_copy(
                    update={
                        "case_id": "x3",
                        "task_id": "read-aloud-1",
                        "answer": "",
                        "category": "read_aloud",
                        "pronunciation": PronunciationFixture(
                            status="scored", flagged={"rocket": (10, "mispronounced")}
                        ),
                    }
                ),  # flagged word not in the segment
            )
        }
    )
    assert len(validate(broken, reference_tasks(ROOT))) == 4


def test_pronunciation_fixture_marks_flagged_words():
    text = "She puts two books on the table. There is a space between the books."
    fixture = PronunciationFixture(
        status="scored", flagged={"between": (35, "mispronounced"), "table": (0, "omitted")}
    )
    scored = pronunciation_for(fixture, text)
    marked = {w.word: (w.accuracy, w.error) for w in scored.words if w.error != "none"}
    assert marked == {"between": (35, "mispronounced"), "table": (0, "omitted")}
    assert scored.completeness == pytest.approx(100 * 13 / 14)
    unknown = pronunciation_for(PronunciationFixture(status="unknown", reason="too_short"), text)
    assert unknown.status == "unknown" and unknown.words == ()


def case_and_task(case_id):
    reference, _ = load_reference(ROOT)
    case = next(c for c in reference.cases if c.case_id == case_id)
    return case, reference_tasks(ROOT)[case.source, case.task_id]


async def captured_classroom_request(application, text):
    core = await start(application)
    deliver_all(core)
    await answer(application, "听到了，先读故事再试纸桥。", "confirmation")  # now at reading-fact
    seen, original = [], core.models.run

    async def record(request):
        seen.append(request)
        return await original(request)

    core.models.run = record
    await answer(application, text)
    return seen[0]


def test_reference_request_equals_the_classroom_request(application):
    sent = asyncio.run(captured_classroom_request(application, "a car"))
    built = reference_request(*case_and_task("F1-wrong-known"))
    assert (built.purpose, built.instructions, built.output) == (
        sent.purpose,
        sent.instructions,
        sent.output,
    )
    assert built.context == sent.context


@pytest.mark.parametrize(
    ("output", "failure", "reasons"),
    [
        (None, "timeout", ["model_failure:timeout"]),
        ({"judgment": "incorrect", "text": "回到第二句再找找。"}, None, []),
        ({"judgment": "correct", "text": "对。"}, None, ["judgment:correct"]),
        ({"judgment": "incorrect", "text": "她想要一座桥。"}, None, ["leak:桥"]),
        ({"judgment": "incorrect", "text": "She wants a BRIDGE."}, None, ["leak:bridge"]),
        ({"judgment": "incorrect", "text": "你说的是 a car，再看看。"}, None, []),
        (
            {"judgment": "incorrect", "text": "再找找。", "evidence": "别的依据"},
            None,
            ["evidence_changed"],
        ),
    ],
)
def test_grade_reasons(output, failure, reasons):
    case, task = case_and_task("F1-wrong-known")
    judged = ClassroomJudgment(**({"evidence": task.evidence} | output)) if output else None
    assert grade(case, task, judged, failure) == reasons


def test_grade_requires_the_prepared_support():
    case, task = case_and_task("F1-help-2")
    judged = ClassroomJudgment(judgment="help", evidence=task.evidence, text="想想她要什么。")
    assert grade(case, task, judged, None) == ["missing_required"]


def scripted_service(slow=(), invalid=()):
    """Answers every case as expected, except slow (sleeps) and invalid (bad structure) ones."""
    reference, _ = load_reference(ROOT)
    tasks = reference_tasks(ROOT)

    def key(context):
        return json.dumps(context, sort_keys=True, ensure_ascii=False)

    cases = {
        key(reference_request(c, tasks[c.source, c.task_id]).context): c for c in reference.cases
    }
    assert len(cases) == 50
    service = ModelService.simulated()

    async def invoke(request, binding):
        case = cases[key(request.context)]
        if case.case_id in slow:
            await asyncio.sleep(1)
        if case.case_id in invalid:
            return AdapterReply(data={"other": 1})
        text = case.required_any[0] if case.required_any else "再看看原文。"
        data = {"judgment": case.expected, "evidence": request.context["evidence"], "text": text}
        return AdapterReply(data=data)

    service.adapters["simulated"].invoke = invoke
    return service


def test_run_reference_counts_failures(monkeypatch):
    monkeypatch.setattr(reference_module, "DEADLINE_SECONDS", 0.2)
    service = scripted_service(slow={"F1-variant-1"}, invalid={"B2-variant"})
    report = asyncio.run(run_reference(service, ROOT, repeats=2))
    assert report["summary"]["total"] == 100 and report["summary"]["failed"] == 4
    assert report["summary"]["failed_cases"] == ["B2-variant", "F1-variant-1"]
    assert report["summary"]["all_passed"] is False
    failures = {r["failure"] for r in report["results"] if not r["passed"]}
    assert failures == {"timeout", "invalid_output"}
    assert report["source_type"] == "technical" and report["qualification"] == "not_granted"


def copy_courses(root):
    shutil.copytree(ROOT / "courses/paper-bridge", root / "courses/paper-bridge")


def report_path(root, run_id="ref1"):
    return root / "project_j/private/study" / run_id / "judgment-reference.json"


def test_cli_refuses_unready_models(tmp_path):
    copy_courses(tmp_path)  # no model configuration under this root
    assert main(["--run-id", "ref1", "--root", str(tmp_path)]) == 2
    assert not report_path(tmp_path).exists()


def test_cli_writes_the_report(tmp_path, monkeypatch):
    copy_courses(tmp_path)
    monkeypatch.setattr(reference_module, "load_service", lambda files: scripted_service())
    assert main(["--run-id", "ref1", "--root", str(tmp_path)]) == 0
    saved = json.loads(report_path(tmp_path).read_text())
    assert saved["summary"]["all_passed"] and saved["reference"]["cases"] == 50


def test_cli_refuses_existing_report(tmp_path, monkeypatch):
    copy_courses(tmp_path)
    monkeypatch.setattr(reference_module, "load_service", lambda files: scripted_service())
    assert main(["--run-id", "ref1", "--root", str(tmp_path)]) == 0
    before = report_path(tmp_path).read_bytes()
    assert main(["--run-id", "ref1", "--root", str(tmp_path)]) == 2
    assert report_path(tmp_path).read_bytes() == before


def test_cli_refuses_invalid_set(tmp_path, monkeypatch):
    copy_courses(tmp_path)
    path = tmp_path / REFERENCE
    data = json.loads(path.read_text())
    data["cases"][0]["task_id"] = "missing"
    path.write_text(json.dumps(data, ensure_ascii=False))
    service = scripted_service()

    async def refuse(request, binding):
        raise AssertionError("no model call before the set is valid")

    service.adapters["simulated"].invoke = refuse
    monkeypatch.setattr(reference_module, "load_service", lambda files: service)
    assert main(["--run-id", "ref1", "--root", str(tmp_path)]) == 2
    assert not report_path(tmp_path).parent.exists()
