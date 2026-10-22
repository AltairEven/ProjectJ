"""Classroom judgment reference set: fixed adult-written answers checked against the bound model."""

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from statistics import mean, median
from typing import Literal

from pydantic import Field

from project_j.application.acceptance import fingerprint
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
from project_j.platform.storage import check_run_id
from project_j.preparation.contracts import TaskDefinition
from project_j.preparation.packages import COURSES, course_tasks, story_text
from project_j.preparation.reading import words
from project_j.teacher.methods import JANE

REFERENCE = "courses/paper-bridge/judgment-reference.json"
PROJECT_ROOT = Path(__file__).resolve().parents[5]  # The build being checked, not --root.
DEADLINE_SECONDS = 10  # The classroom's judgment deadline.


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


async def run_reference(service: ModelService, root: Path, repeats: int = 1) -> dict:
    """Sequential calls, no retry: a failure counts exactly as it would in class."""
    reference, sha256 = load_reference(root)
    tasks = reference_tasks(root)
    if problems := validate(reference, tasks):
        raise ValueError("；".join(problems))
    binding = service.bindings["classroom_judgment"]
    adapter = service.adapters.get(binding.adapter)
    results = []
    for case in reference.cases:
        task = tasks[case.source, case.task_id]
        for repeat in range(1, repeats + 1):
            result = await service.run(reference_request(case, task))
            call = service.calls[-1]
            reasons = grade(case, task, result.output, result.failure)
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
        },
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
    return lines


async def run_and_save(root: Path, path: Path, repeats: int) -> int:
    service = load_service(ModelFiles(root / "project_j/private/models"))
    try:
        if reasons := service.reasons("classroom_judgment"):
            print("课堂判断模型未就绪：" + "；".join(reasons))
            return 2
        try:
            report = await run_reference(service, root, repeats)
        except ValueError as error:
            print(f"参考题集与课程不一致：{error}")
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
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.repeats < 1:
        parser.error("--repeats 至少为 1")
    try:
        check_run_id(args.run_id)
    except ValueError:
        parser.error("运行编号只能含字母、数字、-、_，最长64字符")
    partition = args.root / "project_j/private/study"
    partition.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = check_path(partition, f"{args.run_id}/judgment-reference.json")
    if path.exists():
        print("该运行编号已有参考题集报告，换一个运行编号。")
        return 2
    return asyncio.run(run_and_save(args.root, path, args.repeats))
