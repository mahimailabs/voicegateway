"""One provider, one stored id, whatever derived it (#279).

The Call detail modal rendered six chips for three providers because the two
derivation paths disagreed about spelling and nothing normalized before the
write. These tests pin the agreement rather than the implementation: each case
is a pair of spellings that must collapse to the same id.
"""

from __future__ import annotations

import pytest

from voicegateway.core.provider_names import canonical_provider


@pytest.mark.parametrize(
    ("module_derived", "name_derived"),
    [
        ("cartesia", "Cartesia"),
        ("cartesia", "CartesiaTTSService"),
        ("cartesia", "CartesiaTTSService#0"),
        ("deepgram", "Deepgram"),
        ("deepgram", "DeepgramSTTService#1"),
        ("openai", "OpenAILLMService"),
        ("google", "Gemini"),
        ("google", "GoogleLLMService"),
    ],
)
def test_both_derivations_collapse_to_one_id(module_derived, name_derived):
    """The module segment and the class-name fallback must agree."""
    assert canonical_provider(module_derived) == canonical_provider(name_derived)


def test_the_agreed_id_is_the_lowercase_module_segment():
    """Agreement is not enough: they must agree on the storable form."""
    assert canonical_provider("CartesiaTTSService#0") == "cartesia"
    assert canonical_provider("DeepgramSTTService") == "deepgram"
    assert canonical_provider("Gemini") == "google"


@pytest.mark.parametrize("raw", ["", "   ", None, "#0", "___"])
def test_unusable_input_is_empty_not_a_provider(raw):
    """EOU rows write an empty provider; it must never become a chip."""
    assert canonical_provider(raw) == ""


def test_a_bare_suffix_is_not_stripped_to_nothing():
    """``Service`` alone is a real (if odd) name, not an empty provider."""
    assert canonical_provider("Service") == "service"


def test_an_unknown_provider_passes_through_lowercased():
    """No allowlist: a provider VG has never seen still records.

    ``sarvam`` is real and in use but absent from voice-prices 0.6.0, so this
    is not a hypothetical: an unpriced provider must still meter under a
    predictable id rather than under a class name.
    """
    assert canonical_provider("SomeNewVendor") == "somenewvendor"
    assert canonical_provider("sarvam") == "sarvam"
    assert canonical_provider("SarvamTTSService#0") == "sarvam"


def test_the_catalog_is_the_authority_for_ids_it_carries():
    """VG must store the id the catalog prices, not a second vocabulary.

    ``component_identity`` builds ``model_id`` as ``provider/model``, so the
    provider half becomes the pricing key. If this module invented its own
    spelling, VG would meter under an id the catalog cannot resolve. Asserting
    against the live catalog means a rename upstream fails here rather than
    silently unpricing traffic.
    """
    from voice_prices.data_snapshot import get_snapshot

    catalog_ids = {p.id for p in get_snapshot().providers}
    assert "google" in catalog_ids and "gemini" not in catalog_ids
    for provider_id in catalog_ids:
        assert canonical_provider(provider_id) == provider_id


def test_branded_spellings_resolve_the_way_the_catalog_resolves_them():
    """The alias table lives in voice-prices; VG does not keep a second one."""
    from voice_prices.data_snapshot import find_provider_by_id, get_snapshot

    providers = get_snapshot().providers
    for raw in ("Gemini", "googlegenai", "OpenAILLMService", "DeepgramSTTService"):
        expected = find_provider_by_id(providers, raw)
        assert expected is not None, f"catalog no longer resolves {raw!r}"
        assert canonical_provider(raw) == expected.id
