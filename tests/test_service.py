import sqlite3
import unittest
from datetime import datetime
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


class PayrollSupportServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.metrics_repo = InMemoryMetricsRepository()
        self.payroll_repo = SpreadsheetPayrollRepository(Path(__file__).resolve().parents[1] / "payslip.xlsx")
        self.hr_db_path = Path(__file__).resolve().parent / "test_hr_requests.db"
        self.ticket_repo = SQLiteHRRequestRepository(self.hr_db_path)
        self.ticket_repo.clear_requests()
        self.service = PayrollSupportService(
            auth_service=AuthService(self.payroll_repo.get_supported_employee_ids()),
            nlp_engine=RuleBasedNLPEngine(),
            knowledge_repo=InMemoryKnowledgeRepository(),
            payroll_repo=self.payroll_repo,
            ticket_repo=self.ticket_repo,
            metrics_repo=self.metrics_repo,
        )

    def tearDown(self) -> None:
        self.ticket_repo.clear_requests()

    def test_security_route_for_unauthorized_user(self) -> None:
        result = self.service.handle_message("BAD", "Show me my payslip")
        self.assertEqual(result.route, "security")
        self.assertEqual(result.status, "error")

    def test_latest_payslip_summary_uses_most_recent_row(self) -> None:
        result = self.service.handle_message("NTU001", "Show me my payslip")
        self.assertEqual(result.route, "payroll")
        self.assertEqual(result.data["pay_period"], "01/08/2025 - 31/08/2025")
        self.assertEqual(result.data["pay_date"], "2025-08-31")
        self.assertEqual(result.data["gross_salary"], 6000.0)
        self.assertEqual(result.data["total_deductions"], 2265.0)
        self.assertEqual(result.data["net_pay"], 3735.0)

    def test_supported_payroll_queries_return_spreadsheet_values(self) -> None:
        cases = [
            ("What are my employee details?", "employee_name", "Jane Doe"),
            ("What is my tax code?", "tax_code", "1257L"),
            ("What is my pay date?", "pay_date", "2025-08-31"),
            ("What is my pay period?", "pay_period", "01/08/2025 - 31/08/2025"),
            ("What is my gross salary?", "gross_salary", 6000.0),
            ("What is my net pay?", "net_pay", 3735.0),
            ("How much PAYE tax did I pay?", "paye_tax", 1250.0),
            ("How much National Insurance did I pay?", "national_insurance", 510.0),
            ("What is my pension deduction?", "pension", 240.0),
            ("What is my student loan deduction?", "student_loan", 240.0),
            ("What is my healthcare scheme deduction?", "healthcare_scheme", 25.0),
            ("What are my total deductions?", "total_deductions", 2265.0),
        ]

        for query, key, expected in cases:
            with self.subTest(query=query):
                result = self.service.handle_message("NTU001", query)
                self.assertEqual(result.route, "payroll")
                self.assertEqual(result.data[key], expected)
                self.assertFalse(result.awaiting_confirmation)

    def test_unknown_query_returns_hr_offer(self) -> None:
        result = self.service.handle_message("NTU001", "Can you tell me my annual leave balance?")
        self.assertEqual(result.route, "hr_offer")
        self.assertTrue(result.awaiting_confirmation)
        self.assertIn("Would you like me to put you in touch with HR", result.message)

    def test_yes_after_hr_offer_creates_handoff_and_persists_request(self) -> None:
        self.service.handle_message("NTU001", "Can you tell me my annual leave balance?")
        result = self.service.handle_message("NTU001", "yes")
        self.assertEqual(result.route, "hr_escalation")
        self.assertFalse(result.awaiting_confirmation)
        self.assertEqual(result.data["hr_contacted"], True)
        self.assertTrue(result.data["ticket_id"].startswith("HR-"))
        stored_requests = self.ticket_repo.list_requests()
        self.assertEqual(len(stored_requests), 1)
        self.assertEqual(stored_requests[0][0], "NTU001")
        self.assertEqual(stored_requests[0][1], "Can you tell me my annual leave balance?")
        datetime.fromisoformat(stored_requests[0][2])
        datetime.fromisoformat(result.data["sent_at"])

    def test_no_after_hr_offer_cancels_handoff(self) -> None:
        self.service.handle_message("NTU001", "Can you tell me my annual leave balance?")
        result = self.service.handle_message("NTU001", "no thanks")
        self.assertEqual(result.route, "hr_offer")
        self.assertFalse(result.awaiting_confirmation)
        self.assertEqual(result.data["hr_contacted"], False)
        self.assertEqual(self.ticket_repo.list_requests(), [])

    def test_new_supported_query_clears_pending_handoff(self) -> None:
        self.service.handle_message("NTU001", "Can you tell me my annual leave balance?")
        result = self.service.handle_message("NTU001", "What is my net pay?")
        self.assertEqual(result.route, "payroll")
        self.assertEqual(result.data["net_pay"], 3735.0)

    def test_hr_database_contains_only_employee_id_and_query_columns(self) -> None:
        connection = sqlite3.connect(str(self.hr_db_path))
        try:
            columns = connection.execute("PRAGMA table_info(hr_requests)").fetchall()
        finally:
            connection.close()
        self.assertEqual([column[1] for column in columns], ["employee_id", "hr_query", "sent_at"])

    def test_metrics_include_offer_handoff_error_and_automated(self) -> None:
        self.service.handle_message("NTU001", "What is my net pay?")
        self.service.handle_message("NTU001", "Can you tell me my annual leave balance?")
        self.service.handle_message("NTU001", "yes")
        self.service.handle_message("BAD", "Show me my payslip")

        summary = self.metrics_repo.get_summary()
        self.assertEqual(summary.total_interactions, 4)
        self.assertAlmostEqual(summary.deflection_rate, 0.25)
        self.assertAlmostEqual(summary.offer_rate, 0.25)
        self.assertAlmostEqual(summary.handoff_rate, 0.25)
        self.assertAlmostEqual(summary.error_rate, 0.25)


if __name__ == "__main__":
    unittest.main()
