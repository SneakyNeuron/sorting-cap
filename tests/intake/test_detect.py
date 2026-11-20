"""Source detection in user messages and the `ingest_source` allow rule (Phase 3 R5, R6, T6, T7)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.intake.detect import MAX_SOURCES, allow_ingest, detect, split_cap


@pytest.fixture
def here(tmp_path):
    folder = tmp_path / "here"
    folder.mkdir()
    (folder / "README.md").write_text("x")
    (folder / "docs").mkdir()
    (folder / "docs" / "cv.PDF").write_text("x")
    return folder


def found(message: str, cwd: Path) -> list[tuple[str, str, bool]]:
    return [(d.normalised, d.kind, d.explicit) for d in detect(message, cwd)]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        # Czech and English sentences with trailing punctuation
        (
            "Tady je moje CV: ~/Dokumenty/cv.pdf.",
            [("~/Dokumenty/cv.pdf", "path", True)],
        ),
        (
            "My profile is at https://example.com/me?tab=cv&lang=en, have a look!",
            [("https://example.com/me?tab=cv&lang=en", "url", True)],
        ),
        (
            "Podívej se na www.firma.cz/kariera).",
            [("https://www.firma.cz/kariera", "url", True)],
        ),
        ("(see http://x.org/a.pdf)", [("http://x.org/a.pdf", "url", True)]),
        ("Absolute: /home/u/notes.txt;", [("/home/u/notes.txt", "path", True)]),
        (
            "relative ./a/b.md and ../c.htm!",
            [("./a/b.md", "path", True), ("../c.htm", "path", True)],
        ),
        (
            "UPPER: ~/CV.PDF, ~/page.HTML",
            [("~/CV.PDF", "path", True), ("~/page.HTML", "path", True)],
        ),
        # quoted paths with spaces, ASCII and Czech quotes
        (
            'soubor "~/Moje dokumenty/životopis.pdf", prosím',
            [("~/Moje dokumenty/životopis.pdf", "path", True)],
        ),
        ("file '/tmp/my cv.md'.", [("/tmp/my cv.md", "path", True)]),
        ("soubor „~/Plocha/můj text.txt“", [("~/Plocha/můj text.txt", "path", True)]),
        # bare relative words only when they exist
        ("Read README.md please", [("README.md", "path", False)]),
        ("and docs/cv.PDF too", [("docs/cv.PDF", "path", False)]),
        ("missing.md and other/none.pdf", []),
        # not sources
        ("write to e-mail@x.cz", []),
        ("I use version 1.md5 of it", []),
        ("firma.cz/kariera is the page", []),
        ("I'm done with Java, it's fine", []),
        ("just .md and www. alone", []),
        ("ftp://x.org/a.pdf", []),
        ("", []),
    ],
)
def test_detection_table(here, message, expected):
    assert found(message, here) == expected


def test_spec_as_written_and_position(here):
    message = 'see "~/a b.pdf" and https://x.cz/'
    first, second = detect(message, here)
    assert first.spec == '"~/a b.pdf"'
    assert first.position == message.index('"')
    assert second.spec == "https://x.cz/"
    assert second.position == message.index("https")


def test_explicit_missing_path_is_kept(here):
    # the caller reports it as not found (R5); only bare words are dropped
    assert found("~/nowhere/cv.pdf", here) == [("~/nowhere/cv.pdf", "path", True)]


def test_order_is_by_position(here):
    message = "README.md 'x y.md' https://a.cz"
    assert [d.kind for d in detect(message, here)] == ["path", "path", "url"]
    positions = [d.position for d in detect(message, here)]
    assert positions == sorted(positions)


def test_more_than_ten_sources(here):
    message = " ".join(f"https://x.cz/{i}" for i in range(12))
    sources = detect(message, here)
    assert len(sources) == 12
    taken, skipped = split_cap(sources)
    assert MAX_SOURCES == 10
    assert [d.normalised for d in taken] == [f"https://x.cz/{i}" for i in range(10)]
    assert [d.normalised for d in skipped] == ["https://x.cz/10", "https://x.cz/11"]


@pytest.mark.parametrize(
    "chunk",
    [
        "http://",
        "https://www.",
        ".",
        "/",
        '"',
        "'",
        "„",
        ".md",
        "a.md ",
        "~/x.pdf ",
        "http://. / ' \" .md www.a. ",
        "'a b.pdf",
        '"x \'y.md z" ',
    ],
)
def test_adversarial_input_is_linear(here, chunk):
    message = chunk * (1024 * 1024 // len(chunk))
    started = time.monotonic()
    detect(message, here)
    assert time.monotonic() - started < 5


# --- allow rule (T7) ------------------------------------------------------------------


def verdict(spec, messages, found_paths=(), home=Path("/home/u"), cwd=Path("/w"), detected=()):
    return allow_ingest(
        spec, messages, list(found_paths), home=home, cwd=cwd, detected=list(detected)
    )


@pytest.mark.parametrize(
    ("spec", "messages", "decision"),
    [
        ("https://firma.cz/jobs?id=1", ["see https://firma.cz/jobs?id=1."], "allowed"),
        ("http://firma.cz/jobs/", ["see https://firma.cz/jobs"], "refused"),
        ("https://firma.cz/jobs/", ["see http://firma.cz/jobs"], "allowed"),
        ("http://firma.cz/jobs/", ["see http://firma.cz/jobs"], "allowed"),
        ("https://firma.cz/kariera", ["moje firma.cz/kariera, díky"], "allowed"),
        ("https://www.firma.cz", ["www.firma.cz!"], "allowed"),
        ("www.firma.cz", ["see www.firma.cz"], "allowed"),
        ("https://FIRMA.cz/kariera", ["firma.cz/kariera"], "allowed"),
        ("https://firma.cz/kariera", ["firma.cz/Kariera"], "refused"),
        ("https://evil.cz/x", ["firma.cz/kariera"], "refused"),
        ("https://e-mail@x.cz", ["mail me at e-mail@x.cz"], "refused"),
        ("https://firma.cz/kariera/x", ["firma.cz/kariera"], "refused"),
        ("~/cv.pdf", ["this is my CV: ~/cv.pdf"], "allowed"),
        ("/home/u/cv.pdf", ["this is my CV: ~/cv.pdf"], "allowed"),
        ("~/Downloads/cv.pdf", ["cv.pdf in my Downloads"], "confirm"),
        ("/home/u/Downloads/CV.pdf", ["cv.pdf in my Downloads"], "refused"),
        ("/tmp/a b/cv 2.pdf", ['it is "~/x/cv 2.pdf"'], "confirm"),
        ("/home/u/x/cv 2.pdf", ['it is "~/x/cv 2.pdf"'], "allowed"),
        ("cv.pdf", ["my ./cv.pdf"], "allowed"),
        ("/w/cv.pdf", ["my cv.pdf"], "allowed"),
        ('"~/cv.pdf"', ["~/cv.pdf"], "allowed"),
        ("/etc/secret.txt", ["my cv.pdf"], "refused"),
        ("", ["anything"], "refused"),
        ("~/cv.pdf", [], "refused"),
    ],
)
def test_allow_rule_table(spec, messages, decision):
    assert verdict(spec, messages).decision == decision


def test_url_only_in_a_value_is_refused():
    # the agent saw the URL in a profile value or a source report, not in a user message
    result = verdict("https://evil.example/payload", ["please add my LinkedIn"])
    assert result.decision == "refused"
    assert result.reason == "url_not_typed"


def test_unrelated_path_reason():
    assert verdict("/etc/passwd", ["hello"]).reason == "path_not_typed"


def test_found_file_needs_confirmation():
    found_paths = ["/home/u/Downloads/Životopis 2026.pdf"]
    result = verdict("/home/u/Downloads/Životopis 2026.pdf", ["my CV is in Downloads"], found_paths)
    assert result.decision == "confirm"
    assert result.spec == "/home/u/Downloads/Životopis 2026.pdf"
    assert result.kind == "path"
    # also when given with `~`
    assert verdict("~/Downloads/Životopis 2026.pdf", [], found_paths).decision == "confirm"


def test_typed_name_only_needs_confirmation():
    # validation fix D6: a file-name match, also on a `find_files` hit, is confirmed
    found_paths = ["/home/u/Downloads/cv.pdf"]
    assert verdict("/home/u/Downloads/cv.pdf", ["it is cv.pdf"], found_paths).decision == "confirm"
    assert verdict("/home/u/Downloads/cv.pdf", ["it is ~/Downloads/cv.pdf"]).decision == ("allowed")


def test_url_is_never_allowed_through_found_files():
    assert verdict("https://x.cz/a.pdf", [], ["https://x.cz/a.pdf"]).decision == "refused"


def test_verdict_normalises_spec():
    assert verdict("www.firma.cz", ["www.firma.cz"]).spec == "https://www.firma.cz"
    assert verdict(' "~/cv.pdf" ', ["~/cv.pdf"]).spec == "/home/u/cv.pdf"
    assert verdict("cv.pdf", ["cv.pdf"]).spec == "/w/cv.pdf"
    assert verdict("https://x.cz", ["https://x.cz"]).kind == "url"


def test_allow_rule_reads_no_files(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("file system touched")

    monkeypatch.setattr(Path, "exists", boom)
    monkeypatch.setattr(Path, "is_file", boom)
    monkeypatch.setattr(Path, "stat", boom)
    monkeypatch.setattr(Path, "read_bytes", boom)
    assert verdict("/x/cv.pdf", ["/x/cv.pdf"]).decision == "allowed"
    assert verdict("/x/cv.pdf", ["cv.pdf"]).decision == "confirm"
    assert verdict("/x/other.pdf", ["cv.pdf"]).decision == "refused"


def test_allow_rule_on_adversarial_messages_is_fast():
    message = "http://. / ' \" .md www.a. " * (1024 * 1024 // 26)
    started = time.monotonic()
    verdict("https://x.cz", [message])
    verdict("~/x.pdf", [message])
    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    ("message", "host"),
    [
        ("mám 10.1 let praxe", "10.1"),
        ("10.5 years", "10.5"),
        ("verze 127.1", "127.1"),
        ("see 0x7f.1", "0x7f.1"),
        ("see 0x7f.0x1", "0x7f.0x1"),
        ("see 0177.0.0.1", "0177.0.0.1"),
        ("host 127.0.0.1", "127.0.0.1"),
        ("host 192.168.1.10:8080", "192.168.1.10:8080"),
        ("host [::ffff:127.0.0.1]", "[::ffff:127.0.0.1]"),
        ("host 1.2.3.4.", "1.2.3.4"),
    ],
)
def test_numeric_bare_token_is_not_a_typed_host(message, host):
    # a bare token whose host is an IP literal or has a numeric last label is no host
    for spec in (f"http://{host}", f"https://{host}/"):
        result = verdict(spec, [message])
        assert result.decision == "refused"
        assert result.reason == "url_not_typed"


def test_ip_literal_typed_with_scheme_stays_allowed():
    result = verdict("http://127.0.0.1", ["see http://127.0.0.1"])
    assert result.decision == "allowed"
    assert result.bare_host is False
    assert verdict("http://[::1]/", ["at http://[::1]/"]).decision == "allowed"


def test_bare_host_flag():
    bare = verdict("https://firma.cz/kariera", ["moje firma.cz/kariera, díky"])
    assert bare.decision == "allowed"
    assert bare.bare_host is True
    assert verdict("https://firma.cz/kariera", ["https://firma.cz/kariera"]).bare_host is False
    assert verdict("https://www.firma.cz", ["www.firma.cz"]).bare_host is False
    # typed both ways: the scheme-typed match wins
    both = verdict("https://firma.cz/kariera", ["firma.cz/kariera", "http://firma.cz/kariera"])
    assert both.bare_host is False
    assert verdict("https://evil.cz", ["firma.cz"]).bare_host is False


def test_bare_host_with_letters_and_port():
    assert verdict("https://firma.cz:8080/x", ["firma.cz:8080/x"]).decision == "allowed"
    assert verdict("https://my.host1/x", ["my.host1/x"]).decision == "allowed"


def test_verdict_url_has_no_trailing_punctuation():
    result = verdict("https://firma.cz/kariera).", ["see https://firma.cz/kariera)."])
    assert result.decision == "allowed"
    assert result.spec == "https://firma.cz/kariera"
    assert verdict("www.firma.cz!", ["www.firma.cz"]).spec == "https://www.firma.cz"


@pytest.mark.parametrize(
    "token", ["fe80::1/a.pdf", "fe80::1%eth0/x.html", "::1/cv.md", "2001:db8::5/a.b"]
)
def test_unbracketed_ipv6_bare_token_is_not_a_typed_host(token):
    # T7: no IPv6 literal in any form; the host is no longer cut at the first `:`
    result = verdict(f"http://{token}", [f"see {token}"])
    assert result.decision == "refused"
    assert result.reason == "url_not_typed"


# --- validation fix D3: a bare `host/path` spec without a scheme ---------------------


def test_bare_spec_typed_as_bare_token_is_a_bare_host_url():
    # R6's own example passed as the user wrote it, without `https://`
    result = verdict("firma.cz/kariera", ["moje firma.cz/kariera, díky"])
    assert (result.decision, result.kind, result.spec, result.bare_host) == (
        "allowed",
        "url",
        "https://firma.cz/kariera",
        True,
    )
    assert verdict("firma.cz/kariera).", ["firma.cz/kariera"]).spec == "https://firma.cz/kariera"
    assert verdict("firma.cz/cv.pdf", ["see firma.cz/cv.pdf"]).kind == "url"
    host_only = verdict("firma.cz", ["see firma.cz"])
    assert (host_only.kind, host_only.spec, host_only.bare_host) == (
        "url",
        "https://firma.cz",
        True,
    )


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("cv.pdf", "my cv.pdf"),  # a bare file name stays a path
        ("README.md", "see README.md"),
        ("docs/cv.pdf", "see docs/cv.pdf"),  # no dot in the first segment
        ("./firma.cz/kariera", "firma.cz/kariera"),  # path-like prefixes
        ("../firma.cz/kariera", "firma.cz/kariera"),
        ("/firma.cz/kariera", "firma.cz/kariera"),
        ("~/firma.cz/kariera", "firma.cz/kariera"),
    ],
)
def test_path_like_or_file_name_spec_stays_a_path(spec, message):
    result = verdict(spec, [message])
    assert result.kind == "path" and result.bare_host is False


def test_bare_spec_not_typed_is_refused():
    assert verdict("firma.cz/kariera", ["hello"]).decision == "refused"
    assert verdict("10.1/x", ["10.1/x"]).kind == "path"  # an address is never a bare host


# --- validation fixes D6 and D8 (security) -------------------------------------------


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("/home/u/.config/x/notes.txt", "my notes.txt are old"),
        ("~/.ssh/cv.pdf", "cv.pdf is attached"),
    ],
)
def test_file_name_match_needs_confirmation_with_the_full_path(spec, message):
    result = verdict(spec, [message])
    assert result.decision == "confirm"
    assert result.spec == spec.replace("~", "/home/u", 1)
    assert result.kind == "path"


@pytest.mark.parametrize(
    "message",
    [
        "see https://firma.cz/jobs/cv.pdf",
        "see www.firma.cz/cv.pdf",
        "see firma.cz/jobs/cv.pdf",
    ],
)
def test_name_from_a_url_token_is_no_typed_file_name(message):
    result = verdict("/etc/x/cv.pdf", [message])
    assert result.decision == "refused"
    assert result.reason == "path_not_typed"


def test_found_file_with_a_typed_name_is_confirmed_not_allowed():
    found_paths = ["/home/u/.config/x/notes.txt"]
    result = verdict("/home/u/.config/x/notes.txt", ["my notes.txt"], found_paths)
    assert result.decision == "confirm"


def test_exact_path_after_resolution_is_allowed():
    assert verdict("/w/docs/cv.pdf", ["see docs/cv.pdf"]).decision == "allowed"
    assert verdict("docs/./cv.pdf", ["see ./docs/cv.pdf"]).spec == "/w/docs/cv.pdf"
    # `..` is kept: it may cross a symlink, so it is not folded away
    assert verdict("/w/x/../cv.pdf", ["see /w/cv.pdf"]).decision == "confirm"


def test_detected_path_is_not_read_as_a_bare_link():
    # `project.v2/notes.md` exists and was detected as a path: it stays a path
    result = verdict(
        "project.v2/notes.md", ["see project.v2/notes.md"], detected=["project.v2/notes.md"]
    )
    assert (result.decision, result.kind) == ("allowed", "path")
    assert result.spec == "/w/project.v2/notes.md"
    # not detected (no such file): the bare-link reading of batch B stays
    assert verdict("project.v2/notes.md", ["see project.v2/notes.md"]).kind == "url"


def test_https_typed_is_never_downgraded():
    result = verdict("http://firma.cz/kariera", ["https://firma.cz/kariera"])
    assert result.decision == "refused"
    assert result.reason == "url_not_typed"


def test_www_and_bare_tokens_allow_https_only():
    assert verdict("http://www.firma.cz", ["www.firma.cz"]).decision == "refused"
    assert verdict("http://firma.cz/kariera", ["firma.cz/kariera"]).decision == "refused"
    assert verdict("https://firma.cz/kariera", ["firma.cz/kariera"]).decision == "allowed"
