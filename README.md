# Payroll Pilot Assistant

A small Python API demo for answering common questions about a payslip. It reads payroll fields from an Excel workbook, checks the employee ID, routes supported questions to a rule-based handler, and asks before recording an unsupported query for HR.

## Run locally

Requires Python 3.10 or newer. The API and tests use only the Python standard library.

```bash
python app.py
```

The server listens on `http://localhost:8000`.

Run the tests from the repository root:

```bash
python -m unittest discover -s tests -v
```

## API

- `GET /api/health` — health status
- `GET /api/metrics` — interaction totals and rates
- `POST /api/chat` — answer a supported payslip question or offer an HR handoff

The chat endpoint accepts an `employee_id` and `message`. For a local demonstration, it also accepts `token-<employee_id>` as a token. This is a simple authentication stub, not production authentication.

## Project layout

```text
app.py                         HTTP API entry point
src/payroll_support/           Chat service, intent engine, auth, and repositories
tests/                         Service and HTTP API tests
payslip.xlsx                   Workbook used by the local demo
```

The application creates `hr_requests.db` locally when an HR handoff is confirmed; the database is ignored by Git. Keep real employee or payroll data out of this repository.

## Current scope

Supported queries cover payslip summaries, employee details, tax code, pay date and period, gross and net pay, and common deductions. Unsupported questions are offered for HR follow-up, and a request is saved only after the user confirms.
