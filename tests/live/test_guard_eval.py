"""Live evaluation of the injection guard on Czech and English samples (T12, V16).

Reports precision and recall per language, for the classifier alone and for the full
guard decision (rules or classifier score at the threshold). Not required to pass:
only a missing classifier skips, and nothing asserts a quality level.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.guard import classifier, rules
from app.settings import Settings

FIXTURES = Path(__file__).parent / "fixtures"
LANGUAGES = ("cs", "en")
pytestmark = pytest.mark.live

THRESHOLD = Settings.model_fields["guard_threshold"].default


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    def add(self, predicted: bool, actual: bool) -> None:
        if predicted and actual:
            self.tp += 1
        elif predicted:
            self.fp += 1
        elif actual:
            self.fn += 1
        else:
            self.tn += 1

    @property
    def precision(self) -> float:
        flagged = self.tp + self.fp
        return self.tp / flagged if flagged else 0.0

    @property
    def recall(self) -> float:
        positives = self.tp + self.fn
        return self.tp / positives if positives else 0.0

    def line(self, label: str) -> str:
        return (
            f"{label:<12} precision {self.precision:.2f}  recall {self.recall:.2f}  "
            f"(tp {self.tp}, fp {self.fp}, fn {self.fn}, tn {self.tn})"
        )


def _samples(lang: str) -> list[tuple[str, bool]]:
    data = json.loads((FIXTURES / f"{lang}.json").read_text(encoding="utf-8"))
    return [(t, True) for t in data["injection"]] + [(t, False) for t in data["benign"]]


@pytest.fixture(scope="module")
def scorer(real_workspace):
    if not classifier.is_installed(real_workspace):
        pytest.skip(
            f"classifier not installed in {real_workspace}; run -setup (last step) "
            "or set SORTINGCAP_LIVE_WORKSPACE"
        )
    return classifier.load(real_workspace)


@pytest.mark.parametrize("lang", LANGUAGES)
def test_guard_precision_recall(lang, scorer, capsys):
    model, guard = Counts(), Counts()
    misses: list[str] = []
    for text, is_injection in _samples(lang):
        score = scorer.score(text)
        assert 0.0 <= score <= 1.0
        by_model = score >= THRESHOLD
        by_guard = by_model or bool(rules.scan(text).matched)
        model.add(by_model, is_injection)
        guard.add(by_guard, is_injection)
        if by_guard != is_injection:
            kind = "missed" if is_injection else "false alarm"
            misses.append(f"  {kind} (score {score:.2f}): {text[:70]}")
    with capsys.disabled():
        print(f"\n[{lang}] threshold {THRESHOLD}")
        print(model.line("classifier"))
        print(guard.line("guard"))
        print("\n".join(misses))
