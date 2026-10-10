"""Gateway construction rejects a config with a misspelled top-level key."""

import pytest
import yaml

from voicegateway.core.config import ConfigError
from voicegateway.core.gateway import Gateway


def test_config_validation_catches_typos(tmp_path):
    """A typo in config keys raises a clear error naming the bad key."""
    config_path = tmp_path / "bad.yaml"
    config_path.write_text(yaml.dump({"providrs": {"openai": {"api_key": "test"}}}))

    with pytest.raises(ConfigError, match="providrs"):
        Gateway(config_path=str(config_path))
