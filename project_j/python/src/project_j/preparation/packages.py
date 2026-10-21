"""Fixed simulation template compiled from actual versioned source materials."""

import re

from project_j.contracts import RevisionRef
from project_j.learning.contracts import DemandBody
from project_j.preparation.contracts import (
    RequirementMapping,
    Support,
    TaskDefinition,
    demand_requirements,
)
from project_j.preparation.reading import segment_reading
from project_j.preparation.task_types import (
    CONTENT_TYPES,
    ChoiceContent,
    FillContent,
    MatchContent,
    OpenContent,
    OrderContent,
)

FIRST = "courses/paper-bridge/reading-main.md"
TRANSFER = "courses/paper-bridge/reading-transfer.md"
PRONUNCIATION = "english.speaking.pronunciation"
READING = "english.reading.facts"
CORRECT_TEXT = "对了，你找得很准。"
SAMPLE_EXIT = "这题先到这里，我们接着往下。"
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
        # (task id, prompt, evidence, content, first help, second help); clicked types first.
        "samples": [
            (
                "choice-first-try",
                "What happens the first time Mia puts her car on the paper?",
                "The paper bends. The car falls.",
                ChoiceContent(
                    variant="single",
                    options=(
                        "The car stays on the bridge.",
                        "The paper bends and the car falls.",
                        "Mia draws two bridges.",
                    ),
                    correct=1,
                ),
                "回到第二段最后一句，看看纸和车怎样了。",
                "第一次纸弯了，车也跟着动了：选说到纸和车的那一项。",
            ),
            (
                "order-bridge",
                "Put these in the order they happen in the story.",
                "She folds a new piece of paper.",
                OrderContent(
                    variant="events",
                    items=(
                        "Mia puts a piece of paper on the books.",
                        "The paper bends.",
                        "She folds a new piece of paper.",
                        "This time, the car stays on the bridge.",
                    ),
                ),
                "先找纸第一次放上书的那一句。",
                "纸先放上去，弯了以后Mia才想到新办法。",
            ),
            (
                "match-words",
                "Match each English word with its meaning.",
                "The paper bends. The car falls.",
                MatchContent(
                    left=("bends", "folds", "smiles"),
                    right=("弯了", "折", "微笑"),
                    distractors=("掉下来",),
                ),
                "在故事里找到这几个词，看看它们说的是纸、人还是车。",
                "bends说纸，folds是Mia对纸做的事，smiles是Mia的表情。",
            ),
            (
                "fill-puts",
                "Fill in the blank with a word from the story.",
                "She puts her car on the paper.",
                FillContent(
                    variant="word_bank",
                    text="She ___ her car on the paper.",
                    answers=(("puts",),),
                    distractors=("folds", "draws"),
                ),
                "找到写her car on the paper的那一句。",
                "这一句说Mia把车放到纸上：选表示“放”的词。",
            ),
            (
                "retell-bridge",
                "Tell the story in two or three sentences: what went wrong first, "
                "and what did Mia change?",
                "She folds a new piece of paper. She puts it on the same books. "
                "She tries the same car again.",
                OpenContent(
                    variant="retell",
                    points=(
                        "第一次纸弯了、车掉了",
                        "Mia把一张新纸折起来",
                        "第二次车留在桥上",
                    ),
                    guidance="用自己的话讲出先后，说出改变和结果就够了，不要求逐字复述。",
                ),
                "先说第一次发生了什么，再说Mia改了什么。",
                "可以这样开头：First, the paper … Then Mia …",
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
        "samples": [
            (
                "choice-small-bus",
                "判断：The small bus stays on the paper.",
                "It stays on the paper.",
                ChoiceContent(variant="true_false", options=("对", "错"), correct=0),
                "找到He puts the small bus on the bridge后面的那一句。",
                "小巴士放上去以后，纸没有弯：想想它留住了没有。",
            ),
            (
                "order-buses",
                "Put these in the order they happen in the story.",
                "He puts the big bus on the bridge.",
                OrderContent(
                    variant="events",
                    items=(
                        "He makes a bridge with paper and two books.",
                        "He puts the small bus on the bridge.",
                        "He puts the big bus on the bridge.",
                        "The big bus falls.",
                    ),
                ),
                "先找Ben搭桥的那一句。",
                "Ben先搭桥，再先试小的、后试大的。",
            ),
            (
                "match-buses",
                "Match each English word with its meaning.",
                "One bus is small. One bus is big.",
                MatchContent(
                    left=("small", "big", "falls"),
                    right=("小的", "大的", "掉下来"),
                    distractors=("弯曲",),
                ),
                "在故事里找到这几个词，看看它们说的是哪辆车、发生了什么。",
                "small和big说两辆车的大小，falls说大巴士最后怎样了。",
            ),
            (
                "fill-bends",
                "Fill in the blank with a word from the story.",
                "The paper bends.",
                FillContent(
                    variant="word_bank",
                    text="The paper ___. The big bus falls.",
                    answers=(("bends",),),
                    distractors=("stays", "takes"),
                ),
                "看故事最后两句。",
                "大巴士掉下来之前，纸先怎样了：选表示“弯”的词。",
            ),
            (
                "opinion-bus",
                "Why do you think the big bus fell? What would you change so it stays on the "
                "bridge?",
                "The paper bends. The big bus falls.",
                OpenContent(
                    variant="opinion",
                    points=("原因与故事相符（大巴士更重、纸撑不住）", "提出一个说得通的改法"),
                    guidance="原因与故事相符、改法说得通就算对，没有标准答案。",
                ),
                "想一想两辆车有什么不同。",
                "可以这样说：The big bus is … I would …",
            ),
        ],
    },
}


def next_material(taught: str, read: set[str]) -> dict:
    """The other course of the pair; a story the learner already read is never new material.

    After a story the model wrote, the first unread library story follows (else the first)."""
    if taught in COURSES:
        source = COURSES[taught]["next"]
    else:
        source = next((item for item in COURSES if item not in read), FIRST)
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
        "budget_minutes": ("按各题型预计用时估算约30分钟，挑战只替换活动。", ()),
        "offscreen_requirements": ("离屏任务停止采集，返回后取实际报告。", offscreen),
        "fallback": ("作答以文字模拟；朗读段以模拟录音交模拟评分，不代表真实发音。", every),
        "prerequisites": ("不预设先备，以首个未提示初答取样。", answers),
        "sampling_requirements": (
            f"使用{source}；每题先取未提示初答，帮助后再答另记尝试。",
            answers,
        ),
        "help_requirements": ("每题两级不同支持：定位提示与句框。", answers),
        "gaps": ("沿用：本包不验证此项，课后保持未知。", ()),
        "topic": ("固定模板课使用课程库短文，不按所选题材改写；课后据实记录。", ()),
        # The fixed course cannot be rewritten around a ticked skill; it says so.
        "skill_focus": ("固定模板课不专门安排该项目；回答里用到时课后据实记录。", answers),
        "skill_note": ("固定模板课不专门安排；课后据实记录。", ()),
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


def course_tasks(
    source: str,
    story: str,
    material_ref: RevisionRef,
    primary_dimension: str,
    auxiliary_dimension: str,
) -> tuple[TaskDefinition, ...]:
    """Every task of a fixed course: answers, point, offscreen and reading segments."""
    specifications = COURSES[source]["tasks"]
    tasks = []
    for identity, prompt, evidence, correct, incorrect, hint, scaffold in specifications:
        tasks.append(
            TaskDefinition(
                task_id=identity,
                dimension_id=primary_dimension,
                purpose="answer",
                prompt=prompt,
                material_ref=material_ref,
                evidence=evidence,
                acceptable_simulated=correct,
                incorrect_simulated=incorrect,
                supports=(
                    Support(text=hint, help={primary_dimension: "light_prompt"}),
                    Support(
                        text=scaffold,
                        help={
                            primary_dimension: "scaffold",
                            auxiliary_dimension: "scaffold",
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
                minutes=7 if purpose == "offscreen" else None,
            )
        )
    later = []  # Open questions come after reading aloud.
    for identity, prompt, evidence, item, hint, scaffold in COURSES[source]["samples"]:
        kind = type(item).__name__.removesuffix("Content").lower()
        # Clicked types measure reading facts only; a retelling is English expression (the
        # auxiliary goal), an opinion on the story's reasons is reading.
        retell = kind == "open" and item.variant == "retell"
        dimension = auxiliary_dimension if retell else READING
        sample = TaskDefinition(
            task_id=identity,
            dimension_id=dimension,
            purpose=kind,
            prompt=prompt,
            material_ref=material_ref,
            evidence=evidence,
            acceptable_simulated=(),
            supports=(
                Support(text=hint, help={dimension: "light_prompt"}),
                Support(
                    text=scaffold,
                    help={primary_dimension: "scaffold", auxiliary_dimension: "scaffold"},
                ),
            ),
            exit_text=SAMPLE_EXIT,
            correct_text=None if kind == "open" else CORRECT_TEXT,
            **{kind: item},
        )
        (later if kind == "open" else tasks).append(sample)
    return (*tasks, *reading_tasks(story, material_ref), *later)


def reading_tasks(story: str, material_ref: RevisionRef) -> tuple[TaskDefinition, ...]:
    """The story's read-aloud segments, each scored against its own reference text."""
    segments = segment_reading(story)
    return tuple(
        TaskDefinition(
            task_id=f"read-aloud-{index}",
            dimension_id=PRONUNCIATION,
            purpose="read_aloud",
            prompt=f"请朗读故事第{index}/{len(segments)}段：按麦克风开始，读完按结束。",
            material_ref=material_ref,
            evidence="以发音评分模块对本段参考文本的评分为据；模型不另评发音。",
            acceptable_simulated=(),
            reference_text=segment,
            reading_id="story-read-aloud",
            supports=(
                Support(
                    text="看着标出的词再读一遍，读慢一点。",
                    help={PRONUNCIATION: "light_prompt"},
                ),
                Support(text="先听Jane示范这一段，再跟读。", help={PRONUNCIATION: "full_model"}),
            ),
            exit_text="这一段先读到这里，我们接着往下。",
            optional=True,
        )
        for index, segment in enumerate(segments, 1)
    )


PREVIEW_MATERIALS = "纸、同一本书和同一辆小车。"
BRIEF = {
    "history": "没有学习历史，以初始未知状态开始。",
    "adjustments": "挑战替换活动，降阶/跳过/暂停/停止遵守原目标与预算。",
    "closing": "只总结实际范围，取得收到确认；停止不强迫小结。",
}


def library_entry(source: str, story: str, read: bool) -> dict:
    """One verified library story as the preparation model sees it."""
    return {"material_id": source, "title": COURSES[source]["title"], "story": story, "read": read}


def template_draft(
    demand: DemandBody,
    source: str,
    story: str,
    familiar: bool = False,
    offscreen: bool = True,
    open_allowed: bool = True,
) -> dict:
    """The fixed paper-bridge course written as a course draft the model may reuse or adapt.

    The onboarding test, or a student who listed no usable things, gets it on screen only:
    reading and reading aloud, with the offscreen minutes given to reading."""
    reference = RevisionRef(object_id="template", revision=1)
    goals = (demand.primary_goal.dimension_id, demand.auxiliary_goal.dimension_id)
    tasks = course_tasks(source, story, reference, *goals)
    screen_only = demand.purpose == "placement" or not offscreen
    if demand.purpose == "placement":  # The onboarding test uses no new task type.
        tasks = tuple(task for task in tasks if task.purpose in {"answer", "read_aloud"})
    elif screen_only:
        tasks = tuple(task for task in tasks if task.purpose not in {"point", "offscreen"})
    if not open_allowed:  # Study lessons wait for the reference set (QT-3).
        tasks = tuple(task for task in tasks if task.purpose != "open")
    title = COURSES[source]["title"]
    practice = uses_new_material(demand)
    choice = f"选{title}复习模板，明示已读熟悉条件" if familiar else f"选{title}新材料短观察模板"
    mapping = map_demand(demand, tasks, source, choice)
    stage = "已读，复习" if familiar else "新材料" if practice else "首课"

    def ids(task_ids):
        named = [i for i in task_ids if not i.startswith("read-aloud-")]
        return named + (["read_aloud"] if len(named) < len(task_ids) else [])

    return {
        "material_id": source,
        "title": None,
        "story": None,
        "lesson_type": "practice" if practice else "observation",
        "tasks": [
            {
                "task_id": task.task_id,
                "dimension_id": task.dimension_id,
                "purpose": task.purpose,
                "prompt": task.prompt,
                "evidence": task.evidence,
                "acceptable_answers": list(task.acceptable_simulated),
                "known_incorrect": list(task.incorrect_simulated),
                "supports": [
                    {
                        "text": support.text,
                        "help": [
                            {"dimension_id": dimension, "level": level}
                            for dimension, level in support.help.items()
                        ],
                    }
                    for support in task.supports
                ],
                "exit_text": task.exit_text,
                "optional": task.optional,
                "minutes": task.minutes,
                "correct_text": task.correct_text,
            }
            | {
                code: getattr(task, code).model_dump(mode="json") if getattr(task, code) else None
                for code in sorted(CONTENT_TYPES)
            }
            for task in tasks
            if task.purpose != "read_aloud"
        ],
        "read_aloud": True,
        "demand_mapping": [
            {"index": index, "implementation": item.implementation, "task_ids": ids(item.task_ids)}
            for index, item in enumerate(mapping)
        ],
        "resources": [],
        "preview": {
            "story": f"{title}（{stage}）",
            "why": "复习已读故事：熟悉条件下的回答不作首次新材料观察或迁移，科学只按实际报告解释。"
            if familiar
            else "按当前需求观察阅读事实与短表达，科学只按实际报告解释。",
            "materials": "不需要实物。" if screen_only else PREVIEW_MATERIALS,
            "help": "定位提示和不同句框分开；外部帮助随后补记。",
            "exit": "30分钟内，挑战只替换；可跳过、暂停或停止。",
        },
        "brief": BRIEF,
        "note": "沿用课程库的纸桥模板。",
        "limitations": [],
    }


def student_text(source: str) -> str:
    """Only the story reaches the student; the source's provenance line is adult metadata."""
    # ponytail: assumes the fixed course format ("# title", one 素材版本 line); a structured
    # student field on the material object is the upgrade path for other formats.
    lines = [
        line.removeprefix("# ") for line in source.splitlines() if not line.startswith("素材版本：")
    ]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip() + "\n"


def story_text(source: str) -> str:
    """The story a course is compiled from: the student text without its title line."""
    return student_text(source).split("\n", 1)[1]
