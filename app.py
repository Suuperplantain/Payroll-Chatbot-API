from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from src.payroll_support import (
    AuthService,
    InMemoryKnowledgeRepository,
    InMemoryMetricsRepository,
    PayrollSupportService,
    RuleBasedNLPEngine,
    SQLiteHRRequestRepository,
    SpreadsheetPayrollRepository,
)

APP_DIR = Path(__file__).parent
PAYSLIP_PATH = APP_DIR / "payslip.xlsx"
HR_REQUESTS_DB_PATH = APP_DIR / "hr_requests.db"

payroll_repo = SpreadsheetPayrollRepository(PAYSLIP_PATH)
ticket_repo = SQLiteHRRequestRepository(HR_REQUESTS_DB_PATH)
auth_service = AuthService(
    allowed_employee_ids=payroll_repo.get_supported_employee_ids(),
    token_to_employee_id={
        f"token-{employee_id}": employee_id
        for employee_id in payroll_repo.get_supported_employee_ids()
    },
)
metrics_repo = InMemoryMetricsRepository()

service = PayrollSupportService(
    auth_service=auth_service,
    nlp_engine=RuleBasedNLPEngine(),
    knowledge_repo=InMemoryKnowledgeRepository(),
    payroll_repo=payroll_repo,
    ticket_repo=ticket_repo,
    metrics_repo=metrics_repo,
)


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        try:
            if self.path == "/api/health":
                self._json_response(HTTPStatus.OK, {"status": "ok"})
                return

            if self.path == "/api/metrics":
                summary = metrics_repo.get_summary()
                self._json_response(
                    HTTPStatus.OK,
                    {
                        "total_interactions": summary.total_interactions,
                        "deflection_rate": round(summary.deflection_rate, 4),
                        "average_response_time": round(summary.average_response_time, 4),
                        "error_rate": round(summary.error_rate, 4),
                        "handoff_rate": round(summary.handoff_rate, 4),
                        "offer_rate": round(summary.offer_rate, 4),
                    },
                )
                return

            self._json_response(HTTPStatus.NOT_FOUND, {"error": "Not found"})
        except Exception:
            self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self.path != "/api/chat":
                self._json_response(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return

            self._handle_chat()
        except Exception:
            self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error"})

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return

    def _json_response(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _chat_response(self, status: int, payload: dict) -> None:
        self._json_response(status, payload)

    def _read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", 0))
        payload_raw = self.rfile.read(content_length)
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON payload") from exc

        if not isinstance(payload, dict):
            raise ValueError("JSON payload must be an object")
        return payload

    def _validate_auth(self, payload: dict) -> str | None:
        employee_id = payload.get("employee_id")
        token = payload.get("token")
        employee_id_str = str(employee_id) if employee_id is not None else None
        token_str = str(token) if token is not None else None
        return auth_service.validate_credentials(employee_id=employee_id_str, token=token_str)

    def _handle_chat(self) -> None:
        try:
            payload = self._read_json_body()
        except ValueError as exc:
            self._chat_response(
                HTTPStatus.BAD_REQUEST,
                {
                    "status": "error",
                    "route": "request",
                    "message": str(exc),
                    "data": None,
                    "awaiting_confirmation": False,
                },
            )
            return

        employee_id = self._validate_auth(payload)
        message = payload.get("message")

        if employee_id is None:
            metrics_repo.record_interaction(outcome="error", response_time=0.0)
            self._chat_response(
                HTTPStatus.UNAUTHORIZED,
                {
                    "status": "error",
                    "route": "security",
                    "message": "Unauthorized",
                    "data": None,
                    "awaiting_confirmation": False,
                },
            )
            return

        if not isinstance(message, str) or not message.strip():
            self._chat_response(
                HTTPStatus.BAD_REQUEST,
                {
                    "status": "error",
                    "route": "request",
                    "message": "Invalid request body",
                    "data": None,
                    "awaiting_confirmation": False,
                },
            )
            return

        result = service.handle_message(employee_id=employee_id, message=message)
        self._chat_response(
            HTTPStatus.OK,
            {
                "status": result.status,
                "route": result.route,
                "message": result.message,
                "data": result.data,
                "awaiting_confirmation": result.awaiting_confirmation,
            },
        )


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", 8000), Handler)
    print("Payroll API running at http://localhost:8000")
    server.serve_forever()
