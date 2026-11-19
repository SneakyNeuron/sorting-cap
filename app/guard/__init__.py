"""Prompt-injection guard: deterministic rules, local classifier and quarantine.

`check(ctx, content, source)` is the one entry point (T2). It gets the classifier
first, so without a working one guarded input is refused with `GuardUnavailable`
before anything else happens: rules alone never pass it (R4). Then the rules run and
the classifier scores the visible and the hidden text separately (T4). Input is
flagged when any rule matches or the highest score reaches `guard_threshold`;
flagged input is encrypted into the quarantine and the verdict carries the item id.

If the quarantine refuses new content (`QuarantineKeyLost` and other
`QuarantineError`s), that error propagates: the input is refused, not stored, not
quarantined and not forwarded (T5).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.guard.classifier import GuardUnavailable, Scorer, ensure_classifier
from app.guard.quarantine import Quarantine
from app.guard.rules import scan
from app.schemas.guard import Verdict

if TYPE_CHECKING:
    from app.tools.context import ToolContext

__all__ = ["GuardUnavailable", "Scorer", "check", "ensure_classifier"]


def _reason(ctx: ToolContext, rules: list[str], score: float, threshold: float) -> str:
    parts = []
    if rules:
        parts.append(", ".join(rules))
    if score >= threshold:
        parts.append(ctx.msg("quarantine.reason_score", score=f"{score:.2f}"))
    return "; ".join(parts)


def check(
    ctx: ToolContext,
    content: str | bytes,
    source: str,
    *,
    html: bool = False,
    text: str | None = None,
    scorer: Scorer | None = None,
) -> Verdict:
    """The verdict on one guarded input; flagged input is quarantined first.

    `content` is the original text or bytes (bytes are decoded as UTF-8 for scanning
    and quarantined as they are). `text` is the decoded text to scan instead, for
    bytes in another encoding (a cp1250 page): the scan then sees exactly what a
    reader extracts, while the original bytes are quarantined. `html` splits it into
    visible and hidden text. `scorer` replaces the installed classifier (tests).
    """
    scorer = scorer or ensure_classifier(ctx)
    raw = content if isinstance(content, bytes) else content.encode("utf-8")
    if text is None:
        text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    rules = scan(text, html=html)
    score = max(scorer.score(rules.visible), scorer.score(rules.hidden) if rules.hidden else 0.0)
    threshold = ctx.settings.guard_threshold
    if not rules.matched and score < threshold:
        return Verdict(status="clean", max_score=score)
    report = Quarantine(ctx.workspace, ctx.credentials).create(
        raw, source, matched_rules=rules.matched, max_score=score
    )
    return Verdict(
        status="flagged",
        matched_rules=rules.matched,
        max_score=score,
        reason=_reason(ctx, rules.matched, score, threshold),
        quarantine_id=report.id,
    )
