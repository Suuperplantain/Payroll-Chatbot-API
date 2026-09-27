from __future__ import annotations


class AuthService:
    """Placeholder auth service validating employee IDs and optional API tokens."""

    def __init__(self, allowed_employee_ids: set[str], token_to_employee_id: dict[str, str] | None = None) -> None:
        self._allowed_employee_ids = allowed_employee_ids
        self._token_to_employee_id = token_to_employee_id or {}

    def is_authorized(self, employee_id: str) -> bool:
        return employee_id in self._allowed_employee_ids

    def validate_credentials(self, employee_id: str | None = None, token: str | None = None) -> str | None:
        if employee_id and self.is_authorized(employee_id):
            return employee_id

        if token:
            mapped_employee_id = self._token_to_employee_id.get(token)
            if mapped_employee_id and self.is_authorized(mapped_employee_id):
                if employee_id and mapped_employee_id != employee_id:
                    return None
                return mapped_employee_id

        return None
