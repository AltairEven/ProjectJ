"""Classroom judgment reference set: fixed adult-written answers checked against the bound model."""

import argparse
import asyncio
import functools
import hashlib
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import version
from math import ceil
from pathlib import Path
from statistics import mean, median
from typing import Literal

from pydantic import Field

from project_j.application.acceptance import fingerprint
from project_j.application.progress import in_class
from project_j.application.settings import FIXED
from project_j.classroom.harness import is_help_request
from project_j.classroom.judgment import ClassroomJudgment, judgment_request, method_context
from project_j.contracts import Contract, RevisionRef
from project_j.english import CHINESE
from project_j.learning.fixtures import AUXILIARY_GOAL, PRIMARY_GOAL
from project_j.paths import check_path
from project_j.platform.models.config import ModelFiles
from project_j.platform.models.contracts import ModelRequest
from project_j.platform.models.registry import load_service
from project_j.platform.models.service import ModelService
from project_j.platform.speech.contracts import PronunciationResult, WordScore
from project_j.platform.speech.local import LIMITATIONS
from project_j.platform.storage import Conflict, check_run_id
from project_j.preparation.contracts import TaskDefinition
from project_j.preparation.packages import COURSES, course_tasks, story_text
from project_j.preparation.reading import words
from project_j.teacher.methods import JANE

log = logging.getLogger(__name__)

REFERENCE = "courses/paper-bridge/judgment-reference.json"
# Fixed text forms the courses lack (a fact page, a dialogue), for the reference set only.
FORMS_TASKS = "courses/judgment-forms/tasks.json"
PROJECT_ROOT = Path(__file__).resolve().parents[5]  # The build being checked, not --root.
DEADLINE_SECONDS = 10  # The classroom's judgment deadline.
REPORTS = "project_j/private/study"  # Each run's own folder: <run_id>/judgment-reference.json.
SECONDS_PER_CASE = 4  # Rough page estimate of one judgment call.
RUNNING = "AI work is running; run the check after it finishes."
CLASS_RUNNING = "A class is running; run the check after it ends."
NOT_PROBED = "The current setup has not passed the probe."
NOTHING = "There is nothing to rerun."
CHECKING = "The classroom judgment check is running; start after it finishes."


class RunStopped(RuntimeError):
    """The model service stopped answering (e.g. the service shut down); no report is written."""


class PronunciationFixture(Contract):
    status: Literal["scored", "unknown"]
    reason: Literal["no_speech", "too_short", "noisy", "failed"] | None = None
    flagged: dict[str, tuple[float, Literal["mispronounced", "omitted"]]] = {}


class ReferenceCase(Contract):
    case_id: str = Field(min_length=1)
    category: Literal[
        "correct",
        "variant",
        "chinese",
        "mixed",
        "incorrect",
        "unclear",
        "help",
        "after_help",
        "read_aloud",
        "report",
        "practice",
    ]
    source: str
    task_id: str
    answer: str = ""
    intent: Literal["answer", "help"] = "answer"
    help_count: int = Field(default=0, ge=0)
    pronunciation: PronunciationFixture | None = None
    expected: Literal["correct", "incorrect", "unclear", "help", "reported"]
    forbidden: tuple[str, ...] = ()
    required_any: tuple[str, ...] = ()
    basis: str = Field(min_length=1)


class ReferenceSet(Contract):
    version: str
    course: str
    authored: str
    cases: tuple[ReferenceCase, ...]


def load_reference(root: Path) -> tuple[ReferenceSet, str]:
    raw = (root / REFERENCE).read_bytes()
    return ReferenceSet.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


def reference_tasks(root: Path) -> dict[tuple[str, str], TaskDefinition]:
    # The context never carries the material reference, so a fixed placeholder suffices.
    material = RevisionRef(object_id="judgment-reference", revision=1)
    forms = json.loads((root / FORMS_TASKS).read_text())
    return {
        (source, task.task_id): task
        for source in COURSES
        for task in course_tasks(
            source,
            story_text((root / source).read_text()),
            material,
            PRIMARY_GOAL.dimension_id,
            AUXILIARY_GOAL.dimension_id,
        )
    } | {
        (source, task["task_id"]): TaskDefinition.model_validate(task)
        for source, items in forms.items()
        for task in items
    }


def validate(reference: ReferenceSet, tasks: dict[tuple[str, str], TaskDefinition]) -> list[str]:
    problems, seen = [], set()
    for case in reference.cases:
        name = case.case_id
        if name in seen:
            problems.append(f"{name}：题号重复")
        seen.add(name)
        task = tasks.get((case.source, case.task_id))
        if task is None:
            problems.append(f"{name}：课程中没有题目{case.task_id}")
            continue
        if (case.pronunciation is None) == (task.purpose == "read_aloud"):
            problems.append(f"{name}：只有朗读题带评分结果")
        if case.pronunciation and task.reference_text:
            present = {word.casefold() for word in words(task.reference_text)}
            missing = [w for w in case.pronunciation.flagged if w.casefold() not in present]
            if missing:
                problems.append(f"{name}：标记的词不在本段原文：{'、'.join(missing)}")
        if (case.expected == "help") != is_help_request(task, case.answer, case.intent):
            problems.append(f"{name}：预期与核心的求助判定不一致")
        hits = [w for w in case.forbidden if w.casefold() in case.answer.casefold()]
        if hits:
            problems.append(f"{name}：禁止词出现在本题作答里：{'、'.join(hits)}")
        asked = [w for w in case.forbidden if w.casefold() in task.prompt.casefold()]
        if asked:
            problems.append(f"{name}：禁止词出现在题目里，复述题目不算泄露：{'、'.join(asked)}")
    return problems


def pronunciation_for(fixture: PronunciationFixture, reference_text: str) -> PronunciationResult:
    identity = dict(
        service="reference-fixture",
        version="1",
        simulated=True,
        model=None,
        thresholds="reference-fixture",
        limitations=LIMITATIONS,
    )
    if fixture.status == "unknown":
        return PronunciationResult(status="unknown", reason=fixture.reason, **identity)
    flagged = {word.casefold(): mark for word, mark in fixture.flagged.items()}
    scores = [
        WordScore(word=word, accuracy=mark[0], error=mark[1])
        if (mark := flagged.get(word.casefold()))
        else WordScore(word=word, accuracy=90.0, error="none")
        for word in words(reference_text)
    ]
    read = sum(score.error != "omitted" for score in scores)
    return PronunciationResult(
        status="scored",
        words=tuple(scores),
        accuracy=mean(score.accuracy for score in scores),
        completeness=100 * read / len(scores),
        pauses=1,
        words_per_minute=90.0,
        **identity,
    )


def reference_request(case: ReferenceCase, task: TaskDefinition) -> ModelRequest:
    """Exactly what the classroom core sends for this input (see the equality test)."""
    pronunciation = (
        pronunciation_for(case.pronunciation, task.reference_text or "")
        if case.pronunciation
        else None
    )
    return judgment_request(
        task,
        case.answer,
        case.help_count,
        is_help_request(task, case.answer, case.intent),
        DEADLINE_SECONDS,
        pronunciation,
        jane_method=method_context(JANE),
        goals=(PRIMARY_GOAL.description, AUXILIARY_GOAL.description),
    )


def grade(
    case: ReferenceCase,
    task: TaskDefinition,
    output: ClassroomJudgment | None,
    failure: str | None,
) -> list[str]:
    if failure or output is None:
        return [f"model_failure:{failure}"]
    reasons = []
    if output.judgment != case.expected:
        reasons.append(f"judgment:{output.judgment}")
    if output.evidence != task.evidence:
        reasons.append("evidence_changed")  # The core would reject the proposal.
    if CHINESE.search(output.text):  # The student hears English only (ADR-0021).
        reasons.append("chinese")
    text = output.text.casefold()
    # ponytail: substring heuristic for leaks; the report keeps every text for adult reading.
    reasons += [f"leak:{word}" for word in case.forbidden if word.casefold() in text]
    if case.required_any and not any(word.casefold() in text for word in case.required_any):
        reasons.append("missing_required")
    return reasons


async def run_reference(
    service: ModelService,
    root: Path,
    repeats: int = 1,
    case_ids: frozenset[str] = frozenset(),
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Sequential calls, no retry: a failure counts exactly as it would in class.

    case_ids reruns only those cases (to settle a failure); the report then says it is partial.
    """
    reference, sha256 = load_reference(root)
    tasks = reference_tasks(root)
    if problems := validate(reference, tasks):
        raise ValueError("；".join(problems))
    binding = service.bindings["classroom_judgment"]
    adapter = service.adapters.get(binding.adapter)
    results, unavailable = [], 0
    chosen = [case for case in reference.cases if not case_ids or case.case_id in case_ids]
    for case in chosen:
        task = tasks[case.source, case.task_id]
        for repeat in range(1, repeats + 1):
            result = await service.run(reference_request(case, task))
            call = service.calls[-1]
            reasons = grade(case, task, result.output, result.failure)
            # ponytail: two in a row means the runtime is gone (a restart kills it); one alone is
            # a failed call, which a rerun settles.
            unavailable = unavailable + 1 if result.failure == "unavailable" else 0
            if unavailable == 2:
                log.warning(
                    "judgment reference stopped calls=%d reason=unavailable", len(results) + 1
                )
                raise RunStopped(
                    "The model service stopped answering; the run ended without a report. "
                    "Run it again."
                )
            results.append(
                {
                    "case_id": case.case_id,
                    "category": case.category,
                    "repeat": repeat,
                    "answer": case.answer,
                    "help_count": case.help_count,
                    "expected": case.expected,
                    "judgment": result.output.judgment if result.output else None,
                    "text": result.output.text if result.output else None,
                    "evidence_ok": bool(result.output) and result.output.evidence == task.evidence,
                    "passed": not reasons,
                    "reasons": reasons,
                    **{k: call[k] for k in ("failure", "elapsed_ms", "request_id", "usage")},
                }
            )
            if progress:
                progress(len(results), len(chosen) * repeats)
    timings = sorted(r["elapsed_ms"] for r in results if r["failure"] is None)
    categories = {}
    for result in results:
        counts = categories.setdefault(result["category"], {"total": 0, "passed": 0})
        counts["total"] += 1
        counts["passed"] += result["passed"]
    passed = sum(result["passed"] for result in results)
    return {
        "kind": "judgment_reference",
        "source_type": "technical",
        "realm": "study",
        "qualification": "not_granted",
        "captured_at": datetime.now(UTC).isoformat(),
        "fingerprint": fingerprint(PROJECT_ROOT),
        "reference": {
            "path": REFERENCE,
            "version": reference.version,
            "sha256": sha256,
            "cases": len(reference.cases),
            "selected": sorted(case_ids) or None,
        },
        "check_key": check_key(service),
        "binding": binding.model_dump(),
        "adapter_version": adapter.version if adapter else None,
        "simulated": adapter.simulated if adapter else None,
        "repeats": repeats,
        "results": results,
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "by_category": categories,
            "failed_cases": sorted({r["case_id"] for r in results if not r["passed"]}),
            # Judgment time only, not the voice latency from submit to first playback.
            "judgment_ms": {
                "median": median(timings) if timings else None,
                "p95": timings[ceil(0.95 * len(timings)) - 1] if timings else None,
            },
            "all_passed": passed == len(results),
        },
    }


@functools.cache
def request_digest() -> str:
    """Every request the set sends, built by the code being checked; fixed while it runs."""
    reference, _ = load_reference(PROJECT_ROOT)
    tasks = reference_tasks(PROJECT_ROOT)
    hasher = hashlib.sha256()
    for case in reference.cases:
        request = reference_request(case, tasks[case.source, case.task_id])
        text = json.dumps([request.instructions, request.context], sort_keys=True)
        hasher.update(text.encode())
    return hasher.hexdigest()


def check_key(service: ModelService) -> str:
    """What a report was run against: the requests, the classroom binding, adapter and SDK."""
    binding = service.bindings["classroom_judgment"]
    adapter = service.adapters.get(binding.adapter)
    parts = [
        request_digest(),
        binding.model_dump(),
        adapter.version if adapter else None,
        version("openai-codex"),
    ]
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def read_report(path: Path, key: str | None = None) -> dict | None:
    """A report, or None for a half-written or foreign file (or, given a key, another setup's)."""
    try:
        text = path.read_text()
        return json.loads(text) if key is None or key in text else None  # Parse only a match.
    except OSError, ValueError:
        return None


def judged_wrong(result: dict) -> bool:
    return any(not reason.startswith("model_failure:") for reason in result["reasons"])


def judgment_check(root: Path, service: ModelService) -> dict | None:
    """The reference-set state of the current classroom setup, from the reports under root."""
    binding = service.bindings.get("classroom_judgment")
    if binding is None:
        return None
    key, cases = check_key(service), len(load_reference(PROJECT_ROOT)[0].cases)
    fulls = [
        (report, path.parent)
        for path in (root / REPORTS).glob("*/judgment-reference.json")
        if (report := read_report(path, key))
        and report.get("check_key") == key
        and not report.get("reference", {}).get("selected")
    ]
    found = {
        "state": "not_run",
        "binding": binding.model_dump(),
        "cases": cases,
        "estimate_seconds": cases * SECONDS_PER_CASE,
        "report": None,
        "rerun_cases": [],
    }
    if not fulls:
        return found
    full, folder = max(fulls, key=lambda item: item[0]["captured_at"])
    reruns = [r for p in sorted(folder.glob("rerun-*.json")) if (r := read_report(p))]
    rerun_results = [result for rerun in reruns for result in rerun["results"]]
    failed = [r for r in full["results"] if not r["passed"]]
    failed += [r for r in rerun_results if judged_wrong(r)]
    called = sorted({r["case_id"] for r in failed if not judged_wrong(r)})
    settled = {
        case_id
        for rerun in reruns
        for case_id in called
        if any(r["passed"] for r in rerun["results"] if r["case_id"] == case_id)
    }
    remaining = [case_id for case_id in called if case_id not in settled]
    state = "failed" if any(map(judged_wrong, failed)) else "needs_rerun" if remaining else "passed"
    summary = full["summary"]
    found |= {
        "state": state,
        "rerun_cases": remaining if state == "needs_rerun" else [],
        "report": {
            "run_id": folder.name,
            "captured_at": full["captured_at"],
            "passed": summary["passed"],
            "total": summary["total"],
            "median_ms": summary["judgment_ms"]["median"],
            "p95_ms": summary["judgment_ms"]["p95"],
            "settled_by_rerun": state == "passed" and bool(settled),
            "failed": [
                {k: r[k] for k in ("case_id", "reasons", "text")} for r in failed if judged_wrong(r)
            ],
        },
    }
    return found


def check_start(app, mode: Literal["full", "rerun"]) -> tuple[frozenset[str], Path]:
    """Refuse a run the page must not start; else the cases to run (none named: all) and the
    report to write: a full run gets its own folder, a rerun sits beside the full report."""
    if app.models.files is None:
        raise Conflict(FIXED)
    if in_class(app):
        raise Conflict(CLASS_RUNNING)
    if app.jobs:
        raise Conflict(RUNNING)
    if app.models.reasons("classroom_judgment"):
        raise Conflict(NOT_PROBED)
    reports = app.root / REPORTS
    if mode == "full":
        folder = reports / f"judgment-ref-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
        return frozenset(), folder / "judgment-reference.json"
    found = judgment_check(app.root, app.models)
    if found is None or found["state"] != "needs_rerun":
        raise Conflict(NOTHING)
    folder = reports / found["report"]["run_id"]
    rerun = folder / f"rerun-{len(list(folder.glob('rerun-*.json'))) + 1}.json"
    return frozenset(found["rerun_cases"]), rerun


async def reference_job(app, mode: str, case_ids: frozenset[str], path: Path, progress) -> None:
    started = time.perf_counter()
    # Each case once, a rerun too: a case that failed by a call passes when a rerun passes (user
    # decision 2026-11-19, formerly five times: a slow model made reruns longer than the set).
    report = await run_reference(app.models, PROJECT_ROOT, case_ids=case_ids, progress=progress)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    results, summary = report["results"], report["summary"]
    log.info(
        "judgment reference run mode=%s cases=%d passed=%d failed=%d model_failures=%d "
        "elapsed_ms=%.0f",
        mode,
        len({r["case_id"] for r in results}),
        summary["passed"],
        summary["failed"],
        sum(r["failure"] is not None for r in results),
        (time.perf_counter() - started) * 1000,
    )


def summary_lines(report: dict, path: Path) -> list[str]:
    binding, summary = report["binding"], report["summary"]
    timing = summary["judgment_ms"]
    lines = [
        "课堂判断：" + ":".join(str(binding[k]) for k in ("adapter", "model", "effort", "tier")),
        f"通过 {summary['passed']}/{summary['total']}",
        *(f"  {name}：{c['passed']}/{c['total']}" for name, c in summary["by_category"].items()),
        *(
            f"  不通过 {r['case_id']}（第{r['repeat']}次）：{'、'.join(r['reasons'])}"
            for r in report["results"]
            if not r["passed"]
        ),
        f"判断耗时中位 {timing['median']} 毫秒，95分位 {timing['p95']} 毫秒"
        "（仅为判断耗时，不是声音延迟；报告不授予使用资格）",
        f"报告：{path}",
    ]
    if selected := report["reference"]["selected"]:
        lines.insert(1, f"只跑了所选题目（{'、'.join(selected)}），不是整套结果")
    return lines


async def run_and_save(
    root: Path, path: Path, repeats: int, case_ids: frozenset[str] = frozenset()
) -> int:
    service = load_service(ModelFiles(root / "project_j/private/models"))
    try:
        if reasons := service.reasons("classroom_judgment"):
            print("课堂判断模型未就绪：" + "；".join(reasons))
            return 2
        try:
            report = await run_reference(service, root, repeats, case_ids)
        except ValueError as error:
            print(f"参考题集与课程不一致：{error}")
            return 2
        except RunStopped:
            print("模型服务连续不可用（例如服务已关闭），运行已中止，未写报告。")
            return 2
    finally:
        for adapter in service.adapters.values():
            if hasattr(adapter, "aclose"):
                await adapter.aclose()
    path.parent.mkdir(mode=0o700, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("\n".join(summary_lines(report, path)))
    return 0 if report["summary"]["all_passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="project-j-judge-reference",
        description="课堂判断参考题集：用课堂原样请求逐题调用当前绑定的课堂判断模型，比对预期并保存报告。",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--case", action="append", default=[], help="只跑这道题，可重复")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats 至少为 1")
    try:
        check_run_id(args.run_id)
    except ValueError:
        parser.error("运行编号只能含字母、数字、-、_，最长64字符")
    case_ids = frozenset(args.case)
    if case_ids and (unknown := case_ids - {c.case_id for c in load_reference(args.root)[0].cases}):
        parser.error("题集里没有这些题号：" + "、".join(sorted(unknown)))
    partition = args.root / "project_j/private/study"
    partition.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = check_path(partition, f"{args.run_id}/judgment-reference.json")
    if path.exists():
        print("该运行编号已有参考题集报告，换一个运行编号。")
        return 2
    return asyncio.run(run_and_save(args.root, path, args.repeats, case_ids))
