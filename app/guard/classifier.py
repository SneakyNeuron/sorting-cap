"""Local prompt-injection classifier: pinned download, checksum, ONNX scoring (T3, T4).

The model is `protectai/deberta-v3-base-prompt-injection-v2` (Apache-2.0), the ONNX
export in that repository at a pinned revision. Its files live in
`<workspace>/models/prompt-guard/<revision>/` under their repository paths. Each file
is checked against its pinned SHA-256 after download (into a temporary file, renamed
only after the checksum matches) and on the first load in a process, not again
within that process.

Scoring is behind the `Scorer` protocol (`score(text) -> float`, the highest
`INJECTION` probability over all windows), so tests inject a fake. `WindowedScorer`
does the windowing: at most 510 text tokens per window with a 64-token overlap, each
wrapped in `[CLS]` … `[SEP]` by the app (512 in total).

The download runs only after an explicit yes in chat (`offer_download`), never
headless, even with `--yes` (R8). `ensure_classifier` is what guarded input calls:
it returns a scorer, offers the download in chat, or fails closed with
`GuardUnavailable` (R4).
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx

from app.errors import LocalizedError
from app.messages import short_error

if TYPE_CHECKING:
    from app.tools.context import ToolContext

REPO = "protectai/deberta-v3-base-prompt-injection-v2"
REVISION = "90c9989b1a342275dd0d1a95aad283c04e075671"
LICENSE = "Apache-2.0"
BASE_URL = "https://huggingface.co"
MODELS_DIR = Path("models") / "prompt-guard"


@dataclass(frozen=True)
class ModelFile:
    path: str  # repository path, also the path under the model directory
    size: int
    sha256: str


FILES: tuple[ModelFile, ...] = (
    ModelFile(
        "onnx/model.onnx",
        738563188,
        "f0ea7f239f765aedbde7c9e163a7cb38a79c5b8853d3f76db5152172047b228c",
    ),
    ModelFile(
        "onnx/tokenizer.json",
        8648886,
        "752fe5f0d5678ad563e1bd2ecc1ddf7a3ba7e2024d0ac1dba1a72975e26dff2f",
    ),
    ModelFile(
        "onnx/config.json",
        1014,
        "3093743035223c46b1497a72e939e56fa0a50afbd7bafbf7eb8aad060b8d23f8",
    ),
)
MODEL_FILE = "onnx/model.onnx"
TOKENIZER_FILE = "onnx/tokenizer.json"

# config.json: 0 = SAFE, 1 = INJECTION; max_position_embeddings 512.
INJECTION_LABEL = 1
MAX_TEXT_TOKENS = 510
OVERLAP = 64
CLS_TOKEN = "[CLS]"
SEP_TOKEN = "[SEP]"

DOWNLOAD_TIMEOUT = 60.0
_CHUNK = 1 << 20


class GuardUnavailable(LocalizedError):
    """The classifier is missing, fails its checksum, does not load or was not
    downloaded: guarded input is refused (not stored, not quarantined, not forwarded)."""


# --- files -------------------------------------------------------------------


def model_dir(workspace: Path) -> Path:
    return workspace / MODELS_DIR / REVISION


def total_size() -> int:
    return sum(f.size for f in FILES)


def size_mb() -> int:
    return round(total_size() / 1_000_000)


def file_url(file: ModelFile) -> str:
    return f"{BASE_URL}/{REPO}/resolve/{REVISION}/{file.path}"


def is_installed(workspace: Path) -> bool:
    """Every pinned file present with its pinned size (the checksum is checked on load)."""
    root = model_dir(workspace)
    for file in FILES:
        try:
            if (root / file.path).stat().st_size != file.size:
                return False
        except OSError:
            return False
    return True


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


# Process-wide caches: a verified file's stat signature, and loaded scorers.
_verified: dict[Path, tuple[int, int]] = {}
_scorers: dict[Path, Scorer] = {}


def reset_cache() -> None:
    """Forget verified files and loaded scorers (tests; a fresh process does this)."""
    _verified.clear()
    _scorers.clear()


def _signature(path: Path) -> tuple[int, int]:
    st = path.stat()
    return st.st_size, st.st_mtime_ns


def _remember(path: Path) -> None:
    _verified[path] = _signature(path)


def verify_files(workspace: Path) -> None:
    """Check every file against its pinned SHA-256, once per process per file.

    A missing file raises `guard.classifier_missing`; a mismatching one is removed (it
    is not the pinned file, and removing it lets the next offer download it again) and
    raises `guard.classifier_corrupt`.
    """
    root = model_dir(workspace)
    for file in FILES:
        path = root / file.path
        try:
            signature = _signature(path)
        except OSError as exc:
            raise GuardUnavailable("guard.classifier_missing") from exc
        if _verified.get(path) == signature:
            continue
        if signature[0] != file.size or sha256_file(path) != file.sha256:
            _verified.pop(path, None)
            with contextlib.suppress(OSError):
                path.unlink()
            raise GuardUnavailable("guard.classifier_corrupt", file=file.path)
        _verified[path] = signature


# --- scoring -----------------------------------------------------------------


class Scorer(Protocol):
    def score(self, text: str) -> float:
        """The highest `INJECTION` probability over all windows of `text` (0 to 1)."""
        ...


def windows(
    ids: Sequence[int],
    cls_id: int,
    sep_id: int,
    *,
    size: int = MAX_TEXT_TOKENS,
    overlap: int = OVERLAP,
) -> list[list[int]]:
    """Windows of at most `size` text tokens overlapping by `overlap`, each wrapped in
    `[CLS]` … `[SEP]`. No tokens, no windows."""
    if size <= overlap:
        raise ValueError("window size must exceed the overlap")
    step = size - overlap
    result = []
    start = 0
    while start < len(ids):
        result.append([cls_id, *ids[start : start + size], sep_id])
        if start + size >= len(ids):
            break
        start += step
    return result


class WindowedScorer:
    """`Scorer` over a tokenizer (`encode`) and a per-window model (`score_window`)."""

    def __init__(
        self,
        encode: Callable[[str], Sequence[int]],
        score_window: Callable[[list[int]], float],
        cls_id: int,
        sep_id: int,
    ) -> None:
        self._encode = encode
        self._score_window = score_window
        self.cls_id = cls_id
        self.sep_id = sep_id

    def score(self, text: str) -> float:
        if not text.strip():
            return 0.0
        ids = list(self._encode(text))
        return max(
            (self._score_window(w) for w in windows(ids, self.cls_id, self.sep_id)),
            default=0.0,
        )


def _onnx_scorer(root: Path) -> WindowedScorer:
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(root / TOKENIZER_FILE))
    cls_id = tokenizer.token_to_id(CLS_TOKEN)
    sep_id = tokenizer.token_to_id(SEP_TOKEN)
    if cls_id is None or sep_id is None:
        raise ValueError("tokenizer has no [CLS]/[SEP] token")
    options = ort.SessionOptions()
    options.log_severity_level = 3
    session = ort.InferenceSession(
        str(root / MODEL_FILE), sess_options=options, providers=["CPUExecutionProvider"]
    )
    inputs = {i.name for i in session.get_inputs()}
    if not {"input_ids", "attention_mask"} <= inputs:
        raise ValueError(f"unexpected model inputs: {sorted(inputs)}")

    def encode(text: str) -> list[int]:
        return tokenizer.encode(text, add_special_tokens=False).ids

    def score_window(window: list[int]) -> float:
        ids = np.asarray([window], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": np.ones_like(ids)}
        (logits,) = session.run(["logits"], feed)
        row = np.asarray(logits[0], dtype=np.float64)
        probs = np.exp(row - row.max())
        probs /= probs.sum()
        return float(probs[INJECTION_LABEL])

    return WindowedScorer(encode, score_window, cls_id, sep_id)


def load(workspace: Path) -> Scorer:
    """The classifier for `workspace`, loaded once per process; fails closed."""
    root = model_dir(workspace)
    if root in _scorers:
        return _scorers[root]
    verify_files(workspace)
    try:
        scorer = _onnx_scorer(root)
    except Exception as exc:  # onnxruntime and tokenizers raise their own types
        raise GuardUnavailable(
            "guard.classifier_load_failed", detail=short_error(exc), dir=str(root)
        ) from exc
    _scorers[root] = scorer
    return scorer


# --- download ----------------------------------------------------------------


def is_headless(ctx: ToolContext) -> bool:
    from app.tools.context import HeadlessPrompter

    return isinstance(ctx.prompter, HeadlessPrompter)


def _download_file(ctx: ToolContext, client: httpx.Client, file: ModelFile, advance) -> None:
    dest = model_dir(ctx.workspace) / file.path
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dest.name}.", suffix=".part", dir=dest.parent)
    tmp = Path(tmp_name)
    try:
        digest = hashlib.sha256()
        written = 0
        with os.fdopen(fd, "wb") as out:
            with client.stream("GET", file_url(file), follow_redirects=True) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(_CHUNK):
                    written += len(chunk)
                    if written > file.size:
                        raise GuardUnavailable("guard.download_checksum", file=file.path)
                    digest.update(chunk)
                    out.write(chunk)
                    advance(len(chunk))
            out.flush()
            os.fsync(out.fileno())
        if written != file.size or digest.hexdigest() != file.sha256:
            raise GuardUnavailable("guard.download_checksum", file=file.path)
        os.replace(tmp, dest)
        _remember(dest)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


def download(ctx: ToolContext) -> None:
    """Fetch every missing file from the pinned revision, verify it, then rename it.

    Never runs headless. Files already present with their pinned size are kept (their
    checksum is checked on load). Failures raise `GuardUnavailable`; no partial or
    unverified file is left behind.
    """
    if is_headless(ctx):
        raise GuardUnavailable("guard.classifier_missing")
    from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TransferSpeedColumn

    root = model_dir(ctx.workspace)
    todo = [f for f in FILES if not _has_size(root / f.path, f.size)]
    ctx.say("guard.download_start", size=size_mb(), dir=str(root))
    try:
        with (
            ctx.http_client(timeout=DOWNLOAD_TIMEOUT) as client,
            Progress(
                TextColumn("{task.description}", markup=False),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                console=ctx.console,
                transient=True,
            ) as progress,
        ):
            task = progress.add_task(REPO, total=sum(f.size for f in todo))
            for file in todo:
                _download_file(ctx, client, file, lambda n: progress.update(task, advance=n))
    except KeyboardInterrupt as exc:
        raise GuardUnavailable("guard.download_interrupted") from exc
    except httpx.HTTPError as exc:
        raise GuardUnavailable("guard.download_failed", detail=short_error(exc)) from exc
    except OSError as exc:
        raise GuardUnavailable("guard.download_failed", detail=short_error(exc)) from exc
    ctx.say("guard.download_done", style="green", dir=str(root))


def _has_size(path: Path, size: int) -> bool:
    try:
        return path.stat().st_size == size
    except OSError:
        return False


def offer_download(ctx: ToolContext) -> bool:
    """Ask (default no) and download on yes. False when declined or headless."""
    if is_headless(ctx):
        return False
    question = ctx.msg(
        "guard.download_offer",
        repo=REPO,
        license=LICENSE,
        size=size_mb(),
        host=BASE_URL.removeprefix("https://"),
        dir=str(model_dir(ctx.workspace)),
    )
    if not ctx.confirm(question, default=False):
        return False
    download(ctx)
    return True


def ensure_classifier(ctx: ToolContext) -> Scorer:
    """A loaded scorer, or `GuardUnavailable` (fail closed, R4, R8).

    Missing in chat: the download is offered once per command run; a no refuses this
    input and later ones in the same run. Missing headless: refused with the hint to
    run `-setup` or add the source in chat.
    """
    if not is_installed(ctx.workspace):
        if is_headless(ctx):
            raise GuardUnavailable("guard.classifier_missing")
        if ctx.classifier_declined or not offer_download(ctx):
            ctx.classifier_declined = True
            raise GuardUnavailable("guard.classifier_declined")
    return load(ctx.workspace)
