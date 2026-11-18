"""Deterministic injection rules (R3) and HTML visible/hidden text extraction (T6).

Rules are data: a name, a regular expression over normalized text, the languages it
covers and its scope (`any` text, or `hidden` HTML text only). Normalization folds
case, compatibility forms and diacritics and drops invisible format characters, so
the patterns are plain ASCII and `Ignoruj předchozí` matches like `ignoruj predchozi`.

Patterns are deliberately narrower than the plain words in R3 where ordinary job
texts use them (`act as a liaison`, `use the tools`), so benign profiles stay clean.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from bs4.element import CData, Declaration, Doctype, ProcessingInstruction

Scope = Literal["any", "hidden"]

HIDDEN_PREFIX = "hidden:"
ZERO_WIDTH_RULE = "zero_width_in_word"
BIDI_RULE = "bidi_control"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    languages: tuple[str, ...]
    scope: Scope = "any"


def _rx(*alternatives: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{a})" for a in alternatives), re.MULTILINE)


# Up to `n` intervening words between a verb and its object.
def _gap(n: int) -> str:
    return rf"(?:\W+\w+){{0,{n}}}?\W+"


_EN_IGNORE = r"\b(?:ignore|disregard|forget|override|bypass)\b"
_EN_INSTR = r"(?:instructions?|prompts?|directives?|guidelines|system message)\b"
_CS_IGNORE = r"\b(?:ignor\w*|zapomen\w*|nedbej\w*|prepis(?:te)?|obejdi(?:te)?)"
_CS_INSTR = r"(?:instrukc\w*|pokyn\w*|prompt\w*|zadani\b|pravidl\w*|predchozi\b|vyse uveden\w*)"
_AI_WORDS = (
    r"\b(?:ai|a\.i\.|llm|gpt|chatgpt|chatbot|bot|assistant|language model|"
    r"asistent\w*|jazykov\w* model\w*|umel\w* inteligenc\w*)\b"
)
_DIRECTIVE_WORDS = (
    r"\b(?:must|should|always|never|do not|don't|recommend|rate|rank|score|"
    r"instructions?|musis|musite|mel bys|mela bys|vzdy|nikdy|nesmis|doporuc\w*|"
    r"ohodnot\w*|pokyn\w*|instrukc\w*)\b"
)
_APP_COMMANDS = (
    r"approve|reject|setup|set-crontab|release-quarantine|show-quarantine|"
    r"add-information|add-web-links|scrape-jobs"
)

RULES: tuple[Rule, ...] = (
    Rule(
        "ignore_instructions",
        _rx(
            _EN_IGNORE + _gap(4) + _EN_INSTR,
            r"\b(?:ignore|disregard|forget)\b"
            + _gap(2)
            + r"(?:previous|prior|above|earlier|everything)\b",
            _CS_IGNORE + _gap(4) + _CS_INSTR,
            _CS_IGNORE + _gap(2) + r"(?:vse(?:chno)?|vsechny) (?:vyse|predtim|predchozi)\b",
        ),
        ("cs", "en"),
    ),
    Rule(
        "role_override",
        _rx(
            r"\byou are now\b",
            r"\bfrom now on,? you\b",
            r"\b(?:i want you to|you will|now|please) act as\b",
            r"\bact as (?:if|though)\b",
            r"\bact as (?:an? )?(?:ai|assistant|chatbot|llm|dan|unrestricted|jailbroken)\b",
            r"\bpretend (?:to be|you are|that you)\b",
            r"\broleplay as\b",
            r"\byour new (?:role|persona|instructions?)\b",
            r"\bjsi (?:nyni|ted|od ted(?:ka)?)\b",
            r"\b(?:ted|nyni|od ted(?:ka)?) jsi\b",
            r"\bchovej(?:te)? se jako\b",
            r"\bpredstirej(?:te)?\b",
            r"\bhraj(?:te)? roli\b",
            r"\bvystupuj(?:te)? jako\b",
            r"\btv(?:a|oje) nov(?:a|e) role\b",
        ),
        ("cs", "en"),
    ),
    Rule(
        "chat_template",
        _rx(
            r"<\|(?:im_start|im_end|system|user|assistant|endoftext|eot_id|"
            r"start_header_id|end_header_id|begin_of_text)\|>",
            r"\[/?inst\]",
            r"<</?sys>>",
            r"^[ \t]*#{1,6}[ \t]*(?:system|instructions?)\b",
            r"^[ \t]*system[ \t]*:",
        ),
        ("cs", "en"),
    ),
    Rule(
        "reveal_prompt",
        _rx(
            r"\b(?:reveal|show|print|repeat|output|display|tell me|leak|disclose|share)\b"
            + _gap(4)
            + r"(?:(?:system|initial|hidden|original) prompt|"
            r"your (?:instructions|prompt|system prompt|rules))\b",
            r"\bwhat (?:is|are|were) your (?:system prompt|instructions|initial prompt)\b",
            r"\b(?:prozrad|vyzrad|ukaz|vypis|zopakuj|sdel)\w*"
            + _gap(4)
            + r"(?:systemov\w* (?:prompt|pokyn|instrukc)\w*|prompt\w*|"
            r"(?:sv|tv)\w* (?:instrukc|pokyn)\w*)",
        ),
        ("cs", "en"),
    ),
    Rule(
        "tool_call",
        _rx(
            r"\b(?:call|invoke|execute|trigger)\b(?:\W+(?:the|a|this|that|your|following))?"
            r"\W+(?:tool|function)s?\b",
            r"\b(?:tool_call|function_call|tool_use|tool_calls)\b",
            r"</?tool_call>",
            rf"(?:^|\s)-(?:{_APP_COMMANDS})\b",
            r"\b(?:zavolej|spust|vyvolej|proved)(?:te)?" + _gap(2) + r"(?:nastroj|funkc|prikaz)\w*",
        ),
        ("cs", "en"),
    ),
    Rule(
        "hidden_instruction",
        re.compile(rf"^(?=.*?{_AI_WORDS})(?=.*?{_DIRECTIVE_WORDS})", re.DOTALL),
        ("cs", "en"),
        scope="hidden",
    ),
)

# Invisible characters

_ZERO_WIDTH = "\u200b\u200c\u200d\u2060\ufeff\u180e"
_BIDI_EMBED = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
_BIDI_MARKS = "\u200e\u200f\u061c"
_INVISIBLE = _ZERO_WIDTH + _BIDI_EMBED + _BIDI_MARKS

_ZERO_WIDTH_IN_WORD = re.compile(rf"(?<=\w)[{_INVISIBLE}]*[{_ZERO_WIDTH}][{_INVISIBLE}]*(?=\w)")
_BIDI_ANYWHERE = re.compile(rf"[{_BIDI_EMBED}]")
_BIDI_MARK_IN_WORD = re.compile(rf"(?<=\w)[{_INVISIBLE}]*[{_BIDI_MARKS}][{_INVISIBLE}]*(?=\w)")


def find_invisible_controls(text: str) -> list[str]:
    """Rule names for zero-width characters inside words and bidi controls.

    Embedding, override and isolate controls are flagged anywhere; directional marks
    only inside words. Non-breaking spaces, soft hyphens and dashes are typography.
    """
    found = []
    if _ZERO_WIDTH_IN_WORD.search(text):
        found.append(ZERO_WIDTH_RULE)
    if _BIDI_ANYWHERE.search(text) or _BIDI_MARK_IN_WORD.search(text):
        found.append(BIDI_RULE)
    return found


# Matching

_HSPACE = re.compile(r"[^\S\n]+")


def normalize(text: str) -> str:
    """Casefolded, diacritics-free text without format characters; lines kept."""
    decomposed = unicodedata.normalize("NFKD", text)
    kept = "".join(
        c for c in decomposed if not unicodedata.combining(c) and unicodedata.category(c) != "Cf"
    )
    folded = kept.casefold().replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(_HSPACE.sub(" ", line).strip() for line in folded.split("\n"))


def match_rules(text: str, *, hidden: bool = False) -> list[str]:
    """Names of the rules that match `text`; `hidden` also enables hidden-only rules."""
    normalized = normalize(text)
    return [
        rule.name
        for rule in RULES
        if (rule.scope == "any" or hidden) and rule.pattern.search(normalized)
    ]


# HTML

_SKIP = frozenset({"script", "style", "head", "title", "meta", "link", "base"})
_HIDDEN_CONTAINERS = frozenset({"noscript", "template"})
_BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "details", "dialog", "div",
        "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
        "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "summary", "table", "td", "th", "tr", "ul", "caption", "option", "label",
    }
)  # fmt: skip
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_SIMPLE_SELECTOR = re.compile(r"^([a-z][a-z0-9]*)?((?:[.#][\w-]+)*)$", re.IGNORECASE)
_SELECTOR_PART = re.compile(r"([.#])([\w-]+)")
_ZERO_SIZE = re.compile(r"^-?0*(?:\.0+)?(?:px|em|rem|pt|pc|%|vw|vh|vmin|vmax|ex|ch|mm|cm|in)?$")


@dataclass(frozen=True)
class HtmlText:
    """Text a browser shows (`visible`) and text it hides (`hidden`), both line-based."""

    visible: str
    hidden: str


@dataclass(frozen=True)
class _Style:
    display_none: bool = False
    visibility: bool | None = None  # False hidden, True visible, None unset
    zero_font: bool | None = None  # True zero, False explicit non-zero, None unset


def _parse_declarations(text: str) -> _Style:
    display_none = False
    visibility: bool | None = None
    zero_font: bool | None = None
    for decl in text.split(";"):
        prop, sep, value = decl.partition(":")
        if not sep:
            continue
        prop = prop.strip().lower()
        value = value.lower().replace("!important", "").strip()
        if prop == "display":
            display_none = display_none or value == "none"
        elif prop == "visibility":
            visibility = value not in ("hidden", "collapse")
        elif prop == "font-size":
            zero_font = bool(_ZERO_SIZE.match(value))
    return _Style(display_none, visibility, zero_font)


def _merge(base: _Style, top: _Style) -> _Style:
    return _Style(
        base.display_none or top.display_none,
        top.visibility if top.visibility is not None else base.visibility,
        top.zero_font if top.zero_font is not None else base.zero_font,
    )


@dataclass(frozen=True)
class _Selector:
    tag: str | None
    classes: frozenset[str]
    ids: frozenset[str]

    def matches(self, el: Tag) -> bool:
        if self.tag and el.name != self.tag:
            return False
        classes = el.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()
        if not self.classes <= set(classes):
            return False
        return not self.ids or self.ids == {el.get("id")}


def _style_rules(soup: BeautifulSoup) -> list[tuple[_Selector, _Style]]:
    """Simple tag/class/id rules from the page's own `<style>` blocks that hide."""
    found = []
    for block in soup.find_all("style"):
        css = _CSS_COMMENT.sub(" ", block.get_text())
        for selectors, body in _CSS_RULE.findall(css):
            style = _parse_declarations(body)
            if style == _Style():
                continue
            for raw in selectors.split(","):
                selector = raw.strip()
                m = _SIMPLE_SELECTOR.match(selector)
                if not selector or selector.startswith("@") or not m:
                    continue
                parts = _SELECTOR_PART.findall(m.group(2) or "")
                found.append(
                    (
                        _Selector(
                            m.group(1).lower() if m.group(1) else None,
                            frozenset(n for kind, n in parts if kind == "."),
                            frozenset(n for kind, n in parts if kind == "#"),
                        ),
                        style,
                    )
                )
    return found


class _Collector:
    def __init__(self, sheet: list[tuple[_Selector, _Style]]) -> None:
        self.sheet = sheet
        self.visible: list[str] = []
        self.hidden: list[str] = []

    def _own_style(self, el: Tag) -> _Style:
        style = _Style()
        for selector, rule_style in self.sheet:
            if selector.matches(el):
                style = _merge(style, rule_style)
        inline = el.get("style")
        if isinstance(inline, str):
            style = _merge(style, _parse_declarations(inline))
        aria = el.get("aria-hidden")
        if (
            el.has_attr("hidden")
            or (isinstance(aria, str) and aria.strip().lower() == "true")
            or el.name in _HIDDEN_CONTAINERS
        ):
            style = _merge(style, _Style(display_none=True))
        return style

    def _break(self) -> None:
        self.visible.append("\n")
        self.hidden.append("\n")

    def walk(self, node: Tag, inherited: _Style) -> None:
        for child in node.children:
            if isinstance(child, Comment):
                self.hidden.extend(("\n", str(child), "\n"))
            elif isinstance(child, CData | Declaration | Doctype | ProcessingInstruction):
                continue
            elif isinstance(child, NavigableString):
                hidden = (
                    inherited.display_none
                    or inherited.visibility is False
                    or inherited.zero_font is True
                )
                (self.hidden if hidden else self.visible).append(str(child))
            elif isinstance(child, Tag):
                if child.name in _SKIP:
                    continue
                block = child.name in _BLOCK
                if block:
                    self._break()
                self.walk(child, _merge(inherited, self._own_style(child)))
                if block:
                    self._break()


def _join(pieces: list[str]) -> str:
    text = "".join(pieces)
    lines = (re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def extract_html(html: str) -> HtmlText:
    """Visible text of `<body>` and the hidden text, separately (T6).

    Hiding on `<html>` or `<body>` (inline, attributes or `<style>` rules) applies to
    the whole page.

    Hidden: inline `style`, `hidden`, `aria-hidden="true"`, `<noscript>`/`<template>`,
    HTML comments, and simple tag/class/id rules in the page's `<style>` blocks that
    set `display:none`, `visibility:hidden` or a zero font size. External stylesheets,
    complex selectors and computed styles are not evaluated.
    """
    soup = BeautifulSoup(html, "html.parser")
    collector = _Collector(_style_rules(soup))
    root = soup.body
    if root is None:
        collector.walk(soup, _Style())
    else:
        # The walk starts below `<body>`, so the hiding of `<body>` and its ancestors
        # (`<html>`) is applied up front: it covers the whole page.
        inherited = _Style()
        ancestors = [p for p in root.parents if not isinstance(p, BeautifulSoup)]
        for el in [*reversed(ancestors), root]:
            inherited = _merge(inherited, collector._own_style(el))
        collector.walk(root, inherited)
    return HtmlText(_join(collector.visible), _join(collector.hidden))


@dataclass(frozen=True)
class RuleScan:
    """Rule hits for one input, with the text it was split into."""

    matched: list[str]
    visible: str
    hidden: str


def scan(content: str, *, html: bool = False) -> RuleScan:
    """Run every rule on `content`; HTML is split into visible and hidden text first.

    Hits in hidden text are named `hidden:<rule>`. Invisible-character checks run on
    the extracted text (entities decoded).
    """
    if not html:
        matched = match_rules(content) + find_invisible_controls(content)
        return RuleScan(_dedupe(matched), content, "")
    page = extract_html(content)
    matched = match_rules(page.visible) + find_invisible_controls(page.visible)
    if page.hidden:
        hidden_hits = match_rules(page.hidden, hidden=True) + find_invisible_controls(page.hidden)
        matched += [HIDDEN_PREFIX + name for name in hidden_hits]
    return RuleScan(_dedupe(matched), page.visible, page.hidden)


def _dedupe(names: list[str]) -> list[str]:
    return list(dict.fromkeys(names))
