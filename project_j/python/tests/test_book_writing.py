"""Expanded reading: the model's outline and chapter shapes, requests and checks (pure)."""

import json

from project_j.platform.models.simulated import SimulatedAdapter
from project_j.preparation.expansion import (
    ChapterDraft,
    Character,
    Outline,
    OutlineChapter,
    PicturePlace,
    Placement,
    chapter_problems,
    chapter_request,
    cover_request,
    extended,
    outline_problems,
    outline_request,
    picture_request,
    revision_request,
    unnamed,
)
from project_j.preparation.materials import COVER_IMAGE_INSTRUCTIONS

FILLER = "Mia walked on along the quiet road and looked at the hills."  # 12 words


def outline(chapters=4, words=None, pictures=(), title="The Long Rain"):
    words = words or [[] for _ in range(chapters)]
    return Outline(
        title=title,
        summary="Mia walks home in the rain.",
        characters=[Character(called="Mia", look="a small girl in a red coat")],
        style="Soft watercolor.",
        chapters=[
            OutlineChapter(title=f"Part {n}", plot="Mia walks home.", words=list(words[n - 1]))
            for n in range(1, chapters + 1)
        ],
        pictures=[PicturePlace(picture=p, chapter=c) for p, c in pictures],
    )


def chapter(words=600, uses=(), pictures=(), new_picture_paragraph=2):
    text = " ".join(uses)
    count = len(text.split()) if text else 0
    body = []
    while count + 12 <= words:
        body.append(FILLER)
        count += 12
    body.append(" ".join(["Mia"] * (words - count)))
    half = len(body) // 2
    paragraphs = [" ".join([text, *body[:half]]).strip(), " ".join(body[half:]).strip()]
    return ChapterDraft(
        title="The Road",
        paragraphs=[p for p in paragraphs if p],
        pictures=[Placement(picture=p, paragraph=n) for p, n in pictures],
        new_picture_paragraph=new_picture_paragraph,
        new_picture="Mia on the road.",
    )


def test_outline_problems():  # chapter count, every word placed, every original picture once
    good = outline(chapters=4, words=[["cave"], ["Bean"], [], []], pictures=[("pond", 2)])
    assert outline_problems(good, ["cave", "bean"], ["pond"]) == []
    assert outline_problems(good, ["cave", "bean", "box"], ["pond"])  # "box" not placed
    assert outline_problems(good, ["cave", "bean"], ["pond", "hill"])  # "hill" not placed
    assert outline_problems(outline(title="林克"), [], [])  # Chinese


def test_outline_problems_bound_the_chapter_count():
    assert outline_problems(outline(chapters=2), [], []) == []
    assert outline_problems(outline(chapters=12), [], []) == []
    assert "write 2 to 12 chapters" in outline_problems(outline(chapters=1), [], [])
    assert "write 2 to 12 chapters" in outline_problems(outline(chapters=13), [], [])


def test_a_look_must_not_name_anyone():
    named = outline().model_copy(
        update={"characters": [Character(called="Mia", look="Mia in a red coat")]}
    )
    assert any("name" in p for p in outline_problems(named, [], []))


def test_old_outline_records_still_read():
    old = Outline.model_validate(
        {"title": "T", "characters": ["A girl in red."], "style": "S", "chapters": []}
        | {"pictures": []}
    )
    assert old.summary == "" and old.characters == [Character(called="", look="A girl in red.")]


def test_a_continuation_keeps_every_confirmed_part():
    base = outline(chapters=3)
    more = OutlineChapter(title="Part 4", plot="More.", words=[])
    tom = Character(called="Tom", look="a tall boy with a cap")
    proposal = base.model_copy(
        update={
            "title": "Another Name",
            "summary": "Longer.",
            "style": "Oil paint.",
            "chapters": [OutlineChapter(title="New", plot="x", words=[]), *base.chapters[1:], more],
            "characters": [Character(called="Mia", look="a girl"), tom],
            "pictures": [PicturePlace(picture="pond", chapter=2)],
        }
    )
    kept = extended(proposal, base)  # whatever came back for the confirmed part is put back
    assert kept.chapters == [*base.chapters, more] and kept.characters == [*base.characters, tom]
    assert (kept.title, kept.style, kept.pictures) == (base.title, base.style, base.pictures)
    assert kept.summary == "Longer." and extended(proposal, None) == proposal
    assert outline_problems(kept, [], [], kept=3) == []
    assert outline_problems(extended(base, base), [], [], kept=3) == [
        "add at least one chapter at the end"
    ]


def test_a_look_goes_into_a_picture_as_written():
    mia = Character(called="Mia", look=r"a girl with a \d badge")
    assert unnamed("Mia runs.", [mia]) == r"a girl with a \d badge runs."


def test_outline_without_words_passes():
    assert outline_problems(outline(chapters=5), [], []) == []


def test_unknown_original_picture_is_a_problem():
    assert outline_problems(outline(pictures=[("ghost", 1)]), [], ["pond"])
    assert outline_problems(outline(pictures=[("pond", 9)]), [], ["pond"])  # chapter out of range
    assert outline_problems(outline(pictures=[("pond", 1), ("pond", 2)]), [], ["pond"])  # twice


def test_every_chapter_is_400_to_800_words():
    assert chapter_problems(chapter(words=400), [], [])[0] == []
    assert chapter_problems(chapter(words=800), [], [])[0] == []
    assert chapter_problems(chapter(words=399), [], [])[0]
    assert chapter_problems(chapter(words=801), [], [])[0]


def test_chapter_problems_split_hard_from_missing_words():
    hard, missing = chapter_problems(chapter(words=399), ["cave"], [])
    assert hard and missing == ["cave"]
    hard, missing = chapter_problems(chapter(words=400, uses=["caves"]), ["cave"], [])
    assert hard == [] and missing == []
    assert chapter_problems(chapter(words=400, new_picture_paragraph=99), [], [])[0]
    two_on_one = chapter(words=400, pictures=[("pond", 2)], new_picture_paragraph=2)
    assert chapter_problems(two_on_one, [], ["pond"])[0]
    placed = chapter(words=400, pictures=[("pond", 1)])
    assert chapter_problems(placed, [], ["pond"]) == ([], [])
    assert chapter_problems(placed, [], [])[0]  # "pond" is not this chapter's picture
    assert chapter_problems(chapter(words=400), [], ["pond"])[0]  # "pond" left out
    chinese = chapter(words=400).model_copy(update={"title": "雨夜"})
    assert chapter_problems(chinese, [], [])[0]


def test_requests_carry_every_word_and_the_right_purpose():
    words = [{"lemma": f"w{i}", "part_of_speech": "noun"} for i in range(150)]
    request = outline_request("story", "600L", words, [], "")
    assert request.purpose == "reading" and len(request.context["words"]) == 150
    assert request.deadline_key == "preparation_drafting"
    picture = picture_request(outline(), chapter())
    assert picture.media == "image" and picture.deadline_key == "preparation_image"
    assert picture.purpose == "reading"
    assert "a small girl in a red coat" in picture.context["content"]
    assert picture.context["style"] == "Soft watercolor."


def test_outline_request_carries_the_discussion():
    first = outline_request("Story.", "500L", [], [], "")
    assert first.context["current"] is None and first.context["fixed"] == 0
    assert first.context["chapters"] == [2, 12] and first.context["chapter_words"] == [400, 800]
    again = outline_request(
        "Story.", "500L", [], [], "", current=outline(), feedback="Add a fox.", fixed=4
    )
    assert again.context["current"]["title"] == "The Long Rain"
    assert again.context["feedback"] == "Add a fox." and again.context["fixed"] == 4


def test_picture_requests_name_no_one():
    draft = chapter().model_copy(update={"new_picture": "Mia and Mia's dog by the gate."})
    content = picture_request(outline(), draft).context["content"]
    assert "Mia" not in content and "a small girl in a red coat" in content


def test_a_picture_shows_only_its_cast():
    mia = Character(called="Mia", look="a small girl in a red coat")
    two = outline().model_copy(
        update={"characters": [mia, Character(called="Tom", look="a tall boy with a cap")]}
    )
    draft = chapter().model_copy(update={"new_picture": "Mia by the gate."})
    assert "a tall boy with a cap" not in picture_request(two, draft).context["content"]


def test_chapter_and_revision_requests_carry_the_book_so_far():
    book = outline(words=[["cave"], [], [], []], pictures=[("pond", 1), ("hill", 2)])
    pictures = [
        {"picture": "pond", "paragraph": 1, "content": "a pond"},
        {"picture": "hill", "paragraph": 2, "content": "a hill"},
    ]
    first = chapter_request(book, "story", [], 1, None, pictures)
    assert first.context["kind"] == "chapter" and first.context["range"] == [400, 800]
    assert first.context["words"] == ["cave"] and first.context["written"] == []
    assert [p["picture"] for p in first.context["pictures"]] == ["pond"]
    chapters = [chapter(), chapter(), chapter(), chapter()]
    revision = revision_request(book, "story", chapters, 2, "Make it longer.", "600L", pictures)
    assert revision.context["kind"] == "revision" and revision.context["feedback"]
    assert len(revision.context["written"]) == 1 and len(revision.context["later"]) == 2
    assert revision.context["current"]["title"] == "The Road"
    assert [p["picture"] for p in revision.context["pictures"]] == ["hill"]


def test_simulated_reading_replies_pass_the_checks():
    adapter = SimulatedAdapter()
    words = [{"lemma": w, "part_of_speech": "noun"} for w in ("cave", "bean", "lantern")]
    lemmas = ["cave", "bean", "lantern"]
    pictures = [{"picture": "pond", "paragraph": 1, "content": "a pond"}]
    reply = adapter.respond(outline_request("story", "600L", words, pictures, "Watercolor."))
    book = Outline.model_validate(reply)
    assert outline_problems(book, lemmas, ["pond"]) == []
    again = Outline.model_validate(
        adapter.respond(outline_request("story", "600L", words, pictures, "", book, "Shorter."))
    )
    assert outline_problems(again, lemmas, ["pond"]) == [] and again.summary.endswith(" Revised.")
    more = words + [{"lemma": "kettle", "part_of_speech": "noun"}]
    longer = Outline.model_validate(
        adapter.respond(outline_request("story", "600L", more, pictures, "", book, "Go on.", 4))
    )
    assert outline_problems(longer, [*lemmas, "kettle"], ["pond"]) == []
    assert extended(longer, book) == longer and longer.chapters[-1].words == ["kettle"]
    written = []
    for number in range(1, len(book.chapters) + 1):
        request = chapter_request(book, "story", written, number, "600L", pictures)
        draft = ChapterDraft.model_validate(adapter.respond(request))
        mine = [p.picture for p in book.pictures if p.chapter == number]
        assert chapter_problems(draft, book.chapters[number - 1].words, mine) == ([], [])
        written.append(draft)
    request = revision_request(book, "story", written, 2, "Longer.", "600L", pictures)
    revised = ChapterDraft.model_validate(adapter.respond(request))
    assert revised != written[1]
    assert chapter_problems(revised, book.chapters[1].words, []) == ([], [])


def test_a_cover_request_names_no_one_and_no_title():
    book = outline(title="The Long Rain").model_copy(
        update={"summary": "Mia walks home in the rain with Mia's dog."}
    )
    request = cover_request(book)
    assert request.purpose == "reading" and request.media == "image"
    assert request.deadline_key == "preparation_image"
    assert request.instructions == COVER_IMAGE_INSTRUCTIONS
    assert "Mia" not in request.context["content"]
    assert "a small girl in a red coat" in request.context["content"]
    assert "The Long Rain" not in json.dumps(request.context)
    assert request.context["style"] == "Soft watercolor."
    assert request.context["purpose"] == "the cover of this book"


def test_a_first_version_outline_still_gets_a_cover_request():
    book = outline().model_copy(
        update={
            "summary": "",
            "characters": [Character(called="", look="a small girl in a red coat")],
        }
    )
    assert cover_request(book).context["content"] == "Characters: a small girl in a red coat"
