-- venue_profile seed: Indodax, verified 2026-11-08 (T023, docs/adr/0001-indodax-venue-facts.md)
--
-- Rules for this file:
--   * Every number here is traceable to docs/adr/0001-indodax-venue-facts.md.
--   * NULL means "not verifiable from official sources yet" — never 0, never a guess.
--     A NULL in cfx_fee_pct must make the cost model refuse in Live (FR-058 fail-closed);
--     Paper/Backtest may use the *_assumed columns and must mark cost_model_source=ASSUMED.
-- NOTE: every figure below is the INDODAX PRO market. Orders of Rp10.000-24.999 are routed to
--     Lite, whose fees/limits/order types are undocumented - see ADR 0001 addendum. Live
--     promotion stays blocked until that is verified.
--     not silently understate the cost.
--   * Changing any value = new row with version + 1. Never UPDATE in place
--     (data-model.md: a profile change invalidates promotion evidence).
--
-- Verified from the live official endpoint https://indodax.com/api/pairs on 2026-11-08:
--   438/438 IDR pairs: trade_min_base_currency 10000,
--                      trade_fee_percent_maker 0.1, trade_fee_percent_taker 0.2
--   12/12  USDT pairs: trade_min_base_currency 1,
--                      trade_fee_percent_maker 0.03, trade_fee_percent_taker 0.06

-- PRECONDITION: the `venue` row for 'indodax' must exist (FK), and `venue_profile` must have a
-- unique key on (venue_id, version) — otherwise drop the ON CONFLICT clause rather than
-- guessing the constraint name.
--
INSERT INTO venue_profile (
    venue_id,
    version,
    fee_maker_pct,
    fee_taker_pct,
    cfx_fee_pct,          -- UNVERIFIED: SEB 001/SEB2026/CFX-KKI/SPOT/II/2026 sets it, no public %
    cfx_fee_pct_assumed,  -- Paper/Backtest stand-in only; pessimistic on purpose
    tax_buy_pct_assumed,  -- Paper/Backtest stand-in for the open "is the buy side taxed" question
    tax_pph_pct,          -- PMK 50/2025, effective 2025-08-01, seller side
    tax_ppn_pct,          -- PMK 50/2025 removes PPN on crypto transfer
    tax_applies_to,
    min_notional_idr,
    min_order_step,       -- per pair, base units (= /api/pairs quantity_increment)
    tick_size,            -- per pair, quote units (= /api/pairs pricescale)
    rate_limit,
    max_leverage,
    native_stop_orders,   -- proven false: API has no stop type and no OCO (ADR 0001, facts 1-2)
    cost_model_version,
    verified_at
) VALUES (
    'indodax',
    1,
    0.10000,              -- trade_fee_percent_maker, IDR market (USDT market: 0.03000)
    0.20000,              -- trade_fee_percent_taker, IDR market (USDT market: 0.06000)
    NULL,                 -- see ADR 0001 fact 6; do not substitute 0
    0.10000,              -- ASSUMED Upper bound stand-in: no public CFX figure exists.
                          -- Deliberately high: understating cost over-trades.
    0.21000,              -- ASSUMED worst case: assume the buy side is taxed too.
    0.21000,              -- PPh final 0.21% of the sale value
    0.00000,              -- PPN 0% since 2025-08-01
    'SELL_ONLY',          -- ADR 0001 fact 8 + 10 (buy-side tax: still unverified)
    10000.00,             -- /api/pairs trade_min_base_currency, all 438 IDR pairs
    '{"btcidr": 1e-08, "ethidr": 1e-08}'::jsonb,
    '{"btcidr": 1000, "ethidr": 1000}'::jsonb,
    '{
      "public_rpm": 180,
      "private_rpm_per_endpoint": 300,
      "private_rpm_capital_and_fiat": 50,
      "create_order_rps_per_account_per_pair": 20,
      "cancel_rps_per_account": 30,
      "exceed_block_seconds": 5,
      "stop_budget_rps_per_pair": 2,
      "stop_budget_rps_venue": 8,
      "scope": "per IP for the RPM figures, per account+pair for the RPS figures"
     }'::jsonb,
    1,                    -- crypto spot only; no leverage anywhere (FR-005)
    false,
    'indodax-2026-11-08',
    '2026-11-08T00:00:00+07:00'
)
ON CONFLICT (venue_id, version) DO NOTHING;
