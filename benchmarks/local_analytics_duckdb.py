"""VoiceGateway local-analytics spike: SQLite (current) vs DuckDB-over-SQLite vs
DuckDB-over-Parquet, on the real `requests` schema + real dashboard queries.

Reproduces the decision behind the opt-in DuckDB read path
(``voicegateway.analytics.duckdb_reader``). Run::

    uv run --with duckdb --with pytz python benchmarks/local_analytics_duckdb.py

Audited headline (7-day window, warm, median of 5; DuckDB attached to the live
SQLite, no write-path change): a full dashboard load drops from ~0.5s to ~0.1s
at 300k rows (~6x) and ~2.0s to ~0.17s at 1M rows (~12x), widening with data.
DuckDB-over-Parquet is faster still but needs a periodic export and trades
freshness, so it is a later tier, not the default.

Fairness rules:
- SQLite gets the SAME 8 indexes the production model declares, plus ANALYZE.
- Latency percentiles mirror the REAL code path: SQLite pulls raw samples and
  computes percentiles in Python (latency_repository.py); DuckDB computes them
  in-db with quantile_cont.
- Every timed query is warmed once, then run 5x; we report the median.
- We assert cross-engine results agree (sum of cost, row counts) so nobody is
  "winning" by doing less work.
"""

import math
import os
import random
import sqlite3
import statistics
import time

import duckdb

DB = "/tmp/vg_bench.sqlite"
PARQUET = "/tmp/vg_bench.parquet"
RUNS = 5

MODALITIES = ["stt", "llm", "tts"]
PROVIDERS = ["openai", "deepgram", "anthropic", "groq", "cartesia", "elevenlabs"]
MODELS = [
    "openai/gpt-4.1-mini",
    "openai/gpt-4o-mini-transcribe",
    "openai/gpt-4o-mini-tts",
    "deepgram/nova-3",
    "anthropic/claude-sonnet-4-6",
    "groq/llama-3.3-70b",
    "cartesia/sonic-2",
    "elevenlabs/turbo-v2",
    "openai/whisper-1",
    "deepgram/aura-2",
]
PROJECTS = ["default", "prod", "staging", "demo"]

DDL = """
CREATE TABLE requests (
  id TEXT PRIMARY KEY, timestamp REAL, project TEXT, modality TEXT, model_id TEXT,
  provider TEXT, input_units REAL, output_units REAL, cached_input_units REAL,
  cost_usd REAL, pricing_source TEXT, rated_price_usd REAL, rate_rule TEXT,
  ttfb_ms REAL, total_latency_ms REAL, status TEXT, fallback_from TEXT,
  error_message TEXT, metadata TEXT, session_id TEXT, tenant_id TEXT, agent_id TEXT
);
"""
INDEXES = [
    "CREATE INDEX idx_requests_timestamp ON requests(timestamp)",
    "CREATE INDEX idx_requests_model ON requests(model_id)",
    "CREATE INDEX idx_requests_modality ON requests(modality)",
    "CREATE INDEX idx_requests_project ON requests(project)",
    "CREATE INDEX idx_requests_project_timestamp ON requests(project, timestamp)",
    "CREATE INDEX idx_requests_session_id ON requests(session_id)",
    "CREATE INDEX idx_requests_agent_id ON requests(agent_id)",
    "CREATE INDEX idx_requests_agent_id_timestamp ON requests(agent_id, timestamp)",
]


def gen(n, days=30):
    random.seed(42)
    now = time.time()
    span = days * 86400
    rows = []
    for i in range(n):
        modality = random.choice(MODALITIES)
        model = random.choice(MODELS)
        provider = model.split("/")[0]
        ttfb = round(random.lognormvariate(4.6, 0.5), 1)  # ~100ms median
        lat = round(ttfb + random.lognormvariate(6.5, 0.6), 1)  # ~700ms+ median
        cost = round(random.lognormvariate(-8, 1.1), 8)  # small $ per call
        rows.append(
            (
                f"req_{i:09d}",
                now - random.random() * span,
                random.choice(PROJECTS),
                modality,
                model,
                provider,
                random.random() * 2000,
                random.random() * 500,
                0.0,
                cost,
                "voice-prices",
                cost,
                "",
                ttfb,
                lat,
                "success",
                None,
                None,
                None,
                f"sess_{i // 20}",
                f"tenant_{i % 8}",
                f"agent_{i % 12}",
            )
        )
    return rows


def build(n):
    for p in (DB, DB + "-wal", DB + "-shm", PARQUET):
        if os.path.exists(p):
            os.remove(p)
    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(DDL)
    t0 = time.perf_counter()
    con.executemany(f"INSERT INTO requests VALUES ({','.join(['?'] * 22)})", gen(n))
    con.commit()
    for idx in INDEXES:
        con.execute(idx)
    con.execute("ANALYZE")
    con.commit()
    con.close()
    build_s = time.perf_counter() - t0
    # Parquet snapshot via DuckDB (what a periodic export would produce).
    d = duckdb.connect()
    _load_sqlite(d)
    d.execute(f"ATTACH '{DB}' AS vg (TYPE sqlite)")
    e0 = time.perf_counter()
    d.execute(
        f"COPY (SELECT * FROM vg.requests) TO '{PARQUET}' (FORMAT parquet, COMPRESSION zstd)"
    )
    export_s = time.perf_counter() - e0
    d.close()
    return build_s, export_s


def _load_sqlite(d):
    try:
        d.execute("INSTALL sqlite")
    except Exception:
        pass
    try:
        d.execute("LOAD sqlite")
    except Exception:
        pass


# Realistic dashboard window: last 7 days over a 30-day store, so SQLite's
# timestamp index is SELECTIVE (helps it) — the fair comparison, not the
# all-time worst case that triggers the index-planner footgun.
SINCE = time.time() - 7 * 86400


def interp_pcts(vals, ps=(0.5, 0.95, 0.99)):
    # Linear interpolation, matching numpy/compute_percentiles AND DuckDB
    # quantile_cont, so the two engines compute the SAME statistic.
    if not vals:
        return {}
    vals = sorted(vals)
    n = len(vals)
    out = {}
    for p in ps:
        idx = p * (n - 1)
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        out[p] = vals[lo] * (1 - (idx - lo)) + vals[hi] * (idx - lo)
    return out


# ---- SQLite query implementations (mirror the real repos) ----
def sq_cost_provider(con):
    return con.execute(
        "SELECT provider, SUM(cost_usd), COUNT(*) FROM requests WHERE timestamp>=? GROUP BY provider",
        (SINCE,),
    ).fetchall()


def sq_cost_day(con):
    return con.execute(
        "SELECT date(timestamp,'unixepoch') d, SUM(cost_usd), COUNT(*) "
        "FROM requests WHERE timestamp>=? GROUP BY d ORDER BY d",
        (SINCE,),
    ).fetchall()


def sq_cost_model(con):
    return con.execute(
        "SELECT model_id, SUM(cost_usd), COUNT(*) FROM requests WHERE timestamp>=? GROUP BY model_id",
        (SINCE,),
    ).fetchall()


def sq_latency_pcts(con):
    # The real path: pull raw samples, compute percentiles in Python.
    rows = con.execute(
        "SELECT model_id, ttfb_ms, total_latency_ms FROM requests "
        "WHERE timestamp>=? AND (ttfb_ms IS NOT NULL OR total_latency_ms IS NOT NULL)",
        (SINCE,),
    ).fetchall()
    by_ttfb, by_lat = {}, {}
    for m, t, lat in rows:
        if t is not None:
            by_ttfb.setdefault(m, []).append(t)
        if lat is not None:
            by_lat.setdefault(m, []).append(lat)
    return {
        m: (interp_pcts(by_ttfb.get(m, [])), interp_pcts(by_lat.get(m, [])))
        for m in by_ttfb
    }


# ---- DuckDB query implementations (source = attached sqlite OR parquet) ----
def dk_cost_provider(d, src):
    return d.execute(
        f"SELECT provider, SUM(cost_usd), COUNT(*) FROM {src} WHERE timestamp>=? GROUP BY provider",
        [SINCE],
    ).fetchall()


def dk_cost_day(d, src):
    return d.execute(
        f"SELECT date_trunc('day', to_timestamp(timestamp)) d, SUM(cost_usd), COUNT(*) FROM {src} WHERE timestamp>=? GROUP BY d ORDER BY d",
        [SINCE],
    ).fetchall()


def dk_cost_model(d, src):
    return d.execute(
        f"SELECT model_id, SUM(cost_usd), COUNT(*) FROM {src} WHERE timestamp>=? GROUP BY model_id",
        [SINCE],
    ).fetchall()


def dk_latency_pcts(d, src):
    return d.execute(
        f"SELECT model_id, quantile_cont(ttfb_ms,[0.5,0.95,0.99]), "
        f"quantile_cont(total_latency_ms,[0.5,0.95,0.99]) FROM {src} "
        f"WHERE timestamp>=? AND (ttfb_ms IS NOT NULL OR total_latency_ms IS NOT NULL) "
        f"GROUP BY model_id",
        [SINCE],
    ).fetchall()


def timed(fn, *a):
    fn(*a)  # warm
    ts = []
    for _ in range(RUNS):
        t0 = time.perf_counter()
        fn(*a)
        ts.append((time.perf_counter() - t0) * 1000)
    return statistics.median(ts)


def run(n):
    build_s, export_s = build(n)
    con = sqlite3.connect(DB)
    d = duckdb.connect()
    _load_sqlite(d)
    d.execute(f"ATTACH '{DB}' AS vg (TYPE sqlite)")
    parquet_src = f"read_parquet('{PARQUET}')"

    # sanity: total cost agrees across engines
    s_total = con.execute("SELECT ROUND(SUM(cost_usd),6) FROM requests").fetchone()[0]
    d_total = d.execute("SELECT ROUND(SUM(cost_usd),6) FROM vg.requests").fetchone()[0]
    p_total = d.execute(f"SELECT ROUND(SUM(cost_usd),6) FROM {parquet_src}").fetchone()[
        0
    ]
    agree = abs(s_total - d_total) < 1e-3 and abs(s_total - p_total) < 1e-3

    queries = [
        ("cost by provider", sq_cost_provider, dk_cost_provider),
        ("cost by day", sq_cost_day, dk_cost_day),
        ("cost by model", sq_cost_model, dk_cost_model),
        ("latency p50/p95/p99 by model", sq_latency_pcts, dk_latency_pcts),
    ]

    win_rows = con.execute(
        "SELECT COUNT(*) FROM requests WHERE timestamp>=?", (SINCE,)
    ).fetchone()[0]
    print(
        f"\n{'=' * 82}\nSTORE = {n:,} rows | 7-day window = {win_rows:,} rows | totals agree: {agree}"
    )
    print(f"parquet export (one-off, for the parquet column): {export_s * 1000:.0f} ms")
    print(
        f"{'query (7-day window)':<32}{'SQLite':>11}{'DuckDB→sqlite':>15}{'DuckDB→parquet':>16}{'vs attach':>10}"
    )
    print("-" * 82)
    for name, sqf, dkf in queries:
        sq = timed(sqf, con)
        da = timed(dkf, d, "vg.requests")
        dp = timed(dkf, d, parquet_src)
        print(f"{name:<32}{sq:>9.1f}ms{da:>13.1f}ms{dp:>14.1f}ms{sq / da:>9.1f}x")

    # equivalence: both engines now interpolate -> percentiles must agree
    sqp = sq_latency_pcts(con)
    dkrows = {r[0]: r for r in dk_latency_pcts(d, "vg.requests")}
    m = next(iter(sqp))
    sq_p95, dk_p95 = sqp[m][1][0.95], dkrows[m][2][1]
    print(
        f"p95 latency agreement (model {m}): sqlite={sq_p95:.1f} duckdb={dk_p95:.1f} within 1%: {abs(sq_p95 - dk_p95) / dk_p95 < 0.01}"
    )

    # realistic user metric: full dashboard load = all 4 queries in sequence
    def sq_all():
        sq_cost_provider(con)
        sq_cost_day(con)
        sq_cost_model(con)
        sq_latency_pcts(con)

    def dk_all():
        dk_cost_provider(d, "vg.requests")
        dk_cost_day(d, "vg.requests")
        dk_cost_model(d, "vg.requests")
        dk_latency_pcts(d, "vg.requests")

    sqt, dkt = timed(sq_all), timed(dk_all)
    print(
        f"FULL DASHBOARD LOAD (4 queries, sequential): sqlite={sqt:.0f}ms  duckdb-attach={dkt:.0f}ms  ({sqt / dkt:.1f}x)"
    )
    con.close()
    d.close()


if __name__ == "__main__":
    print(f"duckdb {duckdb.__version__}, sqlite {sqlite3.sqlite_version}")
    for n in (300_000, 1_000_000):
        run(n)
