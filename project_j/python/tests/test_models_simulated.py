import asyncio

import pytest
from support import start

from project_j.classroom.judgment import ClassroomJudgment, judgment_request
from project_j.contracts import RevisionRef
from project_j.evaluation.assessment import EvaluationDraft, evaluation_request
from project_j.platform.models.contracts import Binding
from project_j.platform.models.simulated import SIMULATED_MODEL, SimulatedAdapter
from project_j.preparation.contracts import Support, TaskDefinition

BINDING = Binding(adapter="simulated", model=SIMULATED_MODEL, effort="low")


def task(purpose="answer"):
    return TaskDefinition(
        task_id="t1",
        dimension_id="english.reading.facts",
        purpose=purpose,
        prompt="What does Mia want?",
        material_ref=RevisionRef(object_id="m", revision=1),
        evidence="She wants a bridge.",
        acceptable_simulated=("a bridge",),
        incorrect_simulated=("a car",),
        supports=(
            Support(text="看看第二句。", help={"english.reading.facts": "light_prompt"}),
            Support(text="她想要的是一座桥。", help={"english.reading.facts": "full_model"}),
        ),
        exit_text="我们先跳过这一题。",
    )


def judge(text, help_count=0, is_help=False, purpose="answer"):
    request = judgment_request(task(purpose), text, help_count, is_help, 10)
    reply = asyncio.run(SimulatedAdapter().invoke(request, BINDING))
    return ClassroomJudgment.model_validate(reply.data)


@pytest.mark.parametrize(
    "text,expected",
    [("A bridge.", "correct"), ("a car", "incorrect"), ("maybe", "unclear")],
)
def test_answers_follow_the_fixed_simulated_sets(text, expected):
    result = judge(text)
    assert result.judgment == expected and result.evidence == "She wants a bridge."


def test_help_follows_support_order_then_exit_text():
    assert judge("帮我", 0, True).text == "看看第二句。"
    assert judge("帮我", 1, True).text == "她想要的是一座桥。"
    assert judge("帮我", 2, True).text == "我们先跳过这一题。"
    assert judge("帮我", 0, True).judgment == "help"


def test_point_and_offscreen_reports_are_reported():
    assert judge("我指了纸", purpose="point").judgment == "reported"
    assert judge("折好了", purpose="offscreen").judgment == "reported"


def test_classroom_request_carries_task_data_jane_method_and_goals_only():
    request = judgment_request(task(), "a bridge", 0, False, 10)
    assert request.purpose == "classroom_judgment" and request.output is ClassroomJudgment
    assert set(request.context) == {
        "task_purpose",
        "prompt",
        "evidence",
        "reference_answers",
        "known_incorrect",
        "allowed_supports",
        "exit_text",
        "help_count",
        "core_decided_help",
        "answer",
        "reference_text",
        "pronunciation",
        "jane_method",
        "goals",
    }


def test_judgment_request_carries_the_bound_jane_method_and_goals():
    request = judgment_request(
        task(),
        "a car",
        0,
        False,
        10,
        jane_method={"rules": ["先保留未提示初答"], "strategies": {"english": "定位依据→换表征"}},
        goals=("观察故事事实、顺序与文本依据", "用短英语报告实际结果"),
    )
    assert request.context["jane_method"]["strategies"]["english"] == "定位依据→换表征"
    assert request.context["goals"] == ["观察故事事实、顺序与文本依据", "用短英语报告实际结果"]
    assert "不直接说出reference_answers" in request.instructions


def test_judgment_instructions_carry_the_reference_set_fixes():
    """Baseline judgment-ref-20261019: answer words leaked, retries invited, adult wording."""
    request = judgment_request(task(), "a car", 0, False, 10)
    assert "不说出evidence中能直接回答本题的词" in request.instructions
    assert "不再要求学生重说" in request.instructions
    assert "不出现“依据”" in request.instructions


def test_started_class_binds_the_course_jane_version(application):
    core = asyncio.run(start(application))
    assert core.jane["identity"] == "Jane" and core.jane["rules"]


def test_failure_switch_fails_once_and_evaluation_has_a_draft():
    adapter = SimulatedAdapter()
    adapter.fail_next = True
    fact = {"end": {"reason": "normal"}, "attempts": [], "limitations": []}
    course = {"primary_goal": {"description": "a"}, "auxiliary_goal": {"description": "b"}}
    request = evaluation_request(fact, course | {"tasks": [], "demand_mapping": []}, [], None)
    assert asyncio.run(adapter.invoke(request, BINDING)).failure == "unavailable"
    reply = asyncio.run(adapter.invoke(request, BINDING))
    assert EvaluationDraft.model_validate(reply.data).summary
    assert reply.actual["model"].status == "verified"


def test_simulated_report_is_marked_simulated():
    report = SimulatedAdapter().report(SIMULATED_MODEL, ("low", "medium"))
    assert report.simulated and report.items["effort:medium"].status == "verified"
    assert report.items["image_input"].status == "unsupported"


def reading_context(pronunciation, help=False):
    return {
        "task_purpose": "read_aloud",
        "evidence": "以评分为据",
        "reference_text": "She wants a bridge for her car.",
        "pronunciation": pronunciation,
        "allowed_supports": ["看着标出的词再读一遍。", "先听示范再跟读。"],
        "exit_text": "跳过",
        "help_count": 0,
        "core_decided_help": help,
        "answer": "",
    }


def test_reading_judgment_follows_the_score_and_names_words_to_reread():
    from project_j.classroom.judgment import ClassroomJudgment
    from project_j.platform.models.contracts import ModelRequest

    adapter = SimulatedAdapter()

    def reply(pronunciation):
        request = ModelRequest(
            "classroom_judgment", "judge", reading_context(pronunciation), ClassroomJudgment
        )
        return adapter.respond(request)

    words = [{"word": "bridge", "accuracy": 40, "error": "mispronounced"}]
    weak = reply({"status": "scored", "words": words})
    assert weak["judgment"] == "incorrect" and "bridge" in weak["text"]
    good = reply(
        {"status": "scored", "words": [{"word": "bridge", "accuracy": 95, "error": "none"}]}
    )
    assert good["judgment"] == "correct"
    assert reply({"status": "unknown", "words": []})["judgment"] == "unclear"
