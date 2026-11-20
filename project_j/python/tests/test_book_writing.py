"""Expanded reading: the model's outline and chapter shapes, requests and checks (pure)."""

from project_j.platform.models.simulated import SimulatedAdapter
from project_j.preparation.expansion import (
    ChapterDraft,
    Outline,
    OutlineChapter,
    PicturePlace,
    Placement,
    chapter_problems,
    chapter_range,
    chapter_request,
    outline_problems,
    outline_request,
    picture_request,
    revision_request,
)

FILLER = "Mia walked on along the quiet road and looked at the hills."  # 12 words


def outline(chapters=4, words=None, pictures=(), title="The Long Rain"):
    words = words or [[] for _ in range(chapters)]
    return Outline(
        title=title,
        characters=["Mia: a small girl in a red coat."],
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


def test_chapter_range_keeps_the_book_within_2000_to_4000():
    assert chapter_range(4) == (500, 1000) and chapter_range(6) == (334, 666)
    assert chapter_range(5) == (400, 800)


def test_outline_problems():  # chapter count, every word placed, every original picture once
    good = outline(chapters=4, words=[["cave"], ["Bean"], [], []], pictures=[("pond", 2)])
    assert outline_problems(good, ["cave", "bean"], ["pond"]) == []
    assert outline_problems(outline(chapters=3), [], [])  # too few chapters
    assert outline_problems(outline(chapters=7), [], [])  # too many
    assert outline_problems(good, ["cave", "bean", "box"], ["pond"])  # "box" not placed
    assert outline_problems(good, ["cave", "bean"], ["pond", "hill"])  # "hill" not placed
    assert outline_problems(outline(title="林克"), [], [])  # Chinese


def test_outline_without_words_passes():
    assert outline_problems(outline(chapters=5), [], []) == []


def test_unknown_original_picture_is_a_problem():
    assert outline_problems(outline(pictures=[("ghost", 1)]), [], ["pond"])
    assert outline_problems(outline(pictures=[("pond", 9)]), [], ["pond"])  # chapter out of range
    assert outline_problems(outline(pictures=[("pond", 1), ("pond", 2)]), [], ["pond"])  # twice


def test_chapter_problems_split_hard_from_missing_words():
    low, high = chapter_range(4)
    hard, missing = chapter_problems(chapter(words=low - 1), 4, ["cave"], [])
    assert hard and missing == ["cave"]
    assert chapter_problems(chapter(words=high + 1), 4, [], [])[0]
    hard, missing = chapter_problems(chapter(words=low, uses=["caves"]), 4, ["cave"], [])
    assert hard == [] and missing == []
    assert chapter_problems(chapter(words=low, new_picture_paragraph=99), 4, [], [])[0]
    two_on_one = chapter(words=low, pictures=[("pond", 2)], new_picture_paragraph=2)
    assert chapter_problems(two_on_one, 4, [], ["pond"])[0]
    placed = chapter(words=low, pictures=[("pond", 1)])
    assert chapter_problems(placed, 4, [], ["pond"]) == ([], [])
    assert chapter_problems(placed, 4, [], [])[0]  # "pond" is not this chapter's picture
    assert chapter_problems(chapter(words=low), 4, [], ["pond"])[0]  # "pond" left out
    chinese = chapter(words=low).model_copy(update={"title": "雨夜"})
    assert chapter_problems(chinese, 4, [], [])[0]


def test_requests_carry_every_word_and_the_right_purpose():
    words = [{"lemma": f"w{i}", "part_of_speech": "noun"} for i in range(150)]
    request = outline_request("story", "600L", words, [], "")
    assert request.purpose == "reading" and len(request.context["words"]) == 150
    assert request.deadline_key == "preparation_drafting"
    assert request.context["chapters"] == [4, 6] and request.context["book_words"] == [2000, 4000]
    picture = picture_request(outline(), chapter())
    assert picture.media == "image" and picture.deadline_key == "preparation_image"
    assert picture.purpose == "reading" and "Mia: a small girl" in picture.context["content"]
    assert picture.context["style"] == "Soft watercolor."


def test_chapter_and_revision_requests_carry_the_book_so_far():
    book = outline(words=[["cave"], [], [], []], pictures=[("pond", 1), ("hill", 2)])
    pictures = [
        {"picture": "pond", "paragraph": 1, "content": "a pond"},
        {"picture": "hill", "paragraph": 2, "content": "a hill"},
    ]
    first = chapter_request(book, "story", [], 1, None, pictures)
    assert first.context["kind"] == "chapter" and first.context["range"] == [500, 1000]
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
    pictures = [{"picture": "pond", "paragraph": 1, "content": "a pond"}]
    reply = adapter.respond(outline_request("story", "600L", words, pictures, "Watercolor."))
    book = Outline.model_validate(reply)
    assert outline_problems(book, ["cave", "bean", "lantern"], ["pond"]) == []
    written = []
    for number in range(1, len(book.chapters) + 1):
        request = chapter_request(book, "story", written, number, "600L", pictures)
        draft = ChapterDraft.model_validate(adapter.respond(request))
        mine = [p.picture for p in book.pictures if p.chapter == number]
        assert chapter_problems(draft, 4, book.chapters[number - 1].words, mine) == ([], [])
        written.append(draft)
    request = revision_request(book, "story", written, 2, "Longer.", "600L", pictures)
    revised = ChapterDraft.model_validate(adapter.respond(request))
    assert revised != written[1]
    assert chapter_problems(revised, 4, book.chapters[1].words, []) == ([], [])
