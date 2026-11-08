# Feature Specification: ATLAS Autonomous Crypto Trader

**Feature Branch**: `001-autonomous-crypto-trader`

**Created**: 2026-11-08

**Status**: Draft

**Input**: User description: "Bangun ATLAS: sistem trading crypto otonom berbasis multi-agent AI
untuk satu operator. ATLAS menggantikan peran trader manusia: membuka, mengelola (geser SL,
partial TP, scale in/out), dan menutup posisi sendiri secara otomatis 24/7. Manusia hanya
mengawasi lewat dashboard dan bisa intervensi via kill switch. Sistem menyerap data pasar
real-time (harga, orderbook, funding, OI, liquidations, on-chain, makro) dan berita/sentimen
terbaru. Pipeline berita: dedup, entity extraction, sentimen, impact score, horizon, source
credibility tier (berita tier 3/rumor tidak boleh jadi trigger tunggal). Agent: Market, News,
Derivatives, On-chain, Bull/Bear Debate, dan Strategist yang mensintesis menjadi TradeProposal
terstruktur (entry, SL, TP, thesis, evidence, confidence, invalidation, time horizon) atau
NO_TRADE. NO_TRADE adalah keputusan sah. Risk Engine menilai tiap proposal
(approve/modify/reject): position sizing berbasis risiko per trade, max leverage, exposure per
aset dan total, daily loss limit, max drawdown, cooldown loss streak, blackout event makro,
circuit breaker (volatilitas ekstrem, spread melebar, data stale), dan kill switch. Execution
Engine menangani order market/limit/stop/bracket, idempotency, slippage guard, dan
rekonsiliasi dengan exchange. Mode: Backtest, Paper, Co-pilot (fase transisi saja), Autonomous
(target utama), Halted. Dashboard real-time bergaya Bloomberg Terminal (dense, dark, monospace,
keyboard-first dengan command bar, workspace multi-panel): market overview, chart dengan
overlay entry/SL/TP, news feed dengan sentimen dan impact score, agent console (reasoning
live), decision log, posisi dan PnL, risk monitor, system health, backtest lab, dan kill switch
global yang selalu terlihat. Setiap trade bisa ditelusuri dari berita/data sampai fill. Di luar
scope v1: HFT/market making, multi-user/SaaS, kelola dana orang lain, custody, DeFi swap
otomatis, klaim profit."

## Clarifications

### Session 2026-11-08

- Q: Berapa total modal awal (equity) yang dialokasikan untuk ATLAS, dan berapa plafon modal untuk tahap Co-pilot? → A: Rp100.000 total; plafon Co-pilot Rp30.000. Risk Engine wajib menolak trade bila ukuran order minimum exchange melebihi batas risk per trade; tujuan fase awal adalah validasi sistem, bukan profit. (Cap LLM awal Rp10.000/hari kemudian direvisi menjadi Rp5.000/hari pada jawaban anggaran.)
- Q: Exchange mana yang menjadi venue utama ATLAS, dan bagaimana ATLAS harus bersikap ketika minimum order exchange melebihi budget risk per trade? → A: Multi-exchange lewat adapter dengan interface seragam (market data, order, balance, fee, min notional) sejak awal; venue Live pertama Indodax (pair IDR, berizin OJK); venue lain ditambah lewat config tanpa mengubah Risk Engine; hanya venue berizin OJK yang boleh diaktifkan untuk Live; data pasar boleh many-venue read-only; risk 0,5% tanpa pengecualian dengan sizing = risk budget / jarak SL, dan trade ditolak bila ukuran minimum venue × jarak SL melebihi 0,5% equity; posisi tunggal maksimal 25% equity; exposure, drawdown, dan daily loss dihitung global lintas venue; tiap venue punya profil sendiri (fee, pajak, min order, rate limit, status kepatuhan).
- Q: Berapa anggaran bulanan total untuk infra, LLM, dan data/berita, dan apakah sumber berita boleh berbayar? → A: Maksimal Rp250.000/bulan total (infra + LLM + data). Cap LLM Rp5.000/hari. Model murah untuk klasifikasi berita, model lebih kuat hanya untuk Strategist. Semua data dan berita dari sumber gratis (RSS, API publik, free tier); sumber berbayar tidak diperbolehkan di v1. Dashboard wajib menampilkan biaya LLM harian dan bulanan. Auto-throttle: saat cap tercapai, turunkan frekuensi analisis lalu berhenti membuka posisi baru, tetap mengelola dan menutup posisi yang ada, dan tidak pernah menghentikan stop loss atau kill switch.
- Q: Gaya trading mana yang menjadi default ATLAS: swing (posisi diinapkan) atau intraday (semua posisi ditutup di hari yang sama)? → A: Swing. Horizon default 4 jam–3 hari dengan max holding 5 hari dan time-stop otomatis. Siklus 15 menit hanya untuk monitoring dan manajemen posisi. Risk Engine menolak trade bila estimasi reward:risk setelah fee, pajak, dan slippage < 1,5:1; biaya round-trip dihitung otomatis dari profil Venue. Backtest dan Paper memakai periode multi-hari dan dilaporkan per jumlah trade. Promotion gate tidak boleh bergantung pada PnL saja karena sampel trade kecil.
- Q: Apa kriteria promotion gate dari Paper ke Co-pilot dan dari Co-pilot ke Autonomous, mengingat sampel trade akan kecil? → A: Paper→Co-pilot: ≥30 hari, ≥500 siklus keputusan terekam, ≥10 trade tertutup, max drawdown ≤5%, 0 insiden keselamatan terbuka, dan seluruh drill chaos (data stale, exchange down, LLM timeout) serta drill kill switch (dashboard dan Telegram) lolos. Co-pilot→Autonomous: ≥30 hari live dengan plafon Co-pilot, ≥10 trade live tertutup, max drawdown ≤5% equity, 0 insiden keselamatan terbuka, rekonsiliasi tanpa mismatch tak terselesaikan, deviasi slippage dan fill live vs asumsi paper dalam batas yang ditetapkan di plan, dan biaya LLM tidak pernah melewati cap. Insiden keselamatan = order tanpa stop loss, pelanggaran hard limit risk, double order, mismatch posisi yang tidak terselesaikan, atau kill switch gagal. Promosi wajib dikonfirmasi eksplisit operator (tidak otomatis). Demosi otomatis ke Halted jika terjadi insiden keselamatan, max drawdown 10% tercapai, atau daily loss limit terlampaui berulang; operator memutuskan apakah kembali ke Paper. Gate mengukur kebenaran dan keselamatan sistem, bukan profitabilitas.
- Q: Apakah Execution Engine boleh mengirim order entry tanpa verdict Risk Engine, dan berapa lama verdict itu berlaku? (CHK001–CHK003) → A: Tidak, tidak pernah. Setiap order entry wajib membawa verdict Risk Engine yang bertanda tangan, sekali pakai, terikat ke proposal_id + venue + symbol + sisi + ukuran, dan kedaluwarsa 60 detik; verdict yang sudah dipakai, tidak cocok, diubah, atau kedaluwarsa ditolak dan dicatat (FR-062). Order yang hanya mengurangi atau menutup posisi — close, jual sebagian, memasang atau menggeser stop — tidak butuh verdict baru dan tidak boleh diblokir budget cap, circuit breaker, maupun mode Halted (FR-063). Hanya Execution Engine yang memegang kredensial venue; proses agent dan model tidak punya kredensial maupun jalur jaringan terautentikasi ke endpoint order (FR-064).
- Q: Berapa nilai ambang circuit breaker dan batas exposure yang mengikat, dan siapa yang boleh mengubahnya? (CHK006, CHK007, CHK012) → A: Nilainya adalah requirement, bukan default implementasi: blokir posisi baru saat spread melebihi 3x median 24 jam, volatilitas melebihi 5 standar deviasi distribusi return terakhir, atau data harga/order book lebih tua dari 10 detik; exposure maksimum per aset 50% equity dan total 100% equity. Hanya operator yang boleh mengubahnya lewat config, dan setiap perubahan diaudit (FR-065).
- Q: Kapan daily loss direset dan dari titik mana drawdown maksimum diukur? (CHK008, CHK009) → A: Daily loss direset 00:00 WIB berbasis hari kalender (bukan rolling), dihitung dari equity global lintas venue termasuk PnL belum terealisasi. Drawdown maksimum diukur dari high-water mark equity global mark-to-market yang ditetapkan pada awal tahap Live berjalan (FR-066).
- Q: Bagaimana bracket order dipenuhi pada venue spot yang tidak punya OCO native? (CHK018) → A: Stop loss di sisi klien (stop monitor) adalah interpretasi sah dari bracket, dengan syarat stop monitor adalah proses terpisah ber-heartbeat yang memasang order lewat Execution Engine, dan stop native venue dipakai bila tersedia. Jika heartbeat hilang lebih dari 30 detik, ATLAS masuk Halted dan mencoba menutup posisi terbuka (FR-067).
- Q: Apa yang terjadi pada stop loss ketika entry hanya terisi sebagian? (CHK028) → A: Stop dipasang sesuai kuantitas yang sudah terisi, sisa order dibatalkan setelah timeout, dan posisi tidak pernah boleh tanpa stop; aturan yang sama berlaku untuk setiap penambahan atau pengurangan posisi berikutnya (FR-068).
- Q: Bagaimana status order UNKNOWN (timeout, tanpa respons) diselesaikan? (CHK029) → A: Bekukan order baru di venue itu, rekonsiliasi lewat query ke exchange (bukan kirim ulang), dan masuk Halted setelah 3 percobaan berturut-turut gagal (FR-069).
- Q: Bagaimana perilaku ketika close posisi gagal berulang? (CHK030) → A: Retry dengan backoff, kirim alert Telegram setelah kegagalan kedua, lalu coba close dengan market order dalam batas slippage guard; semua entry baru dihentikan sampai posisi tertutup (FR-070).
- Q: Apakah scale in/out dan partial take profit tetap ada di v1, dan apa saja aksi manajemen yang sah? (CHK019) → A: Dicabut dari v1. ManagementAction v1 hanya MOVE_STOP (hanya ke arah mengurangi risiko, tidak pernah menjauhkan stop dari entry) dan CLOSE (penutupan penuh). Scale in/out dan partial take profit dicatat sebagai out-of-scope v1 karena minimum order venue pada modal saat ini membuatnya tidak dapat dijalankan (FR-020).
- Q: Siapa yang boleh melakukan setiap mutasi order, dan apakah cancel/modify tunduk pada aturan verdict? (CHK005) → A: Hanya Execution Engine, sebagai single writer untuk place, cancel, dan modify. Cancel dan modify atas order entry yang belum terisi wajib tercatat di audit log dan ditolak bila verdict tidak valid; cancel untuk order pengurang exposure tidak boleh diblokir (FR-071, FR-063).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Risk-gated autonomous trading in Paper mode (Priority: P1)

The operator starts ATLAS in Paper mode (the default). Without any per-trade input, ATLAS
watches the market around the clock. The analysis agents produce a structured TradeProposal or
a NO_TRADE decision each cycle. Every proposal goes through the Risk Engine, which approves,
modifies (more conservatively), or rejects it. Approved proposals become simulated orders
that always carry a stop loss. ATLAS then manages each open position by itself: it moves the
stop loss to reduce risk and closes the position when a rule triggers.

**Why this priority**: this is the core value of ATLAS (a system that replaces the human
trader) and the first stage of the rollout required by the constitution. Everything else
builds on this loop.

**Independent Test**: run ATLAS in Paper mode against live market data for a fixed period.
Check that decisions are produced, that each simulated order has an approving Risk Engine
verdict and a stop loss, and that positions are opened, managed, and closed without operator
action.

**Acceptance Scenarios**:

1. **Given** ATLAS is started with no mode configured, **When** it boots, **Then** it runs in
   Paper mode and no live order can be sent.
2. **Given** the Strategist produces a TradeProposal with entry, stop loss, take profit,
   thesis, evidence, confidence, invalidation, and time horizon, **When** the Risk Engine
   approves it, **Then** a simulated bracket order is created with the approved size and an
   active stop loss.
3. **Given** a proposal whose risk exceeds the per-trade risk limit, **When** the Risk Engine
   evaluates it, **Then** it either reduces the size to fit the limit (modify) or rejects it.
   The verdict and the rule involved are recorded.
4. **Given** a proposal without a stop loss, **When** the Risk Engine evaluates it, **Then** it
   is rejected.
5. **Given** the Strategist decides NO_TRADE, **When** the cycle ends, **Then** the decision is
   recorded with its reasoning as a valid outcome and no order is created.
6. **Given** an open position moving in profit, **When** the management rules trigger,
   **Then** ATLAS moves the stop loss only in the direction that reduces risk, or closes the
   position fully, and records each action.
7. **Given** a scale-in, scale-out, or partial take-profit request for an open position,
   **When** it is submitted, **Then** it is refused as out of scope for v1 and the refusal is
   recorded.

---

### User Story 2 - Kill switch, circuit breakers, and fail-safe (Priority: P1)

The operator can stop ATLAS at any moment with one always-visible kill switch. ATLAS also
protects itself: when data is stale, the exchange or an AI model fails, volatility or spread
becomes extreme, a macro event blackout is active, or a global risk limit is breached, it
stops opening new positions while it keeps protecting and managing the positions it already
has.

**Why this priority**: autonomy is acceptable only while the operator keeps final control
and the system fails safe. This story is required before any capital is at risk.

**Independent Test**: inject failure conditions (stale feed, exchange outage, AI timeout,
extreme volatility, daily loss breach) and trigger the kill switch while positions are open.
Check that no new position opens, that existing stop losses remain active, and that the mode
and reasons are visible.

**Acceptance Scenarios**:

1. **Given** ATLAS has open positions and pending entry orders, **When** the operator
   triggers the kill switch, **Then** new orders stop, pending entry orders are cancelled,
   protective orders stay active, and the mode becomes Halted.
2. **Given** the kill switch is triggered, **When** the operator also chooses "close all",
   **Then** every open position is closed and the result is shown per position.
3. **Given** the price feed for an asset is older than its freshness threshold, **When** a
   proposal for that asset arrives, **Then** it is rejected because of stale data and existing
   positions keep being managed.
4. **Given** an AI model call times out or returns invalid output, **When** the cycle
   completes, **Then** the outcome is recorded as "no decision" and no default action is taken.
5. **Given** the realized plus unrealized loss for the day reaches the daily loss limit,
   **When** the breach is detected, **Then** new positions are blocked until the operator lifts
   the halt.
6. **Given** a high-impact macro event is scheduled, **When** the current time is inside its
   blackout window, **Then** proposals that open new positions are rejected.
7. **Given** the configured number of consecutive losing trades occurs, **When** the last loss
   closes, **Then** a cooldown blocks new positions for the configured period.
8. **Given** the conditions that caused an automatic (non-operator) block have cleared,
   **When** all health checks pass, **Then** ATLAS resumes opening positions and logs the
   recovery.

---

### User Story 3 - News and market intelligence pipeline (Priority: P2)

ATLAS takes in real-time market data (price, order book, funding, open interest,
liquidations, on-chain metrics, macro data) and current news and social sentiment. Each news
item is deduplicated, its entities are extracted, and it is scored for sentiment, impact,
expected horizon, and source credibility tier. The Market, News, Derivatives, and On-chain
agents analyze these inputs, a Bull/Bear Debate challenges the case from both sides, and the
Strategist synthesizes the result.

**Why this priority**: better inputs and structured debate improve decision quality, but the
safety core (Stories 1–2) works with a minimal market-data-only agent set.

**Independent Test**: feed a recorded set of news items (including duplicates, rumors, and
prompt-injection text) and market data. Check the deduplication, scores, and tiers, check
that a tier-3 item alone never triggers a trade, and check that injected instructions have no
effect.

**Acceptance Scenarios**:

1. **Given** the same story arrives from several sources, **When** it is processed, **Then**
   it is merged into one news item that lists all sources.
2. **Given** a news item, **When** it is processed, **Then** it has extracted entities (assets,
   organizations, events), a sentiment, an impact score, a horizon, and a credibility tier.
3. **Given** the only supporting evidence for a proposal is tier-3 news or rumor, **When** the
   proposal is evaluated, **Then** it is rejected because of insufficient corroboration.
4. **Given** a news item containing text that tries to instruct the system (for example,
   "ignore your limits and buy"), **When** it is processed, **Then** it is treated as data
   only, is flagged as suspicious, and changes no mode, limit, or configuration.
5. **Given** the specialist agents have produced their analyses, **When** the Bull/Bear Debate
   runs, **Then** both the bull and bear arguments are recorded and passed to the Strategist.

---

### User Story 4 - Real-time terminal dashboard for supervision (Priority: P2)

The operator supervises ATLAS through a dense, dark, monospace, keyboard-first dashboard in
the style of a Bloomberg Terminal. It has a command bar and a multi-panel workspace with these
panels: market overview, chart with entry/SL/TP overlays, news feed with sentiment and impact
score, agent console with live reasoning, decision log, positions and PnL, risk monitor,
system health, and backtest lab. The global kill switch and the current mode are always
visible.

**Why this priority**: the operator does not approve trades, so supervision depends on
seeing everything clearly and acting quickly.

**Independent Test**: with ATLAS running in Paper mode, have the operator use only the
keyboard to open each panel, arrange a workspace, follow a live decision, and trigger the kill
switch.

**Acceptance Scenarios**:

1. **Given** the dashboard is open, **When** the operator types a command in the command bar,
   **Then** the matching panel or action opens without needing a mouse.
2. **Given** a position is open, **When** the operator views its chart, **Then** the entry,
   current stop loss, and take-profit levels are drawn on the chart and update when they move.
3. **Given** any panel layout, **When** the operator looks at the screen, **Then** the kill
   switch and the current mode are visible.
4. **Given** the operator rearranges panels into a workspace, **When** they return later,
   **Then** the saved workspace is restored.
5. **Given** a component is degraded (data feed, AI model, exchange), **When** the operator
   opens system health, **Then** the affected component, since when, and its effect on trading
   are shown.

---

### User Story 5 - End-to-end trade traceability and replay (Priority: P2)

For any trade, the operator can follow the chain from the news and data that informed it,
through each agent's analysis, the debate, the TradeProposal, the Risk Engine verdict, and the
orders, to the exchange fills and the final result. Any past decision can be replayed from its
stored snapshot.

**Why this priority**: required by the constitution (auditable and reproducible decisions).
It is also how the operator decides whether ATLAS has earned promotion to the next stage.

**Independent Test**: pick any closed trade from a Paper run, trace it from fill to sources in
the dashboard, then replay the decision and compare the Risk Engine verdict and order
requests.

**Acceptance Scenarios**:

1. **Given** a closed trade, **When** the operator opens its trace, **Then** the source news,
   market snapshot, agent outputs, debate, proposal, risk verdict, orders, and fills are shown
   as one linked chain.
2. **Given** a recorded decision, **When** it is replayed, **Then** the Risk Engine verdict and
   order requests are identical to the originals.
3. **Given** any audit record, **When** it is inspected, **Then** it contains no secrets.

---

### User Story 6 - Backtest lab (Priority: P3)

The operator runs a strategy configuration against historical market data and news in
Backtest mode. Backtests use the same Risk Engine rules and the same order simulation as
Paper mode, and the results (equity curve, drawdown, trade list, rejection reasons) appear in
the backtest lab panel.

**Why this priority**: useful for tuning risk parameters and judging behavior, but not needed
to run Paper mode.

**Independent Test**: run a backtest over a historical period and check that each simulated
trade has a stored Risk Engine verdict, that results are reproducible across runs, and that
no future data was used.

**Acceptance Scenarios**:

1. **Given** a historical period and configuration, **When** a backtest runs, **Then** it
   produces an equity curve, maximum drawdown, a trade list, and a count of rejections by rule.
2. **Given** the same period, configuration, and recorded AI responses, **When** the backtest
   is run twice, **Then** the results are identical.
3. **Given** a backtest at time T, **When** an agent or rule reads data, **Then** only data
   available at or before T is visible.
4. **Given** a backtest is running, **When** it executes, **Then** no live or paper order is
   created.

---

### User Story 7 - Staged promotion to live and Autonomous trading (Priority: P3)

After Paper mode has met the promotion criteria, the operator explicitly moves ATLAS to
Co-pilot, the transition stage with a hard small-capital cap, and later to Autonomous with full
capital. Each promotion is an explicit, authenticated operator action recorded in the audit
log.

**Why this priority**: Autonomous mode is the end goal, but the constitution requires it to
come last, after the earlier stages have been proven.

**Independent Test**: try to activate a live mode by every non-operator path (configuration
default, invalid setting, restart, AI output) and confirm all of them fail. Then promote
through the operator action and confirm the cap and the audit entry.

**Acceptance Scenarios**:

1. **Given** Paper mode, **When** the operator promotes to Co-pilot with explicit
   authentication and confirmation, **Then** the mode changes, the capital cap applies, and the
   change is recorded in the audit log.
2. **Given** an attempt to skip from Paper directly to Autonomous, **When** it is submitted,
   **Then** it is refused.
3. **Given** a missing or invalid mode setting at startup, **When** ATLAS boots, **Then** it
   runs in Paper mode, and any live positions found on the exchange are still managed and
   protected.
4. **Given** Co-pilot mode, **When** a Risk-approved proposal is ready, **Then** ATLAS
   places the trade by itself without operator confirmation, exactly as in Autonomous mode,
   except that total live exposure can never exceed the Co-pilot capital cap.
5. **Given** the exchange API key permits withdrawals or has no IP whitelist (where the
   exchange exposes these settings), **When** the operator tries to enter a live mode, **Then**
   activation is refused with the reason.
6. **Given** a promotion gate criterion is not met (for example, fewer than 10 closed trades or
   an open safety incident), **When** the operator attempts promotion, **Then** the promotion is
   refused and the failing criterion is named.
7. **Given** ATLAS is in Co-pilot or Autonomous, **When** a safety incident, a 10% maximum
   drawdown, or repeated daily-loss breaches occur, **Then** ATLAS demotes itself to Halted,
   alerts the operator, and waits for the operator to choose the next mode.

---

### Edge Cases

- The exchange accepts the entry order but placing the stop loss fails: the position is closed
  immediately and the incident is raised.
- An entry order fills partially: the protective stop is sized to the filled quantity, the
  unfilled remainder is cancelled after its timeout, and the position is never left without a
  stop loss (FR-068).
- The exchange is unreachable while positions are open: no new positions are opened, existing
  stop losses resting on the exchange stay in place, and ATLAS reconciles state when the
  exchange connection returns.
- Local state and the exchange disagree (unknown position, missing order, balance mismatch):
  new positions are blocked until the difference is resolved, and the operator is alerted.
- A network timeout leaves an order's status unknown: new orders on that venue are frozen,
  the state is resolved by querying the exchange rather than resubmitting, and three
  consecutive unresolved attempts move ATLAS to Halted (FR-069).
- The client-side stop monitor stops sending heartbeats: ATLAS enters Halted within the
  heartbeat window and attempts to close the open positions (FR-067).
- An exit is requested while ATLAS is Halted, a circuit breaker is active, or the AI spend cap
  is exhausted: the reduce-only order still executes, because suppressing entries never
  suppresses exits (FR-063).
- Market slippage exceeds the slippage guard: the order is not sent, or the unfilled part is
  cancelled, and the reason is recorded.
- A proposal's stop loss is on the wrong side of the entry, or its take profit is not
  reachable from the entry: the proposal is rejected as invalid.
- The Risk Engine is unavailable or errors: no order is created.
- ATLAS restarts while positions are open: it restores state from the exchange and the audit
  log, keeps managing the positions, and starts in Paper mode unless an operator-authorized
  live mode is still recorded as valid.
- Insufficient free quote balance for an approved size: the order is reduced to the
  available balance only if it still meets the minimum order size; otherwise it is skipped
  and the reason is recorded. ATLAS never borrows to fill an order.
- A remote kill-switch command arrives from an identity not on the allowlist, or with a wrong
  or expired second factor: the command is refused, nothing changes, and the attempt is
  logged and alerted.
- The dashboard disconnects: trading safety does not depend on it, and the kill switch remains
  available through the remote control path (FR-045).
- Clock drift beyond tolerance: data freshness cannot be trusted, so new positions are blocked.
- Exchange maintenance or delisting of a tracked asset: the asset is removed from the tradable
  universe and open positions in it are closed according to their rules.

## Requirements *(mandatory)*

### Functional Requirements

**Modes and control**

- **FR-001**: System MUST support the modes Backtest, Paper, Co-pilot, Autonomous, and Halted,
  and MUST show the current mode at all times. Each mode MUST be enforced at the Execution
  Engine boundary, not only in the interface: an order that opens or increases a position is
  refused with `MODE_NOT_TRADABLE` in Paper (for a venue enabled for live trading) and in
  Halted, while orders that only reduce exposure proceed in every mode (FR-063, FR-071).
- **FR-002**: System MUST start in Paper mode by default. A missing, invalid, or unreadable
  mode setting MUST resolve to Paper.
- **FR-003**: System MUST allow live modes (Co-pilot, Autonomous) only through an explicit,
  authenticated operator action recorded in the audit log, only in the order Paper →
  Co-pilot → Autonomous, and only when every promotion gate criterion for that step is met.
  Promotion MUST NEVER happen automatically.
- **FR-004**: System MUST enforce a hard capital cap in Co-pilot mode, set by the operator.
  Apart from this cap, Co-pilot MUST behave exactly like Autonomous: Risk-approved trades are
  executed without operator confirmation.
- **FR-005**: System MUST NOT require operator approval for individual trades in Autonomous
  mode.
- **FR-006**: System MUST provide a global kill switch that immediately stops new orders,
  cancels pending entry orders, keeps protective orders active, and sets the mode to Halted.
  An optional "close all" action MUST close every open position.
- **FR-007**: The kill switch MUST work without any AI model and MUST stay available when the
  dashboard, data feeds, or agents fail, including through a remote path that does not need
  the dashboard (see FR-045).
- **FR-008**: Only the operator MUST be able to leave Halted mode or lift a halt caused by a
  global risk limit.

**Market data and news**

- **FR-009**: System MUST ingest real-time price, order book, funding rate, open interest,
  liquidations, on-chain metrics, and macro data (including a calendar of macro events) for
  the tradable assets.
- **FR-010**: System MUST track the freshness of every data source against a configurable
  threshold per source and MUST mark data as stale when it is exceeded.
- **FR-011**: System MUST ingest news and social sentiment and MUST deduplicate stories that
  come from several sources, keeping the list of sources.
- **FR-012**: System MUST attach to each news item its extracted entities, sentiment, impact
  score, expected horizon, and source credibility tier (tier 1 most credible, tier 3 rumor or
  unverified).
- **FR-013**: System MUST NOT open a position when tier-3 news or rumor is the only supporting
  evidence. Risk Engine rule `NEWS_ONLY_EVIDENCE` enforces this: a proposal passes only with at
  least one technical (`BAR`) item or one tier-1/tier-2 news item, and is rejected when every
  item is tier-3 news or social, or when the evidence list is empty.
- **FR-014**: System MUST treat all external text as untrusted data. It MUST flag suspected
  prompt-injection content, and external text MUST NOT change mode, risk limits,
  configuration, or agent instructions.

**Agents and decisions**

- **FR-015**: System MUST run Market, News, Derivatives, and On-chain analysis agents, a
  Bull/Bear Debate, and a Strategist each decision cycle, and MUST record each agent's output.
- **FR-016**: The Strategist MUST output either a TradeProposal (asset, side, entry, stop
  loss, one or more take-profit levels, thesis, evidence with sources, confidence,
  invalidation condition, time horizon) or NO_TRADE with its reasoning. The time horizon MUST
  fall within the configured swing range (default 4 hours to 3 days).
- **FR-017**: System MUST validate every TradeProposal against its schema. An invalid proposal
  MUST be rejected and logged, and MUST NOT be repaired into an order.
- **FR-018**: System MUST treat NO_TRADE as a valid outcome, and MUST treat an AI timeout or
  invalid AI output as "no decision", never as a default trade.
- **FR-019**: Agents MUST NOT be able to place, modify, or cancel orders directly. Only the
  Execution Engine may do so, and only the Execution Engine holds venue credentials (FR-064).
- **FR-020**: Position management actions MUST be structured decisions that pass the Risk
  Engine before execution, and v1 MUST support exactly two kinds: MOVE_STOP, which may only
  reduce risk and MUST never move the stop away from the entry, and CLOSE, which closes the
  position in full. Scale in, scale out, and partial take profit are out of scope for v1 and
  MUST be refused; the refusal is recorded with its reason. The risk-increasing and
  partial-exit semantics of a management action remain reserved for a later version.

**Risk Engine**

- **FR-021**: The Risk Engine MUST evaluate every proposal and management action and return
  approve, modify, or reject, along with the rules applied. It MUST be deterministic and MUST
  NOT use an AI model.
- **FR-022**: A modify verdict MUST only make a proposal more conservative (smaller size).
  It MUST NOT change the entry, stop loss, or take profit.
- **FR-023**: The Risk Engine MUST size positions from the configured risk per trade (the
  maximum loss if the stop loss is hit, as a share of equity), including expected fees, taxes,
  and slippage read from the venue profile. Position size MUST be derived as
  `risk budget / stop-loss distance`, so a wider stop always yields a smaller position.
- **FR-024**: The Risk Engine MUST enforce spot-only, unleveraged buying (total position
  value never exceeds available quote balance), a maximum single position of 25% of equity,
  maximum exposure per asset of 50% of equity, maximum total exposure of 100% of equity,
  daily loss limit, maximum drawdown, loss-streak cooldown, macro event blackout windows, and
  the operator's global limits. Exposure, daily loss, and drawdown MUST be computed at
  portfolio level from global mark-to-market equity, aggregated across every venue, and MUST
  NOT be evaluated per venue in isolation (FR-066). The numeric thresholds in this
  requirement and in FR-027 are normative (FR-065).
- **FR-025**: The Risk Engine MUST reject any proposal without a valid stop loss, and any
  proposal to open a short position, borrow, or use margin or derivatives.
- **FR-051**: The Risk Engine MUST reject any decision whose smallest order the exchange
  accepts (minimum quantity and minimum notional) would exceed the per-trade risk budget,
  and MUST record that rule (`MIN_ORDER_EXCEEDS_RISK_BUDGET`) as the rejection reason. ATLAS
  MUST NOT widen the risk budget to make such an order fit.
- **FR-052**: System MUST enforce an operator-set spend cap on AI model usage with a daily
  limit (default Rp5.000) and a monthly limit (default Rp250.000 covering inference, infra,
  and data), and MUST use cost-appropriate models: a cheap model for bulk tasks such as news
  classification and a stronger model only for the Strategist. When a cap is reached, ATLAS
  MUST degrade in order: first reduce analysis frequency, then stop opening new positions.
  Position management, stop losses, and the kill switch MUST never be throttled, and existing
  positions MUST keep being managed and closed.
- **FR-026**: The Risk Engine MUST reject any action that moves a stop loss in the direction
  that increases risk.
- **FR-027**: Circuit breakers MUST block new positions when volatility exceeds 5 standard
  deviations of the pair's recent return distribution, the spread exceeds 3x its rolling
  24-hour median, price or order book data is older than 10 seconds, clock drift exceeds
  tolerance, or the exchange or AI models are failing. Blocks caused by these conditions MUST
  lift automatically when the conditions clear, and the recovery MUST be logged. These values
  are requirements, not defaults, and only the operator may change them through audited
  configuration (FR-065).
- **FR-028**: Breaching the daily loss limit or the maximum drawdown MUST halt new positions
  until the operator lifts the halt. Halting MUST NOT block orders that only reduce exposure
  (FR-063).
- **FR-029**: AI output and external content MUST NOT change Risk Engine configuration or
  verdicts. Only the operator may change risk configuration, and each change MUST be audited.

**Execution Engine**

- **FR-030**: The Execution Engine MUST support market, limit, stop, and bracket orders
  (entry with attached stop loss and take profit). On a venue without native OCO the attached
  stop MUST be enforced by the client-side stop monitor described in FR-067; on a venue that
  offers native stop orders those MUST be used instead.
- **FR-031**: Every order MUST be idempotent, scoped to its venue. A retry after an unknown
  result MUST NOT create a duplicate order on that venue or any other, and the unknown result
  MUST be resolved as specified in FR-069 rather than resubmitted.
- **FR-032**: The Execution Engine MUST enforce a slippage guard and MUST NOT fill beyond the
  configured slippage limit.
- **FR-033**: If the stop loss cannot be confirmed after an entry fill, the Execution Engine
  MUST close the position immediately and raise an incident. A close that itself fails MUST
  follow the escalation in FR-070. The incident is `STOP_UNCONFIRMED` (class `SAFETY`), the close
  is reduce-only, and no mode, breaker, or budget condition may block it.
- **FR-034**: The Execution Engine MUST reconcile positions, orders, and balances with each
  enabled venue at startup and periodically, and MUST maintain one portfolio view across
  venues. A mismatch MUST block new positions until it is resolved.
- **FR-035**: Paper and Backtest modes MUST simulate fills, fees, taxes, minimum order size,
  slippage, and rate limits using each venue's own profile and the same order rules as live
  modes.
- **FR-036**: System MUST verify the exchange key permissions where the exchange exposes them,
  and MUST refuse live modes if withdrawals are enabled or no IP whitelist is active.

**Audit and traceability**

- **FR-037**: System MUST store an append-only audit record for each decision cycle: input
  snapshots, the full prompts, model identifiers and versions with their parameters, raw and
  parsed outputs, the risk verdict with reasons, order requests, exchange responses and fills,
  the code version, and a configuration fingerprint.
- **FR-038**: System MUST link every fill back to its orders, risk verdict, proposal, agent
  outputs, and source data and news, so that the full chain can be viewed.
- **FR-039**: System MUST be able to replay any recorded decision and produce the same risk
  verdict and order requests.
- **FR-040**: Secrets MUST NOT appear in audit records, logs, prompts, or the dashboard.

**Dashboard**

- **FR-041**: The dashboard MUST provide these panels: market overview, chart with entry/SL/TP
  overlays, news feed with sentiment and impact score, agent console with live reasoning,
  decision log, positions and PnL, risk monitor, system health, and backtest lab.
- **FR-042**: The dashboard MUST be dark, dense, monospace, and keyboard-first, with a command
  bar that reaches every panel and action, and a multi-panel workspace that can be saved.- **FR-043**: The kill switch and the current mode MUST be visible in every layout.
- **FR-044**: Dangerous actions (kill switch with close-all, mode promotion, risk limit
  changes) MUST require explicit confirmation. The kill switch itself MUST take at most one
  confirmation step.
- **FR-045**: The dashboard MUST be accessible only to the authenticated operator, and only
  from the operator's machine or a private network. It MUST NOT be exposed to the public
  internet. A separate, minimal remote control path MUST let the operator trigger the kill
  switch (and view mode, halt state, and open positions) from anywhere. That path MUST accept
  commands only from an allowlisted operator identity and MUST require a second-factor
  confirmation (for example, a PIN or one-time code) before any action is taken. It MUST NOT
  offer mode promotion or risk limit changes.
- **FR-046**: The risk monitor MUST show current usage against every limit (risk per trade,
  exposure, daily loss, drawdown, cooldowns, active blackouts, active circuit breakers).
- **FR-047**: System MUST alert the operator about halts, circuit breakers, reconciliation
  mismatches, stop-loss failures, and component failures.

**Backtest**

- **FR-048**: Backtest mode MUST use only data available at each simulated moment, MUST apply
  the same Risk Engine rules as the other modes, and MUST produce reproducible results when
  given recorded AI responses.
- **FR-049**: Backtest results MUST include an equity curve, maximum drawdown, a trade list,
  rejection counts by rule, fees and taxes paid, and per-trade statistics. Backtest and Paper
  runs MUST be evaluated over multi-day periods and reported per number of trades, not as
  PnL alone.

**Scope**

- **FR-050**: System MUST trade only spot pairs (long only: buy to open, sell to reduce or
  close) on venue accounts owned by the operator. Derivatives market data (funding, open
  interest, liquidations) MUST be used only as analysis signals, never traded.
- **FR-053**: System MUST support multiple venues through one uniform venue interface
  covering market data, orders, balances, fees and taxes, and minimum order size. Each venue
  MUST carry its own profile (fees, taxes, minimum order, rate limits, compliance status,
  enabled-for-live flag). Adding or disabling a venue MUST be configuration only and MUST NOT
  require a Risk Engine change.
- **FR-054**: Only venues with a valid OJK license MUST be enabled for live modes; a venue
  without one MUST be refused for Co-pilot and Autonomous and an attempt MUST be logged.
  Market data MAY be ingested read-only from any venue, including venues not enabled for
  trading.
- **FR-055**: Every order, position, balance, fill, and risk verdict MUST record the venue it
  belongs to, and the portfolio view MUST combine all enabled venues for exposure, daily
  loss, and drawdown.
- **FR-056**: The dashboard MUST show AI spend for the current day and month against their
  caps, and MUST show when a cap is reached and which throttle stage is active.
- **FR-057**: Every position MUST be closed automatically when it reaches the maximum holding
  time (default 5 days), or earlier under its exit rules, and the time-stop close MUST be
  recorded with its reason.
- **FR-058**: The Risk Engine MUST reject any proposal whose expected reward-to-risk ratio,
  computed net of the venue's fees, taxes, and slippage, is below 1.5:1.
- **FR-059**: Promotion gates MUST be evaluated from recorded data and shown to the operator
  before any promotion: Paper → Co-pilot requires at least 30 days, at least 500 recorded
  decision cycles, at least 10 closed trades, maximum drawdown of 5% of equity or less, zero
  open safety incidents, and passing chaos drills (stale data, venue outage, AI timeout) plus
  kill-switch drills through both the dashboard and the remote path. Co-pilot → Autonomous
  requires at least 30 days in Co-pilot under the capital cap, at least 10 closed live trades,
  maximum drawdown of 5% or less, zero open safety incidents, no unresolved reconciliation
  mismatch, live slippage no worse than the Paper assumption by more than 50% and never beyond
  the slippage guard of 0.3%, and no AI spend cap breach. A gate that is not met MUST block
  promotion and state which criterion failed.
- **FR-060**: System MUST automatically demote to Halted when a safety incident occurs, when
  maximum drawdown reaches 10% of equity, or when the daily loss limit is breached
  repeatedly. Only the operator may choose the mode to return to afterwards, and the demotion
  and its cause MUST be recorded and alerted.
- **FR-061**: A safety incident MUST mean exactly: an order without a stop loss, a breach of a
  hard risk limit, a duplicated order, an unresolved position mismatch, or a kill switch that
  failed to act.

**Execution Path Integrity**

- **FR-062**: The Execution Engine MUST reject any order that opens or increases a position
  unless it carries a valid Risk Engine verdict. A verdict MUST be signed, single use, bound
  to the proposal id, venue, symbol, side, and size, and MUST expire 60 seconds after issue.
  A consumed, mismatched, altered, or expired verdict MUST be refused and recorded.
- **FR-063**: Orders that only reduce exposure - closing a position, or placing and moving its
  protective stop - MUST NOT require a new verdict and MUST NOT be blocked by an AI spend cap,
  a circuit breaker, or Halted mode. Suppressing new entries MUST never suppress exits.
- **FR-064**: Only the Execution Engine may hold venue credentials. Agent and model processes
  MUST have no venue credentials and no credentialed network path to a venue order endpoint,
  and that isolation MUST be verifiable from configuration and by test, not by code review
  alone.
- **FR-065**: The numeric trigger values are requirements, not implementation defaults: spread
  above 3x its rolling 24-hour median, volatility above 5 standard deviations, data older
  than 10 seconds, exposure per asset above 50% of equity, total exposure above 100% of
  equity. Only the operator may change them, through configuration, and every change MUST be
  audited.
- **FR-066**: The daily loss limit MUST reset at 00:00 WIB on a calendar-day basis, not on a
  rolling window, and MUST be computed from global equity across all venues including
  unrealized PnL. Maximum drawdown MUST be measured from the global mark-to-market equity
  high-water mark established at the start of the current Live stage.
- **FR-067**: Bracket stops on a venue without native OCO MUST be enforced by a client-side
  stop monitor: a separate process with a heartbeat that places the protective order through
  the Execution Engine. Native venue stop orders MUST be used when the venue supports them.
  If the heartbeat is missing for 30 seconds, ATLAS MUST enter Halted and attempt to close the
  open positions.
- **FR-068**: After a partial fill, the protective stop MUST be sized to the filled quantity,
  the unfilled remainder MUST be cancelled after its timeout, and a position MUST never be left
  without a stop loss. The same rule applies to every later increase or reduction of the
  position.
- **FR-069**: An order whose result is unknown (timeout, no response) MUST freeze new orders on
  that venue, MUST be resolved by querying the venue rather than by resubmitting, and MUST move
  ATLAS to Halted after three consecutive unresolved attempts.
- **FR-070**: When closing a position fails, the Execution Engine MUST retry with backoff,
  alert the operator over the remote control channel after the second failure, then close with
  a market order within the slippage guard. All new entries MUST stay stopped until the
  position is closed.
- **FR-071**: The Execution Engine MUST be the single writer for every order mutation - place,
  cancel, and modify - on every venue. Cancelling or modifying an entry order that has not
  filled MUST be recorded in the audit log and MUST be refused when the order's verdict is no
  longer valid, while cancelling an order that reduces exposure MUST never be blocked
  (FR-063).

### Key Entities

- **Operator**: the single human owner who supervises, sets limits, changes modes, and uses
  the kill switch.
- **Mode**: the current operating state (Backtest, Paper, Co-pilot, Autonomous, Halted), with
  its history of changes and who made them.
- **Market Snapshot**: point-in-time market data for an asset (price, order book, funding,
  open interest, liquidations, on-chain and macro values), with its freshness.
- **News Item**: a deduplicated story with its sources, entities, sentiment, impact score,
  horizon, credibility tier, and injection flag.
- **Agent Analysis**: one agent's output in a cycle, including the bull and bear arguments
  from the debate.
- **TradeProposal**: the Strategist's structured decision (asset, side, entry, stop loss, take
  profits, thesis, evidence, confidence, invalidation, horizon), or NO_TRADE with reasoning.
- **Management Action**: a request to move the stop loss in the risk-reducing direction, or to
  close the position in full. Scale in, scale out, and partial take profit are not valid
  management actions in v1.
- **Risk Verdict**: approve, modify, or reject, with the rules applied, any adjusted size,
  and a signature, an expiry, and a consumed flag that make it single use for one bound order
  intent.
- **Risk Configuration**: the operator-set limits and thresholds, with version history.
- **Order**: an instruction to one venue or its simulator, with its venue, idempotency key,
  type,
  status, and the verdict that authorized it.
- **Fill**: an execution report for an order (price, quantity, fee, time).
- **Position**: an open or closed exposure, with its protective orders, the stop monitor
  heartbeat that maintains them, PnL, and history of management actions.
- **Equity Baseline**: the global mark-to-market equity series, its daily reset boundary
  (00:00 WIB), and the high-water mark of the current Live stage that drawdown is measured
  against.
- **Decision Record**: the audit record that links all of the above for one cycle and makes
  replay possible.
- **Incident**: a recorded safety event. A safety incident means exactly one of: an order
  without a stop loss, a breach of a hard risk limit, a duplicated order, an unresolved
  position mismatch, or a kill switch that failed to act. Other recorded events (halt,
  circuit breaker, component failure) are tracked separately as operational incidents.
- **Backtest Run**: a historical simulation with its configuration, period, and results.
- **Venue**: one trading venue with its integration and its profile (fees, taxes, minimum
  order, rate limits, OJK license status, enabled-for-live flag), plus the operator's account
  on it.

## Success Criteria *(mandatory)*

Success criteria intentionally exclude profit targets. ATLAS makes no claims about
profitability (out of scope for v1).

### Measurable Outcomes

- **SC-001**: In a continuous 30-day Paper run, 100% of orders have a prior approving Risk
  Engine verdict, and 0 orders are created outside the Execution Engine.
- **SC-002**: 100% of positions have an active stop loss within 5 seconds of the entry fill,
  and positions whose stop loss cannot be confirmed are closed within 10 seconds.
- **SC-003**: After the operator triggers the kill switch, no new order is sent after 2
  seconds, and all pending entry orders are cancelled within 5 seconds.
- **SC-004**: In chaos tests (stale data, exchange outage, AI timeout, rejected orders, partial
  fills, stop-loss failure), 0 new positions are opened while a failure condition is active,
  and 100% of existing positions keep their protection.
- **SC-005**: 0 positions are opened where tier-3 news or rumor is the only supporting
  evidence, and 0 injected instructions in test news change any setting.
- **SC-006**: The operator can trace any closed trade from its fill back to its source news
  and data in under 1 minute, using only the keyboard.
- **SC-007**: Replaying any recorded decision reproduces the same risk verdict and order
  requests in 100% of a sample of at least 100 decisions.
- **SC-008**: Price, position, and PnL changes appear on the dashboard within 2 seconds, and
  every panel can be reached from the command bar in 2 commands or fewer.
- **SC-009**: The decision loop runs 24/7 with at least 99.5% availability over a 30-day Paper
  run. Each outage is recorded as an incident and causes no unprotected position.
- **SC-010**: At least 95% of duplicate stories in a labeled test set are merged into a single
  news item.
- **SC-011**: Running the same backtest twice with recorded AI responses gives identical
  results, and no future data is read during a backtest.
- **SC-012**: Every attempt to enter a live mode through a non-operator path (default value,
  invalid setting, restart, AI output, external text) fails.
- **SC-013**: 0 orders are sent below the exchange minimum quantity or minimum notional, and
  100% of proposals whose minimum acceptable order would exceed the per-trade risk budget are
  rejected with the `MIN_ORDER_EXCEEDS_RISK_BUDGET` rule recorded.
- **SC-014**: In a 30-day Paper run, recorded AI spend never exceeds the configured daily cap,
  and 0 new positions are opened once the cap is reached.
- **SC-015**: 0 live orders are routed to a venue that is not marked OJK-licensed or not
  enabled for live modes, and disabling a venue in configuration leaves the Risk Engine
  unchanged.
- **SC-016**: Exposure, daily loss, and drawdown reported by the Risk Engine equal the sum of
  the per-venue values in 100% of a test set covering at least two venues, and 0 positions
  exceed 25% of equity.
- **SC-017**: When the daily or monthly AI cap is reached, ATLAS reduces analysis frequency
  first and then stops opening positions within one cycle, while 100% of open positions keep
  an active stop loss and the kill switch keeps working. The dashboard shows both caps and
  the active throttle stage within 2 seconds.
- **SC-018**: 100% of promotion attempts that miss at least one gate criterion are refused and
  name the failing criterion, and 0 promotions occur without explicit operator confirmation.
- **SC-019**: When a safety incident, a 10% maximum drawdown, or a repeated daily-loss breach
  occurs, ATLAS reaches Halted within 5 seconds and alerts the operator; 0 cases continue
  opening positions after such an event.
- **SC-020**: In a test suite covering expired, consumed, altered, size-mismatched, and
  venue-mismatched verdicts, 0 entry orders are accepted and 100% of rejections are recorded
  with the failing reason.
- **SC-021**: While ATLAS is Halted, while a circuit breaker is active, and while the AI spend
  cap is exhausted, 100% of close and protective-stop orders still execute, and 0 exits are
  suppressed.
- **SC-022**: When the stop monitor stops heartbeating, ATLAS reaches Halted within 5 seconds
  and records a close attempt for every open position.
- **SC-023**: After a partial fill, 100% of open positions have a stop sized to the filled
  quantity, and 0 positions are recorded without a stop at any moment.
- **SC-024**: An unknown order result freezes new orders on that venue within one decision
  cycle, and three consecutive unresolved attempts move ATLAS to Halted and alert the
  operator.
- **SC-025**: A failing close produces a remote-channel alert by the second failure and stops
  all new entries within one cycle; 0 cases keep opening positions while a close is failing.
- **SC-026**: 100% of order mutations - place, cancel, modify - reach a venue only through the
  Execution Engine, each is audited, and 0 mutations are accepted from any other component.
- **SC-027**: 100% of MOVE_STOP attempts that would move the stop away from the entry are
  rejected, and 0 scale-in, scale-out, or partial take-profit actions are accepted in v1.

## Assumptions

- ATLAS has a single operator. Multi-user access, SaaS, managing other people's money,
  custody, automated DeFi swaps, HFT, and market making are out of scope for v1. Scaling in or
  out of an open position and taking partial profit are also out of scope for v1: the venue
  minimum order size on the current capital makes a meaningful partial position impossible, so
  v1 manages a position only by moving its stop and closing it in full (FR-020).
- ATLAS trades through venue accounts owned by the operator, one account per enabled venue,
  using API keys with trading permission only (no withdrawals) and an IP whitelist where the
  venue exposes one.
- The initial venue for live modes is Indodax (IDR pairs, OJK-licensed). Other OJK-licensed
  venues (for example Tokocrypto, Pintu, Bybit Indonesia) can be enabled later through
  configuration only. Market data may come from any venue read-only.
- The tradable universe is a small, operator-configured list of liquid crypto assets per
  venue (for example, BTC and ETH to start).
- The 15-minute cycle is used for monitoring and managing open positions, not as a mandate to
  open new positions that often. New-entry analysis follows the swing horizon and is
  configurable. The loop also triggers on significant events (high-impact news,
  circuit-breaker clearance). Exits through stop loss, take profit, and the time-stop run in
  the Execution Engine or on the venue without waiting for a cycle.
- Starting capital: Rp100.000 total equity, of which the Co-pilot capital cap is Rp30.000;
  Autonomous mode may use the full Rp100.000. Both figures are operator-configurable and
  every change is audited. Because this equity is small, NO_TRADE is expected to be frequent
  whenever the exchange minimum order size does not fit the per-trade risk budget (FR-051);
  the early phase is meant to validate the system, not to produce profit.
- Default AI spend cap: Rp5.000 per day and Rp250.000 per month in total (inference plus
  infrastructure plus data). Bulk tasks such as news classification use a cheap model; only
  the Strategist may use a stronger model. Exceeding a cap counts as a failure condition
  under FR-052.
- v1 uses only free data and news sources (public APIs, RSS feeds, free tiers). Paid data and
  news providers are out of scope for v1, so ingestion MUST respect the rate limits of free
  tiers and MUST degrade rather than fail when a free quota is exhausted.
- Default risk configuration, adjustable by the operator:
  - risk per trade: 0.5% of equity;
  - no leverage (spot only);
  - maximum single position: 25% of equity;
  - maximum exposure: 50% of equity per asset, 100% of equity in total (no borrowing),
    aggregated across venues;
  - daily loss limit: 2% of equity, which halts until the operator lifts it;
  - maximum drawdown from peak: 10%, which halts until the operator lifts it;
  - cooldown: 4 hours after 3 consecutive losses;
  - macro blackout: 30 minutes before and after high-impact events;
  - spread circuit breaker: more than 3x the 24-hour median spread;
  - volatility circuit breaker: a 1-minute move larger than 5x its recent average;
  - price and order-book data are stale after 10 seconds;
  - slippage guard: 0.3%.
- Default promotion gates, adjustable by the operator, evaluated from recorded data:
  - Paper → Co-pilot: at least 30 days, at least 500 recorded decision cycles, at least 10
    closed trades, maximum drawdown of 5% of equity or less, zero open safety incidents, and
    every chaos drill (stale data, venue outage, AI timeout) and kill-switch drill (dashboard
    and remote path) passed.
  - Co-pilot → Autonomous: at least 30 days in Co-pilot under the capital cap, at least 10
    closed live trades, maximum drawdown of 5% or less, zero open safety incidents, no
    unresolved reconciliation mismatch, live slippage no worse than the Paper assumption by
    more than 50% and never beyond the 0.3% slippage guard, and no AI spend cap breach.
  - Gates measure system correctness and safety, not profitability; a small trade sample MUST
    NOT be presented as evidence of performance.
- Credibility tiers: tier 1 is primary sources and major established outlets; tier 2 is
  reputable industry media and verified analysts; tier 3 is unverified social posts, rumors,
  and unknown sources. The operator can adjust source tiers.
- Audit records are kept for at least 1 year. Market and news data needed to replay a decision
  are kept for as long as its audit record.
- The operator interface is in English. The dashboard is used on a desktop screen; mobile
  layouts are out of scope apart from the remote kill-switch path.
- v1 is spot only and long only. In falling markets, NO_TRADE or holding the quote
  stablecoin is expected to be a frequent, valid outcome. Perpetual futures and shorting are
  deferred to a later version, after the Risk Engine has been proven on real data.
- The dashboard is reached over a private network (for example, a VPN). The remote kill-switch
  path is a chat-based messaging channel (default: Telegram) restricted to the operator's
  identity, with a second factor.
- Access to market data, news, on-chain, and macro sources uses free providers, and AI models
  are reached through the provider chosen during planning within the FR-052 caps. The concrete
  providers are selected during planning; provider outages and exhausted free quotas are
  handled as failure conditions that degrade rather than crash the system.
