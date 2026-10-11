import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib import request, error
from project_j.server import App, handler_for


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.app=App(data_dir=cls.temp.name)
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),handler_for(cls.app))
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base="http://127.0.0.1:"+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(2);cls.temp.cleanup()

    def call(self,path,data=None,origin=None):
        headers={"Content-Type":"application/json"}
        if origin:headers["Origin"]=origin
        req=request.Request(self.base+path,data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            with request.urlopen(req,timeout=3) as response:
                return response.status,json.loads(response.read())
        except error.HTTPError as exc:
            return exc.code,json.loads(exc.read())

    def test_page_assets_and_config(self):
        for path in ["/","/app.js","/styles.css"]:
            with request.urlopen(self.base+path,timeout=3) as response:
                self.assertEqual(200,response.status)
        status,value=self.call("/api/config")
        self.assertEqual(200,status)
        self.assertNotIn("OPENAI_API_KEY",json.dumps(value))
        self.assertFalse(value["formal_available"])

    def test_no_static_directory_escape(self):
        with self.assertRaises(error.HTTPError) as result:
            request.urlopen(self.base+"/../.env",timeout=3)
        self.assertEqual(404,result.exception.code)

    def test_cross_site_request_rejected(self):
        status,_=self.call("/api/sessions",{"mode":"adult"},origin="https://example.org")
        self.assertEqual(403,status)

    def test_fixed_first_question_and_final_export(self):
        status,state=self.call("/api/sessions",{"mode":"adult","provider":"rules"})
        self.assertEqual(200,status)
        for event in [{"kind":"auto","text":"听清了"},{"kind":"auto","text":"读完了"}]:
            status,state=self.call("/api/event",{"session_id":state["session_id"],"revision":state["revision"],"event":event})
            self.assertEqual(200,status)
        self.assertEqual("R1",state["task_id"])
        _,state=self.call("/api/event",{"session_id":state["session_id"],"revision":state["revision"],"event":{"kind":"close"}})
        self.assertEqual("summary",state["task_kind"])
        _,state=self.call("/api/event",{"session_id":state["session_id"],"revision":state["revision"],"event":{"kind":"ack","confirmed":True,"delivery":"read"}})
        status,report=self.call("/api/feedback",{"session_id":state["session_id"],"feedback":"技术测试，未做离屏。","ai":False})
        self.assertEqual(200,status)
        self.assertFalse(report["child_learning_evidence"])
        status,_=self.call("/api/export",{"session_id":state["session_id"]})
        self.assertEqual(200,status)

    def test_child_request_rejected(self):
        status,_=self.call("/api/sessions",{"mode":"child","provider":"rules"})
        self.assertEqual(409,status)


if __name__=="__main__":
    unittest.main()
