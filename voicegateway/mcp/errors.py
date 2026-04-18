"""MCP-specific error types mapped from gateway errors.

These errors carry structured payloads (code, message, details) that get
serialised into MCP tool responses so agents can reason about failures.
"""

from __future__ import annotations

from typing import Any


class MCPToolError(Exception):
    """Base error for MCP tool failures, with agent-friendly messages."""

    error_code: str = "UNKNOWN_ERROR"

    def __init__(
        self,
        message: str,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        self.message = message
        if error_code is not None:
            self.error_code = error_code
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.error_code,
                "message": self.message,
                "details": self.details,
            }
        }


class ProviderNotFoundError(MCPToolError):
    error_code = "PROVIDER_NOT_FOUND"


class ProviderAlreadyExistsError(MCPToolError):
    error_code = "PROVIDER_ALREADY_EXISTS"


class ModelNotFoundError(MCPToolError):
    error_code = "MODEL_NOT_FOUND"


class ModelAlreadyExistsError(MCPToolError):
    error_code = "MODEL_ALREADY_EXISTS"


class ProjectNotFoundError(MCPToolError):
    error_code = "PROJECT_NOT_FOUND"


class ProjectAlreadyExistsError(MCPToolError):
    error_code = "PROJECT_ALREADY_EXISTS"


class ConfirmationRequiredError(MCPToolError):
    """Raised when a destructive op is called without confirm=True."""

    error_code = "CONFIRMATION_REQUIRED"


class ReadOnlyResourceError(MCPToolError):
    """Raised when trying to delete a YAML-defined resource."""

    error_code = "READ_ONLY_RESOURCE"


class BudgetExceededError(MCPToolError):
    error_code = "BUDGET_EXCEEDED"


class ValidationError(MCPToolError):
    error_code = "VALIDATION_ERROR"


class ProviderTestFailedError(MCPToolError):
    error_code = "PROVIDER_TEST_FAILED"
