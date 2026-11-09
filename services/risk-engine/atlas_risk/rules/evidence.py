"""Stage 2: a trade must be justified by something other than an unverified headline."""

from __future__ import annotations

from atlas_schemas.risk import RuleResult

from atlas_risk.rules import RuleContext
from atlas_risk.rules._result import failed, ok

VERIFIED_TIERS = (1, 2)


def news_only_evidence(ctx: RuleContext) -> RuleResult:
    rule_id = "NEWS_ONLY_EVIDENCE"
    trade = ctx.trade
    bars = sum(1 for kind in trade.evidence_kinds if kind == "BAR")
    tiers = [tier for tier in trade.evidence_tiers if tier is not None]
    observed = f"bars={bars} tiers={sorted(set(tiers))} items={len(trade.evidence_kinds)}"
    if bars > 0:
        return ok(rule_id, observed)
    if any(tier in VERIFIED_TIERS for tier in tiers):
        return ok(rule_id, observed)
    return failed(
        rule_id,
        observed,
        "at least one BAR, or NEWS/SOCIAL at source_tier 1-2",
        "tier-3 material may support a thesis, never be its only basis",
    )
