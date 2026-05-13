"""Reconciliation tooling for verifying VG-recorded costs against provider invoices.

The implementation lives in :mod:`voicegateway.reconcile.core`; this
``__init__`` re-exports the public surface so callers can use either
``from voicegateway import reconcile`` followed by attribute access
(e.g. ``reconcile.parse_provider_file``, ``reconcile.reconcile``) or
``from voicegateway.reconcile import reconcile`` directly.
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
