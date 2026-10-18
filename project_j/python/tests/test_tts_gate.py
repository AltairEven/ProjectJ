import pytest
from support import CONDITIONS, LEARNER

from project_j.contracts import RevisionRef
from project_j.platform.tts.contracts import SynthesizerNotReady
from project_j.platform.tts.simulated import SimulatedSynthesizer


class NotReady(SimulatedSynthesizer):
    def reasons(self):
        return ["合成进程未启动"]


def test_a_class_needs_a_ready_synthesizer(application):
    prepared = application.prepare("prepare1", LEARNER)
    application.synthesizer = NotReady()
    with pytest.raises(SynthesizerNotReady, match="语音合成未就绪：合成进程未启动"):
        application.start(
            "start1", LEARNER, RevisionRef.model_validate(prepared["course_ref"]), CONDITIONS
        )
    assert application.core is None
