"""Money arithmetic for ATLAS.

Rules this module exists to enforce:

* Money is ``Decimal``. A ``float`` is rejected at the boundary rather than converted,
  because ``0.1 + 0.2`` is not a rounding nuisance in a system that sizes orders.
* Rounding direction is explicit. A quantity is floored to the venue step (never up:
  rounding up can exceed the risk budget), a price is tick-rounded to a named direction.
* Fees and taxes are functions of a notional and a percentage taken from ``venue_profile``.
  No fee, tax or minimum is hard-coded here (constitution: venue facts are data).
"""

from __future__ import annotations

from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

__all__ = [
    "MoneyError",
    "dec",
    "fee",
    "floor_to_step",
    "meets_min_notional",
    "quantize_idr",
    "round_trip_cost",
    "tax_on_sell",
    "tick_round",
]

# 34 significant digits: enough to multiply a notional by a percentage and still floor
# exactly, without inheriting the ambient context of whatever process calls us.
_PRECISION = 34
_ZERO = Decimal("0")
_ONE = Decimal("1")


class MoneyError(ValueError):
    """Raised for a value or unit that money arithmetic must not silently accept."""


def dec(value: str | int | Decimal) -> Decimal:
    """Convert an exact value to ``Decimal``, refusing floats and non-finite input."""
    if isinstance(value, float):
        raise MoneyError(f"float is not an exact money type; pass a str or Decimal (got {value!r})")
    if isinstance(value, bool):
        raise MoneyError(f"bool is not a money value (got {value!r})")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - defensive
        raise MoneyError(f"cannot read {value!r} as a money value") from exc
    if not result.is_finite():
        raise MoneyError(f"money value must be finite (got {value!r})")
    return result


def quantize_idr(value: str | int | Decimal) -> Decimal:
    """Round to whole rupiah, half-up. IDR has no minor unit in practice."""
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        return dec(value).quantize(_ONE, rounding=ROUND_HALF_UP)


def floor_to_step(quantity: str | int | Decimal, step: str | int | Decimal) -> Decimal:
    """Largest multiple of ``step`` that is ``<= quantity``.

    The direction is the point: the venue rejects a quantity whose remainder is not a
    multiple of the step, and rounding *up* would place an order larger than the risk
    engine sized. A quantity below one step yields ``0`` — callers must treat that as
    "cannot trade", not as "trade the minimum".
    """
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        qty = dec(quantity)
        step_dec = dec(step)
        if step_dec <= _ZERO:
            raise MoneyError(f"step must be positive (got {step})")
        if qty < _ZERO:
            raise MoneyError(f"quantity must not be negative (got {quantity})")
        steps = (qty / step_dec).to_integral_value(rounding=ROUND_FLOOR)
        return steps * step_dec


def tick_round(
    price: str | int | Decimal,
    tick: str | int | Decimal,
    direction: str = "nearest",
) -> Decimal:
    """Round a price to the venue tick.

    ``direction`` is ``down`` for a buy limit that must not pay more than intended,
    ``up`` for a sell limit that must not accept less, and ``nearest`` for display.
    """
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        price_dec = dec(price)
        tick_dec = dec(tick)
        if tick_dec <= _ZERO:
            raise MoneyError(f"tick must be positive (got {tick})")
        if direction not in {"down", "up", "nearest"}:
            raise MoneyError(f"unknown tick direction {direction!r}")
        units = price_dec / tick_dec
        if direction == "down":
            ticks = units.to_integral_value(rounding=ROUND_FLOOR)
        elif direction == "up":
            floored = units.to_integral_value(rounding=ROUND_FLOOR)
            ticks = floored if floored == units else floored + 1
        else:
            ticks = units.to_integral_value(rounding=ROUND_HALF_UP)
        return ticks * tick_dec


def fee(notional: str | int | Decimal, percent: str | int | Decimal) -> Decimal:
    """Absolute fee for a notional at ``percent`` (``0.2`` means 0.2%, not 20%)."""
    with localcontext() as ctx:
        ctx.prec = _PRECISION
        notional_dec = dec(notional)
        pct = dec(percent) / Decimal(100)
        if notional_dec < _ZERO:
            raise MoneyError(f"notional must not be negative (got {notional})")
        if pct < _ZERO:
            raise MoneyError(f"fee percent must not be negative (got {percent})")
        return notional_dec * pct


def tax_on_sell(
    notional: str | int | Decimal,
    pph_percent: str | int | Decimal,
    ppn_percent: str | int | Decimal = 0,
) -> Decimal:
    """Tax payable on the sell leg only (PPh final plus PPN where it applies)."""
    return fee(notional, pph_percent) + fee(notional, ppn_percent)


def round_trip_cost(
    entry_notional: str | int | Decimal,
    exit_notional: str | int | Decimal,
    *,
    taker_percent: str | int | Decimal,
    pph_percent: str | int | Decimal,
    ppn_percent: str | int | Decimal = 0,
    cfx_percent: str | int | Decimal | None = None,
    slippage_percent_each_side: str | int | Decimal = 0,
) -> Decimal:
    """All-in friction for one round trip, in the same currency as the notionals.

    Both legs are priced taker by default because the exit (stop or close) is reduce-only
    and must fill now (``contracts/decision-risk.md`` § Assumed costs). ``cfx_percent``
    must be supplied explicitly once verified; passing ``None`` is honest, and a caller
    that needs a complete cost model must refuse rather than substitute zero.
    """
    entry = dec(entry_notional)
    exit_ = dec(exit_notional)
    friction = fee(entry, taker_percent) + fee(exit_, taker_percent)
    friction += tax_on_sell(exit_, pph_percent, ppn_percent)
    if cfx_percent is not None:
        friction += fee(entry, cfx_percent) + fee(exit_, cfx_percent)
    if slippage_percent_each_side:
        friction += fee(entry, slippage_percent_each_side)
        friction += fee(exit_, slippage_percent_each_side)
    return friction


def meets_min_notional(notional: str | int | Decimal, minimum: str | int | Decimal) -> bool:
    """Whether a notional clears the venue minimum (inclusive, as the venue states it)."""
    return dec(notional) >= dec(minimum)
