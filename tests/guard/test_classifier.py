"""Classifier: windowing, scoring, pinned download, checksums, load and offers (T3, T4, R8)."""

from __future__ import annotations

import hashlib

import httpx
import pytest
from helpers import ScriptedPrompter, make_ctx

from app.guard import classifier
from app.guard.classifier import (
    FILES,
    REPO,
    REVISION,
    GuardUnavailable,
    ModelFile,
    WindowedScorer,
    ensure_classifier,
    is_installed,
    model_dir,
    windows,
)
from app.tools.context import HeadlessPrompter

CLS, SEP = 1, 2


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture(autouse=True)
def fresh_cache():
    classifier.reset_cache()
    yield
    classifier.reset_cache()


@pytest.fixture
def blobs(monkeypatch):
    """Small stand-ins for the pinned files, pinned to their own hashes."""
    data = {
        "onnx/model.onnx": b"\x08\x07model-bytes" * 50,
        "onnx/tokenizer.json": b'{"version": "1.0"}',
        "onnx/config.json": b'{"id2label": {"0": "SAFE", "1": "INJECTION"}}',
    }
    monkeypatch.setattr(
        classifier, "FILES", tuple(ModelFile(p, len(b), sha(b)) for p, b in data.items())
    )
    return data


class FakeScorer:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def score(self, text: str) -> float:
        return self.value


@pytest.fixture
def fake_load(monkeypatch):
    """The ONNX/tokenizer step replaced: checksums still run, nothing is parsed."""
    built = []

    def build(root):
        built.append(root)
        return FakeScorer(0.0)

    monkeypatch.setattr(classifier, "_onnx_scorer", build)
    return built


def install(workspace, data):
    root = model_dir(workspace)
    for path, content in data.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_bytes(content)


def server(data, calls, *, override=None, redirect=False):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "huggingface.co" and redirect:
            return httpx.Response(
                302, headers={"location": f"https://cdn.example{request.url.path}"}
            )
        path = request.url.path.split(f"/resolve/{REVISION}/", 1)[1]
        return httpx.Response(200, content=(override or {}).get(path, data[path]))

    return handler


def leftovers(workspace):
    root = model_dir(workspace)
    return [p.name for p in root.rglob("*") if p.is_file() and p.name.endswith(".part")]


# --- pinned values -----------------------------------------------------------


def test_pinned_revision_and_files():
    assert REPO == "protectai/deberta-v3-base-prompt-injection-v2"
    assert REVISION == "90c9989b1a342275dd0d1a95aad283c04e075671"
    assert [f.path for f in FILES] == ["onnx/model.onnx", "onnx/tokenizer.json", "onnx/config.json"]
    assert all(len(f.sha256) == 64 for f in FILES)
    assert classifier.file_url(FILES[0]) == (
        f"https://huggingface.co/{REPO}/resolve/{REVISION}/onnx/model.onnx"
    )


def test_model_dir_under_workspace(workspace):
    assert model_dir(workspace) == workspace / "models" / "prompt-guard" / REVISION


# --- windowing and aggregation -----------------------------------------------


def test_windows_510_text_tokens_with_cls_sep_and_64_overlap():
    ids = list(range(100, 1100))  # 1000 text tokens
    result = windows(ids, CLS, SEP)
    assert [len(w) for w in result] == [512, 512, 512 - (892 + 510 - 1000)]
    for w in result:
        assert w[0] == CLS and w[-1] == SEP and len(w) <= 512
    texts = [w[1:-1] for w in result]
    assert texts[0][0] == 100 and texts[1][0] == 100 + 446 and texts[2][0] == 100 + 892
    assert texts[0][-64:] == texts[1][:64]  # 64-token overlap
    assert texts[1][-64:] == texts[2][:64]
    assert texts[-1][-1] == 1099  # every token covered


def test_windows_edges():
    assert windows([], CLS, SEP) == []
    assert windows(list(range(510)), CLS, SEP) == [[CLS, *range(510), SEP]]
    two = windows(list(range(511)), CLS, SEP)
    assert len(two) == 2 and two[1][1:-1] == list(range(446, 511))


def test_max_score_over_windows_with_fake_scorer():
    seen = []

    def score_window(window):
        seen.append(window)
        return 0.93 if 1150 in window else 0.1

    scorer = WindowedScorer(lambda text: list(range(1200)), score_window, CLS, SEP)
    assert scorer.score("long text") == pytest.approx(0.93)
    assert len(seen) == 3
    assert all(w[0] == CLS and w[-1] == SEP and len(w) <= 512 for w in seen)


def test_empty_text_scores_zero_without_model():
    scorer = WindowedScorer(lambda t: pytest.fail("encoded"), lambda w: 1.0, CLS, SEP)
    assert scorer.score("  \n ") == 0.0


def test_onnx_scorer_feeds_windows_and_softmaxes(workspace, monkeypatch):
    """The real tokenizer path and the session feed, with a fake ONNX session."""
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import Whitespace

    vocab = {"[UNK]": 0, "[CLS]": 1, "[SEP]": 2, "hello": 3, "evil": 4}
    tok = Tokenizer(WordLevel(vocab, unk_token="[UNK]"))
    tok.pre_tokenizer = Whitespace()
    root = model_dir(workspace) / "onnx"
    root.mkdir(parents=True)
    tok.save(str(root / "tokenizer.json"))

    feeds = []

    class Input:
        def __init__(self, name):
            self.name = name

    class Session:
        def __init__(self, path, sess_options=None, providers=None):
            assert providers == ["CPUExecutionProvider"]

        def get_inputs(self):
            return [Input("input_ids"), Input("attention_mask")]

        def run(self, outputs, feed):
            assert outputs == ["logits"]
            feeds.append(feed)
            evil = 4 in feed["input_ids"][0]
            return [np.array([[0.0, 3.0] if evil else [3.0, 0.0]], dtype=np.float32)]

    monkeypatch.setattr(ort, "InferenceSession", Session)
    scorer = classifier._onnx_scorer(model_dir(workspace))
    assert scorer.score("hello " * 600 + "evil") == pytest.approx(1 / (1 + np.exp(-3.0)))
    assert len(feeds) == 2
    first = feeds[0]
    assert first["input_ids"].dtype == np.int64 and first["input_ids"].shape == (1, 512)
    assert first["input_ids"][0][0] == 1 and first["input_ids"][0][-1] == 2
    assert (first["attention_mask"] == 1).all()
    assert scorer.score("hello") == pytest.approx(1 / (1 + np.exp(3.0)))
    assert isinstance(Tokenizer.from_file(str(root / "tokenizer.json")), Tokenizer)


# --- checksums and load --------------------------------------------------------


def test_checksum_verified_once_per_process(workspace, blobs, fake_load, monkeypatch):
    install(workspace, blobs)
    hashed = []
    real = classifier.sha256_file
    monkeypatch.setattr(classifier, "sha256_file", lambda p: hashed.append(p) or real(p))
    classifier.load(workspace)
    classifier.verify_files(workspace)
    classifier._scorers.clear()  # even a second load does not re-hash
    classifier.load(workspace)
    assert len(hashed) == len(blobs)
    classifier.reset_cache()  # a new process hashes again
    classifier.load(workspace)
    assert len(hashed) == 2 * len(blobs)


def test_corrupt_model_file_fails_closed_and_is_removed(workspace, blobs, fake_load):
    tampered = dict(blobs)
    tampered["onnx/model.onnx"] = b"X" * len(blobs["onnx/model.onnx"])  # same size
    install(workspace, tampered)
    assert is_installed(workspace)
    with pytest.raises(GuardUnavailable) as exc:
        classifier.load(workspace)
    assert exc.value.key == "guard.classifier_corrupt"
    assert "/setup" in exc.value.text()
    assert not (model_dir(workspace) / "onnx/model.onnx").exists()
    assert not is_installed(workspace)
    assert fake_load == []


def test_file_changed_after_verification_is_checked_again(workspace, blobs, fake_load):
    install(workspace, blobs)
    classifier.verify_files(workspace)
    path = model_dir(workspace) / "onnx/config.json"
    path.write_bytes(b"{}")
    with pytest.raises(GuardUnavailable) as exc:
        classifier.verify_files(workspace)
    assert exc.value.key == "guard.classifier_corrupt"


def test_missing_file_fails_closed(workspace, blobs):
    install(workspace, {k: v for k, v in blobs.items() if k != "onnx/config.json"})
    assert not is_installed(workspace)
    with pytest.raises(GuardUnavailable) as exc:
        classifier.load(workspace)
    assert exc.value.key == "guard.classifier_missing"


def test_unloadable_model_fails_closed(workspace, blobs):
    """Files that match their pins but are not a model: onnxruntime/tokenizers refuse."""
    install(workspace, blobs)
    with pytest.raises(GuardUnavailable) as exc:
        classifier.load(workspace)
    assert exc.value.key == "guard.classifier_load_failed"
    assert str(model_dir(workspace)) in exc.value.text()


# --- download ------------------------------------------------------------------


def test_download_verifies_then_renames(workspace, blobs):
    calls = []
    ctx, buf = make_ctx(workspace, ScriptedPrompter(), handler=server(blobs, calls, redirect=True))
    classifier.download(ctx)
    for path, content in blobs.items():
        assert (model_dir(workspace) / path).read_bytes() == content
    assert is_installed(workspace)
    assert leftovers(workspace) == []
    hf = [c for c in calls if "huggingface.co" in c]
    assert hf == [f"https://huggingface.co/{REPO}/resolve/{REVISION}/{p}" for p in blobs]
    done = ctx.msg("guard.download_done", dir=str(model_dir(workspace)))
    assert done in "".join(buf.getvalue().split("\n"))


def test_download_keeps_files_already_present(workspace, blobs):
    install(workspace, {"onnx/tokenizer.json": blobs["onnx/tokenizer.json"]})
    calls = []
    ctx, _ = make_ctx(workspace, ScriptedPrompter(), handler=server(blobs, calls))
    classifier.download(ctx)
    assert not any(c.endswith("tokenizer.json") for c in calls)
    assert is_installed(workspace)


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param(lambda b: b"Y" * len(b), id="same-size-mismatch"),
        pytest.param(lambda b: b + b"extra", id="oversize"),
        pytest.param(lambda b: b[:-1], id="short"),
    ],
)
def test_checksum_mismatch_rejects_and_removes_temp_file(workspace, blobs, bad):
    calls = []
    override = {"onnx/model.onnx": bad(blobs["onnx/model.onnx"])}
    ctx, _ = make_ctx(
        workspace, ScriptedPrompter(), handler=server(blobs, calls, override=override)
    )
    with pytest.raises(GuardUnavailable) as exc:
        classifier.download(ctx)
    assert exc.value.key == "guard.download_checksum"
    assert not (model_dir(workspace) / "onnx/model.onnx").exists()
    assert leftovers(workspace) == []
    assert not is_installed(workspace)


def test_http_error_fails_and_leaves_nothing(workspace, blobs):
    ctx, _ = make_ctx(workspace, ScriptedPrompter(), handler=lambda r: httpx.Response(404))
    with pytest.raises(GuardUnavailable) as exc:
        classifier.download(ctx)
    assert exc.value.key == "guard.download_failed"
    assert leftovers(workspace) == [] and not is_installed(workspace)


def test_download_never_runs_headless_even_with_yes(workspace, blobs):
    calls = []
    ctx, _ = make_ctx(workspace, handler=server(blobs, calls))
    ctx.prompter = HeadlessPrompter(lambda: ctx.texts, yes=True)
    assert classifier.offer_download(ctx) is False
    with pytest.raises(GuardUnavailable):
        classifier.download(ctx)
    with pytest.raises(GuardUnavailable) as exc:
        ensure_classifier(ctx)
    assert exc.value.key == "guard.classifier_missing"
    assert "/setup" in exc.value.text(ctx.texts)
    assert calls == [] and not model_dir(workspace).exists()


def test_headless_hint_names_setup_in_both_languages(workspace):
    from app.texts import Texts

    for lang in ("cs", "en"):
        text = GuardUnavailable("guard.classifier_missing").text(Texts(lang))
        assert "/setup" in text


def test_no_download_without_a_yes(workspace, blobs):
    calls = []
    prompter = ScriptedPrompter(False)
    ctx, _ = make_ctx(workspace, prompter, handler=server(blobs, calls))
    assert classifier.offer_download(ctx) is False
    assert calls == []
    question = prompter.asked[0][1]
    assert REPO in question and "huggingface.co" in question
    assert f"{classifier.size_mb()} MB" in question


def test_offer_shows_real_size():
    assert classifier.size_mb() == 747


# --- the chat offer --------------------------------------------------------------


def test_chat_offer_yes_downloads_and_loads(workspace, blobs, fake_load):
    calls = []
    ctx, _ = make_ctx(workspace, ScriptedPrompter(True), handler=server(blobs, calls))
    scorer = ensure_classifier(ctx)
    assert isinstance(scorer, FakeScorer)
    assert is_installed(workspace) and len(calls) == len(blobs)
    assert ensure_classifier(ctx) is scorer  # loaded once, no second offer


def test_chat_offer_no_refuses_and_is_not_repeated_in_the_run(workspace, blobs):
    calls = []
    prompter = ScriptedPrompter(False)
    ctx, _ = make_ctx(workspace, prompter, handler=server(blobs, calls))
    for _ in range(2):
        with pytest.raises(GuardUnavailable) as exc:
            ensure_classifier(ctx)
        assert exc.value.key == "guard.classifier_declined"
    assert len(prompter.asked) == 1 and calls == []


def test_installed_classifier_is_not_offered(workspace, blobs, fake_load):
    install(workspace, blobs)
    ctx, _ = make_ctx(workspace)  # NoPrompts: any prompt fails the test
    assert isinstance(ensure_classifier(ctx), FakeScorer)


@pytest.mark.parametrize("failure", ["http", "checksum"])
def test_failed_download_is_not_offered_again_in_the_run(workspace, blobs, failure):
    calls = []
    if failure == "http":
        handler = lambda r: httpx.Response(404)  # noqa: E731
        key = "guard.download_failed"
    else:
        handler = server(blobs, calls, override={"onnx/config.json": b"tampered"})
        key = "guard.download_checksum"
    prompter = ScriptedPrompter(True, True)
    ctx, _ = make_ctx(workspace, prompter, handler=handler)
    with pytest.raises(GuardUnavailable) as first:
        ensure_classifier(ctx)
    assert first.value.key == key
    with pytest.raises(GuardUnavailable) as second:
        ensure_classifier(ctx)
    assert second.value.key == "guard.classifier_declined"
    assert len(prompter.asked) == 1
