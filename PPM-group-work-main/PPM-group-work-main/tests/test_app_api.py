import json
import threading
import unittest
from datetime import datetime

import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import app


class AppApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self) -> None:
        app.metrics_repo.reset()  # type: ignore[attr-defined]
        app.service.clear_pending_handoffs()  # type: ignore[attr-defined]
        app.ticket_repo.clear_requests()  # type: ignore[attr-defined]

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as response:
                body = json.loads(response.read().decode("utf-8"))
                return response.status, body
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            return exc.code, body

    def _post_raw(self, path: str, raw: bytes) -> tuple[int, dict]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=raw,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as response:
                body = json.loads(response.read().decode("utf-8"))
                return response.status, body
        except urllib.error.HTTPError as exc:
            body = json.loads(exc.read().decode("utf-8"))
            return exc.code, body

    def _get(self, path: str) -> tuple[int, dict]:
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", method="GET")
        with urllib.request.urlopen(req) as response:
            body = json.loads(response.read().decode("utf-8"))
            return response.status, body

    def test_chat_requires_auth(self) -> None:
        status, body = self._post("/api/chat", {"employee_id": "BAD", "message": "tax"})
        self.assertEqual(status, 401)
        self.assertEqual(body["route"], "security")
        self.assertEqual(body["status"], "error")
        self.assertFalse(body["awaiting_confirmation"])

    def test_chat_returns_latest_payslip_summary(self) -> None:
        status, body = self._post(
            "/api/chat",
            {"employee_id": "NTU001", "message": "show my payslip"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["route"], "payroll")
        self.assertEqual(body["data"]["pay_period"], "01/08/2025 - 31/08/2025")
        self.assertEqual(body["data"]["net_pay"], 3735.0)
        self.assertFalse(body["awaiting_confirmation"])

    def test_chat_accepts_demo_token_without_employee_id(self) -> None:
        status, body = self._post(
            "/api/chat",
            {"token": "token-ntu001", "message": "What is my net pay?"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["route"], "payroll")
        self.assertEqual(body["data"]["net_pay"], 3735.0)

    def test_chat_returns_guidance_for_capability_question(self) -> None:
        status, body = self._post(
            "/api/chat",
            {"employee_id": "NTU001", "message": "What can you help with?"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["route"], "guidance")
        self.assertIn("supported_topics", body["data"])
        self.assertIn("sample_questions", body["data"])

    def test_chat_returns_hr_offer_then_escalates_on_yes_and_persists_request(self) -> None:
        status, body = self._post(
            "/api/chat",
            {"employee_id": "NTU001", "message": "Can you tell me my annual leave balance?"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["route"], "hr_offer")
        self.assertTrue(body["awaiting_confirmation"])

        status, body = self._post(
            "/api/chat",
            {"employee_id": "NTU001", "message": "yes"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["route"], "hr_escalation")
        self.assertEqual(body["data"]["hr_contacted"], True)
        self.assertTrue(body["data"]["ticket_id"].startswith("HR-"))
        datetime.fromisoformat(body["data"]["sent_at"])
        stored_requests = app.ticket_repo.list_requests()
        self.assertEqual(len(stored_requests), 1)
        self.assertEqual(stored_requests[0][0], "NTU001")
        self.assertEqual(stored_requests[0][1], "Can you tell me my annual leave balance?")
        datetime.fromisoformat(stored_requests[0][2])

    def test_chat_declines_hr_handoff_on_no(self) -> None:
        self._post(
            "/api/chat",
            {"employee_id": "NTU001", "message": "Can you tell me my annual leave balance?"},
        )
        status, body = self._post(
            "/api/chat",
            {"employee_id": "NTU001", "message": "no thanks"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["route"], "hr_offer")
        self.assertEqual(body["data"]["hr_contacted"], False)
        self.assertFalse(body["awaiting_confirmation"])
        self.assertEqual(app.ticket_repo.list_requests(), [])

    def test_invalid_json_returns_bad_request(self) -> None:
        status, body = self._post_raw("/api/chat", b"{bad json")
        self.assertEqual(status, 400)
        self.assertEqual(body["route"], "request")
        self.assertEqual(body["status"], "error")

    def test_non_object_json_returns_bad_request(self) -> None:
        status, body = self._post_raw("/api/chat", b"[]")
        self.assertEqual(status, 400)
        self.assertEqual(body["route"], "request")
        self.assertEqual(body["message"], "JSON payload must be an object")

    def test_health_endpoint_reports_runtime_details(self) -> None:
        status, body = self._get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["data_source"], "payslip.xlsx")
        self.assertEqual(body["hr_database"], "hr_requests.db")
        self.assertGreaterEqual(body["supported_employee_count"], 1)
        self.assertEqual(body["pending_hr_confirmations"], 0)

    def test_metrics_endpoint_includes_offer_rate(self) -> None:
        self._post("/api/chat", {"employee_id": "NTU001", "message": "What is my net pay?"})
        self._post("/api/chat", {"employee_id": "NTU001", "message": "Can you tell me my annual leave balance?"})
        self._post("/api/chat", {"employee_id": "NTU001", "message": "yes"})
        self._post("/api/chat", {"employee_id": "BAD", "message": "tax"})

        status, body = self._get("/api/metrics")
        self.assertEqual(status, 200)
        self.assertEqual(body["total_interactions"], 4)
        self.assertEqual(body["automated_interactions"], 1)
        self.assertEqual(body["offer_interactions"], 1)
        self.assertEqual(body["handoff_interactions"], 1)
        self.assertEqual(body["error_interactions"], 1)
        self.assertAlmostEqual(body["deflection_rate"], 0.25, places=4)
        self.assertAlmostEqual(body["offer_rate"], 0.25, places=4)
        self.assertAlmostEqual(body["handoff_rate"], 0.25, places=4)
        self.assertAlmostEqual(body["error_rate"], 0.25, places=4)


if __name__ == "__main__":
    unittest.main()
