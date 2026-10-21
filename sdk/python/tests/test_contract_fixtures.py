"""The SDK's tests read the same conformance fixtures as the engine (sdk/contract/examples)."""

import json
from pathlib import Path

import pytest
from conftest import CONTRACT
from jsonschema import Draft202012Validator

VALID = sorted((CONTRACT / "examples" / "valid").glob("*.json"))
INVALID = sorted((CONTRACT / "examples" / "invalid").glob("*.json"))


def test_fixtures_exist() -> None:
    assert VALID and INVALID


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.name)
def test_valid_fixture_passes(path: Path, validator: Draft202012Validator) -> None:
    validator.validate(json.loads(path.read_text()))


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.name)
def test_invalid_fixture_fails(path: Path, validator: Draft202012Validator) -> None:
    assert not validator.is_valid(json.loads(path.read_text()))
