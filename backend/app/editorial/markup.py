"""SCRIPTORIUM editorial markup, version 1.

A small, explicit inline notation for scholarly transcription. It is never applied
automatically: researchers type it (or insert it from the editor toolbar). Plain text
without braces is valid markup. Literal braces are written as `\\{` and `\\}`.

| Markup              | Meaning                                   | TEI P5 mapping (Phase 5 export)              |
|---------------------|-------------------------------------------|----------------------------------------------|
| `{?text?}`          | uncertain reading                         | `<unclear>text</unclear>`                    |
| `{illeg}` `{illeg:N}` | illegible passage (N = estimated chars) | `<gap reason="illegible" unit="chars" quantity="N"/>` |
| `{+text+}`          | editorial addition (supplied by editor)   | `<supplied>text</supplied>`                  |
| `{-text-}`          | deleted / struck through in the source    | `<del>text</del>`                            |
| `{abbr|expansion}`  | abbreviation with the editor's expansion  | `<choice><abbr>abbr</abbr><expan>expansion</expan></choice>` |
| `{^text^}`          | superscript / interlinear in the source   | `<hi rend="superscript">text</hi>`           |

Nesting: uncertain, addition, deletion and superscript may contain other elements;
an abbreviation's two parts and an illegible marker contain plain text only.

Renderings (all derived; the stored text is never rewritten):
* `diplomatic` — what the source shows: abbreviations as written, deletions kept,
  editorial additions dropped, illegible → `[…]`, no other markup.
* `expanded`   — like diplomatic, but abbreviations replaced by the editor's expansions.
* `reading`    — expanded, deletions dropped, additions included (a reading text).
* `metrics`    — diplomatic form, used for CER/WER against ground truth (Phase 5).
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Literal, Union

MARKUP_VERSION = 1

Kind = Literal["uncertain", "addition", "deletion", "superscript"]
_PAIRED: dict[str, Kind] = {"?": "uncertain", "+": "addition", "-": "deletion", "^": "superscript"}


class MarkupError(ValueError):
    def __init__(self, message: str, position: int):
        super().__init__(f"{message} (at character {position + 1})")
        self.message, self.position = message, position


@dataclass
class Text:
    value: str


@dataclass
class Span:
    kind: Kind
    children: list[Node] = field(default_factory=list)


@dataclass
class Abbr:
    abbr: str
    expansion: str


@dataclass
class Gap:
    extent: int | None = None


Node = Union[Text, Span, Abbr, Gap]


class _Parser:
    def __init__(self, s: str):
        self.s, self.i = s, 0

    def parse(self) -> list[Node]:
        nodes = self._seq(closer=None)
        return nodes

    def _plain_until(self, stops: str) -> str:
        out = []
        while self.i < len(self.s):
            c = self.s[self.i]
            if c == "\\" and self.i + 1 < len(self.s) and self.s[self.i + 1] in "{}|\\":
                out.append(self.s[self.i + 1])
                self.i += 2
                continue
            if c in stops:
                break
            if c == "{":
                raise MarkupError("nested markup is not allowed here", self.i)
            out.append(c)
            self.i += 1
        return "".join(out)

    def _seq(self, closer: str | None) -> list[Node]:
        nodes: list[Node] = []
        buf: list[str] = []

        def flush():
            if buf:
                nodes.append(Text("".join(buf)))
                buf.clear()

        while self.i < len(self.s):
            c = self.s[self.i]
            if closer and c == closer and self.i + 1 < len(self.s) and self.s[self.i + 1] == "}":
                flush()
                self.i += 2
                return nodes
            if c == "\\" and self.i + 1 < len(self.s) and self.s[self.i + 1] in "{}|\\":
                buf.append(self.s[self.i + 1])
                self.i += 2
                continue
            if c == "}":
                raise MarkupError("closing brace without an opening one", self.i)
            if c == "{":
                flush()
                nodes.append(self._element())
                continue
            buf.append(c)
            self.i += 1
        if closer:
            raise MarkupError(f"unclosed element: expected '{closer}}}'", self.i)
        flush()
        return nodes

    def _element(self) -> Node:
        start = self.i
        self.i += 1  # '{'
        if self.i >= len(self.s):
            raise MarkupError("unclosed '{'", start)
        c = self.s[self.i]
        if c in _PAIRED:
            self.i += 1
            children = self._seq(closer=c)
            if not children:
                raise MarkupError(f"empty {_PAIRED[c]} element", start)
            return Span(_PAIRED[c], children)
        if self.s.startswith("illeg", self.i):
            self.i += 5
            extent = None
            if self.i < len(self.s) and self.s[self.i] == ":":
                self.i += 1
                j = self.i
                while self.i < len(self.s) and self.s[self.i].isdigit():
                    self.i += 1
                if j == self.i:
                    raise MarkupError("{illeg:N} needs a number", j)
                extent = int(self.s[j:self.i])
            if self.i >= len(self.s) or self.s[self.i] != "}":
                raise MarkupError("expected '}' after illeg", self.i)
            self.i += 1
            return Gap(extent)
        abbr = self._plain_until("|}")
        if self.i >= len(self.s) or self.s[self.i] != "|":
            raise MarkupError("unknown element: use {?…?} {+…+} {-…-} {^…^} {illeg} or {abbreviation|expansion}", start)
        self.i += 1
        expan = self._plain_until("}")
        if self.i >= len(self.s):
            raise MarkupError("unclosed abbreviation", start)
        self.i += 1
        if not abbr or not expan:
            raise MarkupError("abbreviation and expansion must both be non-empty", start)
        return Abbr(abbr, expan)


def parse(text: str) -> list[Node]:
    return _Parser(text).parse()


def validate(text: str) -> MarkupError | None:
    try:
        parse(text)
        return None
    except MarkupError as e:
        return e


Mode = Literal["diplomatic", "expanded", "reading", "metrics"]


def render(text: str, mode: Mode = "diplomatic") -> str:
    return "".join(_render(n, mode) for n in parse(text))


def _render(n: Node, mode: Mode) -> str:
    if isinstance(n, Text):
        return n.value
    if isinstance(n, Gap):
        return "[…]"
    if isinstance(n, Abbr):
        return n.expansion if mode in ("expanded", "reading") else n.abbr
    inner = "".join(_render(c, mode) for c in n.children)
    if n.kind == "addition":
        return inner if mode == "reading" else ""
    if n.kind == "deletion":
        return "" if mode == "reading" else inner
    return inner


@dataclass
class Stats:
    uncertain: int = 0
    illegible: int = 0
    additions: int = 0
    deletions: int = 0
    abbreviations: int = 0
    superscripts: int = 0


_STAT_FIELD = {"uncertain": "uncertain", "addition": "additions", "deletion": "deletions", "superscript": "superscripts"}


def stats(text: str) -> Stats:
    st = Stats()

    def walk(ns: list[Node]):
        for n in ns:
            if isinstance(n, Gap):
                st.illegible += 1
            elif isinstance(n, Abbr):
                st.abbreviations += 1
            elif isinstance(n, Span):
                attr = _STAT_FIELD[n.kind]
                setattr(st, attr, getattr(st, attr) + 1)
                walk(n.children)

    walk(parse(text))
    return st


def apply_unicode_policy(text: str, policy: str | None) -> str:
    """Explicit, configurable normalisation of HUMAN input only (never applied to HTR output)."""
    if policy in ("NFC", "NFD", "NFKC", "NFKD"):
        return unicodedata.normalize(policy, text)
    return text
