import pytest
from support import CONDITIONS, LEARNER

from project_j.contracts import RevisionRef
from project_j.platform.speech.contracts import ScorerNotReady
from project_j.platform.speech.simulated import SimulatedScorer


class NotReady(SimulatedScorer):
    def reasons(self):
        return ["评分进程未启动"]


def test_a_course_with_reading_segments_needs_a_ready_scorer(application):
    prepared = application.prepare("prepare1", LEARNER)
    application.scorer = NotReady()
    with pytest.raises(ScorerNotReady, match="发音评分未就绪：评分进程未启动"):
        application.start(
            "start1", LEARNER, RevisionRef.model_validate(prepared["course_ref"]), CONDITIONS
        )
    assert application.core is None
