"""Course-driven Harness. State and events stay in memory until adult feedback/export."""
import copy
import threading
import time
import uuid
from .course import canonical_hash
from .providers import ProviderError


class ClassroomError(ValueError):
    pass


LEVELS = {"unknown": -1, "independent": 0, "cue": 1, "scaffold": 2, "model": 3}


class Classroom:
    def __init__(self, course, provider, mode="adult", clock=time.monotonic):
        if mode != "adult":
            raise ClassroomError("当前版本仅供成人验证，正式儿童课堂尚未验收")
        self.course, self.provider, self.clock = copy.deepcopy(course), provider, clock
        self.id, self.revision = uuid.uuid4().hex, 0
        self.hash = canonical_hash(course)
        self.tasks = {t["id"]: t for t in course["tasks"]}
        self.current = course["start"]
        self.status = "active"
        self.started = clock()
        self.events, self.samples, self.helps = [], [], []
        self.level = "unknown"
        self.attempts, self.help_count = 0, 0
        self.practice_task = None
        self.awaiting_difficulty = False
        self.exit_required = False
        self.exposure = []
        self.parent_feedback = None
        self.final_review = None
        self.lock = threading.RLock()
        self.finalize_lock = threading.Lock()
        self.message = self.task()["prompt"]

    def task(self):
        base = self.tasks[self.current]
        return self.practice_task if base["kind"] == "practice" and self.practice_task else base

    def _summary(self):
        attempted = len(self.samples)
        wrong = sum(s["judgment"]["verdict"] == "incorrect" for s in self.samples)
        unknown = sum(s["judgment"]["verdict"] == "unknown" for s in self.samples)
        helped = sum(s["help"] in {"cue", "scaffold", "model"} for s in self.samples)
        offscreen = any(e["kind"] == "offscreen_report" and e.get("done") is True for e in self.events)
        recommendation = ("下次先缩小阅读任务，练回到原文找依据。" if wrong else
                          "下次保留相近阅读难度，练短英语表达并复查理解。")
        return ("本次成人检查收到{}个回答样本：{}个与题卡不符，{}个保留未知，"
                "{}个在AI内容帮助后取得。离屏{}。家长其他帮助按实际报告保留，"
                "这些结果不能判断孩子能力。{}".format(
                    attempted, wrong, unknown, helped, "已报告做过" if offscreen else "未取得已做报告",
                    recommendation) +
                "本次方法是先尝试、回文找依据，困难时换方法。"
                "正式备课仍需材料、真实语音、备用流程验收；下节建议待家长反馈完善。"
                "请确认小结和下节建议已读到或听到，然后结束。")

    def _enter(self, task_id):
        self.current = task_id
        self.level, self.attempts, self.help_count = "unknown", 0, 0
        self.practice_task = None
        self.awaiting_difficulty = False
        self.exit_required = False
        base = self.tasks[task_id]
        if base["kind"] == "practice":
            candidates = [s for s in self.samples if s["task_id"] in base["candidates"]
                          and s["judgment"]["verdict"] != "correct"]
            target = candidates[0]["task_id"] if candidates else base["default"]
            self.practice_task = copy.deepcopy(self.tasks[target])
            self.practice_task.update(id=base["id"], kind="answer", phase="practice", defer_feedback=False)
            self.practice_task["prompt"] = "回到这一题再试一次：" + self.practice_task["prompt"]
            self.message = self.practice_task["correction"] + " " + self.practice_task["prompt"]
        elif base["kind"] == "summary":
            self.message = self._summary()
        else:
            self.message = base["prompt"]

    def _advance(self):
        nxt = self.tasks[self.current].get("next")
        if nxt is None:
            self.status, self.message = "completed", "本次成人检查结束。关闭语音后，请提交实际反馈。"
        else:
            self._enter(nxt)

    def _route(self, event):
        event = dict(event)
        if event.get("kind") != "auto":
            return event
        text = event.get("text", "").strip()
        if text in {"停止", "结束对话", "不想继续", "stop"}:
            event.update(kind="stop", explicit=True)
        elif text in {"收尾", "到时间了", "请总结", "结束本课并总结"}:
            event["kind"] = "close"
        elif text in {"暂停", "pause", "恢复", "继续", "resume"}:
            event["kind"] = "resume" if text in {"恢复", "继续", "resume"} else "pause"
        elif text in {"再说一次", "重复", "只重复题干"}:
            event["kind"] = "repeat"
        elif text in {"跳过", "skip"}:
            event["kind"] = "skip"
        elif any(word in text for word in ("求助", "不知道", "不懂", "不会", "没听懂", "帮我")):
            event["kind"] = "help"
            event["difficulty"] = ("instruction" if any(w in text for w in ("题目", "怎么做", "指令"))
                                   else "sentence" if any(w in text for w in ("说出来", "句子", "表达"))
                                   else "vocabulary" if any(w in text for w in ("词", "bridge", "fold", "bend"))
                                   else "unknown")
        elif self.awaiting_difficulty:
            choices = {"词":"vocabulary", "词义":"vocabulary", "单词":"vocabulary",
                       "题目":"instruction", "题目说明":"instruction", "指令":"instruction",
                       "表达":"sentence", "英语表达":"sentence", "句子":"sentence",
                       "理解":"meaning", "阅读理解":"meaning"}
            if text in choices:
                event.update(kind="help", difficulty=choices[text])
            else:
                event["kind"] = "clarify_help"
        elif self.task()["kind"] in {"ack", "summary"} and text in {
                "听清了", "听清了，开始", "开始", "本课目标听到了", "读完了",
                "读好了", "小结和下节建议听到了", "确认", "已读到"}:
            event.update(kind="ack", confirmed=True, delivery="heard" if "听" in text else "read")
        elif self.task()["kind"] == "experience":
            event["kind"] = "experience"
        else:
            event["kind"] = "answer"
        return event

    def view(self):
        with self.lock:
            task = self.task()
            material_id = task.get("material")
            material = self.course["materials"].get(material_id) if material_id else None
            return {"session_id": self.id, "revision": self.revision, "mode": "adult",
                    "status": self.status, "task_id": self.current, "task_kind": task["kind"],
                    "phase": task.get("phase", ""), "title": self.course["title"],
                    "course_version": self.course["version"], "course_hash": self.hash,
                    "utterance": self.message, "material": copy.deepcopy(material),
                    "cards": task.get("cards", []),
                    "offscreen_questions": [{"id": q["id"], "prompt": q["prompt"]}
                                           for q in task.get("questions", [])],
                    "help_level": self.level, "help_count": self.help_count,
                    "help_exit_required": self.exit_required,
                    "sample_count": len(self.samples), "elapsed_seconds": round(self.clock() - self.started),
                    "formal_ready": False, "feedback_saved": self.parent_feedback is not None,
                    "speech": {"text_prepared": True, "heard": "unknown"}}

    def handle(self, event, expected_revision):
        """Model work runs outside the lock so Stop can invalidate an in-flight answer."""
        if not isinstance(event, dict):
            raise ClassroomError("事件必须是对象")
        with self.lock:
            event = self._route(event)
            kind = event.get("kind")
            if kind == "stop" and event.get("explicit") is True and self.status not in {"completed", "stopped"}:
                self.status, self.message = "stopped", "已停止，不再出题。正常小结保持未完成。"
                self.revision += 1
                self.events.append({"kind": "stop", "explicit": True, "revision": self.revision})
                return self.view()
            if expected_revision != self.revision:
                raise ClassroomError("版本已变化；请读取当前状态，不重放回答")
            if self.status in {"completed", "stopped"}:
                raise ClassroomError("活动已结束，不补交教学事件")
            if self.status == "paused" and kind not in {"resume", "close"}:
                raise ClassroomError("当前已暂停；可以恢复、收尾或停止")
            if self.exit_required and kind not in {"skip", "close", "repeat", "pause", "resume"}:
                raise ClassroomError("本题已到尝试上限，可以跳过、收尾或停止")
            if event.get("source", "parent_report") not in {"parent_report", "technical_test", "voice_transcript"}:
                raise ClassroomError("输入来源未知")
            if event.get("help_level", "unknown") not in LEVELS:
                raise ClassroomError("帮助条件不合法")
            task = copy.deepcopy(self.task())
            if kind == "answer" and task["kind"] == "answer":
                text = event.get("text", "")
                if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                    raise ClassroomError("请提供本轮实际回答")
                work = [("answer", task, text)]
            elif kind == "action_report" and task["kind"] == "action":
                if event.get("done") is not True or not event.get("text"):
                    raise ClassroomError("动作需要实际完成报告；没有做可跳过")
                work = [("action", task, event["text"])]
            elif kind == "offscreen_report" and task["kind"] == "offscreen":
                answers = event.get("answers")
                if event.get("done") is not True or not isinstance(answers, dict) or any(
                        not isinstance(answers.get(q["id"]), str) or not answers[q["id"]].strip()
                        for q in task["questions"]):
                    raise ClassroomError("请报告实际离屏完成及两题原答；没做可以跳过")
                work = [("offscreen", q, answers[q["id"]]) for q in task["questions"]]
            else:
                work = []
        # If a request fails, state and revision remain unchanged.
        judgments = []
        for phase, question, text in work:
            scoped = copy.deepcopy(question)
            material_id = question.get("material")
            if material_id:
                scoped["reading_material"] = copy.deepcopy(self.course["materials"][material_id])
            judgments.append((phase, question, text, self.provider.judge(scoped, text)))
        with self.lock:
            if expected_revision != self.revision or self.status in {"completed", "stopped"} or (
                    judgments and self.status == "paused"):
                raise ClassroomError("本轮已失效；迟到判断已丢弃")
            return self._apply(event, judgments)

    def _apply(self, event, judgments):
        kind, task = event["kind"], self.task()
        note = ""
        if kind == "pause":
            self.status, self.message = "paused", "已暂停，准备好可以恢复，或收尾、停止。"
        elif kind == "resume":
            if self.status != "paused":
                raise ClassroomError("当前没有暂停")
            self.status, self.message = "active", task["prompt"]
        elif kind == "close":
            self._enter(next(t["id"] for t in self.tasks.values() if t["kind"] == "summary"))
        elif kind == "repeat":
            # Summary repeats the actual summary rather than a generic placeholder.
            self.message = self._summary() if task["kind"] == "summary" else task["prompt"]
        elif kind == "ack" and task["kind"] in {"ack", "summary"}:
            if event.get("confirmed") is not True or event.get("delivery") not in {"read", "heard"}:
                raise ClassroomError("需要实际读到或听到的确认")
            self._advance()
        elif kind == "skip":
            if task["kind"] in {"ack", "summary"}:
                raise ClassroomError("目标和正常收尾需要确认；不愿继续可以停止")
            self._advance()
        elif kind == "help":
            if task["kind"] not in {"answer", "action"}:
                self.message = "这一步可以重复说明或跳过；离屏观察不暗给答案。"
            elif self.help_count >= 3:
                self.exit_required = True
                self.message = "这题已换过帮助，先到这里。可以跳过或停止。"
            else:
                difficulty = event.get("difficulty", "unknown")
                if difficulty not in {"unknown", "instruction", "vocabulary", "sentence", "meaning"}:
                    raise ClassroomError("请说明是题目、词义、理解还是表达困难")
                if difficulty == "unknown" and not self.awaiting_difficulty and self.help_count == 0:
                    self.awaiting_difficulty = True
                    self.message = "你卡在题目说明、词义、理解还是英语表达？选一个告诉我；也可以跳过。"
                else:
                    self.awaiting_difficulty = False
                    cards = task.get("help_by_difficulty", {}).get(difficulty, task["help"])
                    help_card = cards[self.help_count]
                    self.help_count += 1
                    self.level = max((self.level, help_card["level"]), key=lambda x: LEVELS[x])
                    self.helps.append({"task_id": self.current, "difficulty": difficulty, **help_card})
                    self.exposure.append({"task_id": self.current, "level": self.level})
                    self.message = help_card["text"] + " 请再试一次；也可以跳过。"
        elif kind == "clarify_help" and self.awaiting_difficulty:
            self.message = "还没弄清困难，不把这句当作答题。请说题目说明、词义、理解或英语表达；也可以跳过。"
        elif judgments:
            for phase, question, text, judgment in judgments:
                help_level = event.get("help_level", "unknown")
                if help_level not in LEVELS:
                    raise ClassroomError("帮助条件不合法")
                help_level = max((help_level, self.level), key=lambda x: LEVELS[x])
                self.samples.append({"task_id": question["id"], "phase": phase if phase != "answer"
                                     else task.get("phase", "practice"),
                                     "text": text, "help": help_level,
                                     "source": event.get("source", "parent_report"),
                                     "prior_exposure": copy.deepcopy(self.exposure),
                                     "judgment": judgment})
            self.attempts += 1
            if kind == "offscreen_report":
                # Both responses have been collected and judged before any feedback is released.
                results = ["{}：{}。".format(q["id"], "与题卡对应" if j["verdict"] == "correct"
                                           else q["correction"] if j["verdict"] == "incorrect" else "暂不能可靠判断")
                           for _, q, _, j in judgments]
                note = " ".join(results) + " "
                self._advance()
            elif task.get("defer_feedback"):
                note = "收到本次原回答，先保留，稍后一起回文核对。 "
                self._advance()
            else:
                verdict = judgments[0][3]["verdict"]
                if verdict == "correct":
                    note = "这次表达与题卡意思对应；帮助条件已保留。 "
                    self._advance()
                elif self.attempts >= 4 or (self.help_count >= 3 and self.attempts >= 2):
                    self.exit_required = True
                    self.message = "本题先到这里，不继续反复猜。可以跳过、收尾或停止。"
                elif self.attempts >= 2 and self.help_count >= 2:
                    self.message = "已经尝试并换过帮助。可以再用一次示范，或跳过、停止。"
                elif verdict == "incorrect":
                    self.message = task["correction"] + " 请再试一次；需要时可以求助。"
                else:
                    self.message = "这个表达暂不能可靠核对，不判不会或发音。可以换个说法、求助或跳过。"
        elif kind == "experience" and task["kind"] == "experience":
            if not isinstance(event.get("text"), str) or not event["text"].strip():
                raise ClassroomError("请提供实际体验，或跳过")
            self._advance()
        else:
            raise ClassroomError("当前任务需要确认、实际回答或动作报告；不猜完成")
        self.events.append({"task_id": task["id"], **copy.deepcopy(event),
                            "revision": self.revision + 1})
        self.message = note + self.message
        self.revision += 1
        if self.clock() - self.started >= (self.course["duration_minutes"] - 3) * 60:
            if self.status == "active" and self.task()["kind"] not in {"ack", "summary"}:
                self._enter(next(t["id"] for t in self.tasks.values() if t["kind"] == "summary"))
        return self.view()

    def report(self):
        with self.lock:
            if self.status not in {"completed", "stopped"}:
                raise ClassroomError("活动结束后才能整理")
            return {"schema_version": 1, "kind": "adult_preparation_review", "adult_only": True,
                    "child_learning_evidence": False, "session_id": self.id, "status": self.status,
                    "course_id": self.course["id"], "course_version": self.course["version"],
                    "course_hash": self.hash, "events": copy.deepcopy(self.events),
                    "samples": copy.deepcopy(self.samples), "help_trace": copy.deepcopy(self.helps),
                    "parent_feedback": self.parent_feedback, "ai_review": self.final_review,
                    "audio_quality": "not_verified", "formal_ready": False}
