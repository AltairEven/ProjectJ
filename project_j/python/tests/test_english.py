import asyncio

from project_j.english import ENGLISH_ONLY, chinese, han


def test_chinese_finds_han_and_full_width_marks_anywhere():
    assert not chinese("Mia has a toy car.") and not chinese(("a", 1, None))
    assert chinese("你好") and chinese("Good job！") and chinese({"a": ["x", "桥"]})


def test_skipped_keys_are_not_looked_into():
    feedback = {"tasks": [{"help_quote": "妈妈帮我读", "explanation": "ok"}]}
    assert not chinese(feedback, skip=frozenset({"help_quote"}))
    assert chinese(feedback)


def test_student_input_counts_only_han():
    assert han("She wants a 桥.") and not han("puts。") and not han("Yes，I heard.")
    assert ENGLISH_ONLY == "Please answer in English."


def test_library_courses_catalog_and_jane_are_english():
    from project_j.application.reference import PROJECT_ROOT, reference_tasks
    from project_j.learning.catalog import INITIAL
    from project_j.teacher.methods import JANE

    tasks = list(reference_tasks(PROJECT_ROOT).values())
    assert not chinese([task.model_dump(mode="json") for task in tasks])
    assert not chinese([item.model_dump(mode="json") for item in INITIAL])
    assert not chinese(JANE)
    answer = next(task for task in tasks if task.purpose == "answer")
    assert answer.exit_text == "That's OK. Let's skip this one for now."


def test_the_fixture_learner_is_english(application):
    learner = application.learner_id
    for kind in ("profile", "plan", "state", "capability", "demand"):
        bodies = [doc.body for doc in application.store.list_current(learner, kind)]
        assert bodies and not chinese(bodies), kind


CHINESE_ANSWER = "一座桥"


def test_a_simulated_journey_shows_no_chinese(tmp_path):
    """Onboarding, the placement lesson, the profile, a goal, a topic, a lesson with a help
    request, a wrong click and a Chinese answer, its evaluation and the next plan: nothing the
    page or Jane shows has Chinese. Only the student's own Chinese answer is left out."""
    from uuid import uuid4

    from fastapi.testclient import TestClient
    from support import (
        LEARNER,
        answer,
        deliver_all,
        onboarding_app,
        read_aloud_all,
        read_story,
        ready,
        settle,
        wait_jobs,
    )

    from project_j.application.configuration import Settings
    from project_j.application.documents import identity
    from project_j.application.progress import advance, next_step
    from project_j.classroom.responses import presentation
    from project_j.contracts import RevisionRef
    from project_j.interface.api import create_app
    from project_j.learning.fixtures import initialize
    from project_j.learning.onboarding import (
        QUESTIONS,
        ProfileEdit,
        confirm_profile,
        save_chat,
        save_lexile,
    )
    from project_j.preparation.task_types import PROGRAM_JUDGED

    app = onboarding_app(tmp_path)
    seen: list = []

    def look():
        seen.append(app.dashboard(LEARNER))

    def jane():
        seen.append(app.core.snapshot())
        seen.extend(action.model_dump(mode="json") for action in app.core.actions.values())

    async def lesson(course_ref, request_id):
        await ready(app, {"course_ref": course_ref})
        started = app.start(request_id, LEARNER, course_ref, materials_ready=True)
        assert started["decision"] == "allow_simulation"
        core = app.core
        deliver_all(core)
        jane()
        read_story(app.core)
        asked = clicked_wrong = False
        while (task := core.current_task()) and core.mode != "ended":
            if task.purpose == "answer" and not asked:
                await answer(app, CHINESE_ANSWER)
                jane()
                await answer(app, "Can you help me?", "help")
                asked = True
            elif task.purpose == "answer":
                await answer(app, task.acceptable_simulated[0])
            elif task.purpose in PROGRAM_JUDGED and not clicked_wrong and task.purpose == "choice":
                view = presentation(task)
                right = view["options"].index(task.choice.options[task.choice.correct])
                wrong = {"choice": (right + 1) % len(view["options"])}
                await app.adapter.response(str(uuid4()), wrong)
                clicked_wrong = True
            elif task.purpose in {"point", "offscreen"}:
                await answer(app, "I pointed to the folded paper.", "report")
                if core.mode == "offscreen":
                    core.control(str(uuid4()), "return")
            else:
                await read_aloud_all(app)
            deliver_all(core)
            jane()
        await answer(app, "Yes, I heard.", "confirmation")
        jane()
        assert core.mode == "ended" and asked
        return app.seal().body["activity_id"]

    async def scenario():
        initialize(app)
        look()
        save_lexile(app, "l1", LEARNER, "650L")
        look()
        answers = ["For school.", "I like games.", "Two hours.", "Speaking."][: len(QUESTIONS)]
        save_chat(app, "c1", LEARNER, answers)
        look()
        await wait_jobs(app)
        step = next_step(app, LEARNER)
        assert step["kind"] == "placement"
        activity = await lesson(RevisionRef.model_validate(step["subject"]["course_ref"]), "s0")
        advance(app, LEARNER)
        await wait_jobs(app)
        look()
        assert next_step(app, LEARNER)["kind"] == "profile"
        edit = ProfileEdit(
            background="I learn for school.",
            interests="games",
            weekly_time="two hours",
            focus="speaking",
        )
        confirm_profile(app, "p1", LEARNER, edit)
        advance(app, LEARNER)
        await wait_jobs(app)
        look()
        await app.decide_goal("g1", LEARNER, app.current(LEARNER, "demand").ref, "accept")
        look()
        app.choose_focus("f1", LEARNER, app.current(LEARNER, "demand").ref, "bridges", [], None)
        advance(app, LEARNER)
        await wait_jobs(app)
        look()
        step = next_step(app, LEARNER)
        assert step["kind"] == "course", step
        activity = await lesson(RevisionRef.model_validate(step["subject"]["course_ref"]), "s1")
        app.feedback("fb1", LEARNER, activity, "Nothing to add.", [])
        advance(app, LEARNER)
        await wait_jobs(app)
        look()
        evaluation = app.store.current(identity(activity, "evaluation"), LEARNER)
        app.acknowledge("ack1", LEARNER, activity, evaluation.ref)
        advance(app, LEARNER)
        await wait_jobs(app)
        look()
        assert not app.job_failures, app.job_failures

    asyncio.run(scenario())
    with TestClient(create_app(Settings(), app)) as client:
        auth = {"Authorization": f"Bearer {app.token}"}
        seen.append(client.get("/api/dashboard", headers=auth).json())
        chat = {"request_id": "c2", "learner_id": LEARNER, "answers": ["Yes."] * len(QUESTIONS)}
        seen.append(client.post("/api/onboarding/chat", headers=auth, json=chat).json())
        bad = {"request_id": "p9", "learner_id": LEARNER} | edit_with_lexile("650")
        seen.append(client.post("/api/profile", headers=auth, json=bad).json())
        settle(app)
    assert any(ENGLISH_ONLY in text for text in leaves(seen))  # Jane reminded, in English
    found = sorted({text for text in leaves(seen) if chinese(text.replace(CHINESE_ANSWER, ""))})
    assert not found, "\n".join(found)


def leaves(value):
    """Every string inside value, dict keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from leaves(item)
    elif isinstance(value, list | tuple):
        for item in value:
            yield from leaves(item)


def edit_with_lexile(lexile):
    return {
        "background": "School.",
        "interests": "Games.",
        "weekly_time": "Two hours.",
        "focus": "Speaking.",
        "lexile": lexile,
    }


def test_the_check_stamp_the_page_shows_is_english():
    """The dashboard returns the check stamp as written by project_j/scripts/check.py."""
    import importlib.util

    from conftest import ROOT

    spec = importlib.util.spec_from_file_location("check", ROOT / "project_j/scripts/check.py")
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    assert check.STAMP["actual_api"] == "unverified" and not chinese(check.STAMP)
