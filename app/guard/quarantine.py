"""Quarantine store: flagged content encrypted at rest (R5, T5).

Layout: `<workspace>/quarantine/<id>/content.enc` (the original bytes as a Fernet
token) and `report.json` (metadata only, never an excerpt). Directories are 0700,
files 0600.

The Fernet key is `SORTINGCAP_QUARANTINE_KEY`, read through `app.credentials`
(environment first, then `<workspace>/.env`). It is generated and written to `.env`
on a `create` only while no item still holds a `content.enc`, never by listing or
decrypting. If the key is missing while such items exist, `create` raises
`QuarantineKeyLost` and writes nothing, so items are never encrypted under two
different keys. It is not one of `credentials.KNOWN_KEYS`, so `/settings` never
shows it.

Listing needs no key (reports are plaintext). Decrypting without the key, with a
wrong key or on a tampered item fails with a clear error and deletes nothing.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import secrets
import shutil
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from pydantic import ValidationError

from app.credentials import Credentials
from app.errors import LocalizedError
from app.files import private_dir, write_private
from app.schemas.guard import QuarantineReport

QUARANTINE_DIR = "quarantine"
QUARANTINE_KEY = "SORTINGCAP_QUARANTINE_KEY"
CONTENT_FILE = "content.enc"
REPORT_FILE = "report.json"

_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")


class QuarantineError(LocalizedError):
    """A quarantine item cannot be read, decrypted or changed."""


class QuarantineKeyMissing(QuarantineError):
    def __init__(self) -> None:
        super().__init__("quarantine.key_missing", name=QUARANTINE_KEY)


class QuarantineKeyLost(QuarantineKeyMissing):
    """The key is missing while encrypted items exist, so new content cannot be
    quarantined (T5). Callers treat it as fail closed: the flagged input is not
    stored, not quarantined and not forwarded."""

    def __init__(self, count: int) -> None:
        QuarantineError.__init__(
            self, "quarantine.key_lost", name=QUARANTINE_KEY, count=count, dir=QUARANTINE_DIR
        )


class Quarantine:
    def __init__(self, workspace: Path, credentials: Credentials) -> None:
        self.workspace = workspace
        self._credentials = credentials

    def __repr__(self) -> str:
        return f"Quarantine({self.workspace!s})"

    @property
    def root(self) -> Path:
        return self.workspace / QUARANTINE_DIR

    # --- key ---------------------------------------------------------------

    def _fernet(self, *, create: bool) -> Fernet:
        key = self._credentials.get(QUARANTINE_KEY)
        if not key:
            if not create:
                raise QuarantineKeyMissing()
            if encrypted := self._encrypted_items():
                raise QuarantineKeyLost(encrypted)
            key = Fernet.generate_key().decode("ascii")
            self._credentials.set(QUARANTINE_KEY, key)
        try:
            return Fernet(key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise QuarantineError("quarantine.key_invalid", name=QUARANTINE_KEY) from exc

    def _encrypted_items(self) -> int:
        """How many item directories still hold a `content.enc`, whatever their report
        says: each one was encrypted under the existing key."""
        try:
            entries = list(self.root.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            return 0
        return sum(1 for e in entries if e.is_dir() and (e / CONTENT_FILE).exists())

    # --- items -------------------------------------------------------------

    def _item_dir(self, item_id: str) -> Path:
        if not _ID.match(item_id or ""):
            raise QuarantineError("quarantine.not_found", id=item_id)
        path = self.root / item_id
        if not (path / REPORT_FILE).is_file():
            raise QuarantineError("quarantine.not_found", id=item_id)
        return path

    def _new_dir(self, now: datetime) -> tuple[str, Path]:
        private_dir(self.root)
        while True:
            item_id = f"{now:%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
            path = self.root / item_id
            try:
                private_dir(path, exist_ok=False)
            except FileExistsError:
                continue
            return item_id, path

    def create(
        self,
        content: bytes | str,
        source: str,
        *,
        matched_rules: list[str] | tuple[str, ...] = (),
        max_score: float | None = None,
        now: datetime | None = None,
    ) -> QuarantineReport:
        """Encrypt `content` into a new item and write its report; return the report."""
        raw = content.encode("utf-8") if isinstance(content, str) else bytes(content)
        now = now or datetime.now(UTC)
        # Validate the report before anything touches the disk.
        draft = QuarantineReport(
            id="pending",
            source=source,
            created_at=now,
            matched_rules=list(matched_rules),
            max_score=max_score,
            sha256=hashlib.sha256(raw).hexdigest(),
        )
        token = self._fernet(create=True).encrypt(raw)
        item_id, path = self._new_dir(now)
        report = draft.model_copy(update={"id": item_id})
        try:
            write_private(path / CONTENT_FILE, token.decode("ascii"))
            write_private(path / REPORT_FILE, report.model_dump_json(indent=2) + "\n")
        except BaseException:
            shutil.rmtree(path, ignore_errors=True)
            raise
        return report

    def report(self, item_id: str) -> QuarantineReport:
        path = self._item_dir(item_id) / REPORT_FILE
        try:
            report = QuarantineReport.model_validate_json(path.read_bytes())
        except (OSError, ValidationError) as exc:
            raise QuarantineError("quarantine.report_invalid", id=item_id) from exc
        if report.id != item_id:
            raise QuarantineError("quarantine.report_invalid", id=item_id)
        return report

    def list(self) -> list[QuarantineReport]:
        """Every readable report, oldest first; stray or broken entries are skipped."""
        try:
            entries = sorted(self.root.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            return []
        reports = []
        for entry in entries:
            if not entry.is_dir() or not _ID.match(entry.name):
                continue
            try:
                reports.append(self.report(entry.name))
            except QuarantineError:
                continue
        return sorted(reports, key=lambda r: (r.created_at, r.id))

    def decrypt(self, item_id: str) -> bytes:
        """The original bytes, in memory only. Never writes anything."""
        report = self.report(item_id)
        if report.status == "released":
            raise QuarantineError("quarantine.released", id=item_id)
        path = self.root / item_id / CONTENT_FILE
        try:
            token = path.read_bytes()
        except FileNotFoundError as exc:
            raise QuarantineError("quarantine.content_missing", id=item_id) from exc
        fernet = self._fernet(create=False)
        try:
            raw = fernet.decrypt(token)
        except InvalidToken as exc:
            raise QuarantineError("quarantine.key_wrong", id=item_id, name=QUARANTINE_KEY) from exc
        if hashlib.sha256(raw).hexdigest() != report.sha256:
            raise QuarantineError("quarantine.corrupt", id=item_id)
        return raw

    def mark_released(self, item_id: str, *, now: datetime | None = None) -> QuarantineReport:
        """Set the report to `released` with the time, then delete `content.enc`."""
        report = self.report(item_id)
        if report.status == "released":
            raise QuarantineError("quarantine.released", id=item_id)
        released = QuarantineReport.model_validate(
            {
                **report.model_dump(),
                "status": "released",
                "released_at": now or datetime.now(UTC),
            }
        )
        path = self.root / item_id
        write_private(path / REPORT_FILE, released.model_dump_json(indent=2) + "\n")
        with contextlib.suppress(FileNotFoundError):
            os.unlink(path / CONTENT_FILE)
        return released


# --- display -----------------------------------------------------------------

_KEEP = frozenset("\n\t")
_ESCAPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp", "Cs", "Co", "Cn"})


def _escape_char(ch: str) -> str:
    code = ord(ch)
    if code < 0x100:
        return f"\\x{code:02x}"
    if code < 0x10000:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def escape_controls(text: str) -> str:
    """Text safe to print: control, format (bidi, zero-width) and separator characters
    other than newline and tab are shown as `\\xNN` / `\\uNNNN` escapes."""
    return "".join(
        _escape_char(ch)
        if ch not in _KEEP and unicodedata.category(ch) in _ESCAPED_CATEGORIES
        else ch
        for ch in text
    )
