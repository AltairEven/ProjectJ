import asyncio

import pytest

from project_j.application.coordinator import Coordinator
from project_j.contracts import RevisionRef
from project_j.platform.models.service import ModelService, ModelsNotReady

LEARNER = "fictional-L1"


@pytest.fixture
def blocked(application):
    """The same application with an uninitialized model service."""
    application.models = ModelService({}, {}, [], problem="未配置默认模型")
    return application


def test_model_dependent_use_cases_refuse_until_initialized(blocked):
    from project_j.learning.maintenance import review

    with pytest.raises(ModelsNotReady, match="模型未初始化"):
        asyncio.run(blocked.prepare("prep-1", LEARNER))
    with pytest.raises(ModelsNotReady):
        blocked.start("start-1", LEARNER, RevisionRef(object_id="course", revision=1), {})
    with pytest.raises(ModelsNotReady):
        asyncio.run(review(blocked, "review-1", LEARNER, "week"))
    with pytest.raises(ModelsNotReady):
        asyncio.run(blocked.evaluate("eval-1", LEARNER, "no-activity", "next_course"))


def test_viewing_and_export_do_not_need_models(blocked):
    dashboard = blocked.dashboard(LEARNER)
    assert dashboard["models"]["initialized"] is False
    assert dashboard["models"]["purposes"]["evaluation"]["reasons"] == ["未配置默认模型"]
    assert blocked.files.export(LEARNER).exists()


def test_simulation_coordinator_initializes_in_memory(application):
    status = application.dashboard(LEARNER)["models"]
    assert status["initialized"] and status["simulated"]
    assert isinstance(application.models, ModelService)
    assert not hasattr(application, "model")


def test_reopened_coordinator_gets_its_own_simulated_service(application):
    reopened = Coordinator(
        application.store, application.root, verification=application.verification
    )
    assert reopened.models is not application.models and reopened.models.calls == []
