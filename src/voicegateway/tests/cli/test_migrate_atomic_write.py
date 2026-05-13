"""Tests for ``voicegateway/cli/migrate._atomic_write_text``."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from voicegateway.utils.cli.migrate import _atomic_write_text


def test_atomic_write_creates_new_file(tmp_path):
    target = tmp_path / "voicegw.yaml"
    _atomic_write_text(target, "providers: {}\n")
    assert target.exists()
    assert target.read_text() == "providers: {}\n"
    # No leftover staging file.
    assert not (tmp_path / "voicegw.yaml.tmp").exists()


def test_atomic_write_replaces_existing(tmp_path):
    target = tmp_path / "voicegw.yaml"
    target.write_text("providers:\n  old: yes\n")

    _atomic_write_text(target, "providers:\n  new: yes\n")

    assert target.read_text() == "providers:\n  new: yes\n"
    assert not (tmp_path / "voicegw.yaml.tmp").exists()


def test_atomic_write_creates_parent_directory(tmp_path):
    """The helper handles a non-existent parent dir transparently."""
    target = tmp_path / "deep" / "nested" / "voicegw.yaml"
    _atomic_write_text(target, "ok\n")
    assert target.exists()
    assert target.read_text() == "ok\n"


def test_atomic_write_failure_leaves_existing_target_intact(tmp_path, monkeypatch):
    """If the rename step blows up, the original target is untouched"""
    target = tmp_path / "voicegw.yaml"
    original = "providers:\n  preserved: yes\n"
    target.write_text(original)

    # Patch Path.replace to raise so the rename half fails.
    real_replace = type(target).replace

    def _raise(self, _other):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(type(target), "replace", _raise)

    with pytest.raises(OSError, match="simulated rename failure"):
        _atomic_write_text(target, "providers:\n  new: yes\n")

    # Restore the real method so other assertions can use it.
    monkeypatch.setattr(type(target), "replace", real_replace)

    # Original target is untouched.
    assert target.read_text() == original
    # Staging file is gone.
    assert not (tmp_path / "voicegw.yaml.tmp").exists()


def test_atomic_write_failure_during_write_leaves_no_staging(tmp_path, monkeypatch):
    """Write-side failure (e.g., disk full mid-write) cleans up the"""
    target = tmp_path / "voicegw.yaml"
    target.write_text("preserved\n")

    # Make the write fail by patching open() inside the migrate
    # module to return a file-like that errors on write.
    real_open = open

    fake_handle = MagicMock()
    fake_handle.__enter__ = MagicMock(return_value=fake_handle)
    fake_handle.__exit__ = MagicMock(return_value=False)
    fake_handle.write.side_effect = OSError("disk full")

    def _fake_open(path, *args, **kwargs):
        if str(path).endswith(".yaml.tmp"):
            return fake_handle
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", _fake_open)

    with pytest.raises(OSError, match="disk full"):
        _atomic_write_text(target, "new content\n")

    # Original target untouched, staging cleaned up (or never
    # created — the helper unlinks it either way).
    assert target.read_text() == "preserved\n"
    # Staging file should not exist; if it does, content should not
    # be partial since fake_handle.write didn't actually write.
    staging = tmp_path / "voicegw.yaml.tmp"
    assert not staging.exists()
