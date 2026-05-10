"""HTTP-backed ``MetricsClient`` for v0.1.1 Gateway mode.

Wraps an :class:`httpx.AsyncClient` against the daemon's ``/v1/*``
endpoints. The connection lifetime is aligned with the TUIApp's
lifetime via :meth:`HttpClient.aclose` (also exposed via the
``async with`` context-manager protocol) so the app's
``on_unmount`` handler can release resources cleanly.

Polling cadence (seconds between refreshes) and connection timeout
are constructor arguments. ``poll_seconds`` defaults to 1.0 (the
v0.1.1 Gateway-mode value from design.md decision 1) but is
overridable via the Typer ``--poll`` flag. ``connect_timeout``
defaults to 5.0 so disconnect paths surface promptly instead of
hanging the TUI thread on a dead daemon.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

import httpx


class HttpClient:
    """Daemon HTTP client implementing :class:`MetricsClient`.

    Constructor arguments are keyword-only so call sites read clearly
    at the launch point. ``http_client`` is an injection seam for
    tests: pass a pre-built :class:`httpx.AsyncClient` (typically
    backed by ``httpx.MockTransport``) to drive the methods without
    a live daemon.

    Attributes:
        poll_seconds: refresh cadence in seconds for screens that
            poll. The Sessions / Logs screens read this when they
            register their refresh timers.
        connect_timeout: total budget for connect + read on every
            request. Surfaces ``httpx.ConnectError`` /
            ``httpx.ReadTimeout`` upward when the daemon is
            unreachable so the reconnection-indicator path can
            light up.
    """

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
        # Treat the empty string the same as no token so an unset
        # ``--token`` on the Typer command does not produce a
        # ``Bearer `` header that the daemon rejects on protected
        # endpoints with a confusing 401.
        self._token = token or None
        self.poll_seconds = poll_seconds
        self.connect_timeout = connect_timeout
        # Connection-health flag the CounterFooter reads to render
        # the ``Reconnecting...`` indicator. Default True (optimistic);
        # the first failed request flips it to False, the next
        # successful round-trip flips it back. 4xx/5xx responses are
        # NOT connection errors -- the daemon is up, the request was
        # rejected -- so they leave the flag alone.
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

    # -- Connection-health tracking ---------------------------------

    # Errors that signal "the daemon is unreachable" (vs. "the
    # daemon answered with a bad status code"). The first set
    # flips ``is_connected`` to False; HTTPStatusError + JSON
    # decode errors propagate without touching the flag.
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

    # -- lifecycle ---------------------------------------------------

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
        """Issue an HTTP request and track connection state.

        Connection-class errors (``ConnectError`` / ``ReadTimeout`` /
        ``RemoteProtocolError`` / ``NetworkError``) flip
        ``is_connected`` to False before re-raising; the next
        successful round-trip flips it back. ``HTTPStatusError`` and
        JSON-decode failures propagate without touching the flag --
        they mean "the daemon is up, the request was rejected", not
        a connection problem.
        """
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
        """``GET /v1/costs`` -- total + per-modality breakdown.

        Always requests ``per_modality=true`` so the Costs tab can
        render the per-modality breakdown without a second call.
        """
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
        # ``/v1/logs`` historically returns a list; tolerate the
        # wrapped ``{"logs": [...]}`` shape some dashboards use too
        # so the screen does not need a parse fork.
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
        """``POST /v1/providers/{id}/test`` -- run a live key test.

        Write op: the daemon endpoint requires the bearer token from
        ``voicegw.yaml``'s ``auth.api_keys`` block. A 401 surfaces as
        ``httpx.HTTPStatusError`` so the Providers screen can render
        a clear ``token required`` message.
        """
        response = await self._request("POST", f"/v1/providers/{provider_id}/test")
        response.raise_for_status()
        body = response.json()
        return body if isinstance(body, dict) else {}


__all__ = ["HttpClient"]
