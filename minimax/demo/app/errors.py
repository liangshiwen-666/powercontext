"""Error envelope shared by every endpoint (REQ-ORD-005)."""

from __future__ import annotations


class ApiError(Exception):
    """Raised by the service layer; rendered by the FastAPI exception handler."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field = field

    def to_payload(self) -> dict[str, object]:
        error: dict[str, object] = {"code": self.code, "message": self.message}
        if self.field is not None:
            error["field"] = self.field
        return {"error": error}
