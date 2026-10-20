import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from support import LEARNER

from project_j.application.coordinator import story_text
from project_j.contracts import RevisionRef
from project_j.curriculum.content import check_content
from project_j.learning.contracts import DemandBody, Goal
from project_j.platform.models.contracts import Binding
from project_j.platform.models.simulated import SimulatedAdapter
from project_j.preparation.contracts import demand_requirements
from project_j.preparation.drafting import (
    CourseDraft,
    DraftRejected,
    PreparationUnavailable,
    checked_course,
    discussion_request,
    preparation_request,
    repair_request,
)
from project_j.preparation.packages import (
    COURSES,
    FIRST,
    course_tasks,
    library_entry,
    template_draft,
)
from project_j.teacher.methods import JANE

ROOT = Path(__file__).resolve().parents[3]
MATERIAL = RevisionRef(object_id="material", revision=1)
STORY = story_text((ROOT / FIRST).read_text())


def initial(application):
    demand = application.current(LEARNER, "demand")
    body = DemandBody.model_validate(demand.body)
    profile = application.store.read(body.profile_ref, LEARNER).body
    return body, {
        "state": application.current(LEARNER, "state").body,
        "profile": profile,
        "catalog": [doc.body for doc in application.store.list_current(LEARNER, "capability")],
    }


def build(application, template=True):
    demand, data = initial(application)
    library = [
        library_entry(source, story_text((ROOT / source).read_text()), False) for source in COURSES
    ]
    return preparation_request(
        demand,
        data["state"],
        data["profile"],
        data["catalog"],
        JANE,
        library,
        FIRST,
        template_draft(demand, FIRST, STORY) if template else None,
    )


def test_template_draft_is_a_valid_course_draft(application):
    demand, _ = initial(application)
    draft = CourseDraft.model_validate(template_draft(demand, FIRST, STORY))
    assert draft.material_id == FIRST and draft.read_aloud
    assert all(task.evidence in STORY for task in draft.tasks if task.purpose == "answer")
    assert draft.resources == ()


def test_preparation_request_carries_no_identity(application):
    demand, _ = initial(application)
    first = build(application)
    discussion = discussion_request(first, first.context["template"], "第二题再简单一点。", [])
    for request in (first, discussion):
        text = json.dumps(request.context, ensure_ascii=False)
        for word in ("fictional-L1", "learner_id", "object_id", "provider"):
            assert word not in text
        assert request.purpose == "preparation" and request.deadline_seconds == 600
    assert len(first.context["requirements"]) == len(demand_requirements(demand))
    assert first.context["connected_kinds"] == ["image", "animation"]


def test_discussion_and_repair_requests(application):
    first = build(application)
    previous = first.context["template"]
    discussion = discussion_request(
        first, previous, "第二题再简单一点。", [{"feedback": "a", "note": "b"}]
    )
    assert discussion.context["previous"] == previous
    assert discussion.context["feedback"] == "第二题再简单一点。"
    assert discussion.context["discussion"] == [{"feedback": "a", "note": "b"}]
    assert "template" not in discussion.context
    assert "note里说明原因" in discussion.instructions
    draft = CourseDraft.model_validate(previous)
    repaired = repair_request(discussion, draft, ["依据不在原文：reading-fact"])
    assert repaired.context["problems"] == ["依据不在原文：reading-fact"]
    assert repaired.context["rejected"] == draft.model_dump(mode="json")
    assert repaired.context["feedback"] == "第二题再简单一点。"
    assert repaired.instructions.endswith("按problems逐项修正，其余尽量保留。")


def test_course_tasks_unchanged():
    tasks = course_tasks(FIRST, STORY, MATERIAL, "english.reading.facts", "english.speaking.report")
    ids = [task.task_id for task in tasks]
    assert ids[:4] == ["reading-fact", "reading-sequence", "point-paper", "paper-experiment"]
    assert ids[4:] and all(task_id.startswith("read-aloud-") for task_id in ids[4:])


def test_simulated_preparation_replies(application):
    adapter = SimulatedAdapter()
    binding = Binding(adapter="simulated", model="simulated-jane", effort="medium")
    first = build(application)
    reply = asyncio.run(adapter.invoke(first, binding))
    assert reply.data == first.context["template"]
    discussion = discussion_request(first, first.context["template"], "意见", [])
    reply = asyncio.run(adapter.invoke(discussion, binding))
    assert reply.data["tasks"] == first.context["template"]["tasks"]
    assert reply.data["note"] == "模拟：已记录意见，未调用真实模型，课程内容不变。"
    reply = asyncio.run(adapter.invoke(build(application, template=False), binding))
    assert reply.data is None and reply.failure == "unavailable"


READING, REPORT = "english.reading.facts", "english.speaking.report"
SCIENCE, PRONUNCIATION = "science.observation.report", "english.speaking.pronunciation"
CATALOG = {READING, REPORT, SCIENCE, PRONUNCIATION}
REFS = {
    "demand_ref": RevisionRef(object_id="demand", revision=1),
    "jane_ref": RevisionRef(object_id="jane", revision=1),
    "brief_ref": RevisionRef(object_id="brief", revision=1),
    "material_ref": MATERIAL,
}
NEW_STORY = (
    "Sam has a red cup. He fills it with water. The cup is heavy now. "
    "Sam pours the water into a bowl. The cup is light again. "
    "Sam smiles and puts the cup on the table."
)


def library():
    return {
        source: library_entry(source, story_text((ROOT / source).read_text()), False)
        for source in COURSES
    }


def check(demand, draft, read=frozenset()):
    return checked_course(
        CourseDraft.model_validate(draft),
        demand,
        **REFS,
        library=library(),
        read=set(read),
        catalog_ids=CATALOG,
    )


def rejected(demand, draft, read=frozenset()):
    with pytest.raises(DraftRejected) as error:
        check(demand, draft, read)
    return {code for code, _ in error.value.problems}


def with_goals(demand, primary, auxiliary):
    return demand.model_copy(
        update={
            "primary_goal": Goal(dimension_id=primary, description="目标"),
            "auxiliary_goal": Goal(dimension_id=auxiliary, description="辅助"),
        }
    )


@pytest.fixture
def demand(application):
    return initial(application)[0]


@pytest.fixture
def draft(demand):
    return template_draft(demand, FIRST, STORY)


def test_template_draft_checks_into_the_fixed_course(demand, draft):
    checked = check(demand, draft)
    assert checked.course.tasks == course_tasks(FIRST, STORY, MATERIAL, READING, REPORT)
    assert checked.course.budget == {
        "opening": 3,
        "reading": 7,
        "expression": 4,
        "help_practice": 6,
        "offscreen": 7,
        "closing": 3,
    }
    assert checked.course.lesson_type == "observation"
    assert checked.source == FIRST and checked.story == STORY
    assert checked.course.limitations[-1].startswith("课程由备课模型编写")


def test_evidence_must_quote_the_story(demand, draft):
    draft["tasks"][0]["evidence"] = "She wants a boat."
    assert rejected(demand, draft) == {"evidence"}


def test_answer_needs_acceptable_answers(demand, draft):
    draft["tasks"][0]["acceptable_answers"] = []
    assert rejected(demand, draft) == {"answers"}


def test_task_types_stay_within_the_reference_set(demand, draft):
    draft["tasks"][0]["dimension_id"] = SCIENCE
    draft["tasks"][3]["dimension_id"] = READING
    draft["tasks"][1]["dimension_id"] = "math.counting"
    assert "purpose" in rejected(demand, draft)  # Moved dimensions also leave their help.


def test_goals_must_be_covered(demand, draft):
    science = with_goals(demand, SCIENCE, REPORT)
    draft["tasks"] = draft["tasks"][:2]
    draft["budget"] = [{"part": "opening", "minutes": 10}, {"part": "closing", "minutes": 20}]
    for item in draft["demand_mapping"]:
        item["task_ids"] = [
            i for i in item["task_ids"] if i in {"reading-fact", "reading-sequence"}
        ]
    assert "goal" in rejected(science, draft)
    draft["read_aloud"] = False
    for item in draft["demand_mapping"]:
        item["task_ids"] = [i for i in item["task_ids"] if i != "read_aloud"]
    assert "goal" in rejected(with_goals(demand, PRONUNCIATION, READING), draft)


def test_budget_rules(demand, draft):
    parts = {part["part"]: part for part in draft["budget"]}
    parts["opening"]["minutes"] = 2
    assert rejected(demand, draft) == {"budget"}
    parts["opening"]["minutes"] = 3
    draft["budget"] = [part for part in draft["budget"] if part["part"] != "closing"]
    draft["budget"][0]["minutes"] += 3
    assert rejected(demand, draft) == {"budget"}
    no_offscreen = template_draft(demand, FIRST, STORY)
    no_offscreen["tasks"] = no_offscreen["tasks"][:3]
    for item in no_offscreen["demand_mapping"]:
        item["task_ids"] = [i for i in item["task_ids"] if i != "paper-experiment"]
    assert rejected(demand, no_offscreen) == {"budget"}
    offscreen_only = template_draft(demand, FIRST, STORY)
    offscreen_only["budget"] = [
        {"part": part["part"], "minutes": part["minutes"] + 7 * (part["part"] == "opening")}
        for part in offscreen_only["budget"]
        if part["part"] != "offscreen"
    ]
    assert rejected(demand, offscreen_only) == {"budget"}


def test_every_requirement_is_mapped_once(demand, draft):
    missing = template_draft(demand, FIRST, STORY)
    missing["demand_mapping"] = missing["demand_mapping"][1:]
    assert rejected(demand, missing) == {"mapping"}
    twice = template_draft(demand, FIRST, STORY)
    twice["demand_mapping"].append(twice["demand_mapping"][0])
    assert rejected(demand, twice) == {"mapping"}
    draft["demand_mapping"][0]["task_ids"] = ["no-such-task"]
    assert rejected(demand, draft) == {"mapping"}


def test_help_stays_on_task_and_goals(demand, draft):
    draft["tasks"][0]["supports"][0]["help"] = [
        {"dimension_id": PRONUNCIATION, "level": "scaffold"}
    ]
    assert rejected(demand, draft) == {"help"}


def test_resources_are_planned_only(demand, draft):
    resource = {
        "resource_id": "bridge-picture",
        "kind": "image",
        "purpose": "展示纸桥",
        "content": "一张纸搭在两本书之间。",
        "task_ids": ["reading-fact"],
        "audience": "student",
        "required": True,
        "fallback": "只看文字。",
        "stage": "question",
    }
    draft["resources"] = [resource, resource]
    assert rejected(demand, draft) == {"resource"}
    draft["resources"] = [resource | {"task_ids": ["no-such-task"]}]
    assert rejected(demand, draft) == {"resource"}
    video = resource | {"resource_id": "bridge-video", "kind": "video"}
    draft["resources"] = [resource, video]
    assert rejected(demand, draft) == {"resource"}
    draft["resources"] = [resource | {"resource_id": "demo", "kind": "audio"}]
    with pytest.raises(ValidationError):  # Jane's voice comes from the rules, not the model.
        CourseDraft.model_validate(draft)
    draft["resources"] = [resource | {"stage": "help"}, video | {"required": False}]
    resources = check(demand, draft).course.resources
    assert [item.resource_id for item in resources[:2]] == ["bridge-picture", "bridge-video"]
    assert resources[0].stage == "help" and resources[0].required
    assert all(item.resource_id.startswith(("voice:", "demo:")) for item in resources[2:])


def test_new_story_rules(demand, draft):
    draft["title"], draft["story"] = "Sam's Cup", NEW_STORY
    assert rejected(demand, draft) >= {"material"}
    draft["material_id"] = "courses/unknown.md"
    assert "material" in rejected(demand, draft)
    new = template_draft(demand, FIRST, STORY)
    new["material_id"], new["title"], new["story"] = None, "Sam's Cup", "Sam has a cup"
    assert "story" in rejected(demand, new)
    new["story"] = "Sam has a cup. It is red."
    assert "story" in rejected(demand, new)
    new["story"] = NEW_STORY
    for task, evidence in zip(
        new["tasks"][:2], ("He fills it with water.", "The cup is heavy now."), strict=True
    ):
        task["evidence"] = evidence
    checked = check(demand, new)
    assert checked.source is None and checked.title == "Sam's Cup" and checked.story == NEW_STORY
    assert any(task.reference_text for task in checked.course.tasks)


def test_read_story_becomes_review(demand, draft):
    checked = check(demand, draft, read={FIRST})
    assert checked.course.lesson_type == "review"
    assert any(item.startswith("已读材料复习") for item in checked.course.limitations)


def test_reserved_and_duplicate_task_ids(demand, draft):
    draft["tasks"][1]["task_id"] = "read-aloud-9"
    assert "task_id" in rejected(demand, draft)
    draft["tasks"][1]["task_id"] = "reading-fact"
    assert "task_id" in rejected(demand, draft)


def test_unavailable_messages():
    assert (
        str(PreparationUnavailable("timeout")) == "课程未取得（超时）。下节需求已保存，可以重试。"
    )
    assert (
        str(PreparationUnavailable("timeout", "修改"))
        == "修改未取得（超时）。原课程版本不变，可以重试。"
    )


def test_evidence_across_a_line_break_still_quotes_the_story(demand):
    from project_j.preparation.packages import TRANSFER

    story = story_text((ROOT / TRANSFER).read_text())
    draft = template_draft(demand, TRANSFER, story)
    draft["tasks"][0]["evidence"] = "He puts the small bus on the bridge. It stays on the paper."
    assert "bridge.\nIt stays" in story
    checked = check(demand, draft)
    material = application_material(story)
    assert check_content(checked.course, demand, material, FakeFiles(story))["status"] == "checked"


class FakeFiles:
    def __init__(self, text):
        self.text = text

    def read_asset(self, manifest):
        return self.text


def application_material(story):
    from project_j.contracts import Document

    return Document.model_construct(body={"asset": {"sha256": "x"}})


def test_instructions_keep_notes_out_of_what_jane_says():
    from project_j.preparation.drafting import PREPARATION_INSTRUCTIONS

    assert "prompt是Jane对学生说的话" in PREPARATION_INSTRUCTIONS
    assert "写进preview或brief" in PREPARATION_INSTRUCTIONS


def test_instructions_say_the_library_candidate_yields_to_lexile_and_interests():
    """A real run reused the library's picture-book stories for an 850L adult (SELF-5)."""
    from project_j.preparation.drafting import PREPARATION_INSTRUCTIONS

    assert "candidate是规则按课程顺序给的候选" in PREPARATION_INSTRUCTIONS
    assert "不贴近profile.interests时不用它" in PREPARATION_INSTRUCTIONS
    assert "整篇故事逐段切开" in PREPARATION_INSTRUCTIONS  # Not "one short sentence".


def test_no_offscreen_task_without_materials(application):
    demand, _ = initial(application)
    draft = template_draft(demand, FIRST, STORY)
    assert any(task["purpose"] == "offscreen" for task in draft["tasks"])
    with pytest.raises(DraftRejected) as error:
        checked_course(
            CourseDraft.model_validate(draft),
            demand,
            **REFS,
            library=library(),
            read=set(),
            catalog_ids=CATALOG,
            offscreen_allowed=False,
        )
    assert ("offscreen", "学习资料里没有可用实物，不能安排离屏任务") in error.value.problems
