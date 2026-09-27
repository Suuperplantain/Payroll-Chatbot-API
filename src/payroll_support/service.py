from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .engine import RuleBasedNLPEngine
from .repositories import (
    KnowledgeRepository,
    MetricsRepository,
    PayrollRepository,
    PayrollSnapshot,
    TicketRepository,
)
from .security import AuthService


@dataclass
class PendingHandoff:
    original_message: str
    reason: str


@dataclass
class ChatResponse:
    status: str
    message: str
    route: str
    data: dict[str, object] | None = None
    awaiting_confirmation: bool = False


class PayrollSupportService:
    def __init__(
        self,
        auth_service: AuthService,
        nlp_engine: RuleBasedNLPEngine,
        knowledge_repo: KnowledgeRepository,
        payroll_repo: PayrollRepository,
        ticket_repo: TicketRepository,
        metrics_repo: MetricsRepository,
    ) -> None:
        self.auth_service = auth_service
        self.nlp_engine = nlp_engine
        self.knowledge_repo = knowledge_repo
        self.payroll_repo = payroll_repo
        self.ticket_repo = ticket_repo
        self.metrics_repo = metrics_repo
        self._pending_handoffs: dict[str, PendingHandoff] = {}
        self._yes_responses = {
            "yes",
            "yeah",
            "y",
            "please do",
            "ok",
            "okay",
            "sure",
            "yes please",
        }
        self._no_responses = {
            "no",
            "n",
            "no thanks",
            "no thank you",
            "not now",
        }

    def handle_message(self, employee_id: str, message: str) -> ChatResponse:
        start_time = datetime.now()

        if not self.auth_service.is_authorized(employee_id):
            return self._error_response(
                start_time,
                route="security",
                message="Authentication failed. Please verify your employee ID.",
            )

        snapshot = self.payroll_repo.get_latest_snapshot(employee_id)
        if snapshot is None:
            return self._error_response(
                start_time,
                route="security",
                message="No payroll record was found for this employee.",
            )

        pending_handoff = self._pending_handoffs.get(employee_id)
        normalized_message = self._normalize_message(message)
        if pending_handoff is not None:
            if normalized_message in self._yes_responses:
                del self._pending_handoffs[employee_id]
                return self._confirm_hr_handoff(start_time, employee_id, pending_handoff)
            if normalized_message in self._no_responses:
                del self._pending_handoffs[employee_id]
                return self._finalize(
                    start_time,
                    outcome="automated",
                    response=ChatResponse(
                        status="ok",
                        message=(
                            "No problem. I have not contacted HR. "
                            "I can still help with questions about your payslip values."
                        ),
                        route="hr_offer",
                        data={"hr_contacted": False},
                        awaiting_confirmation=False,
                    ),
                )
            del self._pending_handoffs[employee_id]

        intent = self.nlp_engine.classify(message)

        if intent.intent == "payslip_summary":
            return self._payroll_response(
                start_time,
                message=(
                    f"Your latest payslip for {snapshot.pay_period} shows gross salary GBP {snapshot.gross_salary:.2f}, "
                    f"total deductions GBP {snapshot.total_deductions:.2f}, and net pay GBP {snapshot.net_pay:.2f}."
                ),
                data=snapshot.to_summary_data(),
            )

        if intent.intent == "employee_details":
            return self._payroll_response(
                start_time,
                message=(
                    f"Your latest payslip is for {snapshot.employee_name}, {snapshot.job_title}, "
                    f"employee ID {snapshot.employee_id}."
                ),
                data={
                    "employee_id": snapshot.employee_id,
                    "employee_name": snapshot.employee_name,
                    "job_title": snapshot.job_title,
                },
            )

        if intent.intent == "tax_code":
            return self._payroll_response(
                start_time,
                message=f"Your tax code for the latest payslip is {snapshot.tax_code}.",
                data={"tax_code": snapshot.tax_code},
            )

        if intent.intent == "pay_date":
            return self._payroll_response(
                start_time,
                message=f"Your pay date was {snapshot.pay_date.strftime('%d/%m/%Y')}.",
                data={"pay_date": snapshot.pay_date.isoformat()},
            )

        if intent.intent == "pay_period":
            return self._payroll_response(
                start_time,
                message=f"Your pay period is {snapshot.pay_period}.",
                data={"pay_period": snapshot.pay_period},
            )

        if intent.intent == "gross_salary":
            return self._payroll_response(
                start_time,
                message=f"Your gross salary on the latest payslip is GBP {snapshot.gross_salary:.2f}.",
                data={"gross_salary": round(snapshot.gross_salary, 2)},
            )

        if intent.intent == "net_pay":
            return self._payroll_response(
                start_time,
                message=f"Your net pay on the latest payslip is GBP {snapshot.net_pay:.2f}.",
                data={"net_pay": round(snapshot.net_pay, 2)},
            )

        if intent.intent == "paye_tax":
            return self._deduction_response(start_time, snapshot, "PAYE tax", "paye_tax", snapshot.paye_tax)

        if intent.intent == "national_insurance":
            return self._deduction_response(
                start_time,
                snapshot,
                "National Insurance",
                "national_insurance",
                snapshot.national_insurance,
            )

        if intent.intent == "pension":
            return self._deduction_response(
                start_time,
                snapshot,
                "pension",
                "pension",
                snapshot.pension,
            )

        if intent.intent == "student_loan":
            return self._deduction_response(
                start_time,
                snapshot,
                "student loan",
                "student_loan",
                snapshot.student_loan,
            )

        if intent.intent == "healthcare_scheme":
            return self._deduction_response(
                start_time,
                snapshot,
                "healthcare scheme",
                "healthcare_scheme",
                snapshot.healthcare_scheme,
            )

        if intent.intent == "total_deductions":
            return self._payroll_response(
                start_time,
                message=f"Your total deductions on the latest payslip are GBP {snapshot.total_deductions:.2f}.",
                data={"total_deductions": round(snapshot.total_deductions, 2)},
            )

        return self._offer_hr_handoff(
            start_time,
            employee_id,
            original_message=message,
            reason=f"Intent={intent.intent}",
        )

    def _deduction_response(
        self,
        start_time: datetime,
        snapshot: PayrollSnapshot,
        label: str,
        data_key: str,
        amount: float,
    ) -> ChatResponse:
        return self._payroll_response(
            start_time,
            message=f"Your {label} on the latest payslip is GBP {amount:.2f}.",
            data={data_key: round(amount, 2)},
        )

    def _offer_hr_handoff(
        self,
        start_time: datetime,
        employee_id: str,
        original_message: str,
        reason: str,
    ) -> ChatResponse:
        self._pending_handoffs[employee_id] = PendingHandoff(
            original_message=original_message,
            reason=reason,
        )
        return self._finalize(
            start_time,
            outcome="offer",
            response=ChatResponse(
                status="ok",
                message=(
                    "Sorry, I can't handle that specific query. "
                    "Would you like me to put you in touch with HR so they can help with that request?"
                ),
                route="hr_offer",
                data={"hr_contacted": False},
                awaiting_confirmation=True,
            ),
        )

    def _confirm_hr_handoff(
        self,
        start_time: datetime,
        employee_id: str,
        pending_handoff: PendingHandoff,
    ) -> ChatResponse:
        ticket = self.ticket_repo.create_ticket(
            employee_id,
            pending_handoff.original_message,
            pending_handoff.reason,
        )
        return self._finalize(
            start_time,
            outcome="handoff",
            response=ChatResponse(
                status="ok",
                message="I've passed your request to HR, and they will help you with that query shortly.",
                route="hr_escalation",
                data={"ticket_id": ticket.ticket_id, "hr_contacted": True, "sent_at": ticket.sent_at},
                awaiting_confirmation=False,
            ),
        )

    def _payroll_response(
        self,
        start_time: datetime,
        message: str,
        data: dict[str, object],
    ) -> ChatResponse:
        return self._finalize(
            start_time,
            outcome="automated",
            response=ChatResponse(
                status="ok",
                message=message,
                route="payroll",
                data=data,
                awaiting_confirmation=False,
            ),
        )

    def _error_response(self, start_time: datetime, route: str, message: str) -> ChatResponse:
        return self._finalize(
            start_time,
            outcome="error",
            response=ChatResponse(
                status="error",
                message=message,
                route=route,
                data=None,
                awaiting_confirmation=False,
            ),
        )

    def _finalize(self, start_time: datetime, outcome: str, response: ChatResponse) -> ChatResponse:
        response_time = (datetime.now() - start_time).total_seconds()
        self.metrics_repo.record_interaction(outcome=outcome, response_time=response_time)
        return response

    def _normalize_message(self, message: str) -> str:
        return " ".join(
            "".join(char if char.isalnum() or char.isspace() else " " for char in message.lower()).split()
        )
