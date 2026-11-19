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
from typing import Literal, NamedTuple

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

# A whole run of invisible characters between two word characters. One quantifier
# only: a run not followed by a word character fails in linear time.
_INVISIBLE_IN_WORD = re.compile(rf"(?<=\w)[{_INVISIBLE}]+(?=\w)")
_BIDI_ANYWHERE = re.compile(rf"[{_BIDI_EMBED}]")
_ZERO_WIDTH_SET = frozenset(_ZERO_WIDTH)
_BIDI_MARK_SET = frozenset(_BIDI_MARKS)


def find_invisible_controls(text: str) -> list[str]:
    """Rule names for zero-width characters inside words and bidi controls.

    Embedding, override and isolate controls are flagged anywhere; directional marks
    only inside words. Non-breaking spaces, soft hyphens and dashes are typography.
    """
    zero_width = bidi = False
    for run in _INVISIBLE_IN_WORD.finditer(text):
        chars = set(run.group())
        zero_width = zero_width or not chars.isdisjoint(_ZERO_WIDTH_SET)
        bidi = bidi or not chars.isdisjoint(_BIDI_MARK_SET)
        if zero_width and bidi:
            break
    found = []
    if zero_width:
        found.append(ZERO_WIDTH_RULE)
    if bidi or _BIDI_ANYWHERE.search(text):
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


def _match_hidden_only(fragment: str) -> list[str]:
    normalized = normalize(fragment)
    return [r.name for r in RULES if r.scope == "hidden" and r.pattern.search(normalized)]


# HTML

_SKIP = frozenset({"script", "style", "link", "base"})
# Read as hidden text wherever they are: `<head>`, `<title>` (also SVG titles) and
# the `content` of `<meta>`; each child of `<head>` is its own fragment.
_ALWAYS_HIDDEN = frozenset({"head", "title"})
_TEXT_ATTRIBUTES = ("alt", "title")
_HIDDEN_CONTAINERS = frozenset({"noscript", "template"})
_BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "details", "dialog", "div",
        "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
        "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section",
        "summary", "table", "td", "th", "tr", "ul", "caption", "option", "label",
    }
)  # fmt: skip
# The page's own CSS read for hiding rules, at most this many characters in total.
MAX_STYLE_CHARS = 1_000_000
_SIMPLE_SELECTOR = re.compile(r"^([a-z][a-z0-9]*)?((?:[.#][\w-]+)*)$", re.IGNORECASE)
_SELECTOR_PART = re.compile(r"([.#])([\w-]+)")
_ONE_NAME = re.compile(r"([.#])([\w-]+)")
_ZERO_SIZE = re.compile(r"^-?0*(?:\.0+)?(?:px|em|rem|pt|pc|%|vw|vh|vmin|vmax|ex|ch|mm|cm|in)?$")


@dataclass(frozen=True)
class HtmlText:
    """Text a browser shows (`visible`) and text it hides (`hidden`), both line-based.

    `fragments` is the hidden text split into its unrelated pieces: one per hidden
    element (outermost), comment, `<head>` child, attribute value and piece of
    content outside `<body>`. `hidden` is all of them joined.
    """

    visible: str
    hidden: str
    fragments: tuple[str, ...] = ()


# Named tuples, not dataclasses: a page can carry tens of thousands of rules, and
# tuple construction and hashing run in C.
class _Style(NamedTuple):
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


class _Selector(NamedTuple):
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


def _strip_css_comments(css: str) -> str:
    """`/* … */` removed in one pass; an unclosed comment runs to the end."""
    out, pos = [], 0
    while (start := css.find("/*", pos)) != -1:
        out.append(css[pos:start])
        end = css.find("*/", start + 2)
        if end == -1:
            return " ".join(out)
        out.append(" ")
        pos = end + 2
    out.append(css[pos:])
    return "".join(out)


def _css_rules(css: str) -> list[tuple[str, str]]:
    """`(selectors, declarations)` of every innermost `selectors { declarations }`.

    Linear: split on `}`, then the text after the chunk's last `{` is the body and the
    text between the `{` before it (or the chunk start) is the selector list. At-rule
    wrappers such as `@media … {` fall away; a chunk without `{` is no rule.
    """
    found = []
    for chunk in css.split("}")[:-1]:
        head, sep, body = chunk.rpartition("{")
        if not sep:
            continue
        selectors = head.rpartition("{")[2]
        if selectors.strip():
            found.append((selectors, body))
    return found


def _parse_selector(raw: str) -> _Selector | None:
    selector = raw.strip()
    one = _ONE_NAME.fullmatch(selector)  # fast path: a lone `.class` or `#id`
    if one:
        names = frozenset((one.group(2),))
        return (
            _Selector(None, names, _EMPTY)
            if one.group(1) == "."
            else _Selector(None, _EMPTY, names)
        )
    m = _SIMPLE_SELECTOR.match(selector)
    if not selector or not m:
        return None
    tag = m.group(1).lower() if m.group(1) else None
    if not m.group(2):
        return _Selector(tag, _EMPTY, _EMPTY)
    classes, ids = [], []
    for kind, name in _SELECTOR_PART.findall(m.group(2)):
        (classes if kind == "." else ids).append(name)
    return _Selector(tag, frozenset(classes), frozenset(ids))


_EMPTY: frozenset[str] = frozenset()


_NO_STYLE = _Style()


def _style_rules(soup: BeautifulSoup) -> list[tuple[_Selector, _Style]]:
    """Simple tag/class/id rules from the page's own `<style>` blocks that hide.

    At most `MAX_STYLE_CHARS` of CSS are read; later rules are ignored. Declarations
    and selectors are parsed once per distinct text.
    """
    found = []
    budget = MAX_STYLE_CHARS
    styles: dict[str, _Style] = {}
    selectors_seen: dict[str, _Selector | None] = {}
    for block in soup.find_all("style"):
        if budget <= 0:
            break
        text = block.get_text()[:budget]
        budget -= len(text)
        css = _strip_css_comments(text)
        for selectors, body in _css_rules(css):
            style = styles.get(body)
            if style is None:
                style = styles[body] = _parse_declarations(body)
            if style is _NO_STYLE or style == _NO_STYLE:
                continue
            for raw in selectors.split(","):
                if raw in selectors_seen:
                    selector = selectors_seen[raw]
                else:
                    selector = selectors_seen[raw] = _parse_selector(raw)
                if selector is not None:
                    found.append((selector, style))
    return found


_HIDE = _Style(display_none=True)


def _is_hidden(style: _Style) -> bool:
    return style.display_none or style.visibility is False or style.zero_font is True


_Key = tuple[str, str]


def _selector_key(selector: _Selector) -> _Key:
    """The one bucket a selector is filed under: an id, else a class, else its tag.

    An element that matches the selector carries that key, so looking up only the
    element's own keys finds every rule that can match it.
    """
    if selector.ids:
        return ("#", min(selector.ids))
    if selector.classes:
        return (".", min(selector.classes))
    return ("", selector.tag or "")


class _Sheet:
    """The page's hiding rules indexed by selector key, in source order.

    Repeated `(selector, style)` pairs keep only their last occurrence: merging the
    same style again earlier changes nothing (`display:none` accumulates, the other
    properties are last-wins), so no result changes.
    """

    def __init__(self, rules: list[tuple[_Selector, _Style]]) -> None:
        seen: set[tuple[_Selector, _Style]] = set()
        kept: list[tuple[int, _Selector, _Style]] = []
        for i in range(len(rules) - 1, -1, -1):  # from the end: the last one is kept
            rule = rules[i]
            if rule not in seen:
                seen.add(rule)
                kept.append((i, *rule))
        self.buckets: dict[_Key, list[tuple[int, _Selector, _Style]]] = {}
        for entry in reversed(kept):
            self.buckets.setdefault(_selector_key(entry[1]), []).append(entry)

    def styles_for(self, el: Tag) -> list[_Style]:
        """Styles of the rules matching `el`, in source order."""
        if not self.buckets:
            return []
        classes = el.get("class") or []
        if isinstance(classes, str):
            classes = classes.split()
        keys: list[_Key] = [(".", c) for c in dict.fromkeys(classes)]
        el_id = el.get("id")
        if isinstance(el_id, str):
            keys.append(("#", el_id))
        keys.append(("", el.name))
        hits = [
            (i, style)
            for key in keys
            for i, selector, style in self.buckets.get(key, ())
            if selector.matches(el)
        ]
        hits.sort(key=lambda hit: hit[0])
        return [style for _, style in hits]


class _Collector:
    """Visible text, hidden text and the hidden fragments, in document order.

    A fragment is a list of text pieces; the walk passes the current one down while
    the text stays hidden, and a hidden element below visible text starts a new one.
    """

    def __init__(self, sheet: list[tuple[_Selector, _Style]]) -> None:
        self.sheet = _Sheet(sheet)
        self.visible: list[str] = []
        self.hidden: list[str] = []
        self.fragments: list[list[str]] = []

    def _own_style(self, el: Tag) -> _Style:
        style = _Style()
        for rule_style in self.sheet.styles_for(el):
            style = _merge(style, rule_style)
        inline = el.get("style")
        if isinstance(inline, str):
            style = _merge(style, _parse_declarations(inline))
        aria = el.get("aria-hidden")
        if (
            el.has_attr("hidden")
            or (isinstance(aria, str) and aria.strip().lower() == "true")
            or el.name in _HIDDEN_CONTAINERS
            or el.name in _ALWAYS_HIDDEN
        ):
            style = _merge(style, _HIDE)
        return style

    def _fragment(self) -> list[str]:
        self.hidden.append("\n")
        fragment: list[str] = []
        self.fragments.append(fragment)
        return fragment

    def _hidden_text(self, text: str, fragment: list[str] | None) -> list[str]:
        fragment = fragment if fragment is not None else self._fragment()
        self.hidden.append(text)
        fragment.append(text)
        return fragment

    def _standalone(self, text: str) -> None:
        """A piece of hidden text unrelated to its neighbours (comment, attribute)."""
        self._hidden_text(text, None)
        self.hidden.append("\n")

    def _break(self, fragment: list[str] | None) -> None:
        self.visible.append("\n")
        self.hidden.append("\n")
        if fragment is not None:
            fragment.append("\n")

    def _attributes(self, el: Tag) -> None:
        names = (*_TEXT_ATTRIBUTES, "content") if el.name == "meta" else _TEXT_ATTRIBUTES
        for name in names:
            value = el.get(name)
            if isinstance(value, str) and value.strip():
                self._standalone(value)

    def visit(self, child: object, inherited: _Style, fragment: list[str] | None) -> None:
        if isinstance(child, Comment):
            self._standalone(str(child))
        elif isinstance(child, CData | Declaration | Doctype | ProcessingInstruction):
            return
        elif isinstance(child, NavigableString):
            if _is_hidden(inherited):
                self._hidden_text(str(child), fragment)
            else:
                self.visible.append(str(child))
        elif isinstance(child, Tag):
            if child.name in _SKIP:
                return
            self._attributes(child)
            if child.name == "meta":
                return
            style = _merge(inherited, self._own_style(child))
            if not _is_hidden(style):
                fragment = None
            elif fragment is None or child.name == "head":
                # `<head>`'s children (title, stray text) are unrelated to each other
                fragment = None if child.name == "head" else self._fragment()
            block = child.name in _BLOCK
            if block:
                self._break(fragment)
            self.walk(child, style, fragment)
            if block:
                self._break(fragment)

    def walk(self, node: Tag, inherited: _Style, fragment: list[str] | None = None) -> None:
        for child in node.children:
            self.visit(child, inherited, fragment)

    def walk_document(self, soup: BeautifulSoup) -> None:
        """The whole document: `<body>` as the page; everything outside it is hidden.

        Hiding on `<body>` and its ancestors (`<html>`) covers the whole page, so it
        is applied up front. Without a `<body>`, the document is read as the page.
        """
        root = soup.body
        if root is None:
            self.walk(soup, _Style())
            return
        ancestors = [p for p in root.parents if not isinstance(p, BeautifulSoup)]
        inherited = _Style()
        for el in [*reversed(ancestors), root]:
            inherited = _merge(inherited, self._own_style(el))
        path = set(map(id, ancestors))

        def outside(node: Tag) -> None:
            for child in node.children:
                if child is root:
                    self._attributes(root)
                    self.walk(root, inherited, self._fragment() if _is_hidden(inherited) else None)
                elif id(child) in path:
                    assert isinstance(child, Tag)
                    self._attributes(child)
                    outside(child)
                else:
                    self.visit(child, _HIDE, None)

        outside(soup)


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
    set `display:none`, `visibility:hidden` or a zero font size. Also read as hidden
    text: `<head>` (title, `<meta content>`, comments, stray text; not scripts or
    styles), any content outside `<body>`, `<title>` anywhere, and `alt`/`title`
    attribute values. External stylesheets, complex selectors and computed styles are
    not evaluated.
    """
    soup = BeautifulSoup(html, "html.parser")
    collector = _Collector(_style_rules(soup))
    collector.walk_document(soup)
    fragments = tuple(f for f in map(_join, collector.fragments) if f)
    return HtmlText(_join(collector.visible), _join(collector.hidden), fragments)


@dataclass(frozen=True)
class RuleScan:
    """Rule hits for one input, with the text it was split into."""

    matched: list[str]
    visible: str
    hidden: str


def scan(content: str, *, html: bool = False) -> RuleScan:
    """Run every rule on `content`; HTML is split into visible and hidden text first.

    Hits in hidden text are named `hidden:<rule>`. The hidden text is scanned as a
    whole by the general rules and fragment by fragment by the hidden-only ones, so
    an AI word in one hidden element and a directive in another do not add up.
    Invisible-character checks run on the extracted text (entities decoded).
    """
    if not html:
        matched = match_rules(content) + find_invisible_controls(content)
        return RuleScan(_dedupe(matched), content, "")
    page = extract_html(content)
    matched = match_rules(page.visible) + find_invisible_controls(page.visible)
    if page.hidden:
        hidden_hits = match_rules(page.hidden) + find_invisible_controls(page.hidden)
        for fragment in page.fragments:
            hidden_hits += _match_hidden_only(fragment)
        matched += [HIDDEN_PREFIX + name for name in hidden_hits]
    return RuleScan(_dedupe(matched), page.visible, page.hidden)


def _dedupe(names: list[str]) -> list[str]:
    return list(dict.fromkeys(names))
