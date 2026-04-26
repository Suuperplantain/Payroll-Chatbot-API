from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

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
MAX_REQUEST_BYTES = 8 * 1024
MAX_MESSAGE_CHARS = 500
DEFAULT_HOST = os.getenv("PAYROLL_API_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.getenv("PAYROLL_API_PORT", "8000"))
APP_STARTED_AT = datetime.now(UTC)

logger = logging.getLogger("payroll_api")


class RequestValidationError(ValueError):
    def __init__(self, message: str, status: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status = status


payroll_repo = SpreadsheetPayrollRepository(PAYSLIP_PATH)
ticket_repo = SQLiteHRRequestRepository(HR_REQUESTS_DB_PATH)
auth_service = AuthService(allowed_employee_ids=payroll_repo.get_supported_employee_ids)
metrics_repo = InMemoryMetricsRepository()

service = PayrollSupportService(
    auth_service=auth_service,
    nlp_engine=RuleBasedNLPEngine(),
    knowledge_repo=InMemoryKnowledgeRepository(),
    payroll_repo=payroll_repo,
    ticket_repo=ticket_repo,
    metrics_repo=metrics_repo,
)


def build_chat_payload(
    *,
    status: str,
    route: str,
    message: str,
    data: dict[str, object] | None = None,
    awaiting_confirmation: bool = False,
) -> dict[str, object]:
    return {
        "status": status,
        "route": route,
        "message": message,
        "data": data,
        "awaiting_confirmation": awaiting_confirmation,
    }


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        try:
            path = self._get_path()
            if path == "/api/health":
                self._json_response(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "service": "Payroll Pilot Assistant API",
                        "started_at": APP_STARTED_AT.isoformat(),
                        "data_source": PAYSLIP_PATH.name,
                        "hr_database": HR_REQUESTS_DB_PATH.name,
                        "supported_employee_count": len(payroll_repo.get_supported_employee_ids()),
                        "pending_hr_confirmations": service.get_pending_handoff_count(),
                    },
                )
                return

            if path == "/api/metrics":
                summary = metrics_repo.get_summary()
                self._json_response(
                    HTTPStatus.OK,
                    {
                        "total_interactions": summary.total_interactions,
                        "automated_interactions": summary.automated_interactions,
                        "handoff_interactions": summary.handoff_interactions,
                        "offer_interactions": summary.offer_interactions,
                        "error_interactions": summary.error_interactions,
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
            logger.exception("Unhandled GET error for %s", self.path)
            self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error"})

    def do_POST(self) -> None:  # noqa: N802
        try:
            if self._get_path() != "/api/chat":
                self._json_response(HTTPStatus.NOT_FOUND, {"error": "Not found"})
                return

            self._handle_chat()
        except Exception:
            logger.exception("Unhandled POST error for %s", self.path)
            self._json_response(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "Internal server error"})

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        logger.info("%s - %s", self.client_address[0], format % args)

    def _get_path(self) -> str:
        return urlparse(self.path).path

    def _json_response(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type.lower():
            raise RequestValidationError("Content-Type must be application/json")

        content_length = int(self.headers.get("Content-Length", 0))
        if content_length <= 0:
            raise RequestValidationError("Request body is required")
        if content_length > MAX_REQUEST_BYTES:
            raise RequestValidationError(
                f"Request body exceeds the limit of {MAX_REQUEST_BYTES} bytes",
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )

        payload_raw = self.rfile.read(content_length)
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError as exc:
            raise RequestValidationError("Invalid JSON payload") from exc

        if not isinstance(payload, dict):
            raise RequestValidationError("JSON payload must be an object")
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
        except RequestValidationError as exc:
            metrics_repo.record_interaction(outcome="error", response_time=0.0)
            self._json_response(
                exc.status,
                build_chat_payload(
                    status="error",
                    route="request",
                    message=str(exc),
                    data=None,
                    awaiting_confirmation=False,
                ),
            )
            return

        employee_id = self._validate_auth(payload)
        message = payload.get("message")

        if employee_id is None:
            metrics_repo.record_interaction(outcome="error", response_time=0.0)
            self._json_response(
                HTTPStatus.UNAUTHORIZED,
                build_chat_payload(
                    status="error",
                    route="security",
                    message="Unauthorized",
                    data=None,
                    awaiting_confirmation=False,
                ),
            )
            return

        if not isinstance(message, str) or not message.strip():
            metrics_repo.record_interaction(outcome="error", response_time=0.0)
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                build_chat_payload(
                    status="error",
                    route="request",
                    message="Message must be a non-empty string",
                    data=None,
                    awaiting_confirmation=False,
                ),
            )
            return

        normalized_message = message.strip()
        if len(normalized_message) > MAX_MESSAGE_CHARS:
            metrics_repo.record_interaction(outcome="error", response_time=0.0)
            self._json_response(
                HTTPStatus.BAD_REQUEST,
                build_chat_payload(
                    status="error",
                    route="request",
                    message=f"Message must be {MAX_MESSAGE_CHARS} characters or fewer",
                    data=None,
                    awaiting_confirmation=False,
                ),
            )
            return

        result = service.handle_message(employee_id=employee_id, message=normalized_message)
        self._json_response(
            HTTPStatus.OK,
            build_chat_payload(
                status=result.status,
                route=result.route,
                message=result.message,
                data=result.data,
                awaiting_confirmation=result.awaiting_confirmation,
            ),
        )


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("PAYROLL_API_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = ThreadingHTTPServer((DEFAULT_HOST, DEFAULT_PORT), Handler)
    print(f"Payroll API running at http://localhost:{DEFAULT_PORT}")
    server.serve_forever()
