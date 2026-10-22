import asyncio

import pytest
from support import LEARNER

from project_j.contracts import RevisionRef
from project_j.platform.speech.contracts import ScorerNotReady
from project_j.platform.speech.simulated import SimulatedScorer


class NotReady(SimulatedScorer):
    def reasons(self):
        return ["Scorer process not started"]


def test_a_course_with_reading_segments_needs_a_ready_scorer(application):
    prepared = asyncio.run(application.prepare("prepare1", LEARNER))
    application.scorer = NotReady()
    with pytest.raises(
        ScorerNotReady, match="Pronunciation scoring is not ready: Scorer process not started"
    ):
        application.start(
            "start1",
            LEARNER,
            RevisionRef.model_validate(prepared["course_ref"]),
            materials_ready=True,
        )
    assert application.core is None
