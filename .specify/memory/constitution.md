<!--
Sync Impact Report
- Version change: 1.0.0 → 2.0.0
- Bump rationale: MAJOR. The project is redefined from a generic web + AI system into a
  fully autonomous trader. Principles are restructured and renumbered, and Observability
  and Security by Default are redefined and split.
- Modified principles:
  - I. Test-First (NON-NEGOTIABLE) → IX. Test-First & Chaos Testing (NON-NEGOTIABLE)
    (adds mandatory chaos scenarios for the Risk Engine and Execution Engine)
  - II. Simplicity (YAGNI) → X. Simplicity (YAGNI) (renumbered, content kept)
  - III. Security by Default → VI. External Text Is Untrusted Input +
    VIII. Credential & Secret Hygiene (split and made specific to exchange API keys)
  - IV. Observability → VII. Auditable & Reproducible Decisions (expanded with decision
    snapshots and replay)
- Added principles:
  - I. Decision–Execution Separation
  - II. Deterministic Risk Gate (NON-NEGOTIABLE)
  - III. Fail-Safe Operation
  - IV. Staged Rollout & PAPER by Default
  - V. Operator Control Without Per-Trade Approval
- Added sections: Trading System Constraints (replaces Web & AI System Constraints)
- Removed sections: Web & AI System Constraints (its still-relevant rules moved into
  Trading System Constraints)
- Templates reviewed (read at runtime, not modified): plan-template.md (its Constitution
  Check and Complexity Tracking sections match), spec-template.md, tasks-template.md
- Deferred TODOs:
  - TODO(RATIFICATION_DATE): original adoption date not yet decided by the project owner
-->

# ATLAS Constitution

ATLAS is a fully autonomous trader. It analyzes markets, makes decisions, and executes
buy and sell orders without per-trade human approval. Autonomous mode is the primary
target. The principles below define the safety boundary inside which that autonomy runs.

## Core Principles

### I. Decision–Execution Separation

- AI agents MUST output structured decisions only: data that validates against a
  versioned schema (for example: instrument, side, size, entry, stop loss, take profit,
  rationale, and confidence).
- Only the ATLAS Execution Engine may place, modify, or cancel orders. It MUST be the only
  component with access to exchange trading credentials.
- LLMs and agents MUST NOT have tools, credentials, or network paths that reach exchange
  order APIs directly.
- A decision that fails schema validation MUST be rejected and logged, and MUST NOT be
  repaired heuristically into an order.

Rationale: the LLM is non-deterministic and can be manipulated. Keeping it away from the
order path keeps every order inside code that can be tested and controlled.

### II. Deterministic Risk Gate (NON-NEGOTIABLE)

- Every decision MUST pass the Risk Engine before it becomes an order. No code path may
  create an order that bypasses the Risk Engine.
- The Risk Engine MUST be deterministic: the same decision, account state, market
  snapshot, and configuration MUST always give the same verdict. It MUST NOT call an LLM.
- LLM output MUST NOT override, relax, or reconfigure the Risk Engine, its limits, or its
  verdicts. Only the operator may change risk configuration (see Principle V).
- Every position MUST have a stop loss. The Risk Engine MUST reject any decision without a
  valid stop loss. If stop-loss placement fails after entry, the Execution Engine MUST
  close the position immediately.
- Each rejection MUST record the specific rule that failed.

Rationale: an independent, predictable gate is the only reliable defense against a wrong
or manipulated decision turning into a loss.

### III. Fail-Safe Operation

- When market data is stale (older than the configured threshold), or when a data,
  exchange, Risk Engine, or LLM error occurs, ATLAS MUST NOT open new positions.
- An LLM timeout or empty or invalid output MUST be treated as "no decision", never as a
  default action.
- Existing positions MUST keep being managed in degraded states: stop losses stay active,
  and positions are closed automatically according to their exit rules and risk limits.
- Opening new positions MUST resume automatically only after every health condition
  passes again, and the recovery MUST be logged.
- Freshness thresholds and health conditions MUST be explicit in configuration and MUST
  NOT be silently disabled.

Rationale: in an uncertain state, the safe choice is to not add risk while still
protecting the capital already exposed.

### IV. Staged Rollout & PAPER by Default

- Operating modes are, in order: PAPER → LIVE (small capital) → AUTONOMOUS (full
  capital). A mode MUST NOT be skipped.
- The default mode MUST always be PAPER. A missing, invalid, ambiguous, or unreadable mode
  setting MUST resolve to PAPER.
- A LIVE mode (LIVE or AUTONOMOUS) MUST be activated only by an explicit, authenticated
  operator instruction that is recorded in the audit log. Code changes, defaults,
  environment fallbacks, or LLM output MUST NOT activate LIVE.
- LIVE (small capital) MUST enforce a hard capital cap set by the operator.
- Promotion to the next stage MUST be an operator decision based on measured evidence from
  the previous stage (for example: trade count, drawdown, and incident history), using
  criteria defined in the specification.
- If the mode falls back to PAPER, live positions that are already open MUST still be
  managed and protected according to Principle III.

Rationale: real capital is exposed only after the system has proven itself, and only by a
deliberate human decision.

### V. Operator Control Without Per-Trade Approval

- The operator MUST NOT be required to approve individual trades in AUTONOMOUS mode.
- The operator MUST always have a kill switch. When triggered, it immediately stops new
  orders and cancels pending entry orders. The operator MUST also have an option to close
  all positions.
- The kill switch MUST work without an LLM or agent, and MUST remain available while the
  agent, data feeds, or UI are failing.
- The operator MUST be able to set global risk limits, including at least: maximum loss per
  day, maximum drawdown, maximum total exposure, maximum size per position, and maximum
  order frequency.
- Breaching a global limit MUST trigger an automatic halt of new positions, which only the
  operator may lift.

Rationale: autonomy per trade is acceptable only while humans keep final control over the
whole system.

### VI. External Text Is Untrusted Input

- All external text (news, social media, websites, and other third-party feeds) MUST be
  treated as untrusted data, never as instructions.
- External text MUST be delimited and labeled as data in prompts, and MUST be checked for
  prompt-injection patterns.
- External content MUST NOT change mode, risk limits, configuration, tools, or system
  instructions, whatever it contains.
- Decisions influenced by external text MUST still pass schema validation (Principle I)
  and the Risk Engine (Principle II) without exception.
- All other external input (market data, exchange responses, webhooks, files) MUST be
  validated at the boundary.

Rationale: market-related text is easy to manipulate, and an autonomous trader is an
attractive target for prompt injection.

### VII. Auditable & Reproducible Decisions

- Every decision cycle MUST store an append-only audit record containing at least:
  - a snapshot of the inputs (market data, account state, external text);
  - the full rendered prompt;
  - the provider, model ID and version, and parameters (temperature, seed if available);
  - the raw model output and the parsed decision;
  - the Risk Engine verdict and its reasons;
  - the resulting order requests and exchange responses or fills;
  - the code version and a hash of the active configuration.
- Each decision MUST be replayable from its audit record: the Risk Engine and Execution
  Engine MUST give the same verdict and order requests from the same snapshot.
- Audit records MUST NOT contain secrets (see Principle VIII).
- Services MUST emit structured logs with a correlation ID that links data, decisions,
  risk verdicts, and orders. Every model call MUST record its latency, token usage or cost,
  and outcome.

Rationale: without full evidence, losses cannot be explained, bugs cannot be reproduced,
and promotion between stages cannot be justified.

### VIII. Credential & Secret Hygiene

- Exchange API keys MUST be trade-only: withdrawals disabled and an IP whitelist enabled.
- Where the exchange API exposes key permissions, ATLAS MUST verify them at startup and
  MUST refuse to enter a LIVE mode if withdrawals are enabled or the whitelist is
  inactive.
- Secrets MUST NOT appear in the repository, logs, audit records, prompts, model
  requests, or error messages. They MUST be loaded from the environment or a secret store.
- Each mode MUST use separate keys. PAPER mode MUST NOT have access to live keys.

Rationale: a key that cannot withdraw and is limited by IP greatly reduces the impact of
a leak.

### IX. Test-First & Chaos Testing (NON-NEGOTIABLE)

- Every behavior change MUST start with a failing automated test. The red → green →
  refactor cycle MUST be followed, and every bug fix MUST include a regression test.
- The Risk Engine and Execution Engine MUST have tests that cover every risk rule, the
  stop-loss requirement, every global limit, and the kill switch.
- Chaos scenarios MUST be tested, at least: stale data, exchange down or timing out, LLM
  timeout or invalid output, rejected orders, partial fills, and stop-loss placement
  failure.
- AI behavior MUST be tested with stubbed or recorded model responses, so that tests are
  deterministic and need no network or live exchange.

Rationale: in an autonomous trader, an untested failure path costs real money.

### X. Simplicity (YAGNI)

- Each change MUST be the smallest one that fully satisfies its specification.
- Abstractions, configuration options, strategies, and dependencies MUST NOT be added for
  speculative requirements.
- New runtime dependencies MUST be justified in the feature plan.
- Deviations MUST be recorded in the plan's Complexity Tracking table along with the
  simpler alternative that was rejected.

Rationale: every extra moving part in the order path is a new failure mode.

## Trading System Constraints

- The tech stack is not fixed by this constitution. Each feature plan MUST state and
  justify its languages, frameworks, storage, exchanges, and model providers.
- Model providers MUST sit behind an internal interface that has timeouts, retries with
  backoff, and stub support for tests.
- Order submission MUST be idempotent (for example, through client order IDs) and safe
  against duplicate execution.
- The Execution Engine MUST reconcile local state with the exchange (positions, orders,
  balances) at startup and periodically. A mismatch MUST block new positions until it is
  resolved.
- System time MUST be synchronized. Data freshness is measured against that clock.
- AI features MUST have cost limits (per-cycle tokens or steps, and a budget or rate limit).
- The operator interface, if it is web-based, MUST meet WCAG 2.1 AA for keyboard access,
  focus visibility, and contrast, and MUST show the kill switch at all times.

## Development Workflow & Quality Gates

- Work follows the Spec Kit flow: specify → clarify (when ambiguity remains) → plan →
  tasks → implement. Each plan MUST pass the Constitution Check before implementation.
- A change MUST NOT be merged until all of these pass: the full test suite, chaos tests
  for changes that touch the Risk Engine or Execution Engine, linters and type checks, and a
  dependency vulnerability scan.
- Reviewers MUST reject any change that:
  - lets an LLM reach the order path;
  - bypasses or weakens the Risk Engine;
  - allows a position without a stop loss;
  - changes the default mode away from PAPER;
  - adds a path that activates LIVE implicitly;
  - logs secrets.
- Promotion between rollout stages (Principle IV) is an operator decision, separate from
  merging code.
- Commits SHOULD be small, reviewable work units that keep tests and code together.

## Governance

- This constitution supersedes conflicting practices and guidance in the repository.
  Runtime agent guidance (for example, AGENTS.md) MUST stay consistent with it.
- Amendments MUST be made through `/speckit.constitution` and MUST include a Sync Impact
  Report, a rationale, and migration steps where needed.
- Amendments that weaken Principles I–V or VIII are MAJOR changes and MUST have explicit
  operator approval recorded in the amendment.
- Versioning follows semantic versioning:
  - MAJOR: removal or backward-incompatible redefinition of a principle or governance rule.
  - MINOR: a new principle or section, or materially expanded guidance.
  - PATCH: clarifications, wording, and typo fixes with no semantic change.
- Compliance is checked at every plan (Constitution Check gate) and every code review.
  Exceptions are allowed only for Principle X, and MUST be recorded in Complexity
  Tracking. Principles I–IX MUST NOT be waived.

**Version**: 2.0.0 | **Ratified**: TODO(RATIFICATION_DATE): adoption date not yet decided | **Last Amended**: 2026-11-08
