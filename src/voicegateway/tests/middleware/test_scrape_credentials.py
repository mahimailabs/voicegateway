"""A metrics credential must not end up in the log.

A metrics endpoint behind basic auth is configured the obvious way, as
``http://user:secret@host/metrics``, and httpx authenticates from that without
being asked. It also logs the request line at INFO:

    HTTP Request: GET http://user:secret@host/metrics "HTTP/1.1 200 OK"

At a 15-second poll that is four copies of the password per minute, per target,
for as long as the process runs, in a log people paste into issues.

The fix is not to silence that logger. Silencing it would hide the one line an
operator uses to see whether a scrape happened at all, and it would leave the
credential in every other place a URL travels: a warning, an exception, a
dataclass repr. Instead the credential is split out of the URL and carried as an
httpx auth tuple, so the request still authenticates and the URL that gets
logged is the URL without it.

Every credential below is invented for this file.
"""

from __future__ import annotations

import base64
import logging

import httpx
import pytest

from voicegateway.middleware.node_samples_worker_middleware import (
    SOURCE_LIVEKIT_SERVER,
    SOURCE_NODE_EXPORTER,
    TARGETS_ENV_VAR,
    NodeSamplesWorker,
    ScrapeTarget,
    redact_url,
    split_userinfo,
    targets_from_env,
)
from voicegateway.services.storage_service import StorageService

USER = "metrics-reader"
SECRET = "not-a-real-password-9271"
AUTHED_URL = f"http://{USER}:{SECRET}@10.0.0.4:6789/metrics"


@pytest.fixture
async def storage(tmp_path):
    service = StorageService(db_path=str(tmp_path / "creds.db"))
    try:
        yield service
    finally:
        await service.aclose()


def _env(value: str) -> dict[str, str]:
    return {TARGETS_ENV_VAR: value}


# --------------------------------------------------------------------------
# The split itself
# --------------------------------------------------------------------------


def test_the_credential_leaves_the_url(tmp_path) -> None:
    url, auth = split_userinfo(AUTHED_URL)
    assert url == "http://10.0.0.4:6789/metrics"
    assert auth == (USER, SECRET)
    assert SECRET not in url


def test_a_url_without_a_credential_is_untouched() -> None:
    """Non-vacuous: the common case must not acquire an empty auth tuple."""
    url, auth = split_userinfo("http://10.0.0.4:9100/metrics")
    assert url == "http://10.0.0.4:9100/metrics"
    assert auth is None


def test_the_credential_is_percent_decoded() -> None:
    """A password containing @ or / is only expressible encoded in a URL.

    Handing httpx the still-encoded form would send the wrong password and the
    scrape would 401 with nothing saying why.
    """
    url, auth = split_userinfo("http://u:p%40ss%2Fword@host:6789/metrics")
    assert auth == ("u", "p@ss/word")
    assert url == "http://host:6789/metrics"


def test_an_ipv6_host_survives_the_split() -> None:
    """rpartition on "@", not a hostname parse: brackets and case are kept."""
    url, auth = split_userinfo("http://u:pw@[2001:db8::1]:6789/metrics")
    assert url == "http://[2001:db8::1]:6789/metrics"
    assert auth == ("u", "pw")


def test_targets_from_env_carries_the_credential_off_the_url() -> None:
    [target] = targets_from_env(_env(f"livekit-server:sfu-1={AUTHED_URL}"))
    assert target.url == "http://10.0.0.4:6789/metrics"
    assert target.auth == (USER, SECRET)
    assert SECRET not in target.url


def test_a_target_does_not_print_its_own_credential() -> None:
    """A dataclass repr is the other way a secret escapes.

    Any exception, log line or debugger that renders a target would print it.
    """
    [target] = targets_from_env(_env(f"livekit-server:sfu-1={AUTHED_URL}"))
    assert SECRET not in repr(target)
    assert SECRET not in str(target)
    assert "sfu-1" in repr(target)  # and the useful half is still there


# --------------------------------------------------------------------------
# The request still authenticates
# --------------------------------------------------------------------------


async def test_the_scrape_still_sends_the_credential(storage) -> None:
    """Splitting it out must not quietly stop authenticating.

    A silently unauthenticated scrape would 401 and record an http_error row
    forever, which looks like a broken exporter rather than a broken fix.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="livekit_room_total 3\n")

    [target] = targets_from_env(_env(f"livekit-server:sfu-1={AUTHED_URL}"))

    async def provider():
        return [target]

    worker = NodeSamplesWorker(
        storage, target_provider=provider, transport=httpx.MockTransport(handler)
    )
    await worker.tick_now()

    [request] = seen
    header = request.headers["authorization"]
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header.removeprefix("Basic ")).decode()
    assert decoded == f"{USER}:{SECRET}"
    # And it went as a header, not in the line httpx logs.
    assert SECRET not in str(request.url)


# --------------------------------------------------------------------------
# The log, which is the whole point
# --------------------------------------------------------------------------


async def test_the_credential_never_reaches_the_log(storage, caplog) -> None:
    """The defect, pinned end to end across every logger in the process."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="livekit_room_total 3\n")

    [target] = targets_from_env(_env(f"livekit-server:sfu-1={AUTHED_URL}"))

    async def provider():
        return [target]

    worker = NodeSamplesWorker(
        storage, target_provider=provider, transport=httpx.MockTransport(handler)
    )
    with caplog.at_level(logging.DEBUG):
        await worker.tick_now()

    assert caplog.records, "nothing was logged, so this proves nothing"
    for record in caplog.records:
        assert SECRET not in record.getMessage(), record.name
        assert USER not in record.getMessage(), record.name


async def test_the_httpx_line_is_still_emitted(storage, caplog) -> None:
    """Non-vacuous, and the reason this is not fixed by silencing httpx.

    That line is how an operator sees a scrape happened at all. The fix has to
    keep it and change what it says, not delete it.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="livekit_room_total 3\n")

    [target] = targets_from_env(_env(f"livekit-server:sfu-1={AUTHED_URL}"))

    async def provider():
        return [target]

    worker = NodeSamplesWorker(
        storage, target_provider=provider, transport=httpx.MockTransport(handler)
    )
    with caplog.at_level(logging.INFO, logger="httpx"):
        await worker.tick_now()

    httpx_lines = [r.getMessage() for r in caplog.records if r.name == "httpx"]
    assert httpx_lines, "the httpx request line was suppressed rather than cleaned"
    assert any("10.0.0.4:6789/metrics" in line for line in httpx_lines)
    assert not any(SECRET in line for line in httpx_lines)


def test_the_httpx_logger_is_not_muzzled() -> None:
    """Stated directly: no level, filter or disable was applied to it."""
    httpx_logger = logging.getLogger("httpx")
    assert httpx_logger.level in (logging.NOTSET, logging.INFO)
    assert not httpx_logger.disabled
    assert not httpx_logger.filters


# --------------------------------------------------------------------------
# A malformed entry is skipped, and the warning about it is clean too
# --------------------------------------------------------------------------


def test_a_malformed_entry_is_skipped_not_raised(caplog) -> None:
    """A typo must not take down a process that is also serving the dashboard."""
    with caplog.at_level(logging.WARNING):
        targets = targets_from_env(_env(f"garbage-without-an-equals-{AUTHED_URL}"))
    assert targets == []
    assert caplog.records


def test_the_warning_for_a_malformed_entry_hides_the_credential(caplog) -> None:
    """The trap. A skipped entry is where a credential is MOST likely present.

    It was never parsed, so nothing had a chance to take it out, and the
    warning echoes the entry back verbatim.
    """
    with caplog.at_level(logging.WARNING):
        targets_from_env(_env(f"livekit-server-sfu-1{AUTHED_URL}"))
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert SECRET not in logged
    assert "***@" in logged


def test_the_warning_for_an_unknown_source_hides_it_too(caplog) -> None:
    """The other skip path, which parses far enough to look safe and is not."""
    with caplog.at_level(logging.WARNING):
        targets = targets_from_env(_env(f"livekit-sfu:sfu-1={AUTHED_URL}"))
    assert targets == []
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert SECRET not in logged
    assert "livekit-sfu" in logged  # the operator still learns what was wrong


def test_a_good_entry_beside_a_bad_one_still_loads(caplog) -> None:
    """Skipping is per entry: one typo must not empty the whole fleet."""
    with caplog.at_level(logging.WARNING):
        targets = targets_from_env(
            _env(
                f"nonsense,node-exporter:sfu-1=http://10.0.0.4:9100/metrics,"
                f"livekit-server:sfu-1={AUTHED_URL}"
            )
        )
    assert [t.source for t in targets] == [SOURCE_NODE_EXPORTER, SOURCE_LIVEKIT_SERVER]
    assert SECRET not in " ".join(r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------
# Defence in depth: a target built by hand is redacted at the log site
# --------------------------------------------------------------------------


def test_redaction_covers_a_url_that_never_went_through_the_parser() -> None:
    """targets_from_env is not the only way a target is constructed."""
    assert redact_url(AUTHED_URL) == "http://***@10.0.0.4:6789/metrics"
    assert redact_url("http://10.0.0.4:9100/metrics") == "http://10.0.0.4:9100/metrics"
    assert SECRET not in redact_url(f"ignoring {AUTHED_URL}; expected a url")


async def test_an_oversized_body_warning_is_redacted(storage, caplog) -> None:
    """One of the two sites that logs a URL, exercised rather than read."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="livekit_room_total 1\n" * 5_000)

    target = ScrapeTarget(node="sfu-1", url=AUTHED_URL, source=SOURCE_LIVEKIT_SERVER)

    async def provider():
        return [target]

    worker = NodeSamplesWorker(
        storage,
        target_provider=provider,
        max_response_bytes=512,
        transport=httpx.MockTransport(handler),
    )
    with caplog.at_level(logging.WARNING):
        await worker.tick_now()

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("exceeded" in w for w in warnings)
    assert not any(SECRET in w for w in warnings)
