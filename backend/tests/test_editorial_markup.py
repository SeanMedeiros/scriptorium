import unicodedata

import pytest

from app.editorial import markup
from app.editorial.policy import TranscriptionPolicy, check
from app.editorial.service import escape

S = "{q̄|que} {?hũa?} pessoa {illeg:3} {-dixe-}{+disse+} o {^dito^} \\{x\\}"


def test_renderings():
    assert markup.render(S, "diplomatic") == "q̄ hũa pessoa […] dixe o dito {x}"
    assert markup.render(S, "expanded") == "que hũa pessoa […] dixe o dito {x}"
    assert markup.render(S, "reading") == "que hũa pessoa […] disse o dito {x}"


def test_plain_text_is_valid_and_unchanged():
    t = "Aquesta uia er longa ⁊ ala finbona"
    assert markup.validate(t) is None and markup.render(t) == t


def test_stats():
    st = markup.stats(S)
    assert (st.uncertain, st.illegible, st.additions, st.deletions, st.abbreviations, st.superscripts) == (1, 1, 1, 1, 1, 1)


def test_nesting_rules():
    assert markup.render("{?a{-b-}c?}", "reading") == "ac"
    assert markup.validate("{a{?b?}|x}") is not None  # no markup inside abbreviations


@pytest.mark.parametrize("bad,fragment", [
    ("{?abc", "unclosed"), ("abc}", "closing brace"), ("{x}", "unknown element"), ("{|x}", "non-empty"),
    ("{??}", "empty"), ("{illeg:}", "number"), ("{illeg:3", "expected '}'"),
])
def test_errors_have_positions(bad, fragment):
    err = markup.validate(bad)
    assert err is not None and fragment in err.message and err.position >= 0


@pytest.mark.parametrize("htr", ["a {b} c", "x|y", "back\\slash", "⁊ ꝑ q̄ ", "{?not markup?}"])
def test_htr_text_round_trips_through_escaping(htr):
    """Initial versions copy HTR output verbatim: escaping must make render() return it exactly."""
    assert markup.render(escape(htr), "diplomatic") == htr


def test_unicode_policy_is_explicit():
    nfc = unicodedata.normalize("NFC", "hũa")
    assert markup.apply_unicode_policy(nfc, "preserve") == nfc
    assert markup.apply_unicode_policy(nfc, "NFD") == unicodedata.normalize("NFD", nfc) != nfc


def test_policy_enforcement():
    graph = TranscriptionPolicy.for_model("NFD", "graphematic; abbreviations not resolved")
    assert graph.type == "graphematic" and graph.unicode_input == "NFD" and graph.abbreviations == "preserve"
    assert "abbreviations" in check("{q̄|que}", graph)
    assert check("q̄", graph) is None
    p = TranscriptionPolicy(editorial_additions="not-allowed", deletions="omit", superscripts="inline")
    assert "additions" in check("{+a+}", p) and "deleted" in check("{-a-}", p) and "superscripts" in check("{^a^}", p)
    assert "extent" in check("{illeg}", TranscriptionPolicy())
    assert check("{illeg}", TranscriptionPolicy(illegible="mark")) is None
