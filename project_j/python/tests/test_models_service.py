import asyncio

import pytest
from pydantic import BaseModel

from project_j.platform.models.contracts import AdapterReply, Binding, ConfigCheck, ModelRequest
from project_j.platform.models.service import ModelService, ModelsNotReady


class Out(BaseModel):
    text: str


def request(deadline=1.0):
    return ModelRequest("evaluation", "judge", {"activity_id": "a"}, Out, deadline)


def service_with(reply=None, delay=0.0):
    service = ModelService.simulated()
    adapter = service.adapters["simulated"]

    async def invoke(req, binding):
        await asyncio.sleep(delay)
        return reply

    adapter.invoke = invoke
    return service


def test_valid_output_is_returned_and_recorded():
    service = service_with(AdapterReply(data={"text": "ok"}))
    result = asyncio.run(service.run(request()))
    assert result.ok and result.output.text == "ok"
    call = service.calls[-1]
    assert call["purpose"] == "evaluation" and call["failure"] is None
    assert call["adapter"] == "simulated" and call["effort"] == "medium"
    assert call["tier"] == "default"


def test_wrong_structure_is_invalid_output_not_a_wrong_answer():
    service = service_with(AdapterReply(data={"other": 1}))
    assert asyncio.run(service.run(request())).failure == "invalid_output"


def test_explicit_mismatch_fails_but_unverified_passes():
    mismatch = {"model": ConfigCheck(requested="m", actual="n", status="mismatch")}
    unverified = {"effort": ConfigCheck(requested="low", actual=None, status="unverified")}
    service = service_with(AdapterReply(data={"text": "ok"}, actual=mismatch))
    assert asyncio.run(service.run(request())).failure == "config_mismatch"
    service = service_with(AdapterReply(data={"text": "ok"}, actual=unverified))
    assert asyncio.run(service.run(request())).ok


def test_deadline_becomes_timeout_failure():
    service = service_with(AdapterReply(data={"text": "ok"}), delay=0.2)
    assert asyncio.run(service.run(request(deadline=0.01))).failure == "timeout"


def test_cancelled_call_is_recorded_as_cancelled():
    service = service_with(AdapterReply(data={"text": "ok"}), delay=1)

    async def scenario():
        job = asyncio.create_task(service.run(request()))
        await asyncio.sleep(0.01)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job

    asyncio.run(scenario())
    assert service.calls[-1]["failure"] == "cancelled"


def test_not_initialized_service_refuses_and_reports_reasons():
    service = ModelService({}, {}, [], problem="未配置默认模型")
    with pytest.raises(ModelsNotReady, match="模型未初始化"):
        service.require()
    assert asyncio.run(service.run(request())).failure == "not_initialized"
    status = service.status()
    assert status["initialized"] is False
    assert status["purposes"]["evaluation"]["reasons"] == ["未配置默认模型"]


def test_simulated_service_is_initialized_and_marked_simulated():
    service = ModelService.simulated()
    service.require()
    status = service.status()
    assert status["initialized"] and status["simulated"]
    assert (
        status["purposes"]["classroom_judgment"]["binding"]
        == Binding(adapter="simulated", model="simulated-jane", effort="low").model_dump()
    )
    # Four purposes in workflow order; all but the classroom default to medium.
    assert list(status["purposes"]) == [
        "planning",
        "preparation",
        "classroom_judgment",
        "evaluation",
    ]
    efforts = {p: item["binding"]["effort"] for p, item in status["purposes"].items()}
    assert efforts == {
        "planning": "medium",
        "preparation": "medium",
        "classroom_judgment": "low",
        "evaluation": "medium",
    }


def test_unexpected_adapter_error_becomes_unavailable_without_text():
    service = ModelService.simulated()

    async def explode(req, binding):
        raise RuntimeError("Bearer sk-secret must never be recorded")

    service.adapters["simulated"].invoke = explode
    result = asyncio.run(service.run(request()))
    assert result.failure == "unavailable"
    assert service.calls[-1]["error_type"] == "RuntimeError"
    assert "sk-secret" not in repr(service.calls)


def test_not_ready_message_names_purposes_in_chinese():
    service = ModelService({}, {}, [], problem="未配置默认模型")
    with pytest.raises(ModelsNotReady) as caught:
        service.require()
    assert "课堂判断：未配置默认模型" in str(caught.value)
    assert "学习规划：未配置默认模型" in str(caught.value)
    assert "备课：未配置默认模型" in str(caught.value)
    assert "classroom_judgment" not in str(caught.value)
