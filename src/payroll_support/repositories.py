from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from zipfile import ZipFile
import sqlite3
import xml.etree.ElementTree as ET

MAIN_NS = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}

REQUIRED_HEADERS = {
    "employee_id",
    "employee_name",
    "job_title",
    "pay_period",
    "pay_date",
    "tax_code",
    "gross_salary",
    "paye_tax",
    "national_insurance",
    "pension",
    "student_loan",
    "healthcare_scheme",
    "net_pay",
}


@dataclass(frozen=True)
class PayrollSnapshot:
    employee_id: str
    employee_name: str
    job_title: str
    pay_period: str
    pay_date: date
    tax_code: str
    gross_salary: float
    paye_tax: float
    national_insurance: float
    pension: float
    student_loan: float
    healthcare_scheme: float
    total_deductions: float
    net_pay: float

    def to_summary_data(self) -> dict[str, object]:
        return {
            "employee_id": self.employee_id,
            "employee_name": self.employee_name,
            "job_title": self.job_title,
            "pay_period": self.pay_period,
            "pay_date": self.pay_date.isoformat(),
            "tax_code": self.tax_code,
            "gross_salary": round(self.gross_salary, 2),
            "paye_tax": round(self.paye_tax, 2),
            "national_insurance": round(self.national_insurance, 2),
            "pension": round(self.pension, 2),
            "student_loan": round(self.student_loan, 2),
            "healthcare_scheme": round(self.healthcare_scheme, 2),
            "total_deductions": round(self.total_deductions, 2),
            "net_pay": round(self.net_pay, 2),
        }


@dataclass
class HRTicket:
    ticket_id: str
    employee_id: str
    message: str
    reason: str
    sent_at: str


@dataclass
class MetricsSummary:
    total_interactions: int
    deflection_rate: float
    average_response_time: float
    error_rate: float
    handoff_rate: float
    offer_rate: float


class KnowledgeRepository(Protocol):
    def search(self, query: str) -> str | None:
        ...


class PayrollRepository(Protocol):
    def get_latest_snapshot(self, employee_id: str) -> PayrollSnapshot | None:
        ...

    def get_supported_employee_ids(self) -> set[str]:
        ...


class TicketRepository(Protocol):
    def create_ticket(self, employee_id: str, message: str, reason: str) -> HRTicket:
        ...


class MetricsRepository(Protocol):
    def record_interaction(self, outcome: str, response_time: float) -> None:
        ...

    def get_summary(self) -> MetricsSummary:
        ...


class InMemoryKnowledgeRepository:
    def search(self, query: str) -> str | None:
        return None


class InMemoryPayrollRepository:
    def __init__(self) -> None:
        self._snapshots = {
            "NTU001": PayrollSnapshot(
                employee_id="NTU001",
                employee_name="Jane Doe",
                job_title="Senior Software Engineer",
                pay_period="01/08/2025 - 31/08/2025",
                pay_date=date(2025, 8, 31),
                tax_code="1257L",
                gross_salary=6000.00,
                paye_tax=1250.00,
                national_insurance=510.00,
                pension=240.00,
                student_loan=240.00,
                healthcare_scheme=25.00,
                total_deductions=2265.00,
                net_pay=3735.00,
            ),
        }

    def get_latest_snapshot(self, employee_id: str) -> PayrollSnapshot | None:
        return self._snapshots.get(employee_id)

    def get_supported_employee_ids(self) -> set[str]:
        return set(self._snapshots)


class SpreadsheetPayrollRepository:
    def __init__(self, workbook_path: str | Path) -> None:
        self._workbook_path = Path(workbook_path)
        self._cached_by_employee: dict[str, PayrollSnapshot] | None = None
        self._cached_mtime_ns: int | None = None

    def get_latest_snapshot(self, employee_id: str) -> PayrollSnapshot | None:
        return self._load_snapshots().get(employee_id)

    def get_supported_employee_ids(self) -> set[str]:
        return set(self._load_snapshots())

    def _load_snapshots(self) -> dict[str, PayrollSnapshot]:
        mtime_ns = self._workbook_path.stat().st_mtime_ns
        if self._cached_by_employee is None or self._cached_mtime_ns != mtime_ns:
            self._cached_by_employee = self._read_workbook()
            self._cached_mtime_ns = mtime_ns
        return self._cached_by_employee

    def _read_workbook(self) -> dict[str, PayrollSnapshot]:
        if not self._workbook_path.exists():
            raise FileNotFoundError(f"Workbook not found: {self._workbook_path}")

        with ZipFile(self._workbook_path) as archive:
            shared_strings = self._read_shared_strings(archive)
            sheet_path = self._resolve_sheet_path(archive)
            rows = self._read_sheet_rows(archive, sheet_path, shared_strings)

        if not rows:
            raise ValueError("Workbook must contain a header row and at least one payslip row")

        header_row = rows[0]
        headers_by_index = {
            index: self._normalize_header(value)
            for index, value in header_row.items()
            if str(value).strip()
        }

        missing_headers = REQUIRED_HEADERS - set(headers_by_index.values())
        if missing_headers:
            missing = ", ".join(sorted(missing_headers))
            raise ValueError(f"Workbook is missing required columns: {missing}")

        latest_by_employee: dict[str, PayrollSnapshot] = {}
        for row in rows[1:]:
            record = {
                header: str(row.get(index, "")).strip()
                for index, header in headers_by_index.items()
            }
            if not any(record.values()):
                continue
            snapshot = self._build_snapshot(record)
            existing = latest_by_employee.get(snapshot.employee_id)
            if existing is None or snapshot.pay_date >= existing.pay_date:
                latest_by_employee[snapshot.employee_id] = snapshot

        if not latest_by_employee:
            raise ValueError("Workbook does not contain any valid payslip rows")

        return latest_by_employee

    def _build_snapshot(self, record: dict[str, str]) -> PayrollSnapshot:
        paye_tax = self._parse_amount(record["paye_tax"])
        national_insurance = self._parse_amount(record["national_insurance"])
        pension = self._parse_amount(record["pension"])
        student_loan = self._parse_amount(record["student_loan"])
        healthcare_scheme = self._parse_amount(record["healthcare_scheme"])

        total_text = record.get("total_deductions", "")
        total_deductions = (
            self._parse_amount(total_text)
            if total_text.strip()
            else round(
                paye_tax + national_insurance + pension + student_loan + healthcare_scheme,
                2,
            )
        )

        return PayrollSnapshot(
            employee_id=self._require(record, "employee_id"),
            employee_name=self._require(record, "employee_name"),
            job_title=self._require(record, "job_title"),
            pay_period=self._require(record, "pay_period"),
            pay_date=self._parse_date(self._require(record, "pay_date")),
            tax_code=self._require(record, "tax_code"),
            gross_salary=self._parse_amount(record["gross_salary"]),
            paye_tax=paye_tax,
            national_insurance=national_insurance,
            pension=pension,
            student_loan=student_loan,
            healthcare_scheme=healthcare_scheme,
            total_deductions=round(total_deductions, 2),
            net_pay=self._parse_amount(record["net_pay"]),
        )

    def _resolve_sheet_path(self, archive: ZipFile) -> str:
        workbook_root = ET.fromstring(archive.read("xl/workbook.xml"))
        rels_root = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))

        first_sheet = workbook_root.find("main:sheets/main:sheet", MAIN_NS)
        if first_sheet is None:
            raise ValueError("No worksheets found in workbook")

        sheet_rel_id = first_sheet.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        if not sheet_rel_id:
            raise ValueError("Worksheet relationship id is missing")

        for rel in rels_root.findall("rel:Relationship", REL_NS):
            if rel.attrib.get("Id") == sheet_rel_id:
                target = rel.attrib["Target"].lstrip("/")
                return target if target.startswith("xl/") else f"xl/{target}"

        raise ValueError(f"Worksheet path not found for relationship {sheet_rel_id}")

    def _read_shared_strings(self, archive: ZipFile) -> list[str]:
        try:
            shared_strings_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
        except KeyError:
            return []

        values: list[str] = []
        for item in shared_strings_root.findall("main:si", MAIN_NS):
            texts = [node.text or "" for node in item.findall(".//main:t", MAIN_NS)]
            values.append("".join(texts))
        return values

    def _read_sheet_rows(
        self,
        archive: ZipFile,
        sheet_path: str,
        shared_strings: list[str],
    ) -> list[dict[int, str]]:
        root = ET.fromstring(archive.read(sheet_path))
        rows: list[dict[int, str]] = []

        for row in root.findall(".//main:sheetData/main:row", MAIN_NS):
            row_values: dict[int, str] = {}
            for cell in row.findall("main:c", MAIN_NS):
                reference = cell.attrib.get("r")
                if not reference:
                    continue
                column_index = self._column_index(reference)
                row_values[column_index] = self._read_cell_value(cell, shared_strings).strip()
            if row_values:
                rows.append(row_values)

        return rows

    def _read_cell_value(self, cell: ET.Element, shared_strings: list[str]) -> str:
        cell_type = cell.attrib.get("t")
        if cell_type == "inlineStr":
            return "".join(node.text or "" for node in cell.findall(".//main:t", MAIN_NS))
        if cell_type == "s":
            value_node = cell.find("main:v", MAIN_NS)
            if value_node is None or value_node.text is None:
                return ""
            return shared_strings[int(value_node.text)]

        value_node = cell.find("main:v", MAIN_NS)
        return value_node.text if value_node is not None and value_node.text is not None else ""

    def _normalize_header(self, value: str) -> str:
        return (
            value.strip()
            .lower()
            .replace("(", "")
            .replace(")", "")
            .replace("/", "_")
            .replace("-", "_")
            .replace(" ", "_")
        )

    def _require(self, record: dict[str, str], key: str) -> str:
        value = record.get(key, "").strip()
        if not value:
            raise ValueError(f"Missing value for {key}")
        return value

    def _parse_amount(self, value: str) -> float:
        cleaned = (
            value.strip()
            .replace(",", "")
            .replace("GBP", "")
            .replace("gbp", "")
            .replace("£", "")
        )
        if not cleaned:
            return 0.0
        return round(float(cleaned), 2)

    def _parse_date(self, value: str) -> date:
        for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue

        try:
            serial = float(value)
        except ValueError as exc:
            raise ValueError(f"Unsupported pay_date value: {value}") from exc

        return date(1899, 12, 30) + timedelta(days=int(serial))

    def _column_index(self, reference: str) -> int:
        letters = "".join(char for char in reference if char.isalpha()).upper()
        total = 0
        for char in letters:
            total = (total * 26) + (ord(char) - ord("A") + 1)
        return total - 1


class SQLiteHRRequestRepository:
    def __init__(self, database_path: str | Path) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()

    def create_ticket(self, employee_id: str, message: str, reason: str) -> HRTicket:
        sent_at = datetime.now(UTC).isoformat()
        connection = sqlite3.connect(str(self._database_path))
        try:
            cursor = connection.execute(
                "INSERT INTO hr_requests (employee_id, hr_query, sent_at) VALUES (?, ?, ?)",
                (employee_id, message, sent_at),
            )
            connection.commit()
            request_id = cursor.lastrowid
        finally:
            connection.close()

        return HRTicket(
            ticket_id=f"HR-{request_id:04d}",
            employee_id=employee_id,
            message=message,
            reason=reason,
            sent_at=sent_at,
        )

    def list_requests(self) -> list[tuple[str, str, str]]:
        connection = sqlite3.connect(str(self._database_path))
        try:
            rows = connection.execute(
                "SELECT employee_id, hr_query, sent_at FROM hr_requests ORDER BY rowid"
            ).fetchall()
        finally:
            connection.close()
        return [
            (str(employee_id), str(hr_query), str(sent_at))
            for employee_id, hr_query, sent_at in rows
        ]

    def clear_requests(self) -> None:
        connection = sqlite3.connect(str(self._database_path))
        try:
            connection.execute("DELETE FROM hr_requests")
            connection.commit()
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        connection = sqlite3.connect(str(self._database_path))
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS hr_requests (
                    employee_id TEXT NOT NULL,
                    hr_query TEXT NOT NULL,
                    sent_at TEXT NOT NULL
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(hr_requests)").fetchall()}
            if "sent_at" not in columns:
                connection.execute("ALTER TABLE hr_requests ADD COLUMN sent_at TEXT")
                connection.execute(
                    "UPDATE hr_requests SET sent_at = ? WHERE sent_at IS NULL OR sent_at = ''",
                    (datetime.now(UTC).isoformat(),),
                )
            connection.commit()
        finally:
            connection.close()


class InMemoryTicketRepository:
    def __init__(self) -> None:
        self._counter = 1
        self._tickets: list[HRTicket] = []

    def create_ticket(self, employee_id: str, message: str, reason: str) -> HRTicket:
        ticket = HRTicket(
            ticket_id=f"HR-{self._counter:04d}",
            employee_id=employee_id,
            message=message,
            reason=reason,
            sent_at=datetime.now(UTC).isoformat(),
        )
        self._counter += 1
        self._tickets.append(ticket)
        return ticket


class InMemoryMetricsRepository:
    def __init__(self) -> None:
        self._events: list[dict[str, float | str]] = []

    def record_interaction(self, outcome: str, response_time: float) -> None:
        self._events.append({"outcome": outcome, "response_time": max(response_time, 0.0)})

    def get_summary(self) -> MetricsSummary:
        total = len(self._events)
        if total == 0:
            return MetricsSummary(
                total_interactions=0,
                deflection_rate=0.0,
                average_response_time=0.0,
                error_rate=0.0,
                handoff_rate=0.0,
                offer_rate=0.0,
            )

        automated = sum(1 for event in self._events if event["outcome"] == "automated")
        handoff = sum(1 for event in self._events if event["outcome"] == "handoff")
        error = sum(1 for event in self._events if event["outcome"] == "error")
        offer = sum(1 for event in self._events if event["outcome"] == "offer")
        average_response = sum(float(event["response_time"]) for event in self._events) / total

        return MetricsSummary(
            total_interactions=total,
            deflection_rate=automated / total,
            average_response_time=average_response,
            error_rate=error / total,
            handoff_rate=handoff / total,
            offer_rate=offer / total,
        )
