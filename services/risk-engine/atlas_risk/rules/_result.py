"""Rule results: passing rules are still reported, so the audit record shows what was checked."""

from __future__ import annotations

from atlas_schemas.risk import RuleResult


def ok(rule_id: str, observed: str | None = None) -> RuleResult:
    return RuleResult(rule_id=rule_id, passed=True, observed=observed)


def failed(rule_id: str, observed: str, limit: str | None = None, message: str = "") -> RuleResult:
    return RuleResult(
        rule_id=rule_id, passed=False, observed=observed, limit=limit, message=message
    )
