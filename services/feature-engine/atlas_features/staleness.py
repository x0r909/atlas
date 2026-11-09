"""Data freshness as three numbers the Risk Engine can refuse on (FR-027, FR-065).

The thresholds are the spec's, not tuning knobs: a quote older than 10 s, a spread wider than
3x the 24 h median, or a 1-minute move beyond 5 sigma means the market data is not trustworthy
right now, and no rule may widen them.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

MAX_QUOTE_AGE_MS = 10_000
SPREAD_RATIO_MULTIPLE = Decimal("3")
MOVE_SIGMA_MULTIPLE = Decimal("5")

# A sigma needs a few observations before it means anything; below this the move breaker
# stays silent instead of firing on the first tick it ever sees.
MIN_RETURNS_FOR_SIGMA = 5


class StalenessMonitor:
    """Exponentially weighted 1-minute volatility per pair, plus the freshness verdict.

    Volatility is stored as a variance so the weights are a plain scalar update; the square
    root is taken only when someone asks for sigma.
    """

    def __init__(self, *, half_life_bars: int = 30) -> None:
        if half_life_bars < 1:
            raise ValueError("half_life_bars must be positive")
        self._alpha = Decimal(1) - (Decimal("0.5") ** (Decimal(1) / Decimal(half_life_bars)))
        self._variance: Decimal | None = None
        self._previous_price: Decimal | None = None
        self._returns = 0

    def observe_return(self, price: Decimal) -> None:
        """Feed the close of a 1-minute bar, in order."""
        if price <= 0:
            raise ValueError("price must be positive")
        previous = self._previous_price
        self._previous_price = price
        if previous is None:
            return
        change = price / previous - 1
        squared = change * change
        if self._variance is None:
            self._variance = squared
        else:
            self._variance = self._alpha * squared + (1 - self._alpha) * self._variance
        self._returns += 1

    def sigma(self) -> Decimal | None:
        if self._variance is None or self._returns < MIN_RETURNS_FOR_SIGMA:
            return None
        return self._variance.sqrt()

    def evaluate(
        self,
        *,
        now_ms: int,
        quote_ts_ms: int,
        spread: Decimal,
        median_spread: Decimal,
        last_price: Decimal,
        reference_price: Decimal,
    ) -> Freshness:
        if median_spread <= 0:
            raise ValueError("median spread must be positive, not a zero denominator")
        if reference_price <= 0:
            raise ValueError("reference price must be positive")

        quote_age_ms = now_ms - quote_ts_ms
        spread_ratio = spread / median_spread
        move_sigma = None
        sigma = self.sigma()
        if sigma is not None and sigma > 0:
            move_sigma = abs(last_price / reference_price - 1) / sigma

        breaches: list[str] = []
        if quote_age_ms > MAX_QUOTE_AGE_MS:
            breaches.append("STALE_QUOTE")
        if spread_ratio > SPREAD_RATIO_MULTIPLE:
            breaches.append("SPREAD_BREAKER")
        if move_sigma is not None and move_sigma > MOVE_SIGMA_MULTIPLE:
            breaches.append("MOVE_BREAKER")

        return Freshness(
            quote_age_ms=quote_age_ms,
            spread_ratio=spread_ratio,
            move_sigma=move_sigma,
            breaches=tuple(breaches),
        )


@dataclass(frozen=True, slots=True)
class Freshness:
    """The verdict the Risk Engine reads: what breached, and by how much."""

    quote_age_ms: int
    spread_ratio: Decimal
    move_sigma: Decimal | None
    breaches: tuple[str, ...]

    @property
    def is_fresh(self) -> bool:
        return not self.breaches
