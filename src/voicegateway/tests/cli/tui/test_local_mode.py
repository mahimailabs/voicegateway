"""Local-mode end-to-end Pilot tests (REQ-VG-TUI-008 verification).

Mounts the full :class:`TUIApp` against a seeded SQLite backing
``LocalClient`` and visits each tab to confirm:

- The ``[Local mode]`` chip is visible in the header.
- Sessions / Costs / Logs / Providers each render their seeded
  data via the LocalClient read path.
- The CounterFooter renders the ``(as of N s ago)`` suffix
  computed from the SQLite file's mtime.
- Write actions surface :class:`LocalModeUnsupportedError`
  visibly: pressing ``t`` on the Providers screen leaves the row's
  status unchanged (no silent state mutation), the LocalClient
  raises the documented exception with the documented feature
  attribute.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Label, Static

from voicegateway.cli.tui import TUIApp
from voicegateway.cli.tui.data.exceptions import LocalModeUnsupportedError
from voicegateway.cli.tui.data.local import LocalClient
from voicegateway.cli.tui.screens.costs import CostsScreen
from voicegateway.cli.tui.screens.logs import LogsScreen
from voicegateway.cli.tui.screens.providers import ProvidersScreen
from voicegateway.cli.tui.screens.sessions import SessionsScreen
from voicegateway.cli.tui.widgets.cost_card import CostCard
from voicegateway.cli.tui.widgets.header import HeaderBar
from voicegateway.cli.tui.widgets.log_tail import LogTail
from voicegateway.cli.tui.widgets.provider_row import ProviderRow
from voicegateway.cli.tui.widgets.session_row import SessionRow
from voicegateway.middleware.cost_tracker import RequestRecord
from voicegateway.storage.sqlite import SQLiteStorage


@pytest.fixture
async def seeded_local_app(tmp_path: Path) -> TUIApp:
    """Full App with 2 sessions + 2 providers seeded into the SQLite
    file the LocalClient reads.
    """
    db = tmp_path / "voicegw.db"
    storage = SQLiteStorage(db)
    for i in range(2):
        await storage.log_request(
            RequestRecord(
                id=str(uuid.uuid4()),
                timestamp=time.time() + i,
                modality="llm",
                model_id="openai/gpt-4o-mini",
                provider="openai",
                project="default",
                input_units=10,
                output_units=5,
                cost_usd=0.001,
                metadata={},
                session_id=f"vg-s{i}",
                status="success",
            )
        )
    await storage.upsert_managed_provider(
        provider_id="openai-prod",
        provider_type="openai",
        api_key="sk-test",
        project="default",
    )
    await storage.upsert_managed_provider(
        provider_id="deepgram-test",
        provider_type="deepgram",
        api_key="dg-test",
        project=None,
    )
    client = LocalClient(db_path=db, storage=storage)
    return TUIApp(client=client, is_local=True)


async def _settle(pilot, ticks: int = 8) -> None:
    for _ in range(ticks):
        await pilot.pause()


# ---------------------------------------------------------------------------
# Header chip + each tab renders seeded data
# ---------------------------------------------------------------------------


async def test_local_mode_chip_visible_in_header(seeded_local_app: TUIApp) -> None:
    async with seeded_local_app.run_test() as pilot:
        await _settle(pilot)
        chip = seeded_local_app.query_one(HeaderBar).query_one("#header-chip", Static)
        assert "[Local mode]" in str(chip.renderable)


async def test_sessions_renders_seeded_rows_in_local_mode(
    seeded_local_app: TUIApp,
) -> None:
    async with seeded_local_app.run_test() as pilot:
        await _settle(pilot)
        # Default tab is Sessions
        screen = seeded_local_app.query_one(SessionsScreen)
        list_view = screen.query_one("#sessions-list", VerticalScroll)
        rows = list(list_view.query(SessionRow))
        assert len(rows) == 2
        assert {r.session_id for r in rows} == {"vg-s0", "vg-s1"}


async def test_costs_renders_card_in_local_mode(
    seeded_local_app: TUIApp,
) -> None:
    async with seeded_local_app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("2")
        await _settle(pilot)
        card = seeded_local_app.query_one(CostsScreen).query_one(CostCard)
        assert "by_modality" in card._costs
        # Header carries the [today] active-range marker by default.
        header = seeded_local_app.query_one(CostsScreen).query_one(
            "#costs-header", Label
        )
        assert "[today]" in str(header.renderable)


async def test_logs_renders_tail_in_local_mode(
    seeded_local_app: TUIApp,
) -> None:
    async with seeded_local_app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("3")
        await _settle(pilot)
        tail = seeded_local_app.query_one(LogsScreen).query_one("#logs-tail", LogTail)
        # Two seeded requests landed in storage; LogTail's de-dup set
        # carries them after the on_mount fetch + first poll tick.
        assert len(tail._all_entries) == 2


async def test_providers_renders_rows_in_local_mode(
    seeded_local_app: TUIApp,
) -> None:
    async with seeded_local_app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("4")
        await _settle(pilot)
        screen = seeded_local_app.query_one(ProvidersScreen)
        rows = list(screen.query(ProviderRow))
        assert len(rows) == 2
        ids = {r.provider_id for r in rows}
        assert ids == {"openai-prod", "deepgram-test"}


# ---------------------------------------------------------------------------
# Write-path contract: LocalClient.test_provider raises with feature
# ---------------------------------------------------------------------------


async def test_local_client_test_provider_raises_unsupported_error() -> None:
    """Pinned at the data-layer surface so the screen-level Pilot
    test (in test_screens.py) inherits a clear contract: write paths
    in Local mode raise the documented exception with the feature
    name. No silent failure.
    """
    client = LocalClient(db_path=Path("/tmp/nonexistent.db"))
    with pytest.raises(LocalModeUnsupportedError) as exc_info:
        await client.test_provider("openai-prod")
    assert exc_info.value.feature == "test_provider"
    assert "test_provider" in str(exc_info.value)


async def test_providers_t_shortcut_surfaces_visible_notification_in_local_mode(
    seeded_local_app: TUIApp,
) -> None:
    """End-to-end visible-surfacing contract for Phase-10 write paths.

    Drives the full stack the user touches in Local mode: TUIApp wired
    to a real LocalClient over a seeded SQLite file, switches to the
    Providers tab, focuses a row, presses ``t``. Expectations:

    - The LocalClient raises :class:`LocalModeUnsupportedError`
      with the documented ``feature='test_provider'`` payload
      (verified above at the data layer).
    - ProvidersScreen catches the exception and routes it through
      :meth:`App.notify` with the warning severity + the
      ``Action requires the daemon`` title.
    - The notification message names ``test_provider`` so the user
      sees the unsupported feature name verbatim -- the locked
      decision-4 contract that write paths surface honestly.
    - The row's ``status`` stays at the pre-press value: no silent
      mutation, no half-committed state.

    Locked decisions covered: 4 (Local mode raises with feature) +
    5 (Local mode write actions are visible, not silent). Plus
    REQ-VG-TUI-008's bottom contract: Local mode is a read-only
    surface and write attempts say so out loud.
    """
    async with seeded_local_app.run_test() as pilot:
        await _settle(pilot)
        await pilot.press("4")
        await _settle(pilot)

        screen = seeded_local_app.query_one(ProvidersScreen)
        rows = list(screen.query(ProviderRow))
        assert rows, "Providers tab must mount the seeded rows"
        target = rows[0]
        status_before = target.status
        target.focus()
        await pilot.pause()
        await pilot.press("t")

        # Worker dispatch + notification routing takes a few ticks.
        for _ in range(20):
            await pilot.pause()

        # No silent mutation: the row stays put.
        assert target.status == status_before

        # Visible surfacing: a warning notification is in flight.
        notifications = list(seeded_local_app._notifications)
        warnings = [
            n
            for n in notifications
            if n.severity == "warning"
            and "daemon" in n.title.lower()
            and "test_provider" in n.message
        ]
        assert warnings, (
            "Local-mode `t` must surface a visible warning naming the "
            "unsupported feature, not silently fail."
        )


# ---------------------------------------------------------------------------
# CounterFooter staleness suffix
# ---------------------------------------------------------------------------


async def test_counter_footer_renders_age_suffix_in_local_mode(
    seeded_local_app: TUIApp,
) -> None:
    """The footer row carries ``(as of N s ago)`` when in Local mode.
    The just-seeded SQLite file mtime is within seconds of now, so
    the suffix lands in the seconds bucket.
    """
    async with seeded_local_app.run_test() as pilot:
        # Wait for the initial CounterFooter refresh to land.
        for _ in range(15):
            await pilot.pause(0.05)
        from voicegateway.cli.tui.widgets.footer import CounterFooter

        footer = seeded_local_app.query_one(CounterFooter)
        text = str(footer.query_one("#counter-text", Static).renderable)
        assert "(as of" in text
