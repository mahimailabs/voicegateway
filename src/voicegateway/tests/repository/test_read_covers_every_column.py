"""A table's columns and its SELECT are two things that must agree.

The companion to ``test_insert_covers_every_column``, and it exists because
the same drift happened at the other end of the same column.

``revision`` was added to the model, the migration and (after a fix) the
INSERT, so it stored correctly. It was never added to ``_REQUEST_COLUMNS``,
which is the explicit column list all three row readers build their SELECT
from. So the value was written to every row and returned by none of them:
``get_recent_requests`` (the dashboard log view and the MCP request tool),
``get_requests_for_room`` and ``get_requests_in_window`` all silently dropped
it.

That is worse than the INSERT bug in one respect. The INSERT bug wrote NULL,
so a read-back caught it. This one writes correctly and reads back a dict that
simply has no such key, which looks identical to a caller that never asked for
it. The aggregate path worked the whole time -- ``get_cost_by_revision``
groups on the column directly -- so revision totals were right while no
individual row would tell you which revision it belonged to.

The lesson is the same and so is the remedy: two producers of one truth drift
silently, so compare them mechanically instead of hoping a reviewer notices a
name missing from a twenty-four entry tuple.
"""

from __future__ import annotations

from voicegateway.models.request_model import Request
from voicegateway.repository.request_log_repository import _REQUEST_COLUMNS

#: Columns a row reader is not expected to return, and why. This list is the
#: only place a column may be excused, so excusing one is a visible edit in a
#: diff rather than a silent omission in a tuple.
_NOT_READ: dict[str, str] = {}


def test_every_request_column_is_selected_by_the_row_readers() -> None:
    """Nothing in the table may be invisible to the three row readers."""
    table = {column.name for column in Request.__table__.columns}
    selected = set(_REQUEST_COLUMNS)

    missing = sorted(table - selected - set(_NOT_READ))
    assert not missing, (
        f"columns exist on `requests` but no row reader returns them: {missing}. "
        f"Add them to _REQUEST_COLUMNS, or list them in _NOT_READ with the "
        f"reason they are deliberately unreadable. A column that is written "
        f"and never read is indistinguishable, to a caller, from one that was "
        f"never added."
    )


def test_the_reader_does_not_select_columns_the_table_lacks() -> None:
    """The other direction, which fails loudly at query time rather than quietly.

    Included because the two lists drifting apart is the actual fault, and a
    guard that only checks one direction lets half of it through.
    """
    table = {column.name for column in Request.__table__.columns}
    phantom = sorted(set(_REQUEST_COLUMNS) - table)
    assert not phantom, (
        f"_REQUEST_COLUMNS names columns `requests` does not have: {phantom}"
    )


def test_the_excuse_list_only_names_real_columns() -> None:
    """An excuse for a column that no longer exists is an excuse nobody reads."""
    table = {column.name for column in Request.__table__.columns}
    stale = sorted(set(_NOT_READ) - table)
    assert not stale, f"_NOT_READ names columns that no longer exist: {stale}"
