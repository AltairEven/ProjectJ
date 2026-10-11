import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from project_j.course import load_course, validate, readiness
from project_j.controller import Classroom, ClassroomError
from project_j.providers import RuleProvider, OpenAIProvider, ProviderError
from project_j.server import App


class ClassroomTests(unittest.TestCase):
    def setUp(self):
        self.course = load_course()
        self.c = Classroom(self.course, RuleProvider())

    def event(self, kind, **kwargs):
        return self.c.handle({"kind": kind, **kwargs}, self.c.revision)

    def to_r1(self):
        self.event("auto", text="听清了")
        self.event("auto", text="读完了")

    def to_practice(self):
        self.to_r1()
        for text in ["car", "falls", "BAC"]:
            self.event("answer", text=text)
        self.event("action_report", done=True, text="把纸搭在两本书上了")
        self.event("answer", text="she folds paper")

    def test_course_and_budget(self):
        self.assertEqual([], validate(self.course))
        self.assertEqual(30, sum(self.course["phase_minutes"].values()))

    def test_reject_missing_evidence_and_unreachable_task(self):
        course = copy.deepcopy(self.course)
        course["tasks"][2]["rubric"]["evidence"] = []
        course["tasks"].append({"id":"stray", "kind":"ack", "prompt":"bad", "next":None})
        errors = validate(course)
        self.assertTrue(any("依据" in e for e in errors))
        self.assertTrue(any("不可达" in e for e in errors))

    def test_formal_never_unlocked_by_edited_booleans(self):
        self.course["readiness"]["status"] = "ready"
        self.assertFalse(readiness(self.course)["formal_available"])
        with self.assertRaises(ClassroomError):
            Classroom(self.course, RuleProvider(), mode="child")

    def test_goal_ack_reaches_fixed_r1(self):
        self.to_r1()
        self.assertEqual("R1", self.c.current)
        self.assertIn("What does Mia", self.c.view()["utterance"])

    def test_only_current_material_is_exposed(self):
        self.assertIsNone(self.c.view()["material"])
        self.event("auto", text="听清了")
        self.assertIn("Mia has", self.c.view()["material"]["text"])
        self.assertNotIn("Ben", json.dumps(self.c.view(), ensure_ascii=False))
        self.assertNotIn("rubric", self.c.view())

    def test_model_receives_current_reading_context_only(self):
        self.to_r1()
        captured = []
        original = self.c.provider.judge
        def record(task, text):
            captured.append(task)
            return original(task, text)
        self.c.provider.judge = record
        self.event("answer", text="bridge")
        self.assertIn("Mia has", captured[0]["reading_material"]["text"])
        self.assertNotIn("Ben", json.dumps(captured[0], ensure_ascii=False))

    def test_deferred_baseline_preserves_error(self):
        self.to_r1()
        self.event("answer", text="car")
        self.assertEqual("incorrect", self.c.samples[0]["judgment"]["verdict"])
        self.assertEqual("R2", self.c.current)
        self.assertNotIn("bridge是", self.c.message)

    def test_neutral_does_not_confirm(self):
        with self.assertRaises(ClassroomError):
            self.event("auto", text="好的")
        self.assertEqual(0, self.c.revision)

    def test_action_cannot_be_echo_or_assumed(self):
        self.to_r1()
        for text in ["bridge","falls","BAC"]:
            self.event("answer",text=text)
        with self.assertRaises(ClassroomError):
            self.event("answer", text="Put the paper on the books")
        with self.assertRaises(ClassroomError):
            self.event("action_report",done=False,text="把纸搭在两本书上了")
        self.assertEqual("L1", self.c.current)

    def test_unknown_answer_not_scored_as_wrong(self):
        self.to_r1()
        self.event("answer",text="声音不清楚的一段")
        self.assertEqual("unknown",self.c.samples[0]["judgment"]["verdict"])

    def test_help_diagnoses_then_changes_method(self):
        self.to_practice()
        self.event("help")
        self.assertEqual(0,self.c.help_count)
        self.event("auto",text="词")
        first=self.c.message
        self.event("help",difficulty="vocabulary")
        self.assertNotEqual(first,self.c.message)
        self.assertEqual("scaffold",self.c.level)
        self.event("answer",text="bridge",help_level="independent")
        self.assertEqual("scaffold",self.c.samples[-1]["help"])
        self.assertEqual("car",self.c.samples[0]["text"])

    def test_help_switch_does_not_erase_demonstration(self):
        self.to_practice()
        for difficulty in ["vocabulary","instruction","sentence"]:
            self.event("help",difficulty=difficulty)
        self.assertEqual("model",self.c.level)
        self.event("answer",text="bridge",help_level="independent")
        self.assertEqual("model",self.c.samples[-1]["help"])

    def test_full_difficulty_label_never_becomes_an_answer(self):
        self.to_r1()
        self.event("help")
        self.event("auto", text="英语表达")
        self.assertEqual("R1", self.c.current)
        self.assertEqual([], self.c.samples)
        self.assertEqual("sentence", self.c.helps[-1]["difficulty"])

    def test_ambiguous_difficulty_stays_in_diagnosis(self):
        self.to_r1()
        self.event("help")
        self.event("auto", text="这个地方")
        self.assertEqual("R1", self.c.current)
        self.assertEqual([], self.c.samples)
        self.assertTrue(self.c.awaiting_difficulty)
        self.assertIn("不把这句当作答题", self.c.message)

    def test_feedback_save_failure_can_retry_and_success_is_idempotent(self):
        self.event("stop", explicit=True)
        with tempfile.TemporaryDirectory() as directory:
            app = App(data_dir=directory)
            with patch("project_j.server.Path.replace", side_effect=OSError("test failure")):
                with self.assertRaises(OSError):
                    app.finalize(self.c, "技术模拟反馈", False)
            self.assertIsNone(self.c.parent_feedback)
            self.assertEqual([], list(Path(directory).iterdir()))
            report = app.finalize(self.c, "技术模拟反馈", False)
            self.assertEqual(report, app.finalize(self.c, "技术模拟反馈", False))
            self.assertEqual(1, len(list(Path(directory).iterdir())))

    def test_help_exit_and_skip(self):
        self.to_practice()
        for _ in range(4):
            self.event("help",difficulty="vocabulary")
        self.assertTrue(self.c.exit_required)
        with self.assertRaises(ClassroomError):
            self.event("answer",text="car")
        self.event("skip")
        self.assertEqual("U",self.c.current)

    def test_offscreen_needs_actual_two_answers(self):
        self.to_practice()
        self.event("skip")
        with self.assertRaises(ClassroomError):
            self.event("offscreen_report",done=True,answers={"U1":"small"})
        self.assertEqual("U",self.c.current)
        self.event("offscreen_report",done=True,answers={"U1":"big","U2":"stays"},help_level="unknown")
        self.assertEqual(["incorrect","incorrect"],[s["judgment"]["verdict"] for s in self.c.samples[-2:]])
        self.assertIn("小巴士",self.c.message)

    def test_complete_flow_summary_confirmation(self):
        self.to_practice()
        self.event("help",difficulty="vocabulary")
        self.event("answer",text="bridge")
        self.event("offscreen_report",done=True,answers={"U1":"small","U2":"falls"})
        self.event("auto",text="帮助很有用")
        self.assertEqual("END",self.c.current)
        self.assertIn("下次",self.c.message)
        self.assertEqual("active",self.c.status)
        self.event("ack",confirmed=True,delivery="read")
        self.assertEqual("completed",self.c.status)
        report=self.c.report()
        self.assertEqual(8,len(report["samples"]))
        self.assertTrue(report["adult_only"])
        self.assertFalse(report["child_learning_evidence"])

    def test_stop_is_distinct_and_terminal(self):
        self.event("auto",text="停止")
        self.assertEqual("stopped",self.c.status)
        self.assertIsNone(self.c.parent_feedback)
        with self.assertRaises(ClassroomError):
            self.event("ack",confirmed=True,delivery="heard")

    def test_pause_and_resume(self):
        self.event("pause")
        with self.assertRaises(ClassroomError):
            self.event("answer",text="bridge")
        self.event("resume")
        self.assertEqual("active",self.c.status)

    def test_stale_event_not_replayed(self):
        self.event("ack",confirmed=True,delivery="read")
        with self.assertRaises(ClassroomError):
            self.c.handle({"kind":"ack","confirmed":True,"delivery":"read"},0)
        self.assertEqual("READ",self.c.current)

    def test_model_error_does_not_advance(self):
        self.to_r1()
        self.c.provider.judge=lambda *args: (_ for _ in ()).throw(ProviderError("timeout"))
        revision=self.c.revision
        with self.assertRaises(ProviderError):
            self.event("answer",text="bridge")
        self.assertEqual(revision,self.c.revision)
        self.assertEqual([],self.c.samples)

    def test_stop_does_not_wait_for_model(self):
        self.to_r1()
        entered, release = threading.Event(), threading.Event()
        original=self.c.provider.judge
        def slow(task,text):
            entered.set()
            release.wait(2)
            return original(task,text)
        self.c.provider.judge=slow
        failure=[]
        rev=self.c.revision
        def answer():
            try:self.c.handle({"kind":"answer","text":"bridge"},rev)
            except ClassroomError as exc:failure.append(str(exc))
        thread=threading.Thread(target=answer); thread.start()
        self.assertTrue(entered.wait(1))
        self.c.handle({"kind":"stop","explicit":True},rev)
        self.assertEqual("stopped",self.c.status)
        release.set(); thread.join(2)
        self.assertTrue(failure)
        self.assertEqual([],self.c.samples)

    def test_feedback_is_only_write_after_end(self):
        with tempfile.TemporaryDirectory() as directory:
            app=App(self.course,Path(directory)/"records")
            with self.assertRaises(ClassroomError):
                app.finalize(self.c,"还没结束",False)
            self.assertFalse(app.data_dir.exists())
            self.event("auto",text="停止")
            report=app.finalize(self.c,"成人测试结束，离屏没做。",False)
            saved=json.loads((app.data_dir/(self.c.id+".json")).read_text())
            self.assertEqual(report,saved)
            self.assertFalse(saved["child_learning_evidence"])

    def test_judge_rejects_fabricated_source(self):
        provider=OpenAIProvider()
        with patch.object(provider,"_structured",return_value={"verdict":"correct","reason":"x","evidence":["invented"]}):
            with self.assertRaises(ProviderError):
                provider.judge(self.course["tasks"][2],"bridge")

    def test_judge_contract_uses_low_review_medium(self):
        provider=OpenAIProvider()
        task=self.course["tasks"][2]
        with patch.object(provider,"_structured",return_value={"verdict":"correct","reason":"对应","evidence":task["rubric"]["evidence"]}) as call:
            provider.judge(task,"bridge")
            self.assertEqual("low",call.call_args.args[-1])
        with patch.object(provider,"_structured",return_value={"status":"reviewed","summary":"缺口","findings":[]}) as call:
            provider.review({},"preparation")
            self.assertEqual("medium",call.call_args.args[-1])


if __name__=="__main__":
    unittest.main()
