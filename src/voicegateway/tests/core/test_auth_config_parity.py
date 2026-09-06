"""The two AuthConfig definitions must not drift apart.

``schemas.config_schema.AuthConfig`` is pydantic and validates the YAML.
``core.config.AuthConfig`` is a dataclass and is what the Gateway carries at
runtime. They are parallel declarations of one thing, bridged by a
field-by-field construction in ``core.config``.

Adding a field to one and not the other is silent: the YAML validates, the
attribute simply is not there, and the first reader gets an AttributeError at
runtime on whatever code path happens to touch it. That is exactly what
happened while wiring auth.local_development, and it was caught by a CLI test
rather than by either config module.
"""

from __future__ import annotations

import dataclasses

from voicegateway.core.config import AuthConfig as RuntimeAuthConfig
from voicegateway.core.config import GatewayConfig
from voicegateway.schemas.config_schema import AuthConfig as SchemaAuthConfig


def test_both_auth_configs_declare_the_same_fields():
    schema_fields = set(SchemaAuthConfig.model_fields)
    runtime_fields = {f.name for f in dataclasses.fields(RuntimeAuthConfig)}
    assert schema_fields == runtime_fields, (
        f"only in schema: {sorted(schema_fields - runtime_fields)}; "
        f"only in runtime: {sorted(runtime_fields - schema_fields)}"
    )


def test_both_agree_on_defaults_for_the_auth_mode_fields():
    """A field present in both but defaulting differently is worse than absent."""
    schema = SchemaAuthConfig()
    runtime = RuntimeAuthConfig()
    assert schema.local_development == runtime.local_development is False
    assert schema.enforcement == runtime.enforcement == "warn"


def test_the_yaml_bridge_carries_the_new_fields(tmp_path):
    """Declaring a field is not enough; core.config builds this one by hand."""
    import yaml

    path = tmp_path / "voicegw.yaml"
    path.write_text(
        yaml.dump(
            {
                "providers": {"openai": {"api_key": "k"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
                "auth": {"local_development": True, "enforcement": "enforce"},
            }
        )
    )
    config = GatewayConfig.load(str(path))
    assert config.auth.local_development is True
    assert config.auth.enforcement == "enforce"


def test_the_bridge_defaults_when_the_auth_block_is_absent(tmp_path):
    import yaml

    path = tmp_path / "voicegw.yaml"
    path.write_text(
        yaml.dump(
            {
                "providers": {"openai": {"api_key": "k"}},
                "models": {"stt": {}, "llm": {}, "tts": {}},
                "projects": {},
                "fallbacks": {"stt": [], "llm": [], "tts": []},
            }
        )
    )
    config = GatewayConfig.load(str(path))
    assert config.auth.local_development is False
    assert config.auth.enforcement == "warn"
