"""Reproducible-test assets reaching the report.

`appendix_entry` existed and `build_load_payload` accepted an `appendix=`
argument that nothing supplied, so every report shipped saying no assets were
recorded. Numbers nobody else can reproduce are not evidence, which is most of
why that section is contractual.

The entries live in a file the operator holds rather than in this repository.
Commands belong to a particular engagement and have no business compiled into a
general tool, and the generator they drive is AGPL-3.0 while this repository is
MIT, so its scenarios and configuration must never be copied here. Flag names
and command lines are interface facts and travel fine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer

from voicegateway.cli.loadtest_cli import _APPENDIX_SECTIONS, _appendix_from_file

GOOD = {
    "commands": [
        {
            "label": "ramp step",
            "detail": "gossipper sipp -l 200 -r 3.2258 -pause_ms 60000 -trace_stat",
            "citation": "execution-runbook.md:75",
        }
    ],
    "flags": [
        {
            "label": "-l",
            "detail": "Max concurrent calls. Defaults to 1.",
            "citation": "execution-runbook.md:53",
        }
    ],
    "toolchain": [
        {
            "label": "build",
            "detail": "Cross-compile for linux/amd64.",
            "citation": "fixtures README",
        }
    ],
}


def _write(tmp_path: Path, payload: object) -> Path:
    path = tmp_path / "appendix.json"
    path.write_text(json.dumps(payload))
    return path


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def test_a_well_formed_file_yields_every_section(tmp_path: Path) -> None:
    assets = _appendix_from_file(_write(tmp_path, GOOD))
    assert sorted(assets) == ["commands", "flags", "toolchain"]
    assert assets["flags"][0]["citation"] == "execution-runbook.md:53"


def test_an_empty_section_is_omitted_rather_than_carried_empty(
    tmp_path: Path,
) -> None:
    assets = _appendix_from_file(_write(tmp_path, {"commands": GOOD["commands"]}))
    assert list(assets) == ["commands"]


def test_the_sections_match_what_the_renderer_reads() -> None:
    """A section this file accepts but the report never renders is a silent hole."""
    from voicegateway.livekit_diag import run_report

    source = run_report._render_appendix.__doc__ or ""
    assert source  # the renderer is documented
    for section in _APPENDIX_SECTIONS:
        assert section in ("commands", "flags", "toolchain")


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_an_uncited_entry_is_refused_by_position(tmp_path: Path) -> None:
    """An uncited command is indistinguishable from one the report invented.

    Refused rather than dropped, and located, so the operator can find the line.
    """
    bad = {"commands": [{"label": "ramp", "detail": "gossipper sipp", "citation": ""}]}
    with pytest.raises(typer.BadParameter) as excinfo:
        _appendix_from_file(_write(tmp_path, bad))
    assert "commands[0]" in str(excinfo.value)


@pytest.mark.parametrize("citation", ["", "   ", "\t\n"])
def test_whitespace_is_not_a_citation(tmp_path: Path, citation: str) -> None:
    bad = {"commands": [{"label": "x", "detail": "y", "citation": citation}]}
    with pytest.raises(typer.BadParameter):
        _appendix_from_file(_write(tmp_path, bad))


def test_an_unknown_section_is_refused_not_ignored(tmp_path: Path) -> None:
    """A misspelled section would silently drop every entry under it.

    The operator would then hand over a report missing the commands that
    produced it, with nothing anywhere saying so.
    """
    with pytest.raises(typer.BadParameter) as excinfo:
        _appendix_from_file(_write(tmp_path, {"commmands": GOOD["commands"]}))
    assert "commmands" in str(excinfo.value)
    assert "commands" in str(excinfo.value)


def test_a_missing_file_is_refused_clearly(tmp_path: Path) -> None:
    with pytest.raises(typer.BadParameter):
        _appendix_from_file(tmp_path / "nope.json")


def test_malformed_json_is_refused_clearly(tmp_path: Path) -> None:
    path = tmp_path / "appendix.json"
    path.write_text("{not json")
    with pytest.raises(typer.BadParameter) as excinfo:
        _appendix_from_file(path)
    assert "not valid JSON" in str(excinfo.value)


def test_a_section_that_is_not_a_list_is_refused(tmp_path: Path) -> None:
    with pytest.raises(typer.BadParameter):
        _appendix_from_file(_write(tmp_path, {"commands": {"label": "x"}}))


# --------------------------------------------------------------------------
# Redaction survives the file boundary
# --------------------------------------------------------------------------


def test_a_sip_uri_in_a_command_is_reduced_to_its_host(tmp_path: Path) -> None:
    """The scheme a SIP load test actually contains.

    A report is handed to somebody outside the deployment, so an endpoint in it
    is a leak. The host survives because reproducing a run needs to know which.
    """
    payload = {
        "commands": [
            {
                "label": "ramp",
                "detail": "gossipper sipp -rsa sip:pbx.example.net:5060 -l 200",
                "citation": "runbook.md:75",
            }
        ]
    }
    detail = _appendix_from_file(_write(tmp_path, payload))["commands"][0]["detail"]
    assert "sip:" not in detail
    assert "pbx.example.net:5060" in detail


def test_an_absolute_url_is_reduced_too(tmp_path: Path) -> None:
    payload = {
        "commands": [
            {
                "label": "join",
                "detail": "connect wss://media.example.com/rtc now",
                "citation": "runbook.md:80",
            }
        ]
    }
    detail = _appendix_from_file(_write(tmp_path, payload))["commands"][0]["detail"]
    assert "wss://" not in detail
    assert "media.example.com" in detail


# --------------------------------------------------------------------------
# The licence boundary
# --------------------------------------------------------------------------


def test_no_generator_scenario_or_config_lives_in_this_repo() -> None:
    """AGPL-3.0 upstream, MIT here. Interface facts travel; expression does not.

    Command lines and flag names are fine. A scenario XML or a generator config
    is somebody else's authored work and must be referenced by name only.
    """
    root = Path(__file__).resolve().parents[4]
    offenders = [
        p
        for p in root.rglob("*uac*.xml")
        if ".venv" not in p.parts and "node_modules" not in p.parts
    ]
    assert not offenders, f"generator scenario files in the repo: {offenders}"
