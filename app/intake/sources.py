"""Profile sources: resolve, fetch, guard, read and store them, with the manifest (R10, R11, T6).

Layout under `<workspace>/profile/sources/`: one folder per source, `<slug>/`, with
`original.<ext>` (the bytes as given; for URLs the fetched page as `original.html`),
`source.url` (URLs only: the final URL after redirects) and `text.md` (the
extracted text), plus `manifest.json`. Folders are 0700, files 0600.

Guarded input (URLs and `.html`/`.htm`, R1) passes `guard.check` on its raw bytes
before any text is extracted, and the text the guard scanned is the text the reader
extracts. Nothing of a flagged source is written outside the quarantine; without a
classifier, guarded sources are refused (R4) while trusted ones (`.txt`, `.md`,
`.pdf`) are still read.

Every outcome is a `SourceResult`: `added`, `skipped_duplicate` (same SHA-256 as a
manifest entry), `quarantined`, `refused_no_classifier` or `error` with a reason. A
failed source never ends the batch.

Files dropped by hand directly into `profile/sources/` (R11) are ingested like a
supplied source and then moved into their own folder, so they are picked up once; a
flagged drop-in is removed after it is quarantined (it now exists only encrypted).
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import re
import shutil
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from app.errors import LocalizedError
from app.files import private_dir, write_private
from app.guard import GuardUnavailable, Scorer, check
from app.guard.quarantine import QuarantineError
from app.guard.rules import extract_html
from app.intake.profile import PROFILE_DIR
from app.messages import short_error
from app.schemas.sources import (
    Manifest,
    ManifestEntry,
    SourceErrorCode,
    SourceResult,
    SourceType,
)

if TYPE_CHECKING:
    from app.tools.context import ToolContext

SOURCES_DIR = Path(PROFILE_DIR) / "sources"
MANIFEST_FILE = "manifest.json"
TEXT_FILE = "text.md"
URL_FILE = "source.url"
ORIGINAL_STEM = "original"

EXTENSIONS: dict[str, SourceType] = {
    ".txt": "txt",
    ".md": "md",
    ".pdf": "pdf",
    ".html": "html",
    ".htm": "html",
}
GUARDED_TYPES: frozenset[SourceType] = frozenset({"html", "url"})
TEXT_ENCODINGS = ("utf-8-sig", "cp1250", "latin-1")

FETCH_TIMEOUT = 20.0
MAX_REDIRECTS = 5
MAX_BYTES = 5 * 1024 * 1024
MAX_MB = 5
# local `.txt`, `.md` and `.pdf` (unguarded): larger than the fetch cap, since a CV PDF
# with photos can pass 5 MiB, but still bounded before any read (validation fix D10)
LOCAL_MAX_BYTES = 20 * 1024 * 1024
LOCAL_MAX_MB = 20
HTML_TYPES = frozenset({"text/html", "application/xhtml+xml"})

_SLUG_MAX = 40
_URL = re.compile(r"^https?://", re.IGNORECASE)


class SourcesError(LocalizedError):
    """The manifest cannot be read: no source can be deduplicated, so none is added."""


class _Failure(Exception):
    """One source failed; turned into an `error` result."""

    def __init__(self, code: SourceErrorCode, key: str | None = None, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.key = key or f"sources.{code}"
        self.params = params


# --- locations and manifest ------------------------------------------------------


def sources_dir(workspace: Path) -> Path:
    return workspace / SOURCES_DIR


def manifest_path(workspace: Path) -> Path:
    return sources_dir(workspace) / MANIFEST_FILE


def ensure_sources_dir(workspace: Path) -> Path:
    """`profile/` and `profile/sources/`, mode 0700 (T7)."""
    private_dir(workspace / PROFILE_DIR)
    root = sources_dir(workspace)
    private_dir(root)
    return root


def load_manifest(workspace: Path) -> Manifest:
    """The manifest; empty when missing. A broken one raises `SourcesError`."""
    path = manifest_path(workspace)
    try:
        return Manifest.model_validate_json(path.read_bytes())
    except FileNotFoundError:
        return Manifest()
    except (OSError, ValidationError) as exc:
        detail = short_error(exc) if isinstance(exc, OSError) else "invalid JSON or fields"
        raise SourcesError("sources.manifest_invalid", path=str(path), detail=detail) from exc


def save_manifest(workspace: Path, manifest: Manifest) -> None:
    ensure_sources_dir(workspace)
    write_private(manifest_path(workspace), manifest.model_dump_json(indent=2) + "\n")


def unextracted(workspace: Path) -> list[ManifestEntry]:
    """Entries a round has not yet extracted (R10): retried from their `text.md`."""
    return [e for e in load_manifest(workspace).sources if not e.extracted]


def mark_extracted(workspace: Path, slugs: Iterable[str]) -> None:
    """Set `extracted` on the given entries (after the round wrote `profile.md`)."""
    wanted = set(slugs)
    manifest = load_manifest(workspace)
    changed = False
    for entry in manifest.sources:
        if entry.slug in wanted and not entry.extracted:
            entry.extracted = True
            changed = True
    if changed:
        save_manifest(workspace, manifest)


def set_cv(workspace: Path, slug: str) -> None:
    """Flag an entry as the CV (R12)."""
    manifest = load_manifest(workspace)
    entry = manifest.by_slug(slug)
    if entry is not None and not entry.cv:
        entry.cv = True
        save_manifest(workspace, manifest)


def source_text(workspace: Path, slug: str) -> str:
    """The stored extracted text of one source."""
    return (sources_dir(workspace) / slug / TEXT_FILE).read_text(encoding="utf-8")


# --- readers -------------------------------------------------------------------


def decode_text(raw: bytes) -> str:
    """UTF-8, then cp1250 (older Czech files), then latin-1, which always decodes."""
    for encoding in TEXT_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AssertionError("latin-1 decodes any bytes")  # pragma: no cover


def decode_html(raw: bytes, charset: str | None = None) -> str:
    """The page as text: the HTTP charset, a BOM or `<meta charset>`, then UTF-8 or cp1250."""
    from bs4 import UnicodeDammit

    dammit = UnicodeDammit(
        raw,
        known_definite_encodings=[charset] if charset else [],
        user_encodings=["utf-8", "cp1250"],
        is_html=True,
    )
    return dammit.unicode_markup if dammit.unicode_markup is not None else decode_text(raw)


def read_pdf(raw: bytes) -> str:
    """Text of every page through LlamaIndex's PDF reader (in memory, no temp file)."""
    import secrets

    import fsspec
    from llama_index.readers.file import PDFReader

    fs = fsspec.filesystem("memory")
    name = f"/sortingcap-{secrets.token_hex(8)}.pdf"
    fs.pipe_file(name, raw)
    try:
        docs = PDFReader(return_full_document=True).load_data(Path(name), fs=fs)
    finally:
        with contextlib.suppress(FileNotFoundError):
            fs.rm_file(name)
    return "\n".join(doc.text for doc in docs)


def html_visible_text(html: str) -> str:
    """Visible text of `<body>`, without scripts, styles and hidden elements (T6)."""
    return extract_html(html).visible


def _extract(kind: SourceType, raw: bytes, html: str | None) -> str:
    try:
        if kind in GUARDED_TYPES:
            assert html is not None
            return html_visible_text(html)
        if kind == "pdf":
            return read_pdf(raw)
        return decode_text(raw)
    except Exception as exc:  # pypdf, tenacity and bs4 raise their own types
        cause = exc
        # tenacity wraps the reader's last error
        last = getattr(exc, "last_attempt", None)
        if last is not None and last.exception() is not None:
            cause = last.exception()
        raise _Failure("unreadable", detail=short_error(cause)) from exc


# --- fetch ---------------------------------------------------------------------


@dataclass(frozen=True)
class Fetched:
    raw: bytes
    final_url: str
    charset: str | None


def user_agent() -> str:
    return f"SortingCap/{version('sortingcap')}"


def _is_local_ip(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_link_local or ip.is_private or ip.is_unspecified


def is_local_host(host: str, resolve: Callable[[str], list[str]]) -> bool:
    """Loopback, link-local, private or unspecified: the literal, `localhost`, or any
    address the name resolves to. A name that does not resolve is not local."""
    host = host.strip("[]").rstrip(".").lower()
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return any(_is_local_ip(addr) for addr in resolve(host))
    return _is_local_ip(host)


def _redirect_check(ctx: ToolContext) -> Callable[[httpx.Request], None]:
    """A request hook refusing a redirect hop from a public host to a local one, or
    from https to http. The URL the user gave is not checked (it may be local)."""
    previous: list[httpx.URL] = []

    def hook(request: httpx.Request) -> None:
        url = request.url
        if previous:
            before = previous[-1]
            if not is_local_host(before.host, ctx.resolve_host) and is_local_host(
                url.host, ctx.resolve_host
            ):
                raise _Failure("fetch_redirect_private", host=url.host)
            if before.scheme == "https" and url.scheme != "https":
                raise _Failure("fetch_redirect_insecure")
        previous.append(url)

    return hook


def fetch_url(ctx: ToolContext, url: str) -> Fetched:
    """GET a page: 20 s timeout, at most 5 redirects and 5 MB, HTML types only (T6).

    Each redirect hop is checked: public to loopback/link-local/private, and https to
    http, are refused (per-source errors).
    """
    headers = {"User-Agent": user_agent(), "Accept": "text/html,application/xhtml+xml"}
    try:
        with ctx.http_client(timeout=FETCH_TIMEOUT) as client:
            client.max_redirects = MAX_REDIRECTS
            client.event_hooks = {"request": [_redirect_check(ctx)], "response": []}
            with client.stream("GET", url, headers=headers, follow_redirects=True) as response:
                if not response.is_success:
                    raise _Failure("fetch_status", status=response.status_code)
                mime = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if mime not in HTML_TYPES:
                    raise _Failure("fetch_type", type=mime or "?")
                length = response.headers.get("content-length", "")
                if length.isdigit() and int(length) > MAX_BYTES:
                    raise _Failure("fetch_too_large", mb=MAX_MB)
                body = bytearray()
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        raise _Failure("fetch_too_large", mb=MAX_MB)
                return Fetched(bytes(body), str(response.url), response.charset_encoding)
    except httpx.TimeoutException as exc:
        raise _Failure("fetch_timeout", seconds=int(FETCH_TIMEOUT)) from exc
    except httpx.TooManyRedirects as exc:
        raise _Failure("fetch_redirects", count=MAX_REDIRECTS) from exc
    except httpx.HTTPError as exc:
        raise _Failure("fetch_failed", detail=short_error(exc)) from exc


# --- storing -------------------------------------------------------------------


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:_SLUG_MAX].strip("-") or "source"


def _base_slug(kind: SourceType, origin: str) -> str:
    if kind == "url":
        parts = urlsplit(origin)
        host = (parts.hostname or "").removeprefix("www.")
        tail = parts.path.rstrip("/").rsplit("/", 1)[-1]
        tail = tail.rsplit(".", 1)[0] if "." in tail else tail
        return slugify(f"{host} {tail}")
    return slugify(Path(origin).stem)


def _unique_slug(base: str, manifest: Manifest, root: Path) -> str:
    taken = {e.slug for e in manifest.sources}
    candidate, n = base, 2
    while candidate in taken or (root / candidate).exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _store(
    workspace: Path,
    manifest: Manifest,
    *,
    raw: bytes,
    text: str,
    kind: SourceType,
    ext: str,
    origin: str,
    final_url: str | None,
    sha256: str,
    cv: bool,
) -> ManifestEntry:
    root = ensure_sources_dir(workspace)
    slug = _unique_slug(_base_slug(kind, final_url or origin), manifest, root)
    folder = root / slug
    private_dir(folder, exist_ok=False)
    entry = ManifestEntry(
        slug=slug,
        origin=origin,
        sha256=sha256,
        type=kind,
        cv=cv,
        extracted=False,
        added_at=datetime.now(UTC),
    )
    try:
        write_private(folder / f"{ORIGINAL_STEM}{ext}", raw)
        if final_url:
            write_private(folder / URL_FILE, final_url + "\n")
        write_private(folder / TEXT_FILE, text.strip() + "\n")
        updated = manifest.model_copy(update={"sources": [*manifest.sources, entry]})
        save_manifest(workspace, updated)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    manifest.sources.append(entry)
    return entry


def _error(ctx: ToolContext, origin: str, failure: _Failure, **fields) -> SourceResult:
    return SourceResult(
        origin=origin,
        status="error",
        code=failure.code,
        reason=ctx.msg(failure.key, **failure.params),
        **fields,
    )


def ingest_bytes(
    ctx: ToolContext,
    raw: bytes,
    *,
    origin: str,
    kind: SourceType,
    ext: str | None = None,
    final_url: str | None = None,
    charset: str | None = None,
    cv: bool = False,
    guarded: bool = True,
    dropped: Path | None = None,
    scorer: Scorer | None = None,
) -> SourceResult:
    """Dedupe, guard (URLs and HTML), extract and store one source's bytes.

    `guarded=False` skips the guard; only a user's explicit quarantine release uses
    it (R7). `dropped` is the dropped-in file: moved away once stored or quarantined.
    """
    ext = ext or (".html" if kind == "url" else f".{kind}")
    fields = {"type": kind, "final_url": final_url, "dropped_in": dropped is not None}
    sha256 = hashlib.sha256(raw).hexdigest()
    manifest = load_manifest(ctx.workspace)
    if existing := manifest.by_sha(sha256):
        if cv:
            set_cv(ctx.workspace, existing.slug)
        return SourceResult(origin=origin, status="skipped_duplicate", slug=existing.slug, **fields)

    html: str | None = None
    if kind in GUARDED_TYPES:
        html = decode_html(raw, charset)
        if guarded:
            try:
                verdict = check(ctx, raw, final_url or origin, html=True, text=html, scorer=scorer)
            except GuardUnavailable as exc:
                return SourceResult(
                    origin=origin,
                    status="refused_no_classifier",
                    reason=exc.text(ctx.texts),
                    **fields,
                )
            except QuarantineError as exc:
                return SourceResult(
                    origin=origin,
                    status="error",
                    code="quarantine_failed",
                    reason=exc.text(ctx.texts),
                    **fields,
                )
            if verdict.flagged:
                if dropped is not None:
                    # now held only encrypted in the quarantine (R11)
                    with contextlib.suppress(FileNotFoundError):
                        dropped.unlink()
                return SourceResult(
                    origin=origin,
                    status="quarantined",
                    quarantine_id=verdict.quarantine_id,
                    reason=verdict.reason,
                    **fields,
                )

    try:
        text = _extract(kind, raw, html)
        if not text.strip():
            raise _Failure("empty")
    except _Failure as failure:
        return _error(ctx, origin, failure, **fields)

    entry = _store(
        ctx.workspace,
        manifest,
        raw=raw,
        text=text,
        kind=kind,
        ext=ext,
        origin=origin,
        final_url=final_url,
        sha256=sha256,
        cv=cv,
    )
    if dropped is not None:
        with contextlib.suppress(FileNotFoundError):
            dropped.unlink()
    return SourceResult(origin=origin, status="added", slug=entry.slug, **fields)


# --- supplied sources ------------------------------------------------------------


def is_url(spec: str) -> bool:
    return bool(_URL.match(spec.strip()))


def resolve_path(spec: str, cwd: Path | None = None) -> Path:
    """An absolute path: `~` expanded, relative paths against `cwd` (default: the
    current directory). Matching quotes around a pasted path are removed."""
    spec = spec.strip()
    if len(spec) >= 2 and spec[0] == spec[-1] and spec[0] in "'\"":
        spec = spec[1:-1]
    path = Path(spec).expanduser()
    if not path.is_absolute():
        path = (cwd or Path.cwd()) / path
    return path


def _read_file(path: Path) -> tuple[SourceType, bytes]:
    """Type and bytes of a local source, read bounded: guarded `.html`/`.htm` capped at
    `MAX_BYTES`, every other type at `LOCAL_MAX_BYTES`; a larger file is a
    `too_large` error and is never read whole. Only a regular file (after symlinks)
    is opened; anything else is `unreadable`."""
    if not path.exists():
        raise _Failure("not_found")
    kind = EXTENSIONS.get(path.suffix.lower())
    if kind is None:
        raise _Failure("unsupported", ext=path.suffix or "(none)")
    if not path.is_file():  # a FIFO or device would block the read (follows symlinks)
        raise _Failure("unreadable", detail="not a regular file")
    cap, mb = (MAX_BYTES, MAX_MB) if kind in GUARDED_TYPES else (LOCAL_MAX_BYTES, LOCAL_MAX_MB)
    try:
        with path.open("rb") as fh:
            raw = fh.read(cap + 1)
    except OSError as exc:
        raise _Failure("unreadable", detail=short_error(exc)) from exc
    if len(raw) > cap:
        raise _Failure("too_large", mb=mb)
    return kind, raw


def ingest_source(
    ctx: ToolContext,
    spec: str,
    *,
    cwd: Path | None = None,
    cv: bool = False,
    scorer: Scorer | None = None,
) -> SourceResult:
    """One supplied source: a path (`.txt`, `.md`, `.pdf`, `.html`, `.htm`) or URL."""
    spec = spec.strip()
    if is_url(spec):
        try:
            fetched = fetch_url(ctx, spec)
        except _Failure as failure:
            return _error(ctx, spec, failure, type="url")
        return ingest_bytes(
            ctx,
            fetched.raw,
            origin=spec,
            kind="url",
            final_url=fetched.final_url,
            charset=fetched.charset,
            cv=cv,
            scorer=scorer,
        )
    path = resolve_path(spec, cwd)
    try:
        kind, raw = _read_file(path)
    except _Failure as failure:
        return _error(ctx, str(path), failure, type=EXTENSIONS.get(path.suffix.lower()))
    return ingest_bytes(
        ctx, raw, origin=str(path), kind=kind, ext=path.suffix.lower(), cv=cv, scorer=scorer
    )


def ingest_sources(
    ctx: ToolContext,
    specs: Iterable[str],
    *,
    cwd: Path | None = None,
    scorer: Scorer | None = None,
) -> list[SourceResult]:
    """Every supplied source in order; a failed one never ends the batch."""
    return [ingest_source(ctx, s, cwd=cwd, scorer=scorer) for s in specs if s.strip()]


# --- dropped-in files ------------------------------------------------------------


def dropped_files(workspace: Path) -> list[Path]:
    """Supported files directly in `profile/sources/` (not the manifest, not hidden)."""
    root = sources_dir(workspace)
    try:
        entries = sorted(root.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []
    return [
        p
        for p in entries
        if p.is_file()
        and not p.name.startswith(".")
        and p.name != MANIFEST_FILE
        and p.suffix.lower() in EXTENSIONS
    ]


def scan_dropped(ctx: ToolContext, *, scorer: Scorer | None = None) -> list[SourceResult]:
    """Ingest files dropped into `profile/sources/` by hand (R11), `.html` guarded.

    A stored or quarantined drop-in is moved out of the folder root, so it is picked up
    once. A duplicate, a refused or a failed one stays where it is.
    """
    results = []
    for path in dropped_files(ctx.workspace):
        try:
            kind, raw = _read_file(path)
        except _Failure as failure:
            results.append(
                _error(
                    ctx,
                    str(path),
                    failure,
                    type=EXTENSIONS.get(path.suffix.lower()),
                    dropped_in=True,
                )
            )
            continue
        results.append(
            ingest_bytes(
                ctx,
                raw,
                origin=str(path),
                kind=kind,
                ext=path.suffix.lower(),
                dropped=path,
                scorer=scorer,
            )
        )
    return results


__all__ = [
    "Fetched",
    "SourcesError",
    "decode_html",
    "decode_text",
    "dropped_files",
    "ensure_sources_dir",
    "fetch_url",
    "ingest_bytes",
    "ingest_source",
    "ingest_sources",
    "load_manifest",
    "manifest_path",
    "mark_extracted",
    "read_pdf",
    "save_manifest",
    "scan_dropped",
    "set_cv",
    "source_text",
    "sources_dir",
    "unextracted",
]
