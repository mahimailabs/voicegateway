#!/usr/bin/env python3
"""Print skeleton authorization-matrix rows for routes the matrix is missing.

Read-only. Builds the FastAPI app in-process, enumerates every ``APIRoute``,
recovers how each one is gated by walking the resolved dependency graph, and
emits JSON rows for any ``(method, path)`` that ``authorization_matrix.json``
does not already cover.

This exists so the bijection test in
``tests/server/test_telemetry_authorization_matrix.py`` never becomes a tax:
add a route, run this, paste the rows it prints.

Usage:
    .venv/bin/python tools/scripts/gen_authorization_matrix.py
    .venv/bin/python tools/scripts/gen_authorization_matrix.py --all
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

import yaml

# Scope literal recovered from require_scope's closure. Asserted, not assumed:
# if the helper's shape changes this fails loudly rather than mislabelling.
_SCOPE_QUALNAME = "require_scope.<locals>._dep"


def _build_app():
    """Build the app over a throwaway SQLite db and a minimal config."""
    tmp = tempfile.mkdtemp(prefix="vg-matrix-")
    os.environ["VOICEGW_DB_PATH"] = os.path.join(tmp, "matrix.db")
    cfg = {
        "providers": {"openai": {"api_key": "test-key"}},
        "models": {"stt": {}, "llm": {}, "tts": {}},
        "projects": {},
        "fallbacks": {"stt": [], "llm": [], "tts": []},
        "cost_tracking": {"enabled": True},
    }
    cfg_path = os.path.join(tmp, "voicegw.yaml")
    with open(cfg_path, "w") as handle:
        yaml.dump(cfg, handle)

    from voicegateway.core.gateway import Gateway
    from voicegateway.server import build_app

    return build_app(
        Gateway(config_path=cfg_path), enable_mcp_sse=False, enable_dashboard=False
    )


def _walk(dependant, out: list) -> None:
    """Collect every callable in the resolved dependency tree."""
    for sub in dependant.dependencies:
        if sub.call is not None:
            out.append(sub.call)
        _walk(sub, out)


def classify(fn) -> str | None:
    """Return the RouteAuth value a dependency implies, or None."""
    qualname = getattr(fn, "__qualname__", "")
    if qualname == _SCOPE_QUALNAME:
        code = fn.__code__
        if code.co_freevars != ("scope",):
            raise SystemExit(
                "require_scope's closure no longer has exactly one free "
                f"variable named 'scope' (found {code.co_freevars!r}). "
                "Update _SCOPE_QUALNAME/classify in this script and the "
                "matching assertion in test_telemetry_authorization_matrix.py."
            )
        cell = fn.__closure__[code.co_freevars.index("scope")]
        return f"scope:{cell.cell_contents}"
    if qualname == "require_principal":
        return "principal"
    return None


def inspect_routes() -> list[dict]:
    """Return one dict per ``(method, path)`` with its recovered gating."""
    from fastapi.routing import APIRoute

    app = _build_app()
    rows: list[dict] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        calls: list = []
        _walk(route.dependant, calls)
        marks = sorted({m for m in (classify(f) for f in calls) if m})
        # A route gated more than one way is a finding in itself, so surface
        # the pair rather than silently picking the first.
        auth = "+".join(marks) if marks else "open"
        params = {p.name for p in route.dependant.query_params}
        takes_project = "project" in params or "{project_id}" in route.path
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            rows.append(
                {
                    "method": method,
                    "path": route.path,
                    "auth": auth,
                    "takes_project": takes_project,
                }
            )
    return sorted(rows, key=lambda r: (r["path"], r["method"]))


def _skeleton(row: dict) -> dict:
    """Build a default row. Hand-classification refines it afterwards."""
    if row["auth"] == "open":
        out = {
            "method": row["method"],
            "path": row["path"],
            "auth": "open",
            "status": "gap",
            "tenant_scoped": True,
            "gap_id": "VG-SEC-004",
            "wave": 1,
            "note": "GENERATED: unauthenticated read. Classify by hand.",
        }
    else:
        out = {
            "method": row["method"],
            "path": row["path"],
            "auth": row["auth"],
            "status": "enforced",
            "tenant_scoped": True,
            "note": "GENERATED: verify tenant_scoped by hand.",
        }
    if row["takes_project"]:
        out["note"] += " Takes a project identifier (VG-SEC-002)."
    return out


def main() -> int:
    """Print the rows the matrix is missing, or every row with --all."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="print a skeleton for every route, not just uncovered ones",
    )
    args = parser.parse_args()

    from voicegateway.schemas.telemetry.security_schema import (
        load_authorization_matrix,
    )

    live = inspect_routes()
    try:
        covered = load_authorization_matrix().keys()
    except (FileNotFoundError, ValueError):
        covered = set()

    missing = [
        row for row in live if args.all or (row["method"], row["path"]) not in covered
    ]
    stale = sorted(covered - {(r["method"], r["path"]) for r in live})

    if stale:
        print(
            f"# {len(stale)} matrix row(s) reference a route that no longer "
            "exists; delete them:",
            file=sys.stderr,
        )
        for method, path in stale:
            print(f"#   {method} {path}", file=sys.stderr)

    if not missing:
        print(f"# matrix covers all {len(live)} routes", file=sys.stderr)
        return 0

    print(f"# {len(missing)} route(s) need a row:", file=sys.stderr)
    print(json.dumps([_skeleton(row) for row in missing], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
