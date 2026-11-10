"""A sell limit below the market is not a stop: it is a marketable order.

Indodax has no native stop orders, so a protective stop can only be armed locally
(see the stop monitor). The simulated venue has no resting book either: every
accepted order fills on arrival, which is exactly what a real venue does with a
SELL LIMIT parked below the last price. This test pins that hazard so no
"place the stop as a limit sell" shortcut can pass unnoticed in Paper.
"""

from __future__ import annotations

from decimal import Decimal

from atlas_schemas.venue import PlaceOrderRequest

from tests.support.simulated_venue import SimulatedVenue


async def test_a_limit_sell_below_the_market_fills_instantly() -> None:
    venue = SimulatedVenue(price=Decimal("1000"))
    ack = await venue.place_order(
        PlaceOrderRequest(
            pair="BTCIDR",
            side="SELL",
            type="LIMIT",
            quantity=Decimal("1"),
            limit_price=Decimal("900"),
            client_order_id="atlas-rest-hazard-1",
        )
    )
    assert ack.status == "FILLED"
    fills = venue.fills()
    assert len(fills) == 1
    assert fills[0].price == Decimal("900")
    assert await venue.open_orders("BTCIDR") == []
