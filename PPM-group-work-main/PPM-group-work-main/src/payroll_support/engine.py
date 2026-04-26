from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass
class IntentResult:
    intent: str
    confidence: float


class RuleBasedNLPEngine:
    """Rule-based classifier for supported payroll queries."""

    def __init__(self) -> None:
        self._intent_patterns: tuple[tuple[str, float, tuple[re.Pattern[str], ...]], ...] = (
            (
                "employee_details",
                0.95,
                (
                    re.compile(r"\bemployee details\b"),
                    re.compile(r"\bmy details\b"),
                    re.compile(r"\bemployee id\b"),
                    re.compile(r"\bjob title\b"),
                    re.compile(r"\bmy name\b"),
                    re.compile(r"\bwho am i\b"),
                ),
            ),
            ("tax_code", 0.97, (re.compile(r"\btax code\b"),)),
            (
                "pay_date",
                0.96,
                (
                    re.compile(r"\bpay date\b"),
                    re.compile(r"\bpaid on\b"),
                    re.compile(r"\bwhen was i paid\b"),
                    re.compile(r"\bwhen did i get paid\b"),
                    re.compile(r"\bwhen will i be paid\b"),
                ),
            ),
            (
                "pay_period",
                0.95,
                (
                    re.compile(r"\bpay period\b"),
                    re.compile(r"\bperiod\b.*\bpay\b"),
                    re.compile(r"\bpay\b.*\bperiod\b"),
                ),
            ),
            (
                "gross_salary",
                0.95,
                (
                    re.compile(r"\bgross salary\b"),
                    re.compile(r"\bgross pay\b"),
                    re.compile(r"^gross$"),
                ),
            ),
            (
                "net_pay",
                0.96,
                (
                    re.compile(r"\bnet pay\b"),
                    re.compile(r"\btake home\b"),
                    re.compile(r"\btake home pay\b"),
                    re.compile(r"\btake-home\b"),
                ),
            ),
            (
                "national_insurance",
                0.95,
                (
                    re.compile(r"\bnational insurance\b"),
                    re.compile(r"\bni\b"),
                ),
            ),
            ("student_loan", 0.95, (re.compile(r"\bstudent loan\b"),)),
            (
                "healthcare_scheme",
                0.94,
                (
                    re.compile(r"\bhealthcare\b"),
                    re.compile(r"\bhealth care\b"),
                    re.compile(r"\bmedical deduction\b"),
                ),
            ),
            ("pension", 0.95, (re.compile(r"\bpension\b"),)),
            (
                "paye_tax",
                0.95,
                (
                    re.compile(r"\bpaye\b"),
                    re.compile(r"\btax\b"),
                ),
            ),
            (
                "total_deductions",
                0.93,
                (
                    re.compile(r"\btotal deductions\b"),
                    re.compile(r"\bdeductions\b"),
                    re.compile(r"\bdeduction\b"),
                ),
            ),
            (
                "help",
                0.94,
                (
                    re.compile(r"^help$"),
                    re.compile(r"\bwhat can you do\b"),
                    re.compile(r"\bwhat can you help with\b"),
                    re.compile(r"\bwhat questions can i ask\b"),
                    re.compile(r"\bsupported questions\b"),
                    re.compile(r"\bsupported queries\b"),
                    re.compile(r"\bsupported topics\b"),
                ),
            ),
            (
                "payslip_summary",
                0.90,
                (
                    re.compile(r"\bpayslip\b"),
                    re.compile(r"\bsummary\b"),
                    re.compile(r"\bsalary\b"),
                    re.compile(r"\bpayment\b"),
                    re.compile(r"\bpaid\b"),
                ),
            ),
        )

    def classify(self, message: str) -> IntentResult:
        text = self._normalize(message)

        for intent, confidence, patterns in self._intent_patterns:
            if any(pattern.search(text) for pattern in patterns):
                return IntentResult(intent=intent, confidence=confidence)

        return IntentResult(intent="unknown", confidence=0.4)

    def _normalize(self, message: str) -> str:
        return " ".join(message.lower().strip().split())
