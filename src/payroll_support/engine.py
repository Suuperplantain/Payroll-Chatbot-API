from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IntentResult:
    intent: str
    confidence: float


class RuleBasedNLPEngine:
    """Rule-based classifier for supported payroll queries."""

    def classify(self, message: str) -> IntentResult:
        text = message.lower().strip()

        if any(keyword in text for keyword in ("employee details", "my details", "employee id", "job title", "my name", "who am i")):
            return IntentResult(intent="employee_details", confidence=0.95)

        if "tax code" in text:
            return IntentResult(intent="tax_code", confidence=0.97)

        if any(keyword in text for keyword in ("pay date", "paid on", "when was i paid", "when did i get paid")):
            return IntentResult(intent="pay_date", confidence=0.96)

        if "pay period" in text or ("period" in text and "pay" in text):
            return IntentResult(intent="pay_period", confidence=0.95)

        if any(keyword in text for keyword in ("gross salary", "gross pay")) or text == "gross":
            return IntentResult(intent="gross_salary", confidence=0.95)

        if any(keyword in text for keyword in ("net pay", "take home", "take-home")):
            return IntentResult(intent="net_pay", confidence=0.96)

        if "national insurance" in text or " ni " in f" {text} ":
            return IntentResult(intent="national_insurance", confidence=0.95)

        if "student loan" in text:
            return IntentResult(intent="student_loan", confidence=0.95)

        if "healthcare" in text or "health care" in text:
            return IntentResult(intent="healthcare_scheme", confidence=0.94)

        if "pension" in text:
            return IntentResult(intent="pension", confidence=0.95)

        if "paye" in text or ("tax" in text and "tax code" not in text):
            return IntentResult(intent="paye_tax", confidence=0.95)

        if "deduction" in text:
            return IntentResult(intent="total_deductions", confidence=0.93)

        if any(keyword in text for keyword in ("payslip", "summary", "salary", "payment", "paid")):
            return IntentResult(intent="payslip_summary", confidence=0.9)

        return IntentResult(intent="unknown", confidence=0.4)
