"""Reconciliation tooling for verifying VG-recorded costs against provider invoices.

v0.1.2 split the original ``voicegateway/reconcile.py`` into this
subpackage so the top-level ``voicegateway/`` tree contains only
subpackages plus ``__init__.py`` and ``_version.py``
(REQ-VG-POLISH-004). The implementation lives in
``voicegateway/reconcile/core.py``; this ``__init__`` re-exports the
public surface so the v0.1.x callers continue to work:

* ``from voicegateway import reconcile`` followed by attribute access
  (e.g. ``reconcile.parse_provider_file``,
  ``reconcile.reconcile``, ``reconcile.format_text``).

REQ-VG-POLISH-004 AC-2 mandates that every v0.1.x import path keeps
resolving after the refactor; this shim is the mechanism.
"""

from voicegateway.reconcile.core import (
    DEFAULT_DIFF_THRESHOLD_PCT,
    SUPPORTED_PROVIDERS,
    ReconcileLine,
    aggregate_vg_records,
    format_csv,
    format_json,
    format_text,
    parse_provider_file,
    reconcile,
)

__all__ = [
    "DEFAULT_DIFF_THRESHOLD_PCT",
    "ReconcileLine",
    "SUPPORTED_PROVIDERS",
    "aggregate_vg_records",
    "format_csv",
    "format_json",
    "format_text",
    "parse_provider_file",
    "reconcile",
]
