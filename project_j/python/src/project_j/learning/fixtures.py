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
    dimension_id="english.reading.facts",
    description="Find story facts, their order and the words that show them",
)
AUXILIARY_GOAL = Goal(
    dimension_id="english.speaking.report", description="Report real results in short English"
)


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
                    material_condition="Not observed yet",
                    observed_at=None,
                    limitations=("Unknown at the start; no learning samples yet.",),
                )
                for goal in (
                    primary,
                    auxiliary,
                    *(
                        (
                            Goal(
                                dimension_id="english.speaking.pronunciation",
                                description="Reading aloud",
                            ),
                        )
                        if app.onboarding
                        else ()
                    ),
                    Goal(
                        dimension_id="science.observation.report",
                        description="Report real science conditions and results",
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
            publications=(publication(obj, "Simulated unknown start"),),
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
                verification="First tries, tries after help, later recall and new material are "
                "observed apart; no scores are filled in ahead.",
                exit_conditions="End when tired or stopped; never add time or catch-up work.",
                review_at=None,
                limitations=(
                    "A first direction and near-term plan; no daily lessons are planned ahead.",
                ),
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
                publications=(publication(obj, "Simulated plan check and links between levels"),),
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
                background="Simulated learner for development and tests.",
                interests="Stories and small hands-on experiments",
                weekly_time="Three times a week, 30 minutes each",
                focus="Finding facts in reading and short English reports",
                materials="Paper, books and a small car",
                summarized=True,
                limitations=("Simulated profile, not a learning record.",)
                if app.store.realm == "simulation"
                else (),
            ),
        )
        profile = app.save_step(
            operation,
            learner_id,
            "profile",
            [obj],
            publications=(publication(obj, "Simulated profile"),),
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
            prerequisites=(
                "Reading, speaking and hands-on experience are unknown; "
                "short observations sample them.",
            ),
            sampling_requirements=(
                "Keep the first answer without help and each new try after help.",
            ),
            help_requirements=(
                "Two pleas get two different helps; "
                "unknown outside help is never marked independent.",
            ),
            offscreen_requirements="Nothing is collected away from the screen; back here, the "
            "student reports the real conditions and result.",
            fallback="Checked tasks that do not need voice can be simulated in text.",
            gaps=("No learning samples yet; short observations sample them.",),
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
            publications=(publication(obj, "Simulated demand rechecked"),),
            baselines=(state.ref, parent.ref, profile.ref),
        )
    return app.ledger.finish(
        operation,
        learner_id,
        {"demand_ref": demand[0].ref.model_dump(), "realm": app.store.realm},
    )
