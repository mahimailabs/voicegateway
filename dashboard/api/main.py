"""FastAPI dashboard API for VoiceGateway."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

app = FastAPI(
    title="VoiceGateway Dashboard",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set by the CLI when starting the dashboard
_gateway: Any = None


def _get_gateway():
    if _gateway is None:
        raise RuntimeError("Gateway not initialized. Start via: voicegw dashboard")
    return _gateway


@app.get("/api/status")
async def get_status() -> dict:
    """Get status of all configured providers and models."""
    gw = _get_gateway()
    config = gw.config

    providers = {}
    for name, cfg in config.providers.items():
        has_key = bool(cfg.get("api_key")) or name in ("ollama", "whisper", "kokoro", "piper")
        providers[name] = {
            "configured": has_key,
            "type": "local" if name in ("ollama", "whisper", "kokoro", "piper") else "cloud",
        }

    models = {}
    for modality, modality_models in config.models.items():
        if isinstance(modality_models, dict):
            for model_id, model_cfg in modality_models.items():
                if isinstance(model_cfg, dict):
                    models[model_id] = {
                        "modality": modality,
                        "provider": model_cfg.get("provider", ""),
                    }

    return {
        "providers": providers,
        "models": models,
        "fallbacks": config.fallbacks,
    }


@app.get("/api/costs")
async def get_costs(
    period: str = Query("today", enum=["today", "week", "month", "all"]),
    project: str | None = Query(None),
) -> dict:
    """Get cost summary for a period, optionally filtered by project."""
    gw = _get_gateway()
    if gw.storage is None:
        return {"period": period, "project": project, "total": 0.0, "by_provider": {}, "by_model": {}, "by_project": {}}
    summary = await gw.storage.get_cost_summary(period, project=project)
    if project is None:
        summary["by_project"] = await gw.storage.get_cost_by_project(period)
    else:
        summary["by_project"] = {}
    return summary


@app.get("/api/latency")
async def get_latency(
    period: str = Query("today", enum=["today", "week"]),
    project: str | None = Query(None),
) -> dict:
    """Get latency statistics, optionally filtered by project."""
    gw = _get_gateway()
    if gw.storage is None:
        return {}
    return await gw.storage.get_latency_stats(period, project=project)


@app.get("/api/logs")
async def get_logs(
    limit: int = Query(100, ge=1, le=1000),
    modality: str | None = Query(None, enum=["stt", "llm", "tts"]),
    project: str | None = Query(None),
) -> list[dict]:
    """Get recent request logs, optionally filtered by modality and/or project."""
    gw = _get_gateway()
    if gw.storage is None:
        return []
    return await gw.storage.get_recent_requests(limit=limit, modality=modality, project=project)


@app.get("/api/overview")
async def get_overview(
    project: str | None = Query(None),
) -> dict:
    """Get dashboard overview stats, optionally filtered by project."""
    gw = _get_gateway()
    config = gw.config

    model_count = 0
    for modality_models in config.models.values():
        if isinstance(modality_models, dict):
            model_count += len(modality_models)

    if gw.storage is None:
        return {
            "total_requests": 0,
            "total_cost": 0.0,
            "active_models": model_count,
            "providers_configured": len(config.providers),
        }

    cost_summary = await gw.storage.get_cost_summary("today", project=project)
    total_all = await gw.storage.get_cost_summary("all", project=project)

    return {
        "total_requests": sum(
            d["requests"] for d in total_all.get("by_provider", {}).values()
        ),
        "total_cost_today": cost_summary.get("total", 0.0),
        "total_cost_all": total_all.get("total", 0.0),
        "active_models": model_count,
        "providers_configured": len(config.providers),
    }


@app.get("/api/projects")
async def get_projects() -> dict:
    """List configured projects with today's stats."""
    gw = _get_gateway()
    projects = gw.list_projects()
    stats = {}
    if gw.storage is not None:
        for p in projects:
            stats[p["id"]] = await gw.storage.get_project_stats(p["id"])
    return {"projects": projects, "stats": stats}


_NEO_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
  --bg-page: #FFFDF7;
  --bg-card: #FFFFFF;
  --text-primary: #1A1A1A;
  --text-secondary: #555555;
  --border: #1A1A1A;
  --border-width: 2.5px;
  --shadow: 4px 4px 0px #1A1A1A;
  --shadow-sm: 3px 3px 0px #1A1A1A;
  --shadow-hover: 6px 6px 0px #1A1A1A;
  --yellow: #FFD60A;
  --blue: #3A86FF;
  --green: #8AC926;
  --pink: #FF006E;
  --orange: #FB5607;
  --purple: #7B2FF7;
  --status-online: #8AC926;
  --status-offline: #FF006E;
  --radius: 4px;
  --radius-pill: 999px;
}

*, *::before, *::after { box-sizing: border-box; }
html, body, #root { height: 100%; }
body {
  margin: 0;
  background: var(--bg-page);
  color: var(--text-primary);
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 14px;
  -webkit-font-smoothing: antialiased;
}
h1, h2, h3 { font-family: 'Space Grotesk', sans-serif; font-weight: 700; margin: 0; letter-spacing: -0.02em; }
button { font-family: inherit; cursor: pointer; }
input, select { font-family: inherit; }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; }

/* Layout */
.app-shell { display: flex; min-height: 100vh; }
.sidebar {
  width: 240px; flex-shrink: 0;
  background: var(--bg-card);
  border-right: var(--border-width) solid var(--border);
  padding: 24px 0;
  display: flex; flex-direction: column;
  position: sticky; top: 0; height: 100vh;
}
.sidebar__logo {
  padding: 0 24px 16px;
  font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 14px;
  text-transform: uppercase; letter-spacing: 0.1em;
  border-bottom: var(--border-width) solid var(--border);
  margin-bottom: 16px;
}
.sidebar__logo small {
  display: block; font-size: 10px; font-weight: 500; letter-spacing: 0.14em;
  color: var(--text-secondary); margin-top: 3px;
}
.sidebar__nav { display: flex; flex-direction: column; gap: 4px; padding: 0 16px; flex: 1; }

.nav-item {
  display: flex; align-items: center;
  padding: 12px 16px;
  font-family: 'Inter', sans-serif; font-weight: 600; font-size: 13px;
  text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--text-secondary);
  border: var(--border-width) solid transparent;
  border-left-width: 4px;
  border-radius: var(--radius);
  background: transparent;
  text-align: left; width: 100%;
  transition: background 0.15s ease, color 0.15s ease, box-shadow 0.15s ease;
}
.nav-item--overview { border-left-color: var(--yellow); }
.nav-item--models   { border-left-color: var(--blue); }
.nav-item--costs    { border-left-color: var(--green); }
.nav-item--latency  { border-left-color: var(--pink); }
.nav-item--logs     { border-left-color: var(--orange); }

.nav-item:hover {
  background: var(--yellow); color: var(--text-primary);
  border-color: var(--border); border-left-color: var(--border);
}
.nav-item--active {
  background: var(--text-primary); color: white;
  border-color: var(--border);
  box-shadow: 3px 3px 0px var(--yellow);
}
.nav-item--active.nav-item--models  { box-shadow: 3px 3px 0px var(--blue); }
.nav-item--active.nav-item--costs   { box-shadow: 3px 3px 0px var(--green); }
.nav-item--active.nav-item--latency { box-shadow: 3px 3px 0px var(--pink); }
.nav-item--active.nav-item--logs    { box-shadow: 3px 3px 0px var(--orange); }
.nav-item--active:hover { background: var(--text-primary); color: white; }

.sidebar__footer {
  padding: 16px 20px 4px;
  border-top: var(--border-width) solid var(--border);
  margin-top: 16px;
}
.sidebar__footer-label {
  font-size: 10px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.1em; color: var(--text-secondary); margin-bottom: 10px;
}
.sidebar__status-row {
  display: flex; align-items: center; gap: 8px;
  font-size: 11px; font-weight: 600; margin-bottom: 8px;
}
.version-pill {
  display: inline-block; margin-top: 10px;
  background: var(--text-primary); color: white;
  padding: 4px 12px;
  font-family: 'JetBrains Mono', monospace; font-size: 10px; font-weight: 500;
  border: 2px solid var(--border); border-radius: var(--radius-pill);
  box-shadow: 2px 2px 0px var(--border);
}

.main { flex: 1; padding: 40px 48px 64px; min-width: 0; }
.main__header {
  display: flex; align-items: baseline; justify-content: space-between;
  margin-bottom: 32px; padding-bottom: 16px;
  border-bottom: 3px solid var(--border);
}
.main__header--yellow { border-bottom-color: var(--yellow); }
.main__header--blue   { border-bottom-color: var(--blue); }
.main__header--green  { border-bottom-color: var(--green); }
.main__header--pink   { border-bottom-color: var(--pink); }
.main__header--orange { border-bottom-color: var(--orange); }
.page-title {
  font-family: 'Space Grotesk', sans-serif; font-weight: 700;
  font-size: 32px; text-transform: uppercase; letter-spacing: -0.02em;
}
.main__subtitle {
  font-size: 12px; color: var(--text-secondary); font-weight: 500;
  text-transform: uppercase; letter-spacing: 0.08em;
}

/* Cards */
.neo-card {
  background: var(--bg-card);
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow);
  padding: 24px;
  transition: box-shadow 0.15s ease, transform 0.15s ease;
  position: relative;
}
.neo-card:hover {
  box-shadow: var(--shadow-hover);
  transform: translate(-2px, -2px);
}
.neo-card--strip-yellow::before,
.neo-card--strip-blue::before,
.neo-card--strip-green::before,
.neo-card--strip-pink::before,
.neo-card--strip-orange::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0;
  height: 6px; border-bottom: var(--border-width) solid var(--border);
}
.neo-card--strip-yellow::before { background: var(--yellow); }
.neo-card--strip-blue::before   { background: var(--blue); }
.neo-card--strip-green::before  { background: var(--green); }
.neo-card--strip-pink::before   { background: var(--pink); }
.neo-card--strip-orange::before { background: var(--orange); }
.neo-card--strip-yellow, .neo-card--strip-blue,
.neo-card--strip-green, .neo-card--strip-pink, .neo-card--strip-orange {
  padding-top: 30px;
}

/* Buttons */
.neo-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
  padding: 10px 20px;
  font-family: 'Inter', sans-serif; font-weight: 700; font-size: 13px;
  text-transform: uppercase; letter-spacing: 0.05em;
  color: var(--text-primary); background: var(--bg-card);
  cursor: pointer; user-select: none;
  transition: box-shadow 0.1s ease, transform 0.1s ease, background 0.1s ease;
}
.neo-btn:hover { box-shadow: var(--shadow); transform: translate(-1px, -1px); }
.neo-btn:active { box-shadow: 1px 1px 0px var(--border); transform: translate(2px, 2px); }
.neo-btn--primary { background: var(--yellow); }
.neo-btn--blue    { background: var(--blue); color: white; }
.neo-btn--green   { background: var(--green); }
.neo-btn--pink    { background: var(--pink); color: white; }
.neo-btn--orange  { background: var(--orange); color: white; }
.neo-btn--danger  { background: var(--pink); color: white; }
.neo-btn--small   { padding: 6px 12px; font-size: 11px; box-shadow: 2px 2px 0px var(--border); }

/* Inputs / Selects */
.neo-input, .neo-select {
  background: var(--bg-card);
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius);
  padding: 10px 14px;
  font-family: 'Inter', sans-serif; font-size: 14px; font-weight: 500;
  color: var(--text-primary);
  box-shadow: inset 2px 2px 0px rgba(0,0,0,0.05);
}
.neo-input:focus, .neo-select:focus {
  outline: none;
  box-shadow: inset 2px 2px 0px rgba(0,0,0,0.05), 0 0 0 3px var(--yellow);
}
.neo-select {
  appearance: none;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'><path fill='%231A1A1A' d='M2 4l4 4 4-4z'/></svg>");
  background-repeat: no-repeat;
  background-position: right 12px center;
  padding-right: 32px;
}

/* Tables */
.neo-table {
  width: 100%;
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius);
  border-collapse: separate;
  border-spacing: 0;
  background: var(--bg-card);
  box-shadow: var(--shadow);
  overflow: hidden;
}
.neo-table thead tr { background: var(--text-primary); }
.neo-table thead th {
  color: white;
  font-family: 'Inter', sans-serif; font-weight: 700; font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.1em;
  text-align: left; padding: 14px 18px;
  border-bottom: var(--border-width) solid var(--border);
}
.neo-table tbody tr { border-bottom: 1.5px solid var(--border); transition: background 0.1s ease; }
.neo-table tbody tr:last-child { border-bottom: none; }
.neo-table tbody td { padding: 14px 18px; font-size: 14px; vertical-align: middle; }
.neo-table--yellow tbody tr:hover { background: rgba(255, 214, 10, 0.25); }
.neo-table--blue tbody tr:hover   { background: rgba(58, 134, 255, 0.15); }
.neo-table--green tbody tr:hover  { background: rgba(138, 201, 38, 0.2); }
.neo-table--pink tbody tr:hover   { background: rgba(255, 0, 110, 0.12); }
.neo-table--orange tbody tr:hover { background: rgba(251, 86, 7, 0.15); }

/* Badges */
.neo-badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 3px 10px;
  font-family: 'Inter', sans-serif; font-size: 10px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.06em;
  border: 2px solid var(--border);
  border-radius: var(--radius-pill);
  box-shadow: 2px 2px 0px var(--border);
  background: var(--bg-card); color: var(--text-primary);
  white-space: nowrap;
  transition: transform 0.1s ease;
}
.neo-badge:hover { transform: scale(1.05); }
.neo-badge--online  { background: var(--green); color: var(--text-primary); }
.neo-badge--offline { background: var(--pink); color: white; }
.neo-badge--warning { background: var(--yellow); color: var(--text-primary); }
.neo-badge--info    { background: var(--blue); color: white; }
.neo-badge--black   { background: var(--text-primary); color: white; }
.neo-badge--yellow  { background: var(--yellow); color: var(--text-primary); }
.neo-badge--blue    { background: var(--blue); color: white; }
.neo-badge--green   { background: var(--green); color: var(--text-primary); }
.neo-badge--pink    { background: var(--pink); color: white; }
.neo-badge--orange  { background: var(--orange); color: white; }
.neo-badge--latency-fast   { background: var(--green); color: var(--text-primary); }
.neo-badge--latency-medium { background: var(--yellow); color: var(--text-primary); }
.neo-badge--latency-slow   { background: var(--pink); color: white; }

.neo-status-dot {
  display: inline-block; width: 10px; height: 10px;
  border: 1.5px solid var(--border); border-radius: 50%; margin-right: 6px;
}
.neo-status-dot--online  { background: var(--status-online); }
.neo-status-dot--offline { background: var(--status-offline); }

/* Grid */
.grid { display: grid; gap: 20px; }
.grid-cols-2 { grid-template-columns: repeat(2, 1fr); }
.grid-cols-3 { grid-template-columns: repeat(3, 1fr); }
.grid-cols-4 { grid-template-columns: repeat(4, 1fr); }
@media (max-width: 1100px) {
  .grid-cols-4 { grid-template-columns: repeat(2, 1fr); }
  .grid-cols-3 { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 720px) {
  .grid-cols-2, .grid-cols-3, .grid-cols-4 { grid-template-columns: 1fr; }
  .sidebar { display: none; }
  .main { padding: 24px; }
}

/* Typography helpers */
.label {
  font-family: 'Inter', sans-serif; font-weight: 600; font-size: 11px;
  text-transform: uppercase; letter-spacing: 0.1em;
  color: var(--text-secondary);
}
.stat-value {
  font-family: 'Space Grotesk', sans-serif; font-weight: 700;
  font-size: 36px; line-height: 1.1; color: var(--text-primary);
}
.stat-value--xl { font-size: 56px; line-height: 1; }

.icon-square {
  display: inline-flex; align-items: center; justify-content: center;
  width: 36px; height: 36px;
  border: var(--border-width) solid var(--border);
  border-radius: var(--radius);
  font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 14px;
  box-shadow: 2px 2px 0px var(--border);
}
.icon-square--yellow { background: var(--yellow); }
.icon-square--blue   { background: var(--blue); color: white; }
.icon-square--green  { background: var(--green); }
.icon-square--pink   { background: var(--pink); color: white; }
.icon-square--orange { background: var(--orange); color: white; }

.empty-state {
  padding: 48px 24px; text-align: center;
  color: var(--text-secondary); font-weight: 500; font-size: 14px;
}

.stat-label-row {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 12px;
}

.kv-row {
  display: flex; justify-content: space-between; align-items: center;
  padding: 12px 0;
  border-bottom: 1.5px dashed var(--border);
}
.kv-row:last-child { border-bottom: none; }
.kv-row__key { font-family: 'JetBrains Mono', monospace; font-size: 12px; font-weight: 500; }
.kv-row__value {
  font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 16px;
}

.flex-row { display: flex; align-items: center; gap: 12px; }
.flex-wrap { flex-wrap: wrap; }
.mt-md { margin-top: 20px; }
.mt-lg { margin-top: 32px; }
.mb-lg { margin-bottom: 32px; }

.pct-bar {
  height: 12px;
  background: var(--bg-card);
  border: 2px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  margin-top: 6px;
  box-shadow: 2px 2px 0px var(--border);
}
.pct-bar__fill {
  height: 100%;
  border-right: 2px solid var(--border);
}
.pct-bar__fill--yellow { background: var(--yellow); }
.pct-bar__fill--blue   { background: var(--blue); }
.pct-bar__fill--green  { background: var(--green); }
.pct-bar__fill--pink   { background: var(--pink); }
.pct-bar__fill--orange { background: var(--orange); }

.filter-bar {
  display: flex; flex-wrap: wrap; gap: 12px;
  margin-bottom: 24px; align-items: center;
}
"""

_HTML_SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VoiceGateway</title>
    <style>__NEO_CSS__</style>
    <script crossorigin src="https://unpkg.com/react@18/umd/react.production.min.js"></script>
    <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.production.min.js"></script>
    <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
</head>
<body>
    <div id="root"></div>
    <script type="text/babel" data-presets="env,react">
        const { useState, useEffect, useMemo } = React;

        const PAGES = [
            { id: 'overview', label: 'Overview', accent: 'yellow' },
            { id: 'models',   label: 'Models',   accent: 'blue' },
            { id: 'costs',    label: 'Costs',    accent: 'green' },
            { id: 'latency',  label: 'Latency',  accent: 'pink' },
            { id: 'logs',     label: 'Logs',     accent: 'orange' },
        ];

        function latencyBadgeClass(ms) {
            if (ms == null) return '';
            if (ms < 200) return 'neo-badge--latency-fast';
            if (ms < 500) return 'neo-badge--latency-medium';
            return 'neo-badge--latency-slow';
        }

        function statusBadgeClass(status) {
            if (status === 'success') return 'neo-badge--online';
            if (status === 'fallback') return 'neo-badge--warning';
            return 'neo-badge--offline';
        }

        function Sidebar({ page, setPage, status, project, setProject }) {
            const providerCount = status?.providers ? Object.keys(status.providers).length : 0;
            const modelCount = status?.models ? Object.keys(status.models).length : 0;
            const [projects, setProjects] = useState([]);

            useEffect(() => {
                fetch('/api/projects').then(r => r.json()).then(d => setProjects(d.projects || []));
            }, []);

            return (
                <aside className="sidebar">
                    <div className="sidebar__logo">
                        VoiceGateway
                        <small>SELF-HOSTED VOICE AI</small>
                    </div>
                    <div style={{ padding: '0 16px 16px', borderBottom: '2.5px solid #1A1A1A', marginBottom: '16px' }}>
                        <div className="label" style={{ marginBottom: 6 }}>Project</div>
                        <select
                            className="neo-select"
                            style={{ width: '100%' }}
                            value={project}
                            onChange={e => setProject(e.target.value)}
                        >
                            <option value="">All Projects</option>
                            {projects.map(p => (
                                <option key={p.id} value={p.id}>{p.name}</option>
                            ))}
                        </select>
                    </div>
                    <nav className="sidebar__nav">
                        {PAGES.map(p => {
                            const active = page === p.id;
                            return (
                                <button
                                    key={p.id}
                                    onClick={() => setPage(p.id)}
                                    className={`nav-item nav-item--${p.id} ${active ? 'nav-item--active' : ''}`}>
                                    {p.label}
                                </button>
                            );
                        })}
                    </nav>
                    <div className="sidebar__footer">
                        <div className="sidebar__footer-label">Status</div>
                        <div className="sidebar__status-row">
                            <span className="neo-status-dot neo-status-dot--online" />
                            Gateway Online
                        </div>
                        <div className="sidebar__status-row">
                            <span className="neo-status-dot neo-status-dot--online" />
                            {providerCount} Providers · {modelCount} Models
                        </div>
                        <span className="version-pill">v0.1.0</span>
                    </div>
                </aside>
            );
        }

        function PageHeader({ title, subtitle, accent, actions }) {
            return (
                <div className={`main__header main__header--${accent}`}>
                    <div>
                        <h1 className="page-title">{title}</h1>
                        {subtitle && <div className="main__subtitle">{subtitle}</div>}
                    </div>
                    {actions && <div className="flex-row flex-wrap">{actions}</div>}
                </div>
            );
        }

        function StatCard({ label, value, accent, icon }) {
            return (
                <div className={`neo-card neo-card--strip-${accent}`}>
                    <div className="stat-label-row">
                        <span className="label">{label}</span>
                        {icon && <span className={`icon-square icon-square--${accent}`}>{icon}</span>}
                    </div>
                    <div className="stat-value">{value}</div>
                </div>
            );
        }

        function App() {
            const [page, setPage] = useState('overview');
            const [project, setProject] = useState('');
            const [overview, setOverview] = useState(null);
            const [projectsData, setProjectsData] = useState(null);
            const [costs, setCosts] = useState(null);
            const [status, setStatus] = useState(null);
            const [logs, setLogs] = useState([]);
            const [latency, setLatency] = useState(null);
            const [logFilter, setLogFilter] = useState('');
            const [logProjectFilter, setLogProjectFilter] = useState('');

            useEffect(() => {
                fetch('/api/status').then(r => r.json()).then(setStatus);
                fetch('/api/projects').then(r => r.json()).then(setProjectsData);
            }, []);

            useEffect(() => {
                const pq = project ? `&project=${encodeURIComponent(project)}` : '';
                fetch(`/api/overview?_=1${pq}`).then(r => r.json()).then(setOverview);
                fetch(`/api/costs?_=1${pq}`).then(r => r.json()).then(setCosts);
                fetch(`/api/latency?_=1${pq}`).then(r => r.json()).then(setLatency);
            }, [project]);

            useEffect(() => {
                const pq = logProjectFilter ? `&project=${encodeURIComponent(logProjectFilter)}` : '';
                const mq = logFilter ? `&modality=${logFilter}` : '';
                fetch(`/api/logs?limit=50${mq}${pq}`).then(r => r.json()).then(setLogs);
            }, [logFilter, logProjectFilter]);

            return (
                <div className="app-shell">
                    <Sidebar page={page} setPage={setPage} status={status} project={project} setProject={setProject} />
                    <main className="main">
                        {page === 'overview' && <OverviewPage data={overview} status={status} projectsData={projectsData} project={project} />}
                        {page === 'models'   && <ModelsPage data={status} />}
                        {page === 'costs'    && <CostsPage data={costs} project={project} />}
                        {page === 'latency'  && <LatencyPage data={latency} />}
                        {page === 'logs'     && <LogsPage data={logs} filter={logFilter} setFilter={setLogFilter} projectFilter={logProjectFilter} setProjectFilter={setLogProjectFilter} projectsData={projectsData} />}
                    </main>
                </div>
            );
        }

        /* ------------- Overview ------------- */

        function OverviewPage({ data, status, projectsData, project }) {
            if (!data) return <div className="empty-state">Loading overview...</div>;

            const showProjectCards = project === '' && projectsData && (projectsData.projects || []).length > 0;

            return (
                <div>
                    <PageHeader
                        title="Overview"
                        subtitle={project ? `Project: ${project}` : "Live voice AI gateway stats"}
                        accent="yellow"
                        actions={
                            <React.Fragment>
                                <button className="neo-btn neo-btn--primary">Refresh</button>
                                <button className="neo-btn">Export</button>
                            </React.Fragment>
                        }
                    />
                    <div className="grid grid-cols-4">
                        <StatCard
                            label="Total Requests"
                            value={data.total_requests ?? 0}
                            accent="yellow"
                            icon="R"
                        />
                        <StatCard
                            label="Cost Today"
                            value={`$${(data.total_cost_today || 0).toFixed(4)}`}
                            accent="green"
                            icon="$"
                        />
                        <StatCard
                            label="Cost (All Time)"
                            value={`$${(data.total_cost_all || 0).toFixed(4)}`}
                            accent="blue"
                            icon="Σ"
                        />
                        <StatCard
                            label="Active Models"
                            value={data.active_models ?? 0}
                            accent="pink"
                            icon="M"
                        />
                    </div>

                    {showProjectCards && (
                        <div className="mt-lg">
                            <div className="label" style={{ marginBottom: 16 }}>Projects</div>
                            <div className="grid grid-cols-2">
                                {(projectsData.projects || []).map(proj => {
                                    const pStats = (projectsData.stats || {})[proj.id] || {};
                                    return (
                                        <div key={proj.id} className="neo-card neo-card--strip-yellow">
                                            <div className="stat-label-row">
                                                <span className="label">{proj.name}</span>
                                                <span className="neo-badge neo-badge--yellow">{proj.id}</span>
                                            </div>
                                            {proj.description && (
                                                <div style={{ fontSize: 13, color: 'var(--text-secondary)', marginBottom: 12 }}>
                                                    {proj.description}
                                                </div>
                                            )}
                                            {proj.tags && proj.tags.length > 0 && (
                                                <div className="flex-row flex-wrap" style={{ marginBottom: 12 }}>
                                                    {proj.tags.map(tag => (
                                                        <span key={tag} className="neo-badge neo-badge--blue">{tag}</span>
                                                    ))}
                                                </div>
                                            )}
                                            <div className="flex-row" style={{ marginTop: 8 }}>
                                                <div>
                                                    <div className="label">Cost Today</div>
                                                    <div className="kv-row__value">${(pStats.cost_today || 0).toFixed(4)}</div>
                                                </div>
                                                <div style={{ marginLeft: 24 }}>
                                                    <div className="label">Requests Today</div>
                                                    <div className="kv-row__value">{pStats.requests_today || 0}</div>
                                                </div>
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                    )}

                    <div className="mt-lg grid grid-cols-2">
                        <div className="neo-card neo-card--strip-yellow">
                            <div className="label">Providers Configured</div>
                            <div className="stat-value mt-md">{data.providers_configured ?? 0}</div>
                            <div className="label mt-md">This dashboard is served by the gateway itself.</div>
                        </div>
                        <div className="neo-card neo-card--strip-blue">
                            <div className="label">Quick Actions</div>
                            <div className="flex-row flex-wrap mt-md">
                                <button className="neo-btn neo-btn--primary">Refresh</button>
                                <button className="neo-btn neo-btn--blue">View Models</button>
                                <button className="neo-btn neo-btn--green">View Costs</button>
                            </div>
                        </div>
                    </div>
                </div>
            );
        }

        /* ------------- Models ------------- */

        function ModelsPage({ data }) {
            if (!data) return <div className="empty-state">Loading models...</div>;
            const entries = Object.entries(data.models || {});
            const providers = data.providers || {};
            return (
                <div>
                    <PageHeader
                        title="Models"
                        subtitle={`${entries.length} configured`}
                        accent="blue"
                    />
                    <table className="neo-table neo-table--blue">
                        <thead>
                            <tr>
                                <th>Model</th>
                                <th>Modality</th>
                                <th>Provider</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            {entries.map(([id, cfg]) => {
                                const configured = providers[cfg.provider]?.configured;
                                return (
                                    <tr key={id}>
                                        <td className="mono">{id}</td>
                                        <td>
                                            <span className="neo-badge neo-badge--black">
                                                {(cfg.modality || '').toUpperCase()}
                                            </span>
                                        </td>
                                        <td>
                                            <span className="neo-badge neo-badge--blue">
                                                {cfg.provider}
                                            </span>
                                        </td>
                                        <td>
                                            <span className={`neo-badge ${configured ? 'neo-badge--online' : 'neo-badge--offline'}`}>
                                                {configured ? 'Ready' : 'No API Key'}
                                            </span>
                                        </td>
                                    </tr>
                                );
                            })}
                            {entries.length === 0 && (
                                <tr><td colSpan="4" className="empty-state">No models configured.</td></tr>
                            )}
                        </tbody>
                    </table>
                </div>
            );
        }

        /* ------------- Costs ------------- */

        function CostsPage({ data, project }) {
            if (!data) return <div className="empty-state">Loading costs...</div>;
            const providers = Object.entries(data.by_provider || {});
            const models = Object.entries(data.by_model || {});
            const byProject = Object.entries(data.by_project || {});
            const maxProviderCost = Math.max(...providers.map(([, v]) => v.cost), 0.0001);
            const maxModelCost = Math.max(...models.map(([, v]) => v.cost), 0.0001);
            const maxProjectCost = Math.max(...byProject.map(([, v]) => v.cost), 0.0001);

            return (
                <div>
                    <PageHeader
                        title="Costs"
                        subtitle={project ? `Project: ${project} · Period: ${data.period || 'today'}` : `Period: ${data.period || 'today'}`}
                        accent="green"
                    />
                    <div className="neo-card neo-card--strip-green mb-lg">
                        <div className="label">Total Spend</div>
                        <div className="stat-value stat-value--xl mt-md">
                            ${(data.total || 0).toFixed(4)}
                        </div>
                    </div>

                    <div className="grid grid-cols-2">
                        <div className="neo-card neo-card--strip-green">
                            <div className="label">By Provider</div>
                            {providers.length === 0
                                ? <div className="empty-state">No data yet</div>
                                : providers.map(([name, info]) => (
                                    <div key={name} className="kv-row">
                                        <div style={{ flex: 1 }}>
                                            <div className="kv-row__key">{name}</div>
                                            <div className="pct-bar">
                                                <div className="pct-bar__fill pct-bar__fill--green"
                                                     style={{ width: `${(info.cost / maxProviderCost * 100).toFixed(0)}%` }} />
                                            </div>
                                        </div>
                                        <div className="kv-row__value" style={{ marginLeft: 16 }}>
                                            ${info.cost.toFixed(4)}
                                        </div>
                                    </div>
                                ))
                            }
                        </div>
                        <div className="neo-card neo-card--strip-green">
                            <div className="label">By Model</div>
                            {models.length === 0
                                ? <div className="empty-state">No data yet</div>
                                : models.map(([name, info]) => (
                                    <div key={name} className="kv-row">
                                        <div style={{ flex: 1 }}>
                                            <div className="kv-row__key">{name}</div>
                                            <div className="pct-bar">
                                                <div className="pct-bar__fill pct-bar__fill--green"
                                                     style={{ width: `${(info.cost / maxModelCost * 100).toFixed(0)}%` }} />
                                            </div>
                                        </div>
                                        <div className="kv-row__value" style={{ marginLeft: 16 }}>
                                            ${info.cost.toFixed(4)}
                                        </div>
                                    </div>
                                ))
                            }
                        </div>
                    </div>

                    {project === '' && byProject.length > 0 && (
                        <div className="mt-lg">
                            <div className="neo-card neo-card--strip-green">
                                <div className="label">By Project</div>
                                {byProject.map(([name, info]) => (
                                    <div key={name} className="kv-row">
                                        <div style={{ flex: 1 }}>
                                            <div className="kv-row__key">{name}</div>
                                            <div className="pct-bar">
                                                <div className="pct-bar__fill pct-bar__fill--green"
                                                     style={{ width: `${(info.cost / maxProjectCost * 100).toFixed(0)}%` }} />
                                            </div>
                                        </div>
                                        <div className="kv-row__value" style={{ marginLeft: 16 }}>
                                            ${info.cost.toFixed(4)}
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}

                    {models.length > 0 && (
                        <div className="mt-lg">
                            <table className="neo-table neo-table--green">
                                <thead>
                                    <tr>
                                        <th>Model</th>
                                        <th>Requests</th>
                                        <th>Cost</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {models.map(([name, info]) => (
                                        <tr key={name}>
                                            <td className="mono">{name}</td>
                                            <td>
                                                <span className="neo-badge neo-badge--black">{info.requests}</span>
                                            </td>
                                            <td className="mono">${info.cost.toFixed(6)}</td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
            );
        }

        /* ------------- Latency ------------- */

        function LatencyPage({ data }) {
            if (!data) return <div className="empty-state">Loading latency...</div>;
            const entries = Object.entries(data);
            const ttfbValues = entries.map(([, s]) => s.avg_ttfb_ms || 0);
            const p = (arr, pct) => {
                if (arr.length === 0) return 0;
                const sorted = [...arr].sort((a, b) => a - b);
                const idx = Math.floor(sorted.length * pct / 100);
                return sorted[Math.min(idx, sorted.length - 1)];
            };
            const p50 = p(ttfbValues, 50);
            const p95 = p(ttfbValues, 95);
            const p99 = p(ttfbValues, 99);
            const maxLatency = Math.max(...entries.map(([, s]) => s.avg_latency_ms || 0), 1);

            return (
                <div>
                    <PageHeader
                        title="Latency"
                        subtitle="Time to first byte (TTFB) and total latency"
                        accent="pink"
                    />

                    <div className="grid grid-cols-3 mb-lg">
                        <StatCard label="P50 TTFB" value={`${p50.toFixed(0)}ms`} accent="pink" icon="50" />
                        <StatCard label="P95 TTFB" value={`${p95.toFixed(0)}ms`} accent="pink" icon="95" />
                        <StatCard label="P99 TTFB" value={`${p99.toFixed(0)}ms`} accent="pink" icon="99" />
                    </div>

                    {entries.length === 0
                        ? <div className="neo-card"><div className="empty-state">No latency data yet</div></div>
                        : (
                            <React.Fragment>
                                <div className="neo-card neo-card--strip-pink mb-lg">
                                    <div className="label">Total Latency by Model</div>
                                    <div className="mt-md">
                                        {entries.map(([model, stats]) => (
                                            <div key={model} className="kv-row">
                                                <div style={{ flex: 1 }}>
                                                    <div className="kv-row__key">{model}</div>
                                                    <div className="pct-bar">
                                                        <div className="pct-bar__fill pct-bar__fill--pink"
                                                             style={{ width: `${((stats.avg_latency_ms || 0) / maxLatency * 100).toFixed(0)}%` }} />
                                                    </div>
                                                </div>
                                                <div className="kv-row__value" style={{ marginLeft: 16 }}>
                                                    {(stats.avg_latency_ms || 0).toFixed(0)}ms
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                </div>

                                <table className="neo-table neo-table--pink">
                                    <thead>
                                        <tr>
                                            <th>Model</th>
                                            <th>Avg TTFB</th>
                                            <th>Avg Total</th>
                                            <th>Requests</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {entries.map(([model, stats]) => (
                                            <tr key={model}>
                                                <td className="mono">{model}</td>
                                                <td>
                                                    <span className={`neo-badge ${latencyBadgeClass(stats.avg_ttfb_ms)}`}>
                                                        {(stats.avg_ttfb_ms || 0).toFixed(0)}ms
                                                    </span>
                                                </td>
                                                <td>
                                                    <span className={`neo-badge ${latencyBadgeClass(stats.avg_latency_ms)}`}>
                                                        {(stats.avg_latency_ms || 0).toFixed(0)}ms
                                                    </span>
                                                </td>
                                                <td>
                                                    <span className="neo-badge neo-badge--black">{stats.request_count}</span>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </React.Fragment>
                        )
                    }
                </div>
            );
        }

        /* ------------- Logs ------------- */

        function LogsPage({ data, filter, setFilter, projectFilter, setProjectFilter, projectsData }) {
            const projects = (projectsData && projectsData.projects) ? projectsData.projects : [];
            return (
                <div>
                    <PageHeader
                        title="Logs"
                        subtitle="Recent inference requests"
                        accent="orange"
                    />
                    <div className="filter-bar">
                        <span className="label">Modality</span>
                        <select className="neo-select" value={filter} onChange={(e) => setFilter(e.target.value)}>
                            <option value="">All</option>
                            <option value="stt">STT</option>
                            <option value="llm">LLM</option>
                            <option value="tts">TTS</option>
                        </select>
                        <span className="label">Project</span>
                        <select className="neo-select" value={projectFilter} onChange={(e) => setProjectFilter(e.target.value)}>
                            <option value="">All Projects</option>
                            {projects.map(p => (
                                <option key={p.id} value={p.id}>{p.name}</option>
                            ))}
                        </select>
                        <button className="neo-btn neo-btn--orange">Apply</button>
                        <button className="neo-btn" onClick={() => { setFilter(''); setProjectFilter(''); }}>Reset</button>
                    </div>

                    {!data || data.length === 0
                        ? <div className="neo-card"><div className="empty-state">No requests logged yet</div></div>
                        : (
                            <table className="neo-table neo-table--orange">
                                <thead>
                                    <tr>
                                        <th>Time</th>
                                        <th>Model</th>
                                        <th>Type</th>
                                        <th>Project</th>
                                        <th>Cost</th>
                                        <th>Latency</th>
                                        <th>Status</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data.map((log, i) => (
                                        <tr key={i}>
                                            <td className="mono">
                                                {new Date(log.timestamp * 1000).toLocaleTimeString()}
                                            </td>
                                            <td className="mono">{log.model_id}</td>
                                            <td>
                                                <span className="neo-badge neo-badge--black">
                                                    {(log.modality || '').toUpperCase()}
                                                </span>
                                            </td>
                                            <td>
                                                {log.project_id
                                                    ? <span className="neo-badge neo-badge--info">{log.project_id}</span>
                                                    : <span style={{ color: 'var(--text-secondary)', fontSize: 12 }}>—</span>
                                                }
                                            </td>
                                            <td className="mono">${(log.cost_usd || 0).toFixed(6)}</td>
                                            <td>
                                                <span className={`neo-badge ${latencyBadgeClass(log.total_latency_ms)}`}>
                                                    {(log.total_latency_ms || 0).toFixed(0)}ms
                                                </span>
                                            </td>
                                            <td>
                                                <span className={`neo-badge ${statusBadgeClass(log.status)}`}>
                                                    {log.status}
                                                </span>
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )
                    }

                    <div className="flex-row mt-lg">
                        <button className="neo-btn">← Previous</button>
                        <button className="neo-btn neo-btn--orange">Next →</button>
                    </div>
                </div>
            );
        }

        ReactDOM.createRoot(document.getElementById('root')).render(<App />);
    </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the dashboard frontend."""
    return _HTML_SHELL.replace("__NEO_CSS__", _NEO_CSS)
