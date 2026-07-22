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


def test_config_home_fallback(tmp_path, monkeypatch):
    """With no flags/env/CWD config, the resolver reads the config home.

    This is the daemon case: launchd runs ``voicegw serve`` with a home working
    directory, so ``./voicegw.yaml`` does not exist; creds must still resolve
    from ~/.config/voicegateway/voicegw.yaml.
    """
    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "VOICEGW_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)  # ensure ./voicegw.yaml is absent
    monkeypatch.setenv("HOME", str(tmp_path))
    home_cfg = tmp_path / ".config" / "voicegateway" / "voicegw.yaml"
    home_cfg.parent.mkdir(parents=True)
    home_cfg.write_text("livekit:\n  url: wss://home\n  api_key: hk\n  api_secret: hs\n")
    c = resolve_creds(None, None, None)
    assert c.url == "wss://home" and c.api_key == "hk" and c.api_secret == "hs"


def test_missing_raises(tmp_path, monkeypatch):
    for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "VOICEGW_CONFIG"):
        monkeypatch.delenv(k, raising=False)
    # Isolate HOME + CWD so neither ./voicegw.yaml nor the real config-home file
    # can satisfy the resolver.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    with pytest.raises(CredsError):
        resolve_creds(None, None, None)
