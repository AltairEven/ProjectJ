"""Fixed simulation template compiled from actual versioned source materials."""

from pathlib import Path

from project_j.contracts import RevisionRef
from project_j.learning.contracts import DemandBody
from project_j.preparation.contracts import CourseBody, Support, TaskDefinition


def compile_course(
    demand: DemandBody,
    demand_ref: RevisionRef,
    jane_ref: RevisionRef,
    brief_ref: RevisionRef,
    material_ref: RevisionRef,
    next_course: bool,
) -> CourseBody:
    if (
        demand.primary_goal.dimension_id != "english.reading.facts"
        or demand.auxiliary_goal.dimension_id != "english.speaking.report"
    ):
        raise ValueError("the fixed paper-bridge template does not cover the selected dimensions")
    if next_course:
        specifications = [
            (
                "reading-first",
                "Which bus did Ben try first?",
                "He puts the small bus on the bridge.",
                ("the small bus", "small", "小车", "小巴士"),
                ("the big bus", "大车"),
                "找到first try对应的句子，留意small和big。",
                "He first tries the ___ bus.",
            ),
            (
                "reading-result",
                "What happened when Ben tried the big bus?",
                "The paper bends. The big bus falls.",
                ("it fell", "the big bus falls", "纸弯了，车掉了", "大巴士掉了"),
                ("it stayed",),
                "对照第二次试验的最后两句。",
                "The paper ___ and the big bus ___.",
            ),
        ]
    else:
        specifications = [
            (
                "reading-fact",
                "What does Mia want to make?",
                "She wants a bridge for her car.",
                ("a bridge", "给车搭桥", "She wants to build a bridge for her toy car."),
                ("a car",),
                "找到含wants的句子，说说她已经有什么、还想做什么。",
                "She wants a ___.",
            ),
            (
                "reading-sequence",
                "What did Mia do to the new paper before the successful try?",
                "She folds a new piece of paper.",
                ("She folded it.", "把新纸折了"),
                ("added books",),
                "从Can I make a new bridge往下找到新纸发生的变化。",
                "She ___ a new piece of paper.",
            ),
        ]
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
        lesson_type="practice" if next_course else "observation",
        tasks=tuple(tasks),
        implementation=tuple(demand.sampling_requirements + demand.help_requirements)
        + (
            "新材料短观察、保留初答；语言支架后表现另记。"
            if next_course
            else "没有真实旧知，以首课观察开始。",
        ),
        parent_preview={
            "why": "按当前需求观察阅读事实与短表达，科学只按实际报告解释。",
            "materials": "纸、同一本书和同一辆小车；陪伴与当前条件开始前另报。",
            "help": "定位提示和不同句框分开；家长额外帮助随后补充。",
            "exit": "30分钟内，挑战只替换；可跳过、暂停或停止。",
        },
        limitations=("固定模拟编制；实际模型、声音、30分钟和儿童用途未验证。",),
    )


def material_source(project_root: Path, next_course: bool) -> tuple[str, str]:
    relative = (
        "courses/paper-bridge/reading-transfer.md"
        if next_course
        else "courses/paper-bridge/reading-main.md"
    )
    return relative, (project_root / relative).read_text()
