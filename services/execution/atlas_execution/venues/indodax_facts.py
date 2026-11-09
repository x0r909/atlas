"""The one copy of Indodax's cost figures, in percent units.

Every number here is traceable to docs/adr/0001-indodax-venue-facts.md facts 5, 8 and 9. The
adapter and the SQL seed must never carry their own copies: the adapter imports this module and
tests/unit/test_venue_profile_units.py proves the seed still matches it.

NULL means "not verifiable from an official source yet" — never 0, never a guess.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

FEE_MAKER_PCT = Decimal("0.10000")  # fact 5: /api/pairs trade_fee_percent_maker, IDR market
FEE_TAKER_PCT = Decimal("0.20000")  # fact 5: trade_fee_percent_taker, IDR market
CFX_FEE_PCT: Decimal | None = None  # fact 6: no public percentage exists
CFX_FEE_PCT_ASSUMED = Decimal("0.10000")  # Paper only, deliberately pessimistic
TAX_BUY_PCT_ASSUMED = Decimal("0.21000")  # fact 10: buy-side tax still unverified
TAX_PPH_PCT = Decimal("0.21000")  # fact 8: PMK 50/2025, seller side
TAX_PPN_PCT = Decimal("0.00000")  # fact 9: PPN removed 2025-08-01
TAX_APPLIES_TO: Literal["SELL_ONLY", "BOTH_SIDES"] = "SELL_ONLY"  # facts 8 and 10
MIN_NOTIONAL_IDR = Decimal("10000")  # fact 11
