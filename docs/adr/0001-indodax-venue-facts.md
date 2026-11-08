# ADR 0001 — Indodax venue facts verified before any order code exists

- **Status**: Accepted (T023, M0). Supersedes the unverified R5/R6 figures in `research.md`.
- **Date**: 2026-11-08. All sources accessed 2026-11-08 (UTC+7).
- **Scope**: only what the Execution Engine, the stop monitor, and the cost model must know
  before the first order. No strategy or architecture decision is made here.

## Why this ADR exists

`spec.md` FR-053 and `plan.md` forbid any venue number in code. `data-model.md` requires one
`venue_profile` row per venue whose values are stamped into every verdict and backtest.
T023 therefore gates M3: until the values below are recorded, the Risk Engine has no cost
model and the stop design has no answer to "does the venue hold the stop for us?".

Two rules governed this verification, and they are the reason several rows below read
**unverified** instead of carrying a plausible number:

1. A value is *verified* only if an official Indodax or government source states it, or an
   official venue endpoint returns it today.
2. If no source states it, the field is recorded as `NULL` / `UNVERIFIED` and the consumer
   must refuse to proceed. A guessed number in a cost model is worse than a refusal,
   because the guess silently sizes every trade afterwards.

## Verified facts

| # | Fact | Verified value | Source | How verified |
| --- | --- | --- | --- | --- |
| 1 | Native resting stop / bracket order via the trade API | **None** | [Official API documentation](https://indodax.com/downloads/INDODAXCOM-API-DOCUMENTATION.pdf), Error Codes (`-1130`, "Invalid order type") and the Create Order parameter table | API 2.0 accepts `LIMIT` or `MARKET` only and rejects a stop type explicitly: `{"code":-1130,"msg":"Invalid parameter value.\n\nInvalid parameter 'type', stop_limit_not_available"}`. The Create Order parameter table (`price`, `idr`, `coin`/`btc`, `order_type`, `time_in_force`, `smp_cancel`, `client_order_id`) has no stop or trigger price at all. API 1.0 (`/tapi`) documents `order_type: limit, market`; its only `stoplimit` occurrence is the `order_type` field of `cancelOrder`, i.e. a cancellation parameter, not a placement one. |
| 2 | OCO (one-cancels-the-other) | **Not available** | [Help Center: OCO](https://help.indodax.com/hc/en-us/articles/54613319090073-Does-Indodax-offer-the-OCO-One-Cancels-the-Other-feature) | Article states the feature is not available and points to Limit + Stop orders "secara terpisah" (separately). |
| 3 | Stop Limit Order exists on the venue UI | Yes, UI only | [Help Center: Limit / Market / Stop Limit](https://help.indodax.com/hc/id/articles/13348121502361-Apa-itu-Limit-Order-Market-Order-dan-Stop-Limit-Order) | Describes stop price + limit price, triggered off-book. It is a web/app order-entry feature; fact 1 shows it is not reachable from the API, and no public documentation states its trigger semantics (mark vs last, re-arm after cancel). |
| 4 | Cost structure | All-in fee = **service fee + tax + CFX fee**, on **both Buy and Sell**, on **both IDR and USDT** markets | [Help Center: Service Fee / Tax / CFX](https://help.indodax.com/hc/id/articles/52021234143769-Apa-Perbedaan-Antara-Service-Fee-Tax-Pajak-dan-CFX-Fee-pada-saat-Melakukan-Transaksi-Jual-Beli-di-Indodax), [Help Center: biaya transaksi](https://help.indodax.com/hc/id/articles/4416646599705-Rincian-Biaya-Transaksi-di-INDODAX) | The second article's FAQ: the adjustment "berlaku untuk seluruh member INDODAX … baik sebagai Maker maupun Taker pada kedua sisi Buy & Sell di USDT dan IDR market". |
| 5 | Per-pair service fee (maker / taker) | IDR: **0.10% / 0.20%**; USDT: **0.03% / 0.06%** | [Official `/api/pairs`](https://indodax.com/api/pairs) (live probe, 2026-11-08) and the same response documented in the [API PDF, Public REST `Pairs`](https://indodax.com/downloads/INDODAXCOM-API-DOCUMENTATION.pdf) | Fields `trade_fee_percent_maker` / `trade_fee_percent_taker`. 438/438 IDR pairs identical (0.1 / 0.2); 12/12 USDT pairs identical (0.03 / 0.06); `trade_fee_percent` (0.2 IDR) is consistent with the taker figure. |
| 6 | CFX fee rate | **UNVERIFIED — no number recorded** | Only its existence and effective date are official: [Help Center: biaya transaksi](https://help.indodax.com/hc/id/articles/4416646599705-Rincian-Biaya-Transaksi-di-INDODAX), citing SEB No. `001/SEB2026/CFX-KKI/SPOT/II/2026`, effective **1 October 2026** | The article names the circular and says an all-in-fee adjustment implements it, but publishes no percentage; the page it links for the breakdown (`blog.indodax.com/penerapan-fee-cfx/`) now redirects to the homepage, and the in-app "Biaya Trading" page (which does show the numbers) requires a logged-in account. The earlier `research.md` claim of 0.0111% → 0.00555% had no accessible source and is **withdrawn**. |
| 7 | Whether the published per-pair fee already includes tax and CFX | **UNVERIFIED** | — | No official page states the composition of `trade_fee_percent_taker`. Its name and the help-center separation of the three components suggest service fee only, but "suggest" is not evidence; recorded as an open question instead. |
| 8 | PPh final on crypto sales | **0.21%** of the sale transaction value, collected by the exchange, **seller side** | [DJP press release](https://pajak.go.id/id/siaran-pers/pemerintah-perkuat-pengaturan-pajak-aset-kripto), [PMK 50/2025 (JDIH Kemenkeu)](https://jdih.kemenkeu.go.id/api/download/66a7633b-9e1b-4a54-9046-5320aa96c7e1/2025pmkeuangan050.pdf), effective **1 August 2025** | DJP: crypto moved to OJK, "PPh 22 final ditetapkan sebesar 0,21 persen"; PMK 50/2025 Pasal 10–11 imposes the income tax on the *Penjual* (seller). |
| 9 | PPN on crypto transfers | **0%** since 1 August 2025 | [DJP: PMK 50/2025 dan Kaizen Fiskal](https://pajak.go.id/id/artikel/pmk-502025-dan-kaizen-fiskal-jalan-baru-pajak-kripto-yang-berkeadilan) | PMK 50/2025 removes PPN on crypto delivery on official platforms, aligning it with securities. |
| 10 | Whether Indodax charges a tax component on **buy** orders too | **UNVERIFIED** | — | Fact 8 puts the PPh on the seller; fact 4 says the *fee adjustment* applies to both sides, which is not the same statement. Only a real fee statement (or the in-app All-in-Fees detail) can settle it. |
| 11 | Minimum order (IDR market) | **Rp10.000** on every IDR pair; **Rp25.000** to trade directly on INDODAX Pro (Rp10.000–Rp24.999 is auto-routed to Lite) | [Help Center: minimal pembelian](https://help.indodax.com/hc/id/articles/34923721902233-Berapa-minimal-pembelian-koin-di-Indodax-dalam-rupiah), [Help Center: minimal limit order](https://help.indodax.com/hc/id/articles/58066187568409-Berapa-Minimal-Transaksi-Menggunakan-Limit-Order), [Help Center: biaya transaksi](https://help.indodax.com/hc/id/articles/4416646599705-Rincian-Biaya-Transaksi-di-INDODAX), `/api/pairs` field `trade_min_base_currency` | `/api/pairs`: `trade_min_base_currency = 10000` for all 438 IDR pairs, today. The Pro/Lite split is stated in the fee article's notes. |
| 12 | Tick size (price step) | Per pair; equals `/api/pairs` `pricescale` (same value as `price_precision`) | [API PDF, `Pairs` response](https://indodax.com/downloads/INDODAXCOM-API-DOCUMENTATION.pdf), live `/api/pairs` | BTcidr and ETHIDR: `pricescale = 1000` (Rp1.000); USDTIDR and XRPIDR: `pricescale = 1` (Rp1). The PDF response shows the same field for BTCIDR (`1000`) and CATIDR (`0.000001`). |
| 13 | Quantity step | Per pair; equals `/api/pairs` `quantity_increment` | Same | 450/450 pairs publish it: `1e-08` (384 pairs), `1` (61), `1e-06` (5). BTCIDR and ETHIDR: `1e-08`. The PDF example shows `"quantity_increment": "0.00000001"` for BTCIDR and `1` for CATIDR. |
| 14 | Private rate limits | General: **300 requests/minute per IP per endpoint** (create order, cancel, open orders, get order, account, order history, myTrades); **50/minute** for capital and fiat endpoints. Additional on order creation: **20 requests/second per account per pair**, and exceeding it triggers a **5-second trading block** for that account and pair (`429`, "please try again in 5 seconds"). Cancel: **30 requests/second**. Public API: **180 requests/minute**. | [API PDF, "General Rate Limit by Endpoints"](https://indodax.com/downloads/INDODAXCOM-API-DOCUMENTATION.pdf), Create Order and Cancel Order sections | The PDF states both the per-endpoint table and the per-pair order limit, with the 429 body quoted verbatim. |
| 15 | Self-trade prevention | Present, per order: `smp_cancel` = `MAKER` (default), `TAKER`, `BOTH` | API PDF, Create Order and "INDODAX Self-Trade Prevention" | Matters for idempotency and cancel logic: a cancel may not be needed because STP cancelled the order. |
| 16 | Time in force | `GTC` (default) and `MOC` only, and `MOC` only with `order_type=limit`; an `MOC` limit priced better than top-of-book is rejected | API PDF, Create Order notes | Relevant to the 45 s remainder-cancel design: the venue cannot express "cancel if unfilled in 45 s", so the client must. |
| 17 | Other venue facilities that affect the stop design | A **Deadman Switch** endpoint section exists in the official API; `client_order_id` max 36 chars, `[A-Za-z0-9_-]`; private WebSocket exists for order/balance events | API PDF ("INDODAX Deadman Switch", Create Order, Private WebSocket) | Recorded so the stop monitor decision is made with the full menu in view, not just the stop-order question. |

## Decision

1. **`venue_profile.native_stop_orders = false` for Indodax.** This is now proven, not a
   default: the API has no stop order type (fact 1) and no OCO (fact 2). The client-side
   stop monitor of FR-067 is therefore the only protective mechanism, and every entry must
   be sized, recorded, and watched as an unprotected position. `T087`'s "monitored stop"
   path is the primary path, not a fallback for an exotic pair.
2. **The stop monitor must not be built on the venue's Stop Limit Order UI feature**
   (fact 3). It is unreachable from the API, and its trigger semantics are undocumented, so
   it cannot be tested, replayed, or reconciled.
3. **`cfx_fee_pct` stays `NULL`** in the seed, and the cost model must refuse to produce a
   verdict while it is `NULL` (fail closed, same spirit as FR-058's refusal codes) rather
   than quietly using 0. Resolution path: read the All-in-Fees detail from the operator's
   account (menu *Profile → All-in-Fees*, or the *Biaya Trading* page) and paste the exact
   figure into a new `venue_profile` version. That is a data change, not a code change.
4. **`tax_applies_to = SELL_ONLY`**, per PMK 50/2025 (fact 8), with the open question from
   fact 10 recorded in `research.md`. If a real buy-side fee statement later shows a tax
   component, the profile is versioned to `BOTH_SIDES` and old backtests are invalidated by
   `cost_model_version`, exactly as `data-model.md` requires.
5. **`min_notional_idr = 10000`,** with the Pro floor of Rp25.000 recorded separately in the
   ADR and `research.md` because it is an order-entry constraint of the venue's applications,
   not of the API (the API accepts Rp10.000 — fact 11).
6. **Rate limits go into `venue_profile.rate_limit` as a structured object,** including the
   5-second block, because a token bucket tuned to "20 per second" without modelling the
   block would convert a rate-limit error into a five-second trading outage.

## Consequences

- M3 can start against `native_stop_orders = false` without waiting for anything else; the
  stop monitor is on the critical path as designed.
- The Risk Engine cannot compute all-in cost until `cfx_fee_pct` is filled by the operator.
  This is deliberate: `T055`/`T058` tests must assert the refusal, so the gap cannot rot.
- `T023`'s second half — the read-only **account probe** — was not performed (no API key in
  this environment). It remains open, and it is the only path that settles facts 7 and 10.
  Until then the seed is `verified_at 2026-11-08` for documentation facts only.

## Open questions (must be closed by the account probe, not by guessing)

| Question | What settles it | Consequence if unanswered |
| --- | --- | --- |
| Exact CFX percentage per market | All-in-Fees detail in the account (Profile) or a real fill's fee breakdown | Cost model refuses to run (by design) |
| Is there a tax component on the **buy** side | Same fee statement, one buy fill | `tax_applies_to` may become `BOTH_SIDES`; round-trip cost rises by 0.21% |
| Does `trade_fee_percent_taker` already include tax/CFX | Compare a real fill's charged fee against 0.2% × notional | If it is all-in, the seed overstates or understates cost by the tax + CFX amount |
| Trigger semantics of the UI Stop Limit (mark vs last price, re-arm behaviour) | Not needed for the API-only design; only if the feature ever becomes API-callable | None today |

## Addendum 2026-11-08b — two follow-ups that came out of reviewing this ADR

### Which side pays which fee: exits are taker

The exit leg is not a choice. A protective stop, a `CLOSE`, and the market-order fallback are
reduce-only and must fill immediately, so they consume resting liquidity and pay **taker**.
The default round trip is therefore `taker_buy + taker_sell + PPh_sell` (0.20% + 0.20% + 0.21%
= 0.61% on the IDR market), and maker pricing may only be booked when a verdict records that
the entry order actually filled as maker — a stop that fires before a limit entry rests would
otherwise be scored against a cost the trade never paid.

### Open question added: the Lite/Pro boundary

Every documented limit above (min order, fee, order type, rate limit) is stated for the
**Pro** market. Orders between Rp10.000 and Rp24.999 are auto-routed to **Lite**, and no
public document states whether Lite exposes the same `order_type` set, the same fee
percentages, the same `min_notional`, or the same create-order rate limit.

This is not a corner case for us: with Rp100.000 of capital and a 25%-of-equity position cap,
the *typical* order is Rp25.000 — the exact boundary — and any smaller position lands inside
the undocumented side. Consequences:

- `T023` cannot be marked complete, and **promotion to Live stays blocked**, until the Lite
  behaviour, fee, and limits are verified (from the Help Center's Lite/Pro routing rules or a
  read-only account probe that places nothing).
- Until then the seed row describes Pro only; `rate_limit` and `min_notional_idr` carry a
  comment saying so.
- Paper may trade through the boundary, but its fee assumptions are Pro-grade and therefore
  optimistic for Lite fills; that must be stated in any promotion evidence.

### Assumed values are stored, not substituted

Because the two unknowns above are blocked on the operator's account rather than on research,
`venue_profile` carries `cfx_fee_pct_assumed` and `tax_buy_pct_assumed` beside the verified
columns. Paper and Backtest may use them and must then stamp `cost_model_source = ASSUMED` on
the verdict and on every rule result that consumed a cost input; Live never reads them and
refuses with `COST_MODEL_INCOMPLETE` instead. The assumed numbers are deliberately pessimistic
(high), because an understated cost is the failure mode that looks profitable in the log and
loses money at the venue.
