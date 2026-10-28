"""attach(session): map AgentSession events onto the v1 batch (sdk/contract/v1.schema.json).

LiveKit is imported inside attach(), never at module level, so ``import voicegateway``
loads no LiveKit module.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
from collections.abc import Callable, Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from ._delivery import Delivery, rfc3339

if TYPE_CHECKING:
    from livekit.agents import AgentSession

logger = logging.getLogger("voicegateway")

_FIELD_KEY = re.compile(r"^[a-z0-9_.-]{1,64}$")
_FIELD_VALUE_MAX = 256
_FIELDS_PER_EVENT = 32
_MAX_MODELS = 64
_TOKEN_TTL = timedelta(minutes=10)

Handler = Callable[[Any], None]


class Attachment:
    """What attach() returns. A no-op when VoiceGateway is not configured."""

    def __init__(self, delivery: Delivery | None = None) -> None:
        self._delivery = delivery
        self._session: Any = None
        self._handlers: list[tuple[str, Handler]] = []
        self._close_task: asyncio.Task[None] | None = None
        self._dropped_fields = 0

    @property
    def enabled(self) -> bool:
        """False when attach() failed open and this attachment sends nothing."""
        return self._delivery is not None

    @property
    def dropped(self) -> int:
        """Events dropped: queue full, endpoint refused or unreachable, or close timed out."""
        return self._delivery.dropped if self._delivery is not None else 0

    @property
    def dropped_fields(self) -> int:
        """Fields dropped for an invalid key or value; they are never sent."""
        return self._dropped_fields

    def set_field(self, key: str, value: str) -> None:
        """Set a custom field on this call. An invalid key or value is dropped and counted."""
        try:
            self._send_fields({key: value})
        except Exception:
            logger.debug("voicegateway: set_field failed", exc_info=True)

    async def close(self) -> None:
        """Stop listening, flush what is queued and stop, within 2 s. Safe to call twice."""
        try:
            if self._close_task is None:
                self._close_task = asyncio.get_running_loop().create_task(self._close())
            await asyncio.shield(self._close_task)
        except Exception:
            logger.debug("voicegateway: close failed", exc_info=True)

    async def _close(self) -> None:
        for event, handler in self._handlers:
            try:
                self._session.off(event, handler)
            except Exception:
                logger.debug("voicegateway: could not unsubscribe %s", event, exc_info=True)
        self._handlers = []
        if self._delivery is not None:
            await self._delivery.aclose()

    def _send_fields(self, fields: Mapping[str, Any], at: str | None = None) -> None:
        if self._delivery is None:
            return
        valid: dict[str, str] = {}
        for key, value in fields.items():
            if (
                isinstance(key, str)
                and _FIELD_KEY.match(key)
                and isinstance(value, str)
                and len(value) <= _FIELD_VALUE_MAX
            ):
                valid[key] = value
            else:
                self._dropped_fields += 1
        items = list(valid.items())
        for i in range(0, len(items), _FIELDS_PER_EVENT):
            chunk = dict(items[i : i + _FIELDS_PER_EVENT])
            self._delivery.put("fields", at or rfc3339(), {"fields": chunk})


def attach(
    session: AgentSession[Any],
    *,
    url: str | None = None,
    fields: Mapping[str, str] | None = None,
) -> Attachment:
    """Send this session's events to VoiceGateway.

    ``url`` defaults to ``VOICEGATEWAY_URL`` (``https://<host>/ingest/<org>/<project>``);
    the JWT is signed with ``LIVEKIT_API_KEY`` and ``LIVEKIT_API_SECRET``. Call it in the
    job entrypoint, before ``session.start``. When something is missing it logs one
    warning and returns an attachment that does nothing; it never raises.
    """
    try:
        return _attach(session, url, fields)
    except Exception:
        logger.warning("voicegateway: attach failed; the agent runs without it", exc_info=True)
        return Attachment()


def _attach(
    session: AgentSession[Any], url: str | None, fields: Mapping[str, str] | None
) -> Attachment:
    url = url or os.environ.get("VOICEGATEWAY_URL")
    key = os.environ.get("LIVEKIT_API_KEY")
    secret = os.environ.get("LIVEKIT_API_SECRET")
    missing = [
        name
        for name, value in (
            ("VOICEGATEWAY_URL", url),
            ("LIVEKIT_API_KEY", key),
            ("LIVEKIT_API_SECRET", secret),
        )
        if not value
    ]
    if missing:
        logger.warning("voicegateway: %s not set; not attached", ", ".join(missing))
        return Attachment()
    assert url is not None and key is not None and secret is not None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning("voicegateway: attach() needs a running event loop; not attached")
        return Attachment()

    from livekit import api
    from livekit.agents import __version__ as framework_version
    from livekit.agents import get_job_context

    try:
        ctx: Any = get_job_context()
    except RuntimeError:
        ctx = None
    job = getattr(ctx, "job", None)
    room = getattr(job, "room", None)
    call = {
        "jobId": _str_or_none(getattr(job, "id", None)),
        "roomName": _str_or_none(getattr(room, "name", None)),
        "roomSid": _str_or_none(getattr(room, "sid", None)),
    }
    if call["jobId"] is None and call["roomSid"] is None:
        logger.warning("voicegateway: no job context (job id or room SID); not attached")
        return Attachment()

    def token() -> str:
        return (
            api.AccessToken(key, secret)
            .with_observability_grants(api.ObservabilityGrants(write=True))
            .with_ttl(_TOKEN_TTL)
            .to_jwt()
        )

    delivery = Delivery(url=url, token=token, call=call, framework_version=framework_version)
    attachment = Attachment(delivery)
    attachment._session = session
    put = delivery.put

    def on_user_state(ev: Any) -> None:
        put("state", rfc3339(ev.created_at), {"actor": "user", "state": _clip(ev.new_state, 32)})

    def on_agent_state(ev: Any) -> None:
        put("state", rfc3339(ev.created_at), {"actor": "agent", "state": _clip(ev.new_state, 32)})

    def on_item(ev: Any) -> None:
        item = ev.item
        if getattr(item, "type", None) != "message":
            return
        text = item.text_content
        put(
            "message",
            rfc3339(ev.created_at),
            {
                "role": _clip(str(item.role), 32),
                "text": None if text is None else _clip(text, 32768),
                "interrupted": _bool_or_none(getattr(item, "interrupted", None)),
                # ChatMessage.metrics, LiveKit's per-message report (MetricsReport).
                "metrics": _json_object(getattr(item, "metrics", None)),
            },
        )

    def on_usage(ev: Any) -> None:
        models = [
            _json_object(m.model_dump(mode="json")) or {}
            for m in ev.usage.model_usage[:_MAX_MODELS]
        ]
        put("usage", rfc3339(ev.created_at), {"models": models})

    def on_tools(ev: Any) -> None:
        at = rfc3339(ev.created_at)
        for fn_call, output in zip(ev.function_calls, ev.function_call_outputs, strict=False):
            error = None
            if output is not None and output.is_error:
                error = _clip(str(output.output), 1024)
            put("tool", at, {"name": _clip(str(fn_call.name), 128), "error": error})

    def on_error(ev: Any) -> None:
        err = ev.error
        put(
            "error",
            rfc3339(ev.created_at),
            {
                "source": _clip(type(ev.source).__name__, 64),
                "message": _clip(str(getattr(err, "error", err)), 1024),
                "recoverable": _bool_or_none(getattr(err, "recoverable", None)),
            },
        )

    def on_close(ev: Any) -> None:
        reason = getattr(ev.reason, "value", ev.reason)
        put("close", rfc3339(ev.created_at), {"reason": _clip_or_none(reason, 64)})
        if attachment._close_task is None:
            attachment._close_task = loop.create_task(attachment._close())

    handlers: dict[str, Handler] = {
        "user_state_changed": on_user_state,
        "agent_state_changed": on_agent_state,
        "conversation_item_added": on_item,
        "session_usage_updated": on_usage,
        "function_tools_executed": on_tools,
        "error": on_error,
        "close": on_close,
    }
    # Typed loosely: the event names and payloads vary across livekit-agents 1.x.
    emitter: Any = session
    for event, handler in handlers.items():
        guarded = _guard(event, handler)
        emitter.on(event, guarded)
        attachment._handlers.append((event, guarded))

    if fields:
        attachment._send_fields(fields)
    delivery.start()
    if ctx is not None:
        ctx.add_shutdown_callback(attachment.close)
    return attachment


def _guard(event: str, handler: Handler) -> Handler:
    @functools.wraps(handler)
    def guarded(ev: Any) -> None:
        try:
            handler(ev)
        except Exception:
            logger.debug("voicegateway: could not map %s", event, exc_info=True)

    return guarded


def _clip(value: Any, limit: int) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit]


def _clip_or_none(value: Any, limit: int) -> str | None:
    return None if value is None else _clip(value, limit)


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _json_object(value: Any) -> dict[str, Any] | None:
    """A mapping's JSON-serialisable entries as a dict; None when nothing is left."""
    if not isinstance(value, Mapping):
        return None
    out: dict[str, Any] = {}
    for key, item in value.items():
        try:
            json.dumps(item, allow_nan=False)
        except (TypeError, ValueError):
            continue
        out[str(key)] = item
    return out or None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None
