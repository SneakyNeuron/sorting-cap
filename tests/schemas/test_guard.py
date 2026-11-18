"""Guard verdict and quarantine report schemas."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.guard import QuarantineReport, Verdict

SHA = "a" * 64


def test_clean_verdict():
    v = Verdict(status="clean", max_score=0.12)
    assert not v.flagged
    assert v.matched_rules == [] and v.reason is None


def test_flagged_verdict_by_rule_and_by_score():
    by_rule = Verdict(status="flagged", matched_rules=["ignore_instructions"], reason="rule")
    by_score = Verdict(status="flagged", max_score=0.93, reason="score 0.93")
    assert by_rule.flagged and by_score.flagged
    assert by_rule.max_score is None


def test_flagged_verdict_needs_a_reason():
    with pytest.raises(ValidationError):
        Verdict(status="flagged", matched_rules=["x"])
    with pytest.raises(ValidationError):
        Verdict(status="flagged", matched_rules=["x"], reason="  ")


def test_clean_verdict_cannot_carry_rule_hits():
    with pytest.raises(ValidationError):
        Verdict(status="clean", matched_rules=["ignore_instructions"])


@pytest.mark.parametrize("score", [-0.1, 1.1])
def test_score_is_a_probability(score):
    with pytest.raises(ValidationError):
        Verdict(status="clean", max_score=score)


def test_verdict_rejects_unknown_status_and_fields():
    with pytest.raises(ValidationError):
        Verdict(status="maybe")
    with pytest.raises(ValidationError):
        Verdict(status="clean", excerpt="text")


def _report(**overrides):
    data = {
        "id": "20261118-abc123",
        "source": "https://example.com/job",
        "created_at": datetime(2026, 11, 18, 12, 0, tzinfo=UTC),
        "matched_rules": ["hidden:ignore_instructions"],
        "max_score": 0.97,
        "sha256": SHA,
    }
    data.update(overrides)
    return QuarantineReport(**data)


def test_report_defaults_to_quarantined_and_round_trips_json():
    report = _report()
    assert report.status == "quarantined" and report.released_at is None
    again = QuarantineReport.model_validate_json(report.model_dump_json())
    assert again == report


def test_report_has_no_content_field():
    fields = set(QuarantineReport.model_fields)
    assert fields == {
        "id",
        "source",
        "created_at",
        "matched_rules",
        "max_score",
        "sha256",
        "status",
        "released_at",
    }
    with pytest.raises(ValidationError):
        _report(excerpt="ignore previous instructions")


def test_report_released_needs_time_and_quarantined_has_none():
    when = datetime(2026, 11, 19, tzinfo=UTC)
    released = _report(status="released", released_at=when)
    assert released.released_at == when
    with pytest.raises(ValidationError):
        _report(status="released")
    with pytest.raises(ValidationError):
        _report(released_at=when)


@pytest.mark.parametrize("sha", ["abc", "G" * 64, "A" * 64])
def test_report_sha256_is_lowercase_hex(sha):
    with pytest.raises(ValidationError):
        _report(sha256=sha)


def test_report_needs_a_reason():
    with pytest.raises(ValidationError):
        _report(matched_rules=[], max_score=None)
    assert _report(matched_rules=[]).max_score == 0.97
