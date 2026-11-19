import asyncio
import json
import logging
import shutil
from pathlib import Path

import pytest
from support import answer, deliver_all, read_story, start

import project_j.application.reference as reference_module
from project_j.application.reference import (
    FORMS_TASKS,
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
# Frozen: 2.3.0 only adds after these (the set only grows).
ORIGINAL_61 = """
F1-correct F1-variant-1 F1-variant-2 F1-chinese F1-mixed-1 F1-mixed-2 F1-wrong-known
F1-wrong-books F1-unclear-1 F1-unclear-2 F1-help-1 F1-help-2 F1-help-3 F1-after-help-right
F1-after-help-wrong F2-variant F2-chinese F2-mixed F2-wrong-cut F2-wrong-draw F2-unclear
B1-correct B1-variant B1-chinese B1-mixed B1-wrong B1-unclear B2-variant B2-chinese B2-mixed
B2-wrong-stayed B2-wrong-bus B2-unclear RA-good RA-mispronounced RA-omitted RA-unknown
O1-correct O1-variant O1-chinese O1-mixed O1-incorrect O1-short O1-unclear O1-help
O1-after-help O2-correct O2-chinese O2-incorrect O2-offtopic F1-typo F1-dont-understand
P1-report P1-chinese-report RA-many F1-extra-next F1-extra-paragraph F2-extra B1-extra F1-quote
B2-restate
""".split()


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
    assert len(digest) == 64 and len(reference.cases) == 92
    assert reference.version == "2.3.1"


def test_set_two_three_adds_forms_and_practice():
    reference, _ = load_reference(ROOT)
    tasks = reference_tasks(ROOT)
    added = reference.cases[61:]
    assert len(added) == 31
    assert all(c.case_id.startswith(("K", "D")) for c in added)
    assert {c.source for c in added} == {
        "courses/judgment-forms/fact-page.md",
        "courses/judgment-forms/dialogue.md",
    }
    practice = [c for c in added if c.category == "practice"]
    assert len(practice) == 9
    assert {c.expected for c in practice} == {"correct", "incorrect"}
    assert {tasks[c.source, c.task_id].purpose for c in practice} == {"answer", "open"}
    assert [c.case_id for c in reference.cases[:61]] == ORIGINAL_61  # only grows


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
    assert len(asking) == 13
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
                first.model_copy(update={"case_id": "x4", "forbidden": ("make",)}),  # the prompt's
            )
        }
    )
    problems = validate(broken, reference_tasks(ROOT))
    assert len(problems) == 5 and problems[-1] == "x4：禁止词出现在题目里，复述题目不算泄露：make"


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
    assert len(cases) == 92
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
    assert report["summary"]["total"] == 184 and report["summary"]["failed"] == 4
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
    assert report["reference"]["selected"] == ["F1-mixed-2"] and report["reference"]["cases"] == 92
    assert "只跑了所选题目（F1-mixed-2），不是整套结果" in summary_lines(report, Path("r.json"))
    full = asyncio.run(run_reference(scripted_service(), ROOT))
    assert full["reference"]["selected"] is None
    assert not any("所选题目" in line for line in summary_lines(full, Path("r.json")))


def copy_courses(root):
    for course in ("paper-bridge", "judgment-forms"):
        shutil.copytree(ROOT / "courses" / course, root / "courses" / course)


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
    assert saved["summary"]["all_passed"] and saved["reference"]["cases"] == 92


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
    extra = {c.case_id: c.expected for c in reference.cases[55:61]}
    assert extra == {
        "F1-extra-next": "incorrect",
        "F1-extra-paragraph": "incorrect",
        "F2-extra": "incorrect",
        "B1-extra": "incorrect",
        "F1-quote": "correct",
        "B2-restate": "correct",
    }


def test_answer_and_open_questions_carry_the_practice_rule():
    from project_j.classroom.judgment import PRACTICE_RULE

    reference, _ = load_reference(ROOT)
    tasks = reference_tasks(ROOT)
    for case in reference.cases:
        task = tasks[(case.source, case.task_id)]
        carried = PRACTICE_RULE in reference_request(case, task).instructions
        assert carried == (task.purpose in {"answer", "open"}), case.case_id
    assert "incorrect" in PRACTICE_RULE and "action word" in PRACTICE_RULE


def test_fingerprint_covers_the_forms_material(tmp_path):
    from project_j.application.acceptance import fingerprint

    for directory in ("project_j/web/codegen", "project_j/web/schema", "project_j/scripts"):
        (tmp_path / directory).mkdir(parents=True)
    for name in ("python/uv.lock", "python/pyproject.toml", "web/package-lock.json"):
        (tmp_path / "project_j" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "project_j" / name).write_text("x")
    forms = tmp_path / FORMS_TASKS
    forms.parent.mkdir(parents=True)
    forms.write_text("{}")
    before = fingerprint(tmp_path)
    forms.write_text('{"x": []}')
    assert fingerprint(tmp_path) != before


# --- The settings page's check: the latest full report for this exact setup ---


def check_report(
    root,
    run_id,
    results,
    *,
    name="judgment-reference.json",
    key=None,
    selected=None,
    captured="2026-11-19T03:00:00Z",
):
    """A full (or rerun) report as run_reference writes it, reduced to what the check reads."""
    from project_j.application.reference import REPORTS, check_key

    path = root / REPORTS / run_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    passed = sum(r["passed"] for r in results)
    report = {
        "captured_at": captured,
        "check_key": key or check_key(ModelService.simulated()),
        "reference": {"selected": selected},
        "results": results,
        "summary": {
            "passed": passed,
            "total": len(results),
            "judgment_ms": {"median": 900, "p95": 1500},
        },
    }
    path.write_text(json.dumps(report))


def outcome(case_id, *reasons, text="Look again."):
    return {"case_id": case_id, "passed": not reasons, "reasons": list(reasons), "text": text}


def check(root):
    from project_j.application.reference import judgment_check

    return judgment_check(root, ModelService.simulated())


def test_check_is_not_run_without_a_matching_report(tmp_path):
    from project_j.application.reference import REPORTS

    first = check(tmp_path)
    assert first["state"] == "not_run" and first["report"] is None and first["rerun_cases"] == []
    assert first["cases"] == 92 and first["estimate_seconds"] == 368
    assert first["binding"]["effort"] == "low"
    (tmp_path / REPORTS / "me").mkdir(parents=True)  # a study run without any report
    (tmp_path / REPORTS / "broken").mkdir()
    (tmp_path / REPORTS / "broken/judgment-reference.json").write_text('{"check_key": ')
    check_report(tmp_path, "cli-case", [outcome("F1-correct")], selected=["F1-correct"])
    check_report(tmp_path, "old-model", [outcome("F1-correct")], key="0" * 64)
    assert check(tmp_path)["state"] == "not_run"


def test_check_passes_on_a_clean_full_run(tmp_path):
    check_report(
        tmp_path,
        "older",
        [outcome("F1-correct", "judgment:incorrect")],
        captured="2026-11-19T01:00:00Z",
    )
    check_report(tmp_path, "judgment-ref-1", [outcome("F1-correct"), outcome("F2-variant")])
    found = check(tmp_path)
    assert found["state"] == "passed" and found["rerun_cases"] == []
    assert found["report"] == {
        "run_id": "judgment-ref-1",
        "captured_at": "2026-11-19T03:00:00Z",
        "passed": 2,
        "total": 2,
        "median_ms": 900,
        "p95_ms": 1500,
        "settled_by_rerun": False,
        "failed": [],
    }


def test_check_fails_on_a_judgment_error(tmp_path):
    results = [
        outcome("F1-correct", "judgment:incorrect", text="Try again."),
        outcome("F2-variant"),
    ]
    check_report(tmp_path, "judgment-ref-1", results)
    found = check(tmp_path)
    assert found["state"] == "failed"
    assert found["report"]["failed"] == [
        {"case_id": "F1-correct", "reasons": ["judgment:incorrect"], "text": "Try again."}
    ]


def test_check_needs_a_rerun_for_model_failures(tmp_path):
    results = [
        outcome("F2-variant", "model_failure:timeout", text=None),
        outcome("F1-correct", "model_failure:invalid_output", text=None),
        outcome("B1-correct"),
    ]
    check_report(tmp_path, "judgment-ref-1", results)
    found = check(tmp_path)
    assert found["state"] == "needs_rerun" and found["rerun_cases"] == ["F1-correct", "F2-variant"]


def test_one_passed_rerun_settles_it(tmp_path):  # user decision 2026-11-19: once, not five times
    check_report(tmp_path, "judgment-ref-1", [outcome("F1-correct", "model_failure:timeout")])
    check_report(tmp_path, "judgment-ref-1", [outcome("F1-correct")], name="rerun-1.json")
    found = check(tmp_path)
    assert found["state"] == "passed" and found["report"]["settled_by_rerun"] is True


def test_a_rerun_with_a_judgment_error_fails(tmp_path):
    check_report(tmp_path, "judgment-ref-1", [outcome("F1-correct", "model_failure:timeout")])
    reruns = [outcome("F1-correct", "judgment:incorrect")]
    check_report(tmp_path, "judgment-ref-1", reruns, name="rerun-1.json")
    found = check(tmp_path)
    assert found["state"] == "failed"
    assert {"case_id": "F1-correct", "reasons": ["judgment:incorrect"], "text": "Look again."} in (
        found["report"]["failed"]
    )


def test_a_rerun_that_times_out_again_still_needs_a_rerun(tmp_path):
    check_report(tmp_path, "judgment-ref-1", [outcome("F1-correct", "model_failure:timeout")])
    again = [outcome("F1-correct", "model_failure:timeout")]
    check_report(tmp_path, "judgment-ref-1", again, name="rerun-1.json")
    found = check(tmp_path)
    assert found["state"] == "needs_rerun" and found["rerun_cases"] == ["F1-correct"]
    check_report(tmp_path, "judgment-ref-1", [outcome("F1-correct")], name="rerun-2.json")
    assert check(tmp_path)["state"] == "passed"  # a later rerun settles it


def test_report_carries_the_check_key():
    from project_j.application.reference import check_key

    service = scripted_service()
    report = asyncio.run(run_reference(service, ROOT))
    assert report["check_key"] == check_key(service) and len(report["check_key"]) == 64


def test_check_is_none_without_a_classroom_binding(tmp_path):
    from project_j.application.reference import judgment_check

    unset = ModelService({}, {}, [], problem="No default model; run project-j-init configure")
    assert judgment_check(tmp_path, unset) is None


def test_a_pass_among_call_failures_in_a_rerun_settles_it(tmp_path):
    check_report(tmp_path, "judgment-ref-1", [outcome("F1-correct", "model_failure:timeout")])
    mixed = [outcome("F1-correct", "model_failure:unavailable"), outcome("F1-correct")]
    check_report(tmp_path, "judgment-ref-1", mixed, name="rerun-1.json")
    assert check(tmp_path)["state"] == "passed"  # one pass is enough (user decision 2026-11-19)


def stopping_service(after):
    """Answers as expected until `after` calls, then fails every call, as a dead runtime does."""
    service = scripted_service()
    answer = service.adapters["simulated"].invoke
    calls = []

    async def invoke(request, binding):
        calls.append(1)
        if len(calls) > after:
            raise RuntimeError("runtime gone")
        return await answer(request, binding)

    service.adapters["simulated"].invoke = invoke
    return service


def test_run_reference_stops_when_the_service_stops(caplog):
    from project_j.application.reference import RunStopped

    caplog.set_level(logging.INFO, logger="project_j")
    service = stopping_service(after=10)
    with pytest.raises(RunStopped):
        asyncio.run(run_reference(service, ROOT))
    assert len(service.calls) == 12  # two unavailable in a row end it
    assert "judgment reference stopped calls=12 reason=unavailable" in caplog.text


def test_one_unavailable_call_does_not_stop_the_run():
    service = scripted_service()
    answer = service.adapters["simulated"].invoke
    calls = []

    async def once(request, binding):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError("one turn failed")
        return await answer(request, binding)

    service.adapters["simulated"].invoke = once
    report = asyncio.run(run_reference(service, ROOT))
    assert report["summary"]["total"] == 92 and report["summary"]["failed"] == 1


def test_cli_writes_no_report_when_the_service_stops(tmp_path, monkeypatch, capsys):
    copy_courses(tmp_path)
    monkeypatch.setattr(reference_module, "load_service", lambda files: stopping_service(5))
    assert main(["--run-id", "ref1", "--root", str(tmp_path)]) == 2
    assert not report_path(tmp_path).exists()
    assert "未写报告" in capsys.readouterr().out
