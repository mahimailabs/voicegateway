"""The aiosqlite warning filter must stay narrow.

``pyproject.toml`` silences one specific teardown race: an aiosqlite connection
worker thread raising "Event loop is closed" after pytest-asyncio closed the loop
its future was bound to. That warning is unactionable, and pytest blames whichever
test happened to be running rather than the one that leaked the connection.

The danger of silencing it is that the noise is shaped exactly like a real
unhandled thread exception, so a pattern one notch too broad would hide genuine
crashes in background threads for the whole suite, permanently and silently.
Nothing else in the repo would notice: a hidden warning produces no output.

So this pins the pattern by behaviour rather than by string equality. Broadening
it to ``ignore::pytest.PytestUnhandledThreadExceptionWarning``, or dropping either
half of the conjunction, fails here.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[3] / "pyproject.toml"

# A real message, trimmed. Both halves matter: the thread name identifies
# aiosqlite's worker, and the error identifies the loop-teardown race.
_REAL = (
    "Exception in thread Thread-935 (_connection_worker_thread)\n"
    "\n"
    "Traceback (most recent call last):\n"
    '  File "aiosqlite/core.py", line 66, in _connection_worker_thread\n'
    "    future.get_loop().call_soon_threadsafe(set_result, future, result)\n"
    "RuntimeError: Event loop is closed\n"
)

# Same thread, a different failure. A locked database is a real defect and must
# not be swallowed just because it surfaced on the aiosqlite thread.
_OTHER_ERROR = (
    "Exception in thread Thread-12 (_connection_worker_thread)\n"
    "\n"
    "Traceback (most recent call last):\n"
    "sqlite3.OperationalError: database is locked\n"
)

# Same error, a different thread. A worker of ours dying on a closed loop is a
# bug in our shutdown ordering, not aiosqlite's threading model.
_OTHER_THREAD = (
    "Exception in thread Thread-7 (_node_samples_worker)\n"
    "\n"
    "Traceback (most recent call last):\n"
    "RuntimeError: Event loop is closed\n"
)


def _aiosqlite_filter_pattern() -> str:
    """The message regex from the one configured ignore entry."""
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    entries = config["tool"]["pytest"]["ini_options"]["filterwarnings"]

    matching = [e for e in entries if "_connection_worker_thread" in e]
    assert len(matching) == 1, (
        f"expected exactly one aiosqlite filter, found {len(matching)}: {matching}"
    )

    # action:message:category:module:lineno
    action, message, category = matching[0].split(":")[:3]
    assert action == "ignore"
    assert category == "pytest.PytestUnhandledThreadExceptionWarning", (
        "the filter must be scoped to unhandled thread exceptions, not to every "
        f"warning class; got {category!r}"
    )
    return message


def test_no_blanket_thread_exception_filter_exists() -> None:
    """A catch-all would hide every background-thread crash in the suite."""
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    entries = config["tool"]["pytest"]["ini_options"]["filterwarnings"]
    for entry in entries:
        parts = entry.split(":")
        message = parts[1] if len(parts) > 1 else ""
        assert message.strip(), (
            f"{entry!r} ignores a whole warning category with no message filter"
        )


def test_it_matches_the_aiosqlite_teardown_race() -> None:
    """The warning it exists to silence."""
    # re.match, not re.search: Python anchors warning filters at the start.
    assert re.match(_aiosqlite_filter_pattern(), _REAL) is not None


def test_it_does_not_hide_other_failures_on_the_same_thread() -> None:
    assert re.match(_aiosqlite_filter_pattern(), _OTHER_ERROR) is None


def test_it_does_not_hide_a_closed_loop_on_one_of_our_own_threads() -> None:
    assert re.match(_aiosqlite_filter_pattern(), _OTHER_THREAD) is None
