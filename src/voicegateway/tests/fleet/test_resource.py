"""Unit tests for the fleet memory sampler."""

from __future__ import annotations

from voicegateway.fleet import resource


def test_sample_memory_returns_plausible_values() -> None:
    rss, total = resource.sample_memory()
    assert isinstance(rss, int) and rss > 0
    assert isinstance(total, int) and total > 0
    assert rss <= total  # a process cannot use more than the ceiling


def test_cgroup_limit_reads_v2_integer(tmp_path) -> None:
    p = tmp_path / "memory.max"
    p.write_text("536870912\n")  # 512 MiB, below any real machine's total
    assert (
        resource._read_cgroup_limit(v2_path=str(p), v1_path=str(tmp_path / "nope"))
        == 536870912
    )


def test_cgroup_limit_max_means_unlimited(tmp_path) -> None:
    p = tmp_path / "memory.max"
    p.write_text("max\n")
    assert (
        resource._read_cgroup_limit(v2_path=str(p), v1_path=str(tmp_path / "nope"))
        is None
    )


def test_cgroup_limit_missing_files_returns_none(tmp_path) -> None:
    assert (
        resource._read_cgroup_limit(
            v2_path=str(tmp_path / "a"), v1_path=str(tmp_path / "b")
        )
        is None
    )


def test_cgroup_limit_ignores_limit_at_or_above_total(tmp_path) -> None:
    # A limit >= system total is "no real cap" (the v1 unlimited sentinel is huge).
    p = tmp_path / "memory.max"
    p.write_text("9223372036854771712\n")
    assert (
        resource._read_cgroup_limit(v2_path=str(p), v1_path=str(tmp_path / "nope"))
        is None
    )
