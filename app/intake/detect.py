"""Paths and URLs in a user message, and the `ingest_source` allow rule (Phase 3 R5, R6, T6, T7).

`detect(message, cwd)` finds, in one pass over untrusted text:

- `http://` / `https://` URLs, and `www.` hosts (read as `https://`);
- tokens ending in a supported extension (`.txt`, `.md`, `.pdf`, `.html`, `.htm`, any
  case): explicit when absolute, `~/`, `./` or `../`; a bare relative word (such as
  `README.md`) only when that file exists under `cwd`;
- quoted paths, which may contain spaces (`"…"`, `'…'`, `„…“`, `“…”`, `‚…‘`, `‘…’`);
  always explicit.

Trailing punctuation (`.,;:!?)]}>"'` and typographic quotes) is stripped. An explicit
path is returned even when it does not exist, so the caller can report it as not
found. At most `MAX_SOURCES` per message are ingested (`split_cap`).

`allow_ingest` decides, before any file read or fetch, whether the intake agent may
ingest a spec: only URLs and paths the user typed this round, or a `find_files`
result after a yes/no confirmation. It touches no file system.

Every scan is linear: tokens come from one `\\S+` pass, quote openers and closers from
one regex pass each, matched by monotone pointers; no backtracking quantifiers.
"""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from app.intake.sources import EXTENSIONS

MAX_SOURCES = 10
# Longer tokens are not paths any OS opens (PATH_MAX); they stay plain text.
MAX_PATH_LEN = 4096

SourceKind = Literal["url", "path"]
Decision = Literal["allowed", "confirm", "refused"]
RefusalReason = Literal["empty", "url_not_typed", "path_not_typed"]

TRAILING = ".,;:!?)]}>\"'“”‘’„‚"
LEADING = "([{<\"'“”‘’„‚"
QUOTES = {'"': '"', "'": "'", "„": "“", "“": "”", "‚": "‘", "‘": "’"}
EXPLICIT_PREFIXES = ("/", "~/", "./", "../")

_TOKEN = re.compile(r"\S+")
# An opening quote starts the text or follows whitespace, a bracket or a colon.
_OPENER = re.compile(r"(?:^|(?<=[\s(\[{<:]))[\"'„“‚‘]")
# A closing quote ends the text or is followed by whitespace or punctuation.
_CLOSER = re.compile(r"[\"'“”‘’](?=[\s.,;:!?)\]}>\"'“”‘’]|$)")
_SCHEMES = ("https://", "http://")
# A last host label that is a number (decimal, octal or `0x` hex) as `inet_aton` reads it.
_NUMERIC_LABEL = re.compile(r"0x[0-9a-f]*|[0-9]+")


@dataclass(frozen=True)
class Detected:
    """One source in a message: `spec` as written, `normalised` as ingested (quotes
    removed, `www.` read as `https://`), its kind, whether it is explicit (R5) and the
    position of `spec` in the message."""

    spec: str
    normalised: str
    kind: SourceKind
    explicit: bool
    position: int


@dataclass(frozen=True)
class Verdict:
    """The allow rule's answer for one `ingest_source` spec (T7). `spec` is the
    normalised spec to ingest; `reason` is set when refused. `bare_host` is true when
    a URL is allowed only because the user typed it as a bare `host/path` token (no
    scheme, no `www.`): the fetch must then refuse a local or private host (Group 5),
    since the user did not clearly type a URL."""

    decision: Decision
    spec: str
    kind: SourceKind
    reason: RefusalReason | None = None
    bare_host: bool = False


# --- scanning ----------------------------------------------------------------------


@dataclass(frozen=True)
class _Candidate:
    position: int
    written: str
    text: str  # punctuation or quotes removed
    quoted: bool


def _has_extension(text: str) -> bool:
    """`text` ends in a supported extension after a non-empty file name."""
    dot = text.rfind(".")
    if dot < 0 or dot <= text.rfind("/") + 1:
        return False
    return text[dot:].lower() in EXTENSIONS


def _quoted_spans(message: str) -> Iterator[_Candidate]:
    """Quoted paths: the first valid closer after each opener, within `MAX_PATH_LEN`
    and on one line, the content ending in a supported extension."""
    closers: dict[str, list[int]] = {}
    for match in _CLOSER.finditer(message):
        closers.setdefault(match.group(), []).append(match.start())
    pointers = dict.fromkeys(closers, 0)
    newline = -1
    resume = 0
    for match in _OPENER.finditer(message):
        start = match.start()
        if start < resume:
            continue
        close_char = QUOTES[match.group()]
        positions = closers.get(close_char, [])
        index = pointers.get(close_char, 0)
        while index < len(positions) and positions[index] <= start:
            index += 1
        pointers[close_char] = index
        if index == len(positions):
            continue
        end = positions[index]
        if end - start - 1 > MAX_PATH_LEN or end == start + 1:
            continue
        if newline < start:
            newline = message.find("\n", start)
            if newline < 0:
                newline = len(message)
        if newline < end:
            continue
        if not _has_extension(message[max(start + 1, end - 6) : end]):
            continue
        content = message[start + 1 : end].strip()
        if not content or "://" in content or content.lower().startswith("www."):
            continue
        resume = end + 1
        yield _Candidate(start, message[start : end + 1], content, quoted=True)


def _tokens(message: str, spans: Sequence[_Candidate]) -> Iterator[_Candidate]:
    """Whitespace tokens outside the quoted spans, with leading and trailing
    punctuation removed."""
    span = 0
    for match in _TOKEN.finditer(message):
        start, end = match.span()
        while span < len(spans) and spans[span].position + len(spans[span].written) <= start:
            span += 1
        if span < len(spans) and spans[span].position < end:
            continue  # overlaps a quoted path
        token = match.group()
        lead = len(token) - len(token.lstrip(LEADING))
        text = token[lead:].rstrip(TRAILING)
        if text:
            yield _Candidate(start + lead, text, text, quoted=False)


def _candidates(message: str) -> list[_Candidate]:
    spans = list(_quoted_spans(message))
    merged = spans + list(_tokens(message, spans))
    merged.sort(key=lambda c: c.position)
    return merged


def _url_start(text: str) -> int:
    lower = text.lower()
    found = [i for i in (lower.find(s) for s in _SCHEMES) if i >= 0]
    return min(found) if found else -1


def _is_www_host(text: str) -> bool:
    if not text.lower().startswith("www."):
        return False
    host = text[4:].split("/", 1)[0]
    return "." in host and not host.startswith(".") and "@" not in host


def _valid_url(url: str) -> bool:
    try:
        return bool(urlsplit(url).hostname)
    except ValueError:
        return False


def _bare_exists(text: str, cwd: Path) -> bool:
    # os.path.isfile returns False on OSError and ValueError (too long, NUL bytes)
    return os.path.isfile(os.path.join(cwd, os.path.expanduser(text)))


# --- detection (T6) ----------------------------------------------------------------


def detect(message: str, cwd: Path) -> list[Detected]:
    """The URLs and paths in `message`, in order of position (R5, T6)."""
    result: list[Detected] = []
    exists: dict[str, bool] = {}
    for cand in _candidates(message):
        if cand.quoted:
            result.append(Detected(cand.written, cand.text, "path", True, cand.position))
            continue
        text = cand.text
        at = _url_start(text)
        if at >= 0:
            url = text[at:]
            if _valid_url(url):
                result.append(Detected(url, url, "url", True, cand.position + at))
            continue
        if _is_www_host(text):
            result.append(Detected(text, "https://" + text, "url", True, cand.position))
            continue
        if "://" in text or len(text) > MAX_PATH_LEN or not _has_extension(text):
            continue
        if text.startswith(EXPLICIT_PREFIXES):
            result.append(Detected(text, text, "path", True, cand.position))
        else:
            if text not in exists:
                exists[text] = _bare_exists(text, cwd)
            if exists[text]:
                result.append(Detected(text, text, "path", False, cand.position))
    return result


def split_cap(found: Sequence[Detected]) -> tuple[list[Detected], list[Detected]]:
    """The sources to ingest this turn and the ones skipped beyond `MAX_SOURCES`."""
    return list(found[:MAX_SOURCES]), list(found[MAX_SOURCES:])


# --- allow rule (T7) ---------------------------------------------------------------


def _url_key(url: str) -> str:
    """A URL without its scheme, trailing punctuation and trailing `/`; host lowercased."""
    lower = url.lower()
    for scheme in _SCHEMES:
        if lower.startswith(scheme):
            url = url[len(scheme) :]
            break
    url = url.rstrip(TRAILING).rstrip("/")
    cut = len(url)
    for sep in "/?#":
        i = url.find(sep)
        if 0 <= i < cut:
            cut = i
    return url[:cut].lower() + url[cut:]


def expand_home(text: str, home: Path) -> str:
    """`~` and `~/…` expanded against `home`, without reading the environment."""
    if text == "~" or text.startswith("~/"):
        return str(home) + text[1:]
    return text


def _name(text: str) -> str:
    """The last path component (like `Path(text).name`, without building a `Path`);
    empty for `.`, `..` and `/`."""
    name = text.rstrip("/").rsplit("/", 1)[-1]
    return "" if name in (".", "..") else name


def unquote(spec: str) -> str:
    """`spec` stripped, without one pair of matching `'` or `"` around it (and the
    spaces inside them)."""
    spec = spec.strip()
    if len(spec) >= 2 and spec[0] == spec[-1] and spec[0] in "'\"":
        spec = spec[1:-1].strip()
    return spec


def _is_bare_host(key: str) -> bool:
    """The host of a bare token's URL key is a name, not an address: its last label
    contains a letter and is no `0x` number, and the host is no IP literal in any form
    `inet_aton` or `ipaddress` accept (`10.1`, `127.1`, `0x7f.1`, `[::1]`); a host with
    more than one `:` is an unbracketed IPv6 literal (`fe80::1`), never a name. Parses
    only; resolves nothing."""
    host = key
    for sep in "/?#":
        host = host.split(sep, 1)[0]
    if host.startswith("[") or host.count(":") > 1:
        return False  # an IPv6 literal, bracketed or not (`fe80::1`)
    host = host.split(":", 1)[0].rstrip(".")
    if not host:
        return False
    label = host.rsplit(".", 1)[-1]
    if not any(c.isalpha() for c in label) or _NUMERIC_LABEL.fullmatch(label):
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    try:
        socket.inet_aton(host)
        return False
    except OSError:
        return True


@dataclass
class _Typed:
    urls: set[str]  # typed with a scheme or `www.`
    bare_urls: set[str]  # bare `host/path` tokens with a named host
    paths: set[str]
    names: set[str]


def _typed(messages: Iterable[str], home: Path) -> _Typed:
    typed = _Typed(set(), set(), set(), set())
    for message in messages:
        for cand in _candidates(message):
            text = cand.text
            if not cand.quoted:
                at = _url_start(text)
                if at >= 0:
                    typed.urls.add(_url_key(text[at:]))
                elif _is_www_host(text):
                    typed.urls.add(_url_key(text))
                elif "." in text and "@" not in text and "://" not in text:
                    key = _url_key(text)
                    if _is_bare_host(key):
                        typed.bare_urls.add(key)
            typed.paths.add(text)
            if text.startswith("~"):
                typed.paths.add(expand_home(text, home))
            typed.names.add(_name(text))
    return typed


def allow_ingest(
    spec: str,
    messages: Iterable[str],
    found: Iterable[str],
    *,
    home: Path | None = None,
) -> Verdict:
    """May the intake agent ingest `spec`? `messages` are the user's messages of this
    round, `found` the paths `find_files` returned this round (R6, T7).

    URLs: allowed when typed (compared without scheme; a bare `host/path` token with a
    dot counts when its host is a name, not an address, and the verdict then carries
    `bare_host`); the returned URL has trailing punctuation removed. Paths: allowed
    when typed as given (after `~` expansion) or by file name (case-sensitive); a
    `find_files` result needs the user's confirmation.
    Everything else is refused. Reads no file and fetches nothing."""
    home = home if home is not None else Path.home()
    spec = unquote(spec)
    if spec.lower().startswith("www."):
        spec = "https://" + spec
    is_url = spec.lower().startswith(_SCHEMES)
    kind: SourceKind = "url" if is_url else "path"
    if not spec:
        return Verdict("refused", spec, kind, "empty")
    typed = _typed(messages, home)
    if is_url:
        spec = spec.rstrip(TRAILING)
        if "@" not in _netloc(spec):
            key = _url_key(spec)
            if key in typed.urls:
                return Verdict("allowed", spec, kind)
            if key in typed.bare_urls:
                return Verdict("allowed", spec, kind, bare_host=True)
        return Verdict("refused", spec, kind, "url_not_typed")
    expanded = expand_home(spec, home)
    name = _name(spec)
    if spec in typed.paths or expanded in typed.paths or (name and name in typed.names):
        return Verdict("allowed", spec, kind)
    if expanded in set(found):
        return Verdict("confirm", expanded, kind)
    return Verdict("refused", spec, kind, "path_not_typed")


def _netloc(url: str) -> str:
    """The authority part of a URL (between the scheme and the first `/`, `?` or `#`)."""
    rest = url.split("://", 1)[-1]
    for sep in "/?#":
        rest = rest.split(sep, 1)[0]
    return rest
