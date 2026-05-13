"""The wedge gate: voicegateway.inference must mirror livekit.agents.inference."""

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
def test_parameter_names_and_order_match(vg_cls, lk_cls):
    vg_names = list(inspect.signature(vg_cls).parameters.keys())
    lk_names = list(inspect.signature(lk_cls).parameters.keys())
    assert vg_names == lk_names, (
        f"Parameter names/order drifted for {vg_cls.__name__}.\n"
        f"  LK (livekit-agents): {lk_names}\n"
        f"  VG (voicegateway):   {vg_names}"
    )


@pytest.mark.parametrize(("vg_cls", "lk_cls"), _MODALITIES)
def test_parameter_kinds_match(vg_cls, lk_cls):
    vg_params = inspect.signature(vg_cls).parameters
    lk_params = inspect.signature(lk_cls).parameters
    mismatches = []
    for name, lk_param in lk_params.items():
        vg_param = vg_params.get(name)
        if vg_param is None:
            mismatches.append(f"  {name}: missing on VG (LK={lk_param.kind.name})")
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
    for name, lk_param in lk_params.items():
        vg_param = vg_params.get(name)
        if vg_param is None:
            mismatches.append(
                f"  {name}: missing on VG (LK default={lk_param.default!r})"
            )
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
