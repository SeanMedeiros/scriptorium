"""Transcription policies (master prompt §10).

A policy states the editorial conventions of a transcription. It is stored on the project
(default), copied into each transcription at creation and recorded on every revision, so
that every saved line can be interpreted later. Policies are *enforced* on human edits:
markup that the policy forbids is rejected with an explicit message. They never modify the
original HTR output and never expand abbreviations automatically.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.editorial import markup


class TranscriptionPolicy(BaseModel):
    type: Literal["diplomatic", "semi-diplomatic", "graphematic", "normalized"] = "semi-diplomatic"
    # Normalisation applied to text typed by people (HTR output is never normalised).
    unicode_input: Literal["preserve", "NFC", "NFD"] = "preserve"
    abbreviations: Literal["preserve", "mark-expansions"] = "mark-expansions"
    historical_spelling: Literal["preserve", "modernise-in-normalized-layer"] = "preserve"
    superscripts: Literal["mark", "inline"] = "mark"
    ligatures: Literal["preserve", "dissolve-in-normalized-layer"] = "preserve"
    deletions: Literal["record", "omit"] = "record"
    illegible: Literal["mark-with-extent", "mark"] = "mark-with-extent"
    editorial_additions: Literal["allowed", "not-allowed"] = "allowed"
    uncertain_readings: Literal["mark", "not-marked"] = "mark"

    @classmethod
    def for_model(cls, unicode_normalization: str | None, convention: str | None) -> "TranscriptionPolicy":
        """Defaults that follow the recognition model's *documented* conventions."""
        p = cls()
        if unicode_normalization in ("NFC", "NFD"):
            p.unicode_input = unicode_normalization  # keep human edits in the model's form
        if convention and "graphematic" in convention.lower():
            p.type, p.abbreviations = "graphematic", "preserve"
        return p


def check(text: str, policy: TranscriptionPolicy) -> str | None:
    """Return an error message if `text` uses markup the policy forbids."""
    err = markup.validate(text)
    if err:
        return err.message + f" (at character {err.position + 1})"
    st = markup.stats(text)
    if policy.abbreviations == "preserve" and st.abbreviations:
        return "This transcription preserves abbreviations as written: {abbreviation|expansion} is not allowed by its policy"
    if policy.editorial_additions == "not-allowed" and st.additions:
        return "Editorial additions {+…+} are not allowed by this transcription's policy"
    if policy.deletions == "omit" and st.deletions:
        return "This policy omits deleted text: {-…-} is not allowed"
    if policy.superscripts == "inline" and st.superscripts:
        return "This policy writes superscripts inline: {^…^} is not allowed"
    if policy.uncertain_readings == "not-marked" and st.uncertain:
        return "Uncertain readings are not marked under this policy"
    if policy.illegible == "mark-with-extent":
        for node in _gaps(markup.parse(text)):
            if node.extent is None:
                return "This policy requires an extent for illegible passages: use {illeg:N}"
    return None


def _gaps(nodes):
    for n in nodes:
        if isinstance(n, markup.Gap):
            yield n
        elif isinstance(n, markup.Span):
            yield from _gaps(n.children)
