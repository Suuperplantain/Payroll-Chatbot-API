from __future__ import annotations

from collections.abc import Callable

class AuthService:
    """Validates employee IDs and optional demo tokens against the latest payroll data."""

    def __init__(
        self,
        allowed_employee_ids: set[str] | Callable[[], set[str]],
        token_to_employee_id: dict[str, str] | None = None,
        token_prefix: str = "token-",
    ) -> None:
        self._allowed_employee_id_source = allowed_employee_ids
        self._token_prefix = token_prefix
        self._token_to_employee_id = {
            self._normalize_token_value(token): self._normalize_employee_id_value(employee_id)
            for token, employee_id in (token_to_employee_id or {}).items()
            if self._normalize_token_value(token) and self._normalize_employee_id_value(employee_id)
        }

    def is_authorized(self, employee_id: str) -> bool:
        normalized_employee_id = self._normalize_employee_id(employee_id)
        if normalized_employee_id is None:
            return False
        return normalized_employee_id in self._get_allowed_employee_ids()

    def validate_credentials(self, employee_id: str | None = None, token: str | None = None) -> str | None:
        normalized_employee_id = self._normalize_employee_id(employee_id)
        normalized_token = self._normalize_token(token)

        if normalized_employee_id and self.is_authorized(normalized_employee_id):
            return normalized_employee_id

        if normalized_token:
            mapped_employee_id = self._resolve_token_employee_id(normalized_token)
            if mapped_employee_id and self.is_authorized(mapped_employee_id):
                if normalized_employee_id and mapped_employee_id != normalized_employee_id:
                    return None
                return mapped_employee_id

        return None

    def _get_allowed_employee_ids(self) -> set[str]:
        employee_ids = (
            self._allowed_employee_id_source()
            if callable(self._allowed_employee_id_source)
            else self._allowed_employee_id_source
        )
        return {
            normalized_employee_id
            for employee_id in employee_ids
            if (normalized_employee_id := self._normalize_employee_id_value(employee_id))
        }

    def _resolve_token_employee_id(self, token: str) -> str | None:
        mapped_employee_id = self._token_to_employee_id.get(token)
        if mapped_employee_id:
            return mapped_employee_id

        if token.startswith(self._token_prefix.casefold()):
            candidate_employee_id = self._normalize_employee_id_value(token[len(self._token_prefix) :])
            return candidate_employee_id or None

        return None

    def _normalize_employee_id(self, employee_id: str | None) -> str | None:
        if employee_id is None:
            return None
        return self._normalize_employee_id_value(employee_id)

    def _normalize_token(self, token: str | None) -> str | None:
        if token is None:
            return None
        normalized_token = self._normalize_token_value(token)
        return normalized_token or None

    def _normalize_employee_id_value(self, employee_id: str) -> str:
        return str(employee_id).strip().upper()

    def _normalize_token_value(self, token: str) -> str:
        return str(token).strip().casefold()
