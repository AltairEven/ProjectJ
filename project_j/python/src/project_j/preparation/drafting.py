"""The preparation model drafts a text course; rules decide whether it becomes a course."""

from dataclasses import replace
from typing import Literal, NamedTuple

from pydantic import Field

from project_j.contracts import Contract, RevisionRef
from project_j.evaluation.assessment import FAILURE_TEXT
from project_j.learning.contracts import DemandBody, Text
from project_j.learning.planning import learning_context, pick
from project_j.platform.models.contracts import ModelRequest
from project_j.preparation.contracts import (
    CourseBody,
    RequirementMapping,
    ResourceSpec,
    Support,
    TaskDefinition,
    demand_requirements,
)
from project_j.preparation.packages import reading_tasks
from project_j.preparation.reading import flat, segment_reading, words

# Resource kinds with a connected generator; only these may be required by a course.
CONNECTED_KINDS: tuple[str, ...] = ()
TASK_ID = r"^[a-z][a-z0-9-]{0,30}$"

PREPARATION_INSTRUCTIONS = (
    "你是备课助手。Jane是唯一的教师，不是学生；学习者不具名，一律称“孩子”。"
    "按demand与requirements为下一课编写文字课程。只依据上下文里的需求、学习状态、家庭条件、"
    "Jane的规则与策略、能力目录、课程库故事与模板；不补造未观察的表现。"
    "材料：优先复用library里的故事，material_id填它的material_id，title与story为null；"
    "确实需要新故事时material_id为null，自编英语短故事填title与story：每句以.!?结尾，全文30到250词，"
    "难度贴近library，不称教材原文或已测分级，不把科学预测写成实验结论。"
    "library里read为true的故事孩子读过，只能作复习。template不为null时它是已核对的固定课程，可以沿用或修改。"
    "tasks只用三种题：answer是关于本课故事的问答，dimension_id为english.reading.facts或"
    "english.speaking.report，evidence必须逐字摘自故事中的一句或连续几句，acceptable_answers列可接受的回答"
    "与合理变式（可含中文），known_incorrect列常见错答；point与offscreen的dimension_id为"
    "science.observation.report，只记录孩子实际报告的条件、改变与结果，acceptable_answers为空，"
    "offscreen只用家庭条件里有的实物。不要写其他题型。"
    "read_aloud为true时规则会把故事切段生成朗读题（发音评分），主要或辅助目标为"
    "english.speaking.pronunciation时必须为true。"
    "主要目标至少有一题；辅助目标要有题，或由某级帮助覆盖。先取未提示的初答，观察不前置答案，"
    "题目与帮助不泄露后续题的答案。每题至少两级帮助，后一级要换方法而不是只换措辞；"
    "help列这一级影响的维度与程度（light_prompt、scaffold、full_model），维度只能是本题维度或本课主辅目标。"
    "prompt是Jane对孩子说的话：用孩子能懂的短句说清要做什么；给家长或Jane的条件、安全与记录说明"
    "不写进prompt，写进preview或brief。supports的text也是Jane可以直接对孩子说的话。"
    "task_id用小写字母、数字和连字符，不以read-aloud开头。"
    "budget从opening、reading、expression、help_practice、offscreen、closing中选，各为正整数分钟，合计30；"
    "必须有opening与closing；有offscreen题才有offscreen段且必须有。"
    "demand_mapping对requirements每一条写一项：index对应，implementation写本课怎样落实或为何沿用，"
    "task_ids写落实它的题号，朗读题写read_aloud。"
    "lesson_type：observation短观察，practice练习；复习已读故事时由规则定为复习。"
    "resources只列需要的图片、音频、动画、视频，写清用途与要生成的内容，现在不生成；"
    "kind不在connected_kinds里的资源required必须为false，课程在没有这些资源时也要能上，fallback写替代办法。"
    "preview给家长：story本课故事，why为什么上，materials要准备的实物，help帮助方式，exit时长与退出。"
    "brief给Jane：history相关历史（成人或模拟来源不能当孩子的历史，没有儿童历史就写无历史分支），"
    "adjustments允许的挑战、降阶、跳过、暂停与停止，closing正常小结与停止时的简短结束。"
    "note用一两句告诉家长本稿怎样安排。limitations写本课已知的限制。"
    "评价或状态写明成人或模拟来源时，不能当作孩子的学习证据。"
)

DISCUSSION_INSTRUCTIONS = (
    "家长对previous提出了feedback（discussion是此前各轮的意见与说明）。按意见修改，常规小调整直接落实；"
    "改变主要或辅助目标、30分钟预算、主要教学或评价方式的意见不在这里改，"
    "在note里说明需要回到学习规划或由家长决定；没采纳的意见在note里说明原因；其余规则不变。"
)

REPAIR_INSTRUCTIONS = "上一稿未通过规则核对：按problems逐项修正，其余尽量保留。"


class HelpEntry(Contract):
    dimension_id: Text
    level: Literal["light_prompt", "scaffold", "full_model"]


class SupportDraft(Contract):
    text: Text
    help: tuple[HelpEntry, ...] = Field(min_length=1)


class TaskDraft(Contract):
    task_id: str = Field(pattern=TASK_ID)
    dimension_id: Text
    purpose: Literal["answer", "point", "offscreen"]
    prompt: Text
    evidence: Text
    acceptable_answers: tuple[Text, ...]
    known_incorrect: tuple[Text, ...]
    supports: tuple[SupportDraft, ...] = Field(min_length=2)
    exit_text: Text
    optional: bool


class BudgetPart(Contract):
    part: Literal["opening", "reading", "expression", "help_practice", "offscreen", "closing"]
    minutes: int


class MappingDraft(Contract):
    index: int
    implementation: Text
    task_ids: tuple[str, ...]


class ResourceDraft(Contract):
    resource_id: str = Field(pattern=TASK_ID)
    kind: Literal["image", "audio", "animation", "video"]
    purpose: Text
    content: Text
    task_ids: tuple[str, ...]
    audience: Literal["student", "parent", "teacher"]
    required: bool
    fallback: Text


class PreviewDraft(Contract):
    story: Text
    why: Text
    materials: Text
    help: Text
    exit: Text


class BriefDraft(Contract):
    history: Text
    adjustments: Text
    closing: Text


class CourseDraft(Contract):
    material_id: str | None
    title: Text | None
    story: Text | None
    lesson_type: Literal["observation", "practice"]
    tasks: tuple[TaskDraft, ...] = Field(min_length=1)
    read_aloud: bool
    budget: tuple[BudgetPart, ...]
    demand_mapping: tuple[MappingDraft, ...]
    resources: tuple[ResourceDraft, ...]
    preview: PreviewDraft
    brief: BriefDraft
    note: Text
    limitations: tuple[Text, ...]


def preparation_request(
    demand: DemandBody,
    state: dict,
    household: dict,
    catalog: list[dict],
    jane: dict,
    library: list[dict],
    candidate: str,
    template: dict | None,
) -> ModelRequest:
    """Learning data only: no learner, provider or object identity reaches the model."""
    learning = learning_context(state, {}, household, catalog)
    del learning["plans"]
    return ModelRequest(
        "preparation",
        PREPARATION_INSTRUCTIONS,
        learning
        | {
            "demand": pick(
                demand.model_dump(mode="json"),
                "primary_goal",
                "auxiliary_goal",
                "direction",
                "budget_minutes",
                "prerequisites",
                "sampling_requirements",
                "help_requirements",
                "offscreen_requirements",
                "fallback",
                "gaps",
            ),
            "requirements": [
                {"index": index, "field": field, "requirement": requirement}
                for index, (field, requirement) in enumerate(demand_requirements(demand))
            ],
            "jane": pick(jane, "rules", "strategies"),
            "library": library,
            "candidate": candidate,
            "template": template,
            "connected_kinds": list(CONNECTED_KINDS),
        },
        CourseDraft,
        deadline_seconds=600,  # Real xhigh drafts took 263-273 s (2026-10-19).
    )


def discussion_request(
    base: ModelRequest, previous: dict, feedback: str, discussion: list[dict]
) -> ModelRequest:
    context = {key: value for key, value in base.context.items() if key != "template"}
    return replace(
        base,
        instructions=PREPARATION_INSTRUCTIONS + DISCUSSION_INSTRUCTIONS,
        context=context | {"previous": previous, "feedback": feedback, "discussion": discussion},
    )


def repair_request(first: ModelRequest, draft: CourseDraft, problems: list[str]) -> ModelRequest:
    return replace(
        first,
        instructions=first.instructions + REPAIR_INSTRUCTIONS,
        context=first.context | {"rejected": draft.model_dump(mode="json"), "problems": problems},
    )


READING, REPORT = "english.reading.facts", "english.speaking.report"
SCIENCE, PRONUNCIATION = "science.observation.report", "english.speaking.pronunciation"
# Task types the classroom judgment reference set covers (docs/acceptance.md).
ALLOWED = {"answer": {READING, REPORT}, "point": {SCIENCE}, "offscreen": {SCIENCE}}
STORY_WORDS = (30, 250)
RULE_LIMITATION = (
    "课程由备课模型编写，规则核对结构、目标、原文依据与预算；题目质量、声音与教学效果未验证。"
)
REVIEW_LIMITATION = "已读材料复习：初答不代表首次阅读理解，也不作迁移证据。"


class DraftRejected(Exception):
    """The rules refused the draft; problems are (check code, parent-facing reason)."""

    def __init__(self, problems: list[tuple[str, str]]):
        self.problems = problems
        super().__init__("；".join(text for _, text in problems))


class PreparationUnavailable(Exception):
    """Nothing of this course was saved; the demand or the earlier version stays."""

    def __init__(self, reason: str, subject: str = "课程"):
        text = FAILURE_TEXT.get(reason, reason)
        kept = "下节需求已保存" if subject == "课程" else "原课程版本不变"
        super().__init__(f"{subject}未取得（{text}）。{kept}，可以重试。")


class Checked(NamedTuple):
    course: CourseBody
    source: str | None  # The library story reused; None for a story the model wrote.
    title: str
    story: str


def story_problems(story: str) -> list[tuple[str, str]]:
    problems = []
    try:
        segment_reading(story)
    except ValueError:
        problems.append(("story", "自编故事每句须以.!?结尾，单句不超过一个朗读评分段"))
    count = len(words(story))
    if not STORY_WORDS[0] <= count <= STORY_WORDS[1]:
        problems.append(("story", f"自编故事须30到250词，现为{count}词"))
    return problems


def checked_course(
    draft: CourseDraft,
    demand: DemandBody,
    demand_ref: RevisionRef,
    jane_ref: RevisionRef,
    brief_ref: RevisionRef,
    material_ref: RevisionRef,
    library: dict[str, dict],
    read: set[str],
    catalog_ids: set[str],
) -> Checked:
    problems: list[tuple[str, str]] = []
    source, title, story = None, draft.title, draft.story
    if draft.material_id is not None:
        if draft.title is not None or draft.story is not None:
            problems.append(("material", "复用库故事时不能同时自编故事"))
        if draft.material_id in library:
            source = draft.material_id
            title, story = library[source]["title"], library[source]["story"]
        else:
            problems.append(("material", "课程库里没有所选的故事"))
    elif draft.title and draft.story:
        problems += story_problems(draft.story)
    else:
        problems.append(("material", "既没有选库故事，也没有完整的自编故事"))
    story = story or ""

    ids = [task.task_id for task in draft.tasks]
    if len(set(ids)) != len(ids) or any(item.startswith("read-aloud") for item in ids):
        problems.append(("task_id", "题号须唯一，且不以read-aloud开头"))
    goals = {demand.primary_goal.dimension_id, demand.auxiliary_goal.dimension_id}
    for task in draft.tasks:
        if task.dimension_id not in catalog_ids or task.dimension_id not in ALLOWED[task.purpose]:
            problems.append(("purpose", f"{task.task_id}的题型与能力不在参考题集覆盖范围内"))
        if task.purpose == "answer" and flat(task.evidence) not in flat(story):
            problems.append(("evidence", f"{task.task_id}的依据不在故事原文中"))
        if task.purpose == "answer" and not task.acceptable_answers:
            problems.append(("answers", f"{task.task_id}缺少可接受的回答"))
        if len({support.text for support in task.supports}) != len(task.supports):
            problems.append(("help", f"{task.task_id}的各级帮助须不同"))
        for support in task.supports:
            dimensions = [entry.dimension_id for entry in support.help]
            if len(set(dimensions)) != len(dimensions) or set(dimensions) - goals - {
                task.dimension_id
            }:
                problems.append(("help", f"{task.task_id}的帮助只能影响本题维度或主辅目标"))

    covered = {task.dimension_id for task in draft.tasks}
    covered |= {PRONUNCIATION} if draft.read_aloud else set()
    helped = {
        entry.dimension_id for task in draft.tasks for item in task.supports for entry in item.help
    }
    if demand.primary_goal.dimension_id not in covered:
        problems.append(("goal", "主要目标没有对应的题"))
    if demand.auxiliary_goal.dimension_id not in covered | helped:
        problems.append(("goal", "辅助目标没有题或帮助覆盖"))
    if PRONUNCIATION in goals and not draft.read_aloud:
        problems.append(("goal", "朗读发音是本课目标时须有朗读题"))

    parts = [item.part for item in draft.budget]
    minutes = [item.minutes for item in draft.budget]
    offscreen = any(task.purpose == "offscreen" for task in draft.tasks)
    if (
        len(set(parts)) != len(parts)
        or sum(minutes) != 30
        or min(minutes, default=0) <= 0
        or not {"opening", "closing"} <= set(parts)
        or ("offscreen" in parts) != offscreen
    ):
        problems.append(
            ("budget", "预算段须不重复、各为正整数、合计30分钟，含开场与收尾，有离屏题才有离屏段")
        )

    requirements = demand_requirements(demand)
    known = set(ids) | ({"read_aloud"} if draft.read_aloud else set())
    if sorted(item.index for item in draft.demand_mapping) != list(range(len(requirements))):
        problems.append(("mapping", "需求每一条须恰好对应一次"))
    if any(set(item.task_ids) - known for item in draft.demand_mapping):
        problems.append(("mapping", "需求对应指向了不存在的题"))

    resource_ids = [item.resource_id for item in draft.resources]
    if len(set(resource_ids)) != len(resource_ids):
        problems.append(("resource", "资源编号须唯一"))
    for item in draft.resources:
        if item.required and item.kind not in CONNECTED_KINDS:
            problems.append(("resource", f"{item.resource_id}尚未接入生成，只能是可选资源"))
        if set(item.task_ids) - known:
            problems.append(("resource", f"{item.resource_id}关联的题不存在"))

    if problems:
        raise DraftRejected(list(dict.fromkeys(problems)))

    reading = reading_tasks(story, material_ref) if draft.read_aloud else ()

    def expand(task_ids: tuple[str, ...]) -> tuple[str, ...]:
        named = [
            task_id
            for item in task_ids
            for task_id in ([t.task_id for t in reading] if item == "read_aloud" else [item])
        ]
        return tuple(dict.fromkeys(named))

    familiar = source in read
    tasks = tuple(
        TaskDefinition(
            task_id=task.task_id,
            dimension_id=task.dimension_id,
            purpose=task.purpose,
            prompt=task.prompt,
            material_ref=material_ref,
            evidence=task.evidence,
            acceptable_simulated=task.acceptable_answers,
            incorrect_simulated=task.known_incorrect,
            supports=tuple(
                Support(
                    text=support.text,
                    help={entry.dimension_id: entry.level for entry in support.help},
                )
                for support in task.supports
            ),
            exit_text=task.exit_text,
            optional=task.optional,
        )
        for task in draft.tasks
    )
    mapping = {item.index: item for item in draft.demand_mapping}
    course = CourseBody(
        demand_ref=demand_ref,
        state_ref=demand.state_ref,
        plan_ref=demand.plan_ref,
        jane_ref=jane_ref,
        brief_ref=brief_ref,
        material_refs=(material_ref,),
        primary_goal=demand.primary_goal,
        auxiliary_goal=demand.auxiliary_goal,
        budget={item.part: item.minutes for item in draft.budget},
        lesson_type="review" if familiar else draft.lesson_type,
        tasks=tasks + reading,
        demand_mapping=tuple(
            RequirementMapping(
                field=field,
                requirement=requirement,
                implementation=mapping[index].implementation,
                task_ids=expand(mapping[index].task_ids),
            )
            for index, (field, requirement) in enumerate(requirements)
        ),
        parent_preview=draft.preview.model_dump(),
        limitations=(*draft.limitations, RULE_LIMITATION)
        + ((REVIEW_LIMITATION,) if familiar else ()),
        resources=tuple(
            ResourceSpec(**item.model_dump(exclude={"task_ids"}), task_ids=expand(item.task_ids))
            for item in draft.resources
        ),
    )
    return Checked(course=course, source=source, title=title or "", story=story)
