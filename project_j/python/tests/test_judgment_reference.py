import asyncio
import json
import shutil
from pathlib import Path

import pytest
from support import answer, deliver_all, read_story, start

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
    summary_lines,
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
        ("Help me", "answer", "answer", True),
        ("I still don't understand", "answer", "answer", True),
        ("I need help", "answer", "answer", True),
        ("I don't know", "answer", "answer", False),
        ("帮我一下", "answer", "answer", False),
        ("", "help", "answer", True),
        ("help me", "answer", "offscreen", True),
        ("Mom helped me; the paper was folded", "answer", "offscreen", False),
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
    assert len(digest) == 64 and len(reference.cases) == 61
    assert reference.version == "2.2.0"


def test_reference_set_two_is_english():
    from project_j.english import chinese, han

    reference, _ = load_reference(ROOT)
    chinese_cases = {"chinese", "mixed"}
    for case in reference.cases:
        if case.category in chinese_cases or case.case_id == "P1-chinese-report":
            assert han(case.answer), case.case_id
        else:
            assert not han(case.answer), case.case_id
        assert not chinese((case.forbidden, case.required_any)), case.case_id
    asking = [c for c in reference.cases if c.category in chinese_cases]
    assert len(asking) == 12
    assert all(c.expected == "unclear" and c.required_any == ("English",) for c in asking)
    added = {c.case_id: (c.category, c.expected) for c in reference.cases[50:54]}
    assert added == {
        "F1-typo": ("variant", "correct"),
        "F1-dont-understand": ("help", "help"),
        "P1-report": ("report", "reported"),
        "P1-chinese-report": ("report", "unclear"),
    }


def test_readings_follow_the_tenth_rule():
    reference, _ = load_reference(ROOT)
    readings = {c.case_id: c.expected for c in reference.cases if c.category == "read_aloud"}
    assert readings == {
        "RA-good": "correct",
        "RA-mispronounced": "correct",  # One flagged word of 53 is named, not failed.
        "RA-omitted": "correct",
        "RA-unknown": "unclear",
        "RA-many": "incorrect",  # Six of 53 is more than a tenth (ADR-0028).
    }


def test_grade_fails_a_reply_with_chinese():
    reference, _ = load_reference(ROOT)
    case = reference.cases[0]
    task = reference_tasks(ROOT)[case.source, case.task_id]
    reply = ClassroomJudgment(judgment=case.expected, evidence=task.evidence, text="Good, 很好")
    assert "chinese" in grade(case, task, reply, None)


def test_the_open_cases_follow_the_first_37():
    reference, _ = load_reference(ROOT)
    tasks = reference_tasks(ROOT)
    added = reference.cases[37:50]
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
                first.model_copy(update={"case_id": "x1", "answer": "Help me"}),  # help rule
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
    read_story(core)  # now at reading-fact
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
        ({"judgment": "incorrect", "text": "Go back to the second sentence."}, None, []),
        ({"judgment": "correct", "text": "Right."}, None, ["judgment:correct"]),
        ({"judgment": "incorrect", "text": "She wants a bridge."}, None, ["leak:bridge"]),
        ({"judgment": "incorrect", "text": "再找找。"}, None, ["chinese"]),
        ({"judgment": "incorrect", "text": "She wants a BRIDGE."}, None, ["leak:bridge"]),
        ({"judgment": "incorrect", "text": "You said a car. Look again."}, None, []),
        (
            {"judgment": "incorrect", "text": "Look again.", "evidence": "other words"},
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
    judged = ClassroomJudgment(judgment="help", evidence=task.evidence, text="Think about it.")
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
    assert len(cases) == 61
    service = ModelService.simulated()

    async def invoke(request, binding):
        case = cases[key(request.context)]
        if case.case_id in slow:
            await asyncio.sleep(1)
        if case.case_id in invalid:
            return AdapterReply(data={"other": 1})
        text = case.required_any[0] if case.required_any else "Look at the story again."
        data = {"judgment": case.expected, "evidence": request.context["evidence"], "text": text}
        return AdapterReply(data=data)

    service.adapters["simulated"].invoke = invoke
    return service


def test_run_reference_counts_failures(monkeypatch):
    monkeypatch.setattr(reference_module, "DEADLINE_SECONDS", 0.2)
    service = scripted_service(slow={"F1-variant-1"}, invalid={"B2-variant"})
    report = asyncio.run(run_reference(service, ROOT, repeats=2))
    assert report["summary"]["total"] == 122 and report["summary"]["failed"] == 4
    assert report["summary"]["failed_cases"] == ["B2-variant", "F1-variant-1"]
    assert report["summary"]["all_passed"] is False
    failures = {r["failure"] for r in report["results"] if not r["passed"]}
    assert failures == {"timeout", "invalid_output"}
    assert report["source_type"] == "technical" and report["qualification"] == "not_granted"


def test_run_reference_reruns_only_the_selected_cases():
    service = scripted_service()
    selected = frozenset({"F1-mixed-2"})
    report = asyncio.run(run_reference(service, ROOT, repeats=3, case_ids=selected))
    assert [r["case_id"] for r in report["results"]] == ["F1-mixed-2"] * 3
    assert len(service.calls) == 3
    assert report["reference"]["selected"] == ["F1-mixed-2"] and report["reference"]["cases"] == 61
    assert "只跑了所选题目（F1-mixed-2），不是整套结果" in summary_lines(report, Path("r.json"))
    full = asyncio.run(run_reference(scripted_service(), ROOT))
    assert full["reference"]["selected"] is None
    assert not any("所选题目" in line for line in summary_lines(full, Path("r.json")))


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
    assert saved["summary"]["all_passed"] and saved["reference"]["cases"] == 61


def test_cli_refuses_existing_report(tmp_path, monkeypatch):
    copy_courses(tmp_path)
    monkeypatch.setattr(reference_module, "load_service", lambda files: scripted_service())
    assert main(["--run-id", "ref1", "--root", str(tmp_path)]) == 0
    before = report_path(tmp_path).read_bytes()
    assert main(["--run-id", "ref1", "--root", str(tmp_path)]) == 2
    assert report_path(tmp_path).read_bytes() == before


def test_cli_refuses_unknown_case(tmp_path, monkeypatch):
    copy_courses(tmp_path)
    monkeypatch.setattr(reference_module, "load_service", lambda files: scripted_service())
    with pytest.raises(SystemExit) as exit_info:
        main(["--run-id", "ref1", "--case", "no-such-case", "--root", str(tmp_path)])
    assert exit_info.value.code == 2
    assert not report_path(tmp_path).exists()


def test_cli_writes_a_partial_report(tmp_path, monkeypatch):
    copy_courses(tmp_path)
    monkeypatch.setattr(reference_module, "load_service", lambda files: scripted_service())
    args = ["--run-id", "ref1", "--case", "F1-mixed-2", "--repeats", "2", "--root", str(tmp_path)]
    assert main(args) == 0
    saved = json.loads(report_path(tmp_path).read_text())
    assert saved["reference"]["selected"] == ["F1-mixed-2"] and saved["summary"]["total"] == 2


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


def test_exact_questions_alone_carry_the_extra_content_rule():
    from project_j.classroom.judgment import ANSWER_INSTRUCTIONS

    reference, _ = load_reference(ROOT)
    tasks = reference_tasks(ROOT)
    for case in reference.cases:
        task = tasks[(case.source, case.task_id)]
        request = reference_request(case, task)
        assert (ANSWER_INSTRUCTIONS in request.instructions) == (task.purpose == "answer")
    extra = {c.case_id: c.expected for c in reference.cases[55:]}
    assert extra == {
        "F1-extra-next": "incorrect",
        "F1-extra-paragraph": "incorrect",
        "F2-extra": "incorrect",
        "B1-extra": "incorrect",
        "F1-quote": "correct",
        "B2-restate": "correct",
    }
