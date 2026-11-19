"""Character and word error rates against ground truth (master prompt §11).

Methodology (version ``scriptorium-metrics/1``), applied identically to reference and hypothesis:

1. Human text with editorial markup is first rendered in its *diplomatic* form (what the source
   shows: abbreviations as written, deletions kept, editorial additions dropped). Reference lines
   containing an illegible gap ``{illeg}`` are excluded by default (they cannot be compared) and
   counted in ``lines_excluded``.
2. Unicode normalisation (default NFC, configurable NFC | NFD | NFKC | none). NFC is the default
   so that a precomposed letter and its decomposed sequence count as the same character —
   models such as TraPrInq and CATMuS output NFD while ground truth is often NFC.
3. Optional case folding, punctuation removal and combining-mark removal (all off by default).
4. Whitespace: leading/trailing stripped, internal runs collapsed to one space (always).

Errors are the Levenshtein distance (substitutions + insertions + deletions, unit costs) over
Unicode code points (characters) or over whitespace-separated tokens (words).

* CER = character errors / reference characters; WER = word errors / reference words.
* Line alignment (``alignment = "line"``): reference and hypothesis share the segmentation, each
  line is compared with its own hypothesis; page totals are sums (micro-average). A reference line
  without hypothesis counts all its characters as deletions.
* Page alignment (``alignment = "page"``): used only when segmentations differ — lines are joined in
  reading order with a single space; reading-order differences are then counted as errors.
* Character/word accuracy = 1 − rate. Because insertions can exceed the reference length, the
  rate may exceed 1; accuracy is then reported as *undefined* (``None``) rather than negative.

Nothing here uses model confidence: confidences are not calibrated probabilities and are never
mixed with measured error rates.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Literal

from pydantic import BaseModel
from rapidfuzz.distance import Levenshtein

METHODOLOGY_VERSION = "scriptorium-metrics/1"
_WS = re.compile(r"\s+")


class Normalization(BaseModel):
    unicode: Literal["NFC", "NFD", "NFKC", "none"] = "NFC"
    casefold: bool = False
    remove_punctuation: bool = False
    remove_combining_marks: bool = False
    exclude_gap_lines: bool = True

    def describe(self) -> dict:
        return {"version": METHODOLOGY_VERSION, **self.model_dump()}


def normalize(text: str, n: Normalization) -> str:
    if n.unicode != "none":
        text = unicodedata.normalize(n.unicode, text)
    if n.remove_combining_marks:
        text = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))
        if n.unicode != "none":
            text = unicodedata.normalize(n.unicode, text)
    if n.casefold:
        text = text.casefold()
    if n.remove_punctuation:
        text = "".join(c for c in text if not unicodedata.category(c).startswith("P"))
    return _WS.sub(" ", text).strip()


def char_distance(ref: str, hyp: str) -> int:
    return Levenshtein.distance(ref, hyp)


def word_distance(ref: str, hyp: str) -> int:
    return Levenshtein.distance(ref.split(), hyp.split())


def reference_levenshtein(a, b) -> int:
    """Plain dynamic-programming implementation, used by the test-suite to cross-check rapidfuzz."""
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def _rate(errors: int, n: int) -> float | None:
    return errors / n if n else None


def accuracy(rate: float | None) -> float | None:
    return None if rate is None or rate > 1 else 1 - rate


@dataclass
class LineScore:
    line_id: str
    ref: str
    hyp: str | None
    ref_chars: int
    char_errors: int
    ref_words: int
    word_errors: int


@dataclass
class Score:
    alignment: str
    ref_chars: int = 0
    char_errors: int = 0
    ref_words: int = 0
    word_errors: int = 0
    lines_matched: int = 0
    lines_missing: int = 0
    lines_excluded: int = 0
    lines: list[LineScore] = field(default_factory=list)

    @property
    def cer(self) -> float | None:
        return _rate(self.char_errors, self.ref_chars)

    @property
    def wer(self) -> float | None:
        return _rate(self.word_errors, self.ref_words)

    def summary(self) -> dict:
        return {"alignment": self.alignment, "ref_chars": self.ref_chars, "char_errors": self.char_errors,
                "ref_words": self.ref_words, "word_errors": self.word_errors, "cer": self.cer, "wer": self.wer,
                "char_accuracy": accuracy(self.cer), "word_accuracy": accuracy(self.wer),
                "lines_matched": self.lines_matched, "lines_missing": self.lines_missing,
                "lines_excluded": self.lines_excluded}

    def details(self, max_lines: int = 2000) -> dict:
        return {"lines_excluded": self.lines_excluded,
                "lines": [asdict(ln) for ln in self.lines[:max_lines]]}


def _prepare_ref(text: str, n: Normalization) -> str | None:
    """Render human markup diplomatically; None if the line must be excluded (illegible gap)."""
    from app.editorial import markup

    try:
        nodes_text = markup.render(text, "metrics")
        has_gap = markup.stats(text).illegible > 0
    except markup.MarkupError:
        nodes_text, has_gap = text, False  # imported plain text that happens to contain braces
    if has_gap and n.exclude_gap_lines:
        return None
    return normalize(nodes_text, n)


def score_lines(reference: list[tuple[str, str]], hypothesis: dict[str, str], n: Normalization,
                reference_is_markup: bool = True) -> Score:
    """Line-aligned score. `reference`: [(line_key, text)] in reading order; `hypothesis`: {line_key: text}."""
    sc = Score(alignment="line")
    for key, ref_raw in reference:
        ref = _prepare_ref(ref_raw, n) if reference_is_markup else normalize(ref_raw, n)
        if ref is None:
            sc.lines_excluded += 1
            continue
        hyp_raw = hypothesis.get(key)
        hyp = normalize(hyp_raw, n) if hyp_raw is not None else None
        if hyp is None:
            sc.lines_missing += 1
            ce, we = len(ref), len(ref.split())
        else:
            sc.lines_matched += 1
            ce, we = char_distance(ref, hyp), word_distance(ref, hyp)
        ls = LineScore(key, ref, hyp, len(ref), ce, len(ref.split()), we)
        sc.lines.append(ls)
        sc.ref_chars += ls.ref_chars
        sc.char_errors += ce
        sc.ref_words += ls.ref_words
        sc.word_errors += we
    return sc


def score_page(reference: list[str], hypothesis: list[str], n: Normalization, reference_is_markup: bool = True) -> Score:
    """Page-level score for differing segmentations: lines joined in reading order."""
    sc = Score(alignment="page")
    refs = []
    for r in reference:
        p = _prepare_ref(r, n) if reference_is_markup else normalize(r, n)
        if p is None:
            sc.lines_excluded += 1
        else:
            refs.append(p)
    ref = normalize(" ".join(refs), n)
    hyp = normalize(" ".join(hypothesis), n)
    sc.ref_chars, sc.ref_words = len(ref), len(ref.split())
    sc.char_errors, sc.word_errors = char_distance(ref, hyp), word_distance(ref, hyp)
    sc.lines_matched = len(refs)
    return sc


def aggregate(scores: list[dict]) -> dict:
    """Micro-average of stored results (sums of errors over sums of reference units)."""
    rc = sum(s["ref_chars"] for s in scores)
    ce = sum(s["char_errors"] for s in scores)
    rw = sum(s["ref_words"] for s in scores)
    we = sum(s["word_errors"] for s in scores)
    cer, wer = _rate(ce, rc), _rate(we, rw)
    return {"pages": len(scores), "ref_chars": rc, "char_errors": ce, "ref_words": rw, "word_errors": we,
            "cer": cer, "wer": wer, "char_accuracy": accuracy(cer), "word_accuracy": accuracy(wer)}
