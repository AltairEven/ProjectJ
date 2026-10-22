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
CORRECT_TEXT = "Right! You found it."
SAMPLE_EXIT = "Let's stop this one here and go on."
# The two fixed simulation courses are each other's next course, so course switching can loop.
COURSES = {
    FIRST: {
        "title": "The Paper Bridge",
        "focus": "What Mia wants and how the new paper changes",
        "reading_focus": "As you read, notice what Mia wants to make "
        "and what she does to the new paper.",
        "next": TRANSFER,
        "tasks": [
            (
                "reading-fact",
                "What does Mia want to make?",
                "She wants a bridge for her car.",
                (
                    "a bridge",
                    "She wants to build a bridge for her toy car.",
                    "She wants a bridge.",
                    "She wants a bridge for her car.",
                ),
                ("a car",),
                'Find the sentence with "wants". '
                "What does she have, and what does she want to make?",
                "She wants a ___.",
            ),
            (
                "reading-sequence",
                "What did Mia do to the new paper before the successful try?",
                "She folds a new piece of paper.",
                (
                    "She folded it.",
                    "She folds a new piece of paper.",
                    "She folded a new piece of paper.",
                ),
                ("added books",),
                'Read on from "Can I make a new bridge". What happens to the new paper?',
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
                "Go back to the last sentence of part two. What happens to the paper and the car?",
                "The first time, the paper moves and so does the car. Pick the one about both.",
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
                "Find the sentence where the paper first goes on the books.",
                "First the paper goes on. Only after it bends does Mia have a new idea.",
            ),
            (
                "match-words",
                "Match each word from the story with what it means.",
                "The paper bends. The car falls.",
                MatchContent(
                    left=("bends", "folds", "smiles"),
                    right=("curves down", "doubles it over", "looks happy"),
                    distractors=("drops down",),
                ),
                "Find these words in the story. Is each one about the paper, Mia or the car?",
                '"bends" is about the paper, "folds" is what Mia does to the paper, '
                '"smiles" is about her face.',
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
                'Find the sentence with "her car on the paper".',
                "In this sentence Mia moves her car onto the paper. Pick the word for that.",
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
                        "The first time, the paper bends and the car falls.",
                        "Mia folds a new piece of paper.",
                        "The second time, the car stays on the bridge.",
                    ),
                    guidance="Tell what happened in order in your own words; the change and the "
                    "result are enough. Word for word is not needed.",
                ),
                "First say what happened the first time, then what Mia changed.",
                "You can start like this: First, the paper … Then Mia …",
            ),
        ],
    },
    TRANSFER: {
        "title": "Ben’s Bridge",
        "focus": "Small bus then big bus, and what really happened",
        "reading_focus": "As you read, notice which bus Ben tries first "
        "and what happens to each bus.",
        "next": FIRST,
        "tasks": [
            (
                "reading-first",
                "Which bus did Ben try first?",
                "He puts the small bus on the bridge.",
                ("the small bus", "small", "He first tries the small bus."),
                ("the big bus",),
                'Find the sentence about the first try. Look for "small" and "big".',
                "He first tries the ___ bus.",
            ),
            (
                "reading-result",
                "What happened when Ben tried the big bus?",
                "The paper bends. The big bus falls.",
                (
                    "it fell",
                    "the big bus falls",
                    "The paper bends and the big bus falls.",
                ),
                ("it stayed",),
                "Compare the last two sentences of the second try.",
                "The paper ___ and the big bus ___.",
            ),
        ],
        "samples": [
            (
                "choice-small-bus",
                "True or false: The small bus stays on the paper.",
                "It stays on the paper.",
                ChoiceContent(variant="true_false", options=("True", "False"), correct=0),
                'Find the sentence after "He puts the small bus on the bridge".',
                "After the small bus goes on, the paper does not bend. Does the bus stay?",
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
                "Find the sentence where Ben makes the bridge.",
                "Ben makes the bridge first, then tries the small bus, then the big one.",
            ),
            (
                "match-buses",
                "Match each word from the story with what it means.",
                "One bus is small. One bus is big.",
                MatchContent(
                    left=("small", "big", "falls"),
                    right=("little", "large", "drops down"),
                    distractors=("curves down",),
                ),
                "Find these words in the story. Is each one about a bus, or about what happens?",
                '"small" and "big" tell the size of the buses; '
                '"falls" tells what the big bus does at the end.',
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
                "Look at the last two sentences of the story.",
                "Before the big bus falls, what does the paper do? Pick that word.",
            ),
            (
                "opinion-bus",
                "Why do you think the big bus fell? What would you change so it stays on the "
                "bridge?",
                "The paper bends. The big bus falls.",
                OpenContent(
                    variant="opinion",
                    points=(
                        "A reason that fits the story (the big bus is heavier; the paper "
                        "cannot hold it).",
                        "A change that makes sense.",
                    ),
                    guidance="A reason that fits the story and a change that makes sense are "
                    "enough; there is no single right answer.",
                ),
                "Think about how the two buses are different.",
                "You can say: The big bus is … I would …",
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
        "primary_goal": ("Every answer task here uses the story text for this skill.", answers),
        "auxiliary_goal": (
            "The second help of each answer task is a short English sentence frame; "
            "an answer after help is recorded apart.",
            answers,
        ),
        "budget_minutes": (
            "About 30 minutes from the task types; a challenge only replaces an activity.",
            (),
        ),
        "offscreen_requirements": (
            "Nothing is collected away from the screen; back here, the student reports.",
            offscreen,
        ),
        "fallback": (
            "Answers are simulated text; reading aloud uses a simulated recording and score, "
            "not real pronunciation.",
            every,
        ),
        "prerequisites": (
            "Nothing is assumed beforehand; the first answer without help is the sample.",
            answers,
        ),
        "sampling_requirements": (
            f"Uses {source}; each task takes the first answer without help, and an answer after "
            "help is a new try.",
            answers,
        ),
        "help_requirements": (
            "Two different helps per task: where to look, then a sentence frame.",
            answers,
        ),
        "gaps": ("Kept: this course does not check it; it stays unknown after class.", ()),
        "topic": (
            "The fixed course uses a library story and is not rewritten for the chosen "
            "topic; the class records what happens.",
            (),
        ),
        # The fixed course cannot be rewritten around a ticked skill; it says so.
        "skill_focus": (
            "The fixed course has nothing special for this skill; if answers use it, the "
            "class records it.",
            answers,
        ),
        "skill_note": (
            "The fixed course has nothing special for it; the class records what happens.",
            (),
        ),
    }
    mapping = []
    for field, requirement in demand_requirements(demand):
        if field == "suggestion_choices":
            adopted = requirement.split(":", 1)[0] in {"adopt", "modify"}
            text = (
                f"Suggestion adopted: {choice}."
                if adopted
                else "Suggestion not adopted; the course does not change for it."
            )
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
                exit_text="That's OK. Let's skip this one for now.",
            )
        )
    for identity, purpose, prompt in [
        (
            "point-paper",
            "point",
            "Point to the paper you mean, then say which one you pointed to.",
        ),
        (
            "paper-experiment",
            "offscreen",
            "Away from the screen, compare your two paper bridge tries. Come back and report: "
            "were the things the same, what did you change, what really happened, and did "
            "anyone help?",
        ),
    ]:
        tasks.append(
            TaskDefinition(
                task_id=identity,
                dimension_id="science.observation.report",
                purpose=purpose,
                prompt=prompt,
                material_ref=material_ref,
                evidence="Only the student's own report counts; nothing was watched, so do not "
                "claim to have seen it.",
                acceptable_simulated=(),
                supports=(
                    Support(
                        text="First say what you really saw or did.",
                        help={"science.observation.report": "light_prompt"},
                    ),
                    Support(
                        text="Tell the things, the change and the result one by one; say \"I don't "
                        "know\" for any part you don't know.",
                        help={"science.observation.report": "scaffold"},
                    ),
                ),
                exit_text="It's OK to say you did not do it or did not see it.",
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
            prompt=f"Read part {index} of {len(segments)} aloud. Press the microphone to start "
            "and Done when you finish.",
            material_ref=material_ref,
            evidence="The pronunciation score of this part's text is the evidence; the model "
            "does not judge pronunciation itself.",
            acceptable_simulated=(),
            reference_text=segment,
            reading_id="story-read-aloud",
            supports=(
                Support(
                    text="Look at the marked words and read it again, a little slower.",
                    help={PRONUNCIATION: "light_prompt"},
                ),
                Support(
                    text="Listen to Jane read this part first, then read after her.",
                    help={PRONUNCIATION: "full_model"},
                ),
            ),
            exit_text="Let's stop this part here and go on.",
            optional=True,
        )
        for index, segment in enumerate(segments, 1)
    )


PREVIEW_MATERIALS = "Paper, the same book and the same small car."
BRIEF = {
    "history": "No learning history yet; everything starts as unknown.",
    "adjustments": "A challenge replaces an activity; making it easier, skipping, pausing "
    "and stopping keep the goals and the time.",
    "closing": "Sum up only what happened and ask the student to confirm; a stop needs no summary.",
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
    title = COURSES[source]["title"]
    practice = uses_new_material(demand)
    choice = (
        f"{title} as a review course, read before"
        if familiar
        else f"{title} as a short observation course with new material"
    )
    mapping = map_demand(demand, tasks, source, choice)
    stage = "read before, review" if familiar else "new material" if practice else "first lesson"

    def ids(task_ids):
        named = [i for i in task_ids if not i.startswith("read-aloud-")]
        return named + (["read_aloud"] if len(named) < len(task_ids) else [])

    return {
        "material_id": source,
        "title": None,
        "story": None,
        "reading_focus": COURSES[source]["reading_focus"],
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
        "read_aloud_passage": None,  # The library stories are short enough to read whole.
        "demand_mapping": [
            {"index": index, "implementation": item.implementation, "task_ids": ids(item.task_ids)}
            for index, item in enumerate(mapping)
        ],
        "resources": [],
        "preview": {
            "story": f"{title} ({stage})",
            "why": "Review of a story read before: answers in a familiar story are not a first "
            "look at new material; science is judged only from real reports."
            if familiar
            else "Watches reading facts and short English answers for the current need; "
            "science is judged only from real reports.",
            "materials": "No objects needed." if screen_only else PREVIEW_MATERIALS,
            "help": "Where-to-look hints and sentence frames are separate; outside help can be "
            "added after class.",
            "exit": "A challenge only replaces an activity; you can skip, pause or stop.",
        },
        "brief": BRIEF,
        "note": "Reused the paper bridge course from the library.",
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
