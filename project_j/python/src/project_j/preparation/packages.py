"""Fixed simulation template compiled from actual versioned source materials."""

from project_j.contracts import RevisionRef
from project_j.learning.contracts import DemandBody
from project_j.preparation.contracts import (
    CourseBody,
    RequirementMapping,
    Support,
    TaskDefinition,
    demand_requirements,
)

FIRST = "courses/paper-bridge/reading-main.md"
TRANSFER = "courses/paper-bridge/reading-transfer.md"
# The two fixed simulation courses are each other's next course, so course switching can loop.
COURSES = {
    FIRST: {
        "title": "The Paper Bridge",
        "focus": "想做什么与新纸变化",
        "next": TRANSFER,
        "tasks": [
            (
                "reading-fact",
                "What does Mia want to make?",
                "She wants a bridge for her car.",
                (
                    "a bridge",
                    "给车搭桥",
                    "She wants to build a bridge for her toy car.",
                    "She wants a bridge.",
                    "She wants a bridge for her car.",
                ),
                ("a car",),
                "找到含wants的句子，说说她已经有什么、还想做什么。",
                "She wants a ___.",
            ),
            (
                "reading-sequence",
                "What did Mia do to the new paper before the successful try?",
                "She folds a new piece of paper.",
                (
                    "She folded it.",
                    "把新纸折了",
                    "She folds a new piece of paper.",
                    "She folded a new piece of paper.",
                ),
                ("added books",),
                "从Can I make a new bridge往下找到新纸发生的变化。",
                "She ___ a new piece of paper.",
            ),
        ],
    },
    TRANSFER: {
        "title": "Ben’s Bridge",
        "focus": "先小后大及实际结果",
        "next": FIRST,
        "tasks": [
            (
                "reading-first",
                "Which bus did Ben try first?",
                "He puts the small bus on the bridge.",
                ("the small bus", "small", "小车", "小巴士", "He first tries the small bus."),
                ("the big bus", "大车"),
                "找到first try对应的句子，留意small和big。",
                "He first tries the ___ bus.",
            ),
            (
                "reading-result",
                "What happened when Ben tried the big bus?",
                "The paper bends. The big bus falls.",
                (
                    "it fell",
                    "the big bus falls",
                    "纸弯了，车掉了",
                    "大巴士掉了",
                    "The paper bends and the big bus falls.",
                ),
                ("it stayed",),
                "对照第二次试验的最后两句。",
                "The paper ___ and the big bus ___.",
            ),
        ],
    },
}


def next_material(taught: str, read: set[str]) -> dict:
    """The other course of the pair; a story the learner already read is never new material."""
    source = COURSES[taught]["next"]
    course = COURSES[source]
    return {
        "source": source,
        "title": course["title"],
        "focus": course["focus"],
        "familiar": source in read,
    }


def uses_new_material(demand: DemandBody) -> bool:
    """The template follows the saved demand: an adopted/modified suggestion means new material."""
    return any(choice.action in {"adopt", "modify"} for choice in demand.suggestion_choices)


def map_demand(demand: DemandBody, tasks: tuple[TaskDefinition, ...], source: str, choice: str):
    answers = tuple(task.task_id for task in tasks if task.purpose == "answer")
    offscreen = tuple(task.task_id for task in tasks if task.purpose == "offscreen")
    every = tuple(task.task_id for task in tasks)
    how = {
        "primary_goal": ("本包回答题均取该维度的文本依据。", answers),
        "auxiliary_goal": ("各回答题第二级支持为英语短句框，帮助后表达另记。", answers),
        "budget_minutes": ("预算各段合计30分钟，挑战只替换活动。", ()),
        "offscreen_requirements": ("离屏任务停止采集，返回后取实际报告。", offscreen),
        "fallback": ("全部任务以文字模拟呈现，不依赖声音评价。", every),
        "prerequisites": ("不预设先备，以首个未提示初答取样。", answers),
        "sampling_requirements": (
            f"使用{source}；每题先取未提示初答，帮助后再答另记尝试。",
            answers,
        ),
        "help_requirements": ("每题两级不同支持：定位提示与句框。", answers),
        "gaps": ("沿用：本包不验证此项，课后保持未知。", ()),
    }
    mapping = []
    for field, requirement in demand_requirements(demand):
        if field == "suggestion_choices":
            adopted = requirement.split(":", 1)[0] in {"adopt", "modify"}
            text = f"采纳建议，{choice}。" if adopted else "未采纳，模板不因该建议改变。"
            mapping.append(
                RequirementMapping(field=field, requirement=requirement, implementation=text)
            )
            continue
        text, task_ids = how[field]
        mapping.append(
            RequirementMapping(
                field=field, requirement=requirement, implementation=text, task_ids=task_ids
            )
        )
    return tuple(mapping)


def compile_course(
    demand: DemandBody,
    demand_ref: RevisionRef,
    jane_ref: RevisionRef,
    brief_ref: RevisionRef,
    material_ref: RevisionRef,
    source: str,
    familiar: bool,
) -> CourseBody:
    if (
        demand.primary_goal.dimension_id != "english.reading.facts"
        or demand.auxiliary_goal.dimension_id != "english.speaking.report"
    ):
        raise ValueError("the fixed paper-bridge template does not cover the selected dimensions")
    next_course = uses_new_material(demand)
    if source not in COURSES or (not next_course and source != FIRST):
        raise ValueError("material does not follow the saved demand")
    course = COURSES[source]
    title = course["title"]
    stage = "已读，复习" if familiar else "新材料" if next_course else "首课"
    specifications = course["tasks"]
    tasks = []
    for identity, prompt, evidence, correct, incorrect, hint, scaffold in specifications:
        tasks.append(
            TaskDefinition(
                task_id=identity,
                dimension_id=demand.primary_goal.dimension_id,
                purpose="answer",
                prompt=prompt,
                material_ref=material_ref,
                evidence=evidence,
                acceptable_simulated=correct,
                incorrect_simulated=incorrect,
                supports=(
                    Support(text=hint, help={demand.primary_goal.dimension_id: "light_prompt"}),
                    Support(
                        text=scaffold,
                        help={
                            demand.primary_goal.dimension_id: "scaffold",
                            demand.auxiliary_goal.dimension_id: "scaffold",
                        },
                    ),
                ),
                exit_text="可以保留未知，换一个短观察或跳过。",
            )
        )
    for identity, purpose, prompt in [
        ("point-paper", "point", "指一指你说的那张纸，再报告你指的是哪一张。"),
        (
            "paper-experiment",
            "offscreen",
            "离屏比较两次纸桥尝试。回来报告材料是否相同、改变了什么、实际发生什么及帮助。",
        ),
    ]:
        tasks.append(
            TaskDefinition(
                task_id=identity,
                dimension_id="science.observation.report",
                purpose=purpose,
                prompt=prompt,
                material_ref=material_ref,
                evidence="只有提供者实际报告可用；没有视觉观察，不声称看见动作。",
                acceptable_simulated=(),
                supports=(
                    Support(
                        text="先用中文说实际看到或做了什么。",
                        help={"science.observation.report": "light_prompt"},
                    ),
                    Support(
                        text="分开报告材料、改变和结果；不知道的部分保留未知。",
                        help={"science.observation.report": "scaffold"},
                    ),
                ),
                exit_text="没有做或没有观察也可以如实报告，不需要补课。",
                optional=purpose == "offscreen",
            )
        )
    tasks = tuple(tasks)
    return CourseBody(
        demand_ref=demand_ref,
        state_ref=demand.state_ref,
        plan_ref=demand.plan_ref,
        jane_ref=jane_ref,
        brief_ref=brief_ref,
        material_refs=(material_ref,),
        primary_goal=demand.primary_goal,
        auxiliary_goal=demand.auxiliary_goal,
        budget={
            "opening": 3,
            "reading": 7,
            "expression": 4,
            "help_practice": 6,
            "offscreen": 7,
            "closing": 3,
        },
        lesson_type="review" if familiar else "practice" if next_course else "observation",
        tasks=tasks,
        demand_mapping=map_demand(
            demand,
            tasks,
            source,
            f"选{title}复习模板，明示已读熟悉条件" if familiar else f"选{title}新材料短观察模板",
        ),
        parent_preview={
            "story": f"{title}（{stage}）",
            "why": "复习已读故事：熟悉条件下的回答不作首次新材料观察或迁移，科学只按实际报告解释。"
            if familiar
            else "按当前需求观察阅读事实与短表达，科学只按实际报告解释。",
            "materials": "纸、同一本书和同一辆小车；陪伴与当前条件开始前另报。",
            "help": "定位提示和不同句框分开；家长额外帮助随后补充。",
            "exit": "30分钟内，挑战只替换；可跳过、暂停或停止。",
        },
        limitations=("固定模拟编制；实际模型、声音、30分钟和儿童用途未验证。",)
        + (("已读材料复习：初答不代表首次阅读理解，也不作迁移证据。",) if familiar else ()),
    )
