from project_j.contracts import Document


def interpret(
    fact: Document, feedbacks: list[Document], source_revision: int, purpose: str
) -> dict:
    corrections = [
        correction for feedback in feedbacks for correction in feedback.body.get("corrections", [])
    ]
    tasks = []
    for attempt in fact.body["attempts"]:
        affected = [
            correction
            for correction in corrections
            if not correction.get("task_id") or correction["task_id"] == attempt["task_id"]
        ]
        help_map = attempt["help"]
        judgment = (
            "needs_help"
            if any(value not in {"none", "unknown"} for value in help_map.values())
            else "unknown"
        )
        # Correctness of a task never fills unobserved independence, retention or transfer.
        tasks.append(
            {
                "task_id": attempt["task_id"],
                "attempt_id": attempt["attempt_id"],
                "task_instance_id": attempt["task_instance_id"],
                "dimension_id": attempt["dimension_id"],
                "task_judgment": "unclear"
                if affected or attempt.get("recognition_revisions")
                else attempt["judgment"],
                "working_judgment": "unknown" if affected else judgment,
                "help": help_map,
                "external_help": "unknown",
                "retention": None,
                "transfer": None,
                "evidence_ref": fact.ref.model_dump(),
                "corrections": affected,
                "limitations": attempt["limitations"],
            }
        )
    return {
        "status": "checked",
        "activity_id": fact.body["activity_id"],
        "fact_ref": fact.ref.model_dump(),
        "feedback_refs": [fb.ref.model_dump() for fb in feedbacks],
        "source_revision": source_revision,
        "basis_version": "fixed-simulation-1",
        "purpose": purpose,
        "tasks": tasks,
        "alignment": {
            "goals_scope": "按实际已发生范围，未做任务不补造。",
            "tasks_materials": fact.body["course_ref"],
            "help_difficulty": "帮助与任务条件分维度，外部帮助未知。",
            "process": fact.body["end"],
            "time_burden": "模拟流程；实际30分钟与家庭负担未验证。",
        },
        "limitations": ["成人/技术/模拟不能转为真实儿童学习证据。", "保持、迁移与独立条件未观察。"],
        "actual_api": "未验证",
        "actual_voice": "未验证",
    }


def next_suggestion(evaluation: Document, material: dict) -> dict:
    """material names the next fixed story; an already-read story is review, not new material."""
    title, focus = material["title"], material["focus"]
    if material["familiar"]:
        direction, change = "已读材料复习及英语支架巩固", "已读材料与首次新材料分开取样"
        first = (
            f"复习已读的{title}，观察{focus}的文本依据；熟悉条件下的回答不作首次新材料观察或迁移。"
        )
    else:
        direction, change = "新材料短观察及英语支架巩固", "新材料与实际条件分别取样"
        first = f"用{title}观察{focus}的文本依据。"
    return {
        "status": "checked",
        "evaluation_ref": evaluation.ref.model_dump(),
        "direction": direction,
        "primary_change": change,
        "material": material,
        "sampling": [first, "初答与帮助后再试分开。"],
        "help": ["保留英语短报告句框，科学条件可先用中文报告。"],
        "exit": "原30分钟预算内，挑战替换；允许跳过、暂停、停止。",
        "review": "保持与迁移需要后续新日期和新条件证据。",
        "limitations": ["候选建议，须learning采用并保存需求后才能备课。"],
    }
