import pytest

from voicegateway.livekit_diag.config import CredsError, resolve_creds


def test_flags_win(monkeypatch):
    monkeypatch.setenv("LIVEKIT_URL", "wss://env")
    c = resolve_creds("wss://flag", "k", "s")
    assert (c.url, c.api_key, c.api_secret) == ("wss://flag", "k", "s")


def test_env_fallback(monkeypatch):
    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "VOICEGW_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LIVEKIT_URL", "wss://env")
    monkeypatch.setenv("LIVEKIT_API_KEY", "ek")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "es")
    c = resolve_creds(None, None, None)
    assert c.url == "wss://env" and c.api_key == "ek" and c.api_secret == "es"


def test_yaml_fallback(tmp_path, monkeypatch):
    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "VOICEGW_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    cfg = tmp_path / "voicegw.yaml"
    cfg.write_text("livekit:\n  url: wss://yaml\n  api_key: yk\n  api_secret: ys\n")
    c = resolve_creds(None, None, None, config_path=str(cfg))
    assert c.url == "wss://yaml" and c.api_key == "yk" and c.api_secret == "ys"


def test_missing_raises(monkeypatch):
    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "VOICEGW_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(CredsError):
        resolve_creds(None, None, None)
