"""Validate executable course definitions, independently of any model."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KINDS = {"ack", "answer", "action", "practice", "offscreen", "experience", "summary"}


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def validate(course):
    errors = []
    required = {"schema_version", "id", "version", "title", "primary_target",
                "secondary_target", "duration_minutes", "materials", "tasks",
                "start", "readiness", "provenance", "phase_minutes"}
    if not isinstance(course, dict) or not required <= course.keys():
        return ["课程缺少必需字段"]
    if course["schema_version"] != 1:
        errors.append("未知课程格式版本")
    if course["duration_minutes"] != sum(course["phase_minutes"].values()):
        errors.append("阶段预算与总时长不一致")
    tasks = course["tasks"]
    if not isinstance(tasks, list) or not tasks:
        return errors + ["课程缺少任务"]
    ids = [t.get("id") for t in tasks]
    if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
        errors.append("任务ID缺失或重复")
    by_id = {t.get("id"): t for t in tasks}
    if course["start"] not in by_id:
        errors.append("起始任务不存在")
    for t in tasks:
        name = t.get("id", "?")
        if t.get("kind") not in KINDS or not t.get("prompt"):
            errors.append(name + "：任务类型或提示缺失")
        nxt = t.get("next")
        if nxt is not None and nxt not in by_id:
            errors.append(name + "：转场目标不存在")
        if t.get("material") and t["material"] not in course["materials"]:
            errors.append(name + "：素材不存在")
        if t.get("kind") in {"answer", "action"}:
            rubric = t.get("rubric", {})
            if not rubric.get("expected") or not rubric.get("evidence"):
                errors.append(name + "：答案或依据缺失")
            if not t.get("correction") or len(t.get("help", [])) < 3:
                errors.append(name + "：纠错或帮助路径缺失")
        if t.get("kind") == "offscreen":
            if len(t.get("questions", [])) != 2:
                errors.append(name + "：离屏需要两题")
            for q in t.get("questions", []):
                if not q.get("rubric", {}).get("evidence"):
                    errors.append(name + "：离屏答案依据缺失")
        if t.get("kind") == "summary" and nxt is not None:
            errors.append(name + "：收尾之后不能追加题目")
    visited, current = set(), course["start"]
    while current in by_id and current not in visited:
        visited.add(current)
        current = by_id[current].get("next")
    if current is not None:
        errors.append("主路径有循环或不能结束")
    if set(by_id) != visited:
        errors.append("存在不可达任务")
    if not any(t.get("kind") == "summary" for t in tasks):
        errors.append("缺少收尾")
    for name, material in course["materials"].items():
        if not material.get("text") or not material.get("title"):
            errors.append(name + "：素材为空")
    return errors


def load_course(path=None):
    path = Path(path) if path else ROOT / "courses/paper-bridge/lesson.json"
    course = json.loads(path.read_text(encoding="utf-8"))
    errors = validate(course)
    if errors:
        raise ValueError("；".join(errors))
    return course


def readiness(course):
    """A checklist is not a readiness attestation."""
    names = ("content_review", "runtime_tests", "adult_voice_rehearsal", "fallback_review")
    checks = course["readiness"].get("checks", {})
    missing = [name for name in names if checks.get(name, {}).get("result") != "passed"
               or checks.get(name, {}).get("course_hash") != canonical_hash({
                   k: v for k, v in course.items() if k != "readiness"})
               or not checks.get(name, {}).get("evidence_ref")]
    # Initial release has no formal-child runner, even with hand-edited attestations.
    return {"formal_available": False, "adult_available": True,
            "missing": missing, "reason": "0.1版仅供成人开发验证，正式儿童执行入口尚未发布"}
