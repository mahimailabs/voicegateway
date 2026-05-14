"""The wedge gate: voicegateway.inference must mirror livekit.agents.inference.

VG's signature is a strict subset of LK's: the four LK kwargs that VG never
honoured (``api_secret``, ``inference_class``, ``fallback``, ``conn_options``)
are dropped. Every kwarg VG keeps must appear with the same kind and default
on the LK side.
"""

from __future__ import annotations

import inspect

import pytest
from livekit.agents.inference import LLM as LK_LLM
from livekit.agents.inference import STT as LK_STT
from livekit.agents.inference import TTS as LK_TTS

from voicegateway.inference import LLM as VG_LLM
from voicegateway.inference import STT as VG_STT
from voicegateway.inference import TTS as VG_TTS

_MODALITIES = [
    pytest.param(VG_STT, LK_STT, id="STT"),
    pytest.param(VG_LLM, LK_LLM, id="LLM"),
    pytest.param(VG_TTS, LK_TTS, id="TTS"),
]


@pytest.mark.parametrize(("vg_cls", "lk_cls"), _MODALITIES)
def test_vg_params_are_subset_of_lk(vg_cls, lk_cls):
    """Every kwarg VG accepts must exist on LK with the same name + order."""
    vg_names = list(inspect.signature(vg_cls).parameters.keys())
    lk_names = list(inspect.signature(lk_cls).parameters.keys())
    missing_on_lk = [name for name in vg_names if name not in lk_names]
    assert not missing_on_lk, (
        f"{vg_cls.__name__} carries names that LK does not.\n"
        f"  LK (livekit-agents): {lk_names}\n"
        f"  VG (voicegateway):   {vg_names}\n"
        f"  Names on VG missing from LK: {missing_on_lk}"
    )
    # Order check: VG's names must appear on LK in the same relative order.
    last_index = -1
    for name in vg_names:
        idx = lk_names.index(name)
        assert idx > last_index, (
            f"{vg_cls.__name__} param order drifted from LK at {name!r}.\n"
            f"  LK: {lk_names}\n"
            f"  VG: {vg_names}"
        )
        last_index = idx


@pytest.mark.parametrize(("vg_cls", "lk_cls"), _MODALITIES)
def test_parameter_kinds_match(vg_cls, lk_cls):
    vg_params = inspect.signature(vg_cls).parameters
    lk_params = inspect.signature(lk_cls).parameters
    mismatches = []
    for name, vg_param in vg_params.items():
        lk_param = lk_params.get(name)
        if lk_param is None:
            continue
        if lk_param.kind != vg_param.kind:
            mismatches.append(
                f"  {name}: LK={lk_param.kind.name} VG={vg_param.kind.name}"
            )
    assert not mismatches, (
        f"Parameter kinds drifted for {vg_cls.__name__}:\n" + "\n".join(mismatches)
    )


@pytest.mark.parametrize(("vg_cls", "lk_cls"), _MODALITIES)
def test_parameter_defaults_match(vg_cls, lk_cls):
    vg_params = inspect.signature(vg_cls).parameters
    lk_params = inspect.signature(lk_cls).parameters
    mismatches = []
    for name, vg_param in vg_params.items():
        lk_param = lk_params.get(name)
        if lk_param is None:
            continue
        # We compare defaults by repr because NOT_GIVEN is a singleton
        # imported from the same livekit.agents.types module on both
        # sides, so identity AND repr agree. inspect._empty (no default)
        # also reprs deterministically.
        if repr(lk_param.default) != repr(vg_param.default):
            mismatches.append(
                f"  {name}: LK={lk_param.default!r} VG={vg_param.default!r}"
            )
    assert not mismatches, (
        f"Parameter defaults drifted for {vg_cls.__name__}:\n" + "\n".join(mismatches)
    )


def test_public_module_exposes_three_modalities():
    """voicegateway.inference must expose STT, LLM, TTS as importable"""
    from voicegateway import inference

    assert inference.STT is VG_STT
    assert inference.LLM is VG_LLM
    assert inference.TTS is VG_TTS

    public = set(inference.__all__)
    assert {"STT", "LLM", "TTS"}.issubset(public)
