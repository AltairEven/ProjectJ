import asyncio

import pytest
from support import LEARNER

from project_j.contracts import RevisionRef
from project_j.platform.tts.contracts import SynthesizerNotReady
from project_j.platform.tts.simulated import SimulatedSynthesizer


class NotReady(SimulatedSynthesizer):
    def reasons(self):
        return ["Speech process not started"]


def test_a_class_needs_a_ready_synthesizer(application):
    prepared = asyncio.run(application.prepare("prepare1", LEARNER))
    application.synthesizer = NotReady()
    with pytest.raises(
        SynthesizerNotReady, match="Speech is not ready: Speech process not started"
    ):
        application.start(
            "start1",
            LEARNER,
            RevisionRef.model_validate(prepared["course_ref"]),
            materials_ready=True,
        )
    assert application.core is None
