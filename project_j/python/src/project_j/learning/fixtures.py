"""The fixture learner of a simulation (and of study tests): catalog, unknown state, four plan
levels, profile and the first demand; a study space starts at onboarding instead."""

from project_j.application.documents import document, identity, publication
from project_j.contracts import RevisionRef
from project_j.learning.catalog import define
from project_j.learning.contracts import DemandBody, Goal, PlanBody
from project_j.preparation.packages import (
    COURSES,
    FIRST,
)

PRIMARY_GOAL = Goal(
    dimension_id="english.reading.facts", description="观察故事事实、顺序与文本依据"
)
AUXILIARY_GOAL = Goal(dimension_id="english.speaking.report", description="用短英语报告实际结果")


def initialize(app, learner_id: str | None = None):
    learner_id = learner_id or app.learner_id
    from project_j.learning.catalog import INITIAL
    from project_j.learning.contracts import DimensionState, ProfileBody, StateBody

    operation = app.request(
        f"initialize:{learner_id}", learner_id, "initialize", {"fixture_version": 1}
    )
    existing = app.ledger.result(operation, learner_id)
    if existing:
        return existing
    catalog_refs = tuple(
        RevisionRef.model_validate(
            define(app, f"initial-catalog:{learner_id}:{entry.dimension_id}", learner_id, entry)[
                "capability_ref"
            ]
        )
        for entry in INITIAL
    )
    primary, auxiliary = PRIMARY_GOAL, AUXILIARY_GOAL
    state = app.saved_step(operation, learner_id, "state")
    if state is None:
        body = StateBody(
            status="checked",
            dimensions=tuple(
                DimensionState(
                    dimension_id=goal.dimension_id,
                    task_scope=goal.description,
                    judgment="unknown",
                    help="unknown",
                    material_condition="尚未观察",
                    observed_at=None,
                    limitations=("初始未知状态，尚无学习样本。",),
                )
                for goal in (
                    primary,
                    auxiliary,
                    *(
                        (
                            Goal(
                                dimension_id="english.speaking.pronunciation",
                                description="英语朗读发音",
                            ),
                        )
                        if app.onboarding
                        else ()
                    ),
                    Goal(
                        dimension_id="science.observation.report",
                        description="实际科学条件与结果报告",
                    ),
                )
            ),
        )
        obj = document(
            "state",
            identity(learner_id, "state"),
            operation,
            learner_id,
            body,
            refs=catalog_refs,
        )
        state = app.save_step(
            operation,
            learner_id,
            "state",
            [obj],
            publications=(publication(obj, "模拟初始未知状态"),),
        )
    state = state[0]
    parent = None
    for level in ("long_term", "annual", "stage", "week"):
        result = app.saved_step(operation, learner_id, f"plan:{level}")
        if result is None:
            body = PlanBody(
                status="checked",
                level=level,
                parent_ref=parent.ref if parent else None,
                main_goal=primary,
                budget_minutes=30,
                basis_refs=(state.ref,),
                verification="首次、帮助后、延迟保持与新材料分别观察，不预填成绩。",
                exit_conditions="疲惫或停止则结束，不自动加时或产生补课债务。",
                review_at=None,
                limitations=("初始方向与近期安排，不预排每日课程。",),
            )
            obj = document(
                "plan",
                identity(learner_id, f"plan:{level}"),
                operation,
                learner_id,
                body,
                refs=(state.ref,) + ((parent.ref,) if parent else ()),
            )
            result = app.save_step(
                operation,
                learner_id,
                f"plan:{level}",
                [obj],
                publications=(publication(obj, "模拟计划检查与层次连接"),),
            )
        parent = result[0]
    if app.onboarding:  # Profile and first demand come from onboarding.
        return app.ledger.finish(
            operation, learner_id, {"realm": app.store.realm, "onboarding": True}
        )
    profile = app.saved_step(operation, learner_id, "profile")
    if profile is None:
        obj = document(
            "profile",
            identity(learner_id, "profile"),
            operation,
            learner_id,
            ProfileBody(
                confirmed=True,
                lexile="500L",
                background="模拟学习者：用于开发与测试。",
                interests="故事、动手做小实验",
                weekly_time="每周三次，每次30分钟",
                focus="阅读找依据与短英语报告",
                materials="纸、书和小车",
                summarized=True,
                limitations=("模拟学习资料，不是学习记录。",)
                if app.store.realm == "simulation"
                else (),
            ),
        )
        profile = app.save_step(
            operation,
            learner_id,
            "profile",
            [obj],
            publications=(publication(obj, "模拟学习资料"),),
        )
    profile = profile[0]
    demand = app.saved_step(operation, learner_id, "demand")
    if demand is None:
        body = DemandBody(
            status="checked",
            state_ref=state.ref,
            plan_ref=parent.ref,
            profile_ref=profile.ref,
            primary_goal=primary,
            auxiliary_goal=auxiliary,
            budget_minutes=30,
            prerequisites=("相关阅读、表达和材料经历未知，用短观察取样。",),
            sampling_requirements=("保留未提示初答与帮助后新尝试。",),
            help_requirements=("两次求助用不同指导；未知外部帮助不填独立。",),
            offscreen_requirements="离屏停止采集，返回取得实际条件/结果报告。",
            fallback="已核实不依赖声音评价的任务可使用文字模拟。",
            gaps=("尚无学习样本，用短观察取样。",),
            # The simulation's fixture lesson is the library's first story, nothing ticked.
            topic=COURSES[FIRST]["title"],
            skill_focus=(),
        )
        obj = document(
            "demand",
            identity(learner_id, "demand"),
            operation,
            learner_id,
            body,
            refs=(state.ref, parent.ref, profile.ref),
        )
        demand = app.save_step(
            operation,
            learner_id,
            "demand",
            [obj],
            publications=(publication(obj, "模拟需求已复核"),),
            baselines=(state.ref, parent.ref, profile.ref),
        )
    return app.ledger.finish(
        operation,
        learner_id,
        {"demand_ref": demand[0].ref.model_dump(), "realm": app.store.realm},
    )
