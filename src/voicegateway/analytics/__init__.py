"""Local analytics acceleration.

An opt-in DuckDB read path that attaches the live SQLite file read-only and
runs the dashboard's cost + latency aggregations columnar-fast, returning the
same shapes as the SQLAlchemy repositories. See :mod:`duckdb_reader`.
"""
