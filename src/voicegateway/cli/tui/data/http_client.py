"""HTTP-backed ``MetricsClient`` for Gateway mode."""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx


class HttpClient:
    """Daemon HTTP client implementing :class:`MetricsClient`."""

    def __init__(
        self,
        *,
        url: str,
        token: str | None = None,
        poll_seconds: float = 1.0,
        connect_timeout: float = 5.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = url.rstrip("/")
        self._token = token or None
        self.poll_seconds = poll_seconds
        self.connect_timeout = connect_timeout
        self.is_connected: bool = True
        self._last_error: str | None = None

        if http_client is not None:
            self._client = http_client
            self._owns_client = False
        else:
            timeout = httpx.Timeout(
                connect=connect_timeout,
                read=connect_timeout * 2,
                write=connect_timeout,
                pool=connect_timeout,
            )
            self._client = httpx.AsyncClient(
                base_url=self._url,
                timeout=timeout,
                headers=self._auth_headers(),
            )
            self._owns_client = True

    _CONNECTION_ERRORS: tuple[type[Exception], ...] = (
        httpx.ConnectError,
        httpx.ConnectTimeout,
        httpx.ReadTimeout,
        httpx.RemoteProtocolError,
        httpx.NetworkError,
    )

    def _mark_connected(self) -> None:
        self.is_connected = True
        self._last_error = None

    def _mark_disconnected(self, exc: Exception) -> None:
        self.is_connected = False
        self._last_error = str(exc) or type(exc).__name__

    def _auth_headers(self) -> dict[str, str]:
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    async def aclose(self) -> None:
        """Close the underlying ``httpx.AsyncClient`` if we own it."""
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> HttpClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    # -- Internal request helper ------------------------------------

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Issue an HTTP request and track connection state."""
        try:
            response = await self._client.request(method, path, **kwargs)
        except self._CONNECTION_ERRORS as exc:
            self._mark_disconnected(exc)
            raise
        self._mark_connected()
        return response

    # -- MetricsClient methods --------------------------------------

    async def list_sessions(
        self,
        *,
        limit: int = 100,
        project: str | None = None,
        order_by: str = "started_at_desc",
    ) -> list[dict[str, Any]]:
        """``GET /v1/sessions`` -- list of recent sessions."""
        params: dict[str, Any] = {"limit": limit, "order_by": order_by}
        if project is not None:
            params["project"] = project
        response = await self._request("GET", "/v1/sessions", params=params)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, list) else []

    async def get_session_detail(self, session_id: str) -> dict[str, Any] | None:
        """``GET /v1/sessions/{id}`` -- one session, or ``None`` on 404."""
        response = await self._request("GET", f"/v1/sessions/{session_id}")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else None

    async def list_costs(
        self,
        *,
        period: str = "today",
        project: str | None = None,
        include_pricing_source: bool = False,
    ) -> dict[str, Any]:
        """``GET /v1/costs`` -- total + per-modality breakdown."""
        params: dict[str, Any] = {
            "period": period,
            "per_modality": "true",
            "include_pricing_source": str(include_pricing_source).lower(),
        }
        if project is not None:
            params["project"] = project
        response = await self._request("GET", "/v1/costs", params=params)
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}

    async def list_logs(
        self,
        *,
        limit: int = 100,
        project: str | None = None,
        modality: str | None = None,
    ) -> list[dict[str, Any]]:
        """``GET /v1/logs`` -- recent request rows for the Logs tab."""
        params: dict[str, Any] = {"limit": limit}
        if project is not None:
            params["project"] = project
        if modality is not None:
            params["modality"] = modality
        response = await self._request("GET", "/v1/logs", params=params)
        response.raise_for_status()
        body = response.json()

        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            logs = body.get("logs")
            return logs if isinstance(logs, list) else []
        return []

    async def list_providers(
        self,
        *,
        project: str | None = None,
    ) -> list[dict[str, Any]]:
        """``GET /v1/providers`` -- configured providers + last status."""
        params: dict[str, Any] = {}
        if project is not None:
            params["project"] = project
        response = await self._request("GET", "/v1/providers", params=params)
        response.raise_for_status()
        body = response.json()
        if isinstance(body, list):
            return body
        if isinstance(body, dict):
            providers = body.get("providers")
            return providers if isinstance(providers, list) else []
        return []

    async def test_provider(self, provider_id: str) -> dict[str, Any]:
        """``POST /v1/providers/{id}/test`` -- run a live key test."""
        response = await self._request("POST", f"/v1/providers/{provider_id}/test")
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}


__all__ = ["HttpClient"]
