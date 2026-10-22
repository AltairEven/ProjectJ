"""The Lexile a story aims at and how long it is (ADR-0023)."""

from project_j.learning.contracts import CHALLENGE_STEPS, challenge_of
from project_j.preparation.contracts import StoryLevel

# Lowest target of each band and its story words; bands overlap to leave the writer room.
WORD_BANDS = (
    (1000, (400, 600)),
    (800, (350, 550)),
    (600, (250, 450)),
    (400, (150, 350)),
    (200, (100, 250)),
)
LOWEST_BAND = (50, 150)


def lexile_number(lexile: str) -> int:
    """'650L' is 650, 'BR100L' is -100 (a normalized Lexile)."""
    value = int(lexile.removeprefix("BR").removesuffix("L"))
    return -value if lexile.startswith("BR") else value


def lexile_text(value: int) -> str:
    return f"BR{-value}L" if value < 0 else f"{value}L"


def story_words(target: int) -> tuple[int, int]:
    return next((band for low, band in WORD_BANDS if target >= low), LOWEST_BAND)


def story_level(profile: dict, placement: bool) -> StoryLevel | None:
    """The student's Lexile plus the chosen step; the placement lesson adds none."""
    lexile = profile.get("lexile")
    if not lexile:
        return None
    challenge = None if placement else challenge_of(profile)
    target = lexile_number(lexile) + (CHALLENGE_STEPS[challenge] if challenge else 0)
    return StoryLevel(
        lexile=lexile, challenge=challenge, target=lexile_text(target), words=story_words(target)
    )


def targets(lexile: str | None) -> dict[str, str]:
    """Each step's target Lexile, as the topic step offers them."""
    if not lexile:
        return {}
    return {
        name: lexile_text(lexile_number(lexile) + step) for name, step in CHALLENGE_STEPS.items()
    }
