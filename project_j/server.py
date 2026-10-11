"""Local-only HTTP app. No third-party runtime dependencies; keys never reach the browser."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from .course import ROOT, load_course, validate, readiness
from .controller import Classroom, ClassroomError
from .providers import RuleProvider, OpenAIProvider, ProviderError


def load_env():
    path = ROOT / ".env"
    allowed = {"OPENAI_API_KEY", "J_OPENAI_MODEL", "J_ASR_MODEL", "J_TTS_MODEL"}
    if path.is_file():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if key.strip() in allowed:
                    os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class App:
    def __init__(self, course=None, data_dir=None):
        self.course = course or load_course()
        self.data_dir = Path(data_dir) if data_dir else ROOT / ".data/adult"
        self.sessions = {}
        self.providers = {"rules": RuleProvider(), "openai": OpenAIProvider()}
        self.lock = threading.RLock()

    def session(self, identifier):
        with self.lock:
            if identifier not in self.sessions:
                raise ClassroomError("会话不存在；服务重启不会恢复课堂，不补造进度")
            return self.sessions[identifier]

    def finalize(self, session, feedback, ai):
        if not isinstance(feedback, str) or not feedback.strip() or len(feedback) > 4000:
            raise ClassroomError("活动结束后，请提供一段实际反馈")
        with session.finalize_lock:
            with session.lock:
                report = session.report()  # rejects active sessions before any filesystem write
                if session.parent_feedback is not None:
                    if session.parent_feedback == feedback:
                        return report  # retry after a lost HTTP response is idempotent
                    raise ClassroomError("本次已整理，请导出已有报告；补充更正需后续版本支持")
            report["parent_feedback"] = feedback
            if ai:
                try:
                    report["ai_review"] = self.providers["openai"].review(report, "postlesson")
                except ProviderError as exc:
                    report["ai_review"] = {"status": "not_run", "reason": str(exc)}
            self.data_dir.mkdir(parents=True, exist_ok=True)
            target = self.data_dir / (session.id + ".json")
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.data_dir,
                                                 suffix=".tmp", delete=False) as temp:
                    temp_path = Path(temp.name)
                    json.dump(report, temp, ensure_ascii=False, indent=2)
                    temp.write("\n")
                temp_path.replace(target)
            finally:
                if temp_path is not None and temp_path.exists():
                    temp_path.unlink()
            # Only a successful atomic save makes feedback/export available.
            with session.lock:
                session.parent_feedback = feedback
                session.final_review = report["ai_review"]
            return report


def handler_for(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, data, status=200, mime="application/json; charset=utf-8"):
            raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path == "/api/config":
                self.respond({"version": "0.1.0", "model_configured": app.providers["openai"].configured,
                              "classroom_model": app.providers["openai"].model,
                              "classroom_effort": "low", "review_effort": "medium",
                              "formal_available": False})
            elif parsed.path == "/api/course":
                c = app.course
                self.respond({k: c[k] for k in ("id", "version", "title", "primary_target",
                              "secondary_target", "duration_minutes", "phase_minutes")} |
                             {"readiness": readiness(c)})
            elif parsed.path == "/api/preparation":
                self.respond({"errors": validate(app.course), "readiness": readiness(app.course),
                              "tasks": [{"id": t["id"], "kind": t["kind"], "phase": t["phase"]}
                                        for t in app.course["tasks"]],
                              "materials": [{"id": i, "title": m["title"], "grading": m["grading"]}
                                            for i, m in app.course["materials"].items()]})
            elif parsed.path == "/api/session":
                try:
                    self.respond(app.session(parse_qs(parsed.query).get("id", [""])[0]).view())
                except ClassroomError as exc:
                    self.respond({"error": str(exc)}, 404)
            else:
                allowed = {"/": "index.html", "/app.js": "app.js", "/styles.css": "styles.css"}
                if parsed.path not in allowed:
                    self.respond({"error": "不存在"}, 404)
                    return
                mime = {"/": "text/html; charset=utf-8", "/app.js": "text/javascript; charset=utf-8",
                        "/styles.css": "text/css; charset=utf-8"}[parsed.path]
                self.respond((ROOT / "web" / allowed[parsed.path]).read_bytes(), mime=mime)

        def do_POST(self):
            port = self.server.server_port
            origin = self.headers.get("Origin")
            if origin and origin not in {"http://127.0.0.1:" + str(port), "http://localhost:" + str(port)}:
                self.respond({"error": "拒绝跨站请求"}, 403)
                return
            parsed = urlsplit(self.path)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                limit = 12 * 1024 * 1024 if parsed.path == "/api/transcribe" else 64 * 1024
                if length <= 0 or length > limit:
                    raise ClassroomError("输入为空或超过大小限制")
                raw = self.rfile.read(length)
                if parsed.path == "/api/transcribe":
                    query = parse_qs(parsed.query)
                    session = app.session(query.get("id", [""])[0])
                    rev = int(query.get("revision", ["-1"])[0])
                    if session.view()["revision"] != rev or session.status != "active":
                        raise ClassroomError("录音对应任务已变化")
                    if session.task()["kind"] == "offscreen":
                        raise ClassroomError("离屏期间不录音")
                    text = app.providers["openai"].transcribe(raw, self.headers.get("Content-Type", ""))
                    if session.view()["revision"] != rev or session.status != "active":
                        raise ClassroomError("迟到转写已丢弃")
                    self.respond({"text": text, "revision": rev, "needs_confirmation": True})
                    return
                if not self.headers.get("Content-Type", "").startswith("application/json"):
                    raise ClassroomError("请求需要JSON")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ClassroomError("请求需要对象")
                if parsed.path == "/api/sessions":
                    name = data.get("provider", "rules")
                    if name not in app.providers or not app.providers[name].configured:
                        raise ClassroomError("所选模型没有配置；可以选择本地规则模式")
                    with app.lock:
                        if len(app.sessions) >= 16:
                            raise ClassroomError("内存会话已到上限；整理后重启应用")
                        session = Classroom(app.course, app.providers[name], mode=data.get("mode", "adult"))
                        app.sessions[session.id] = session
                    self.respond(session.view())
                elif parsed.path == "/api/preparation/review":
                    self.respond(app.providers["openai"].review(app.course, "preparation"))
                elif parsed.path in {"/api/event", "/api/feedback", "/api/export", "/api/speech"}:
                    session = app.session(data.get("session_id"))
                    if parsed.path == "/api/event":
                        self.respond(session.handle(data.get("event"), data.get("revision")))
                    elif parsed.path == "/api/feedback":
                        self.respond(app.finalize(session, data.get("feedback"), data.get("ai") is True))
                    elif parsed.path == "/api/export":
                        if session.parent_feedback is None:
                            raise ClassroomError("先提交活动结束后的实际反馈")
                        self.respond(session.report())
                    else:
                        view = session.view()
                        if data.get("revision") != view["revision"] or session.status == "stopped":
                            raise ClassroomError("话语已失效，不播放")
                        # No arbitrary client-supplied text can enter speech generation.
                        audio = app.providers["openai"].speech(view["utterance"])
                        if session.view()["revision"] != view["revision"] or session.status == "stopped":
                            raise ClassroomError("迟到语音已丢弃")
                        self.respond(audio, mime="audio/mpeg")
                else:
                    self.respond({"error": "不存在"}, 404)
            except (ClassroomError, ValueError, TypeError, KeyError) as exc:
                self.respond({"error": str(exc) if isinstance(exc, ClassroomError) else "请求格式不正确"}, 409)
            except ProviderError as exc:
                self.respond({"error": str(exc)}, 503)
            except OSError:
                self.respond({"error": "本次尚未保存成功，请保留实际反馈"}, 500)
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    load_env()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(App()))
    print("Project J: http://127.0.0.1:" + str(server.server_port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
