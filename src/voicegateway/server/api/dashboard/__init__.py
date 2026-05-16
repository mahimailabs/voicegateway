"""Dashboard endpoints folded into the daemon.

Each module under this package contributes one APIRouter that
``server.routes.dashboard_router`` aggregates under the ``/api`` prefix.
The daemon serves these alongside ``/v1/*`` (core API), so a single
process and port satisfy both the dashboard UI and the public HTTP API.
"""

__all__: list[str] = []
