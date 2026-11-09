# ADR 0002 — The credential boundary is a property of the deployment, not of a code review

- **Status**: Accepted (T063, M3). Guarded by `tests/security/test_credential_isolation.py`.
- **Date**: 2026-11-09.
- **Scope**: which process may hold which secret, and how that is enforced and checked.

## Context

FR-064 requires that only the Execution Engine holds venue credentials, and constitution I/VIII
require that the trading brain cannot trade. A line of code that happens to call `place_order`
is a weaker guarantee than a process that has no key to sign the request with: the first is a
review finding, the second is physics.

The stack runs nine services from one image definition (`infra/compose/Dockerfile.service`)
with a build argument selecting which service package is installed. Everything else — the
shared packages, the base image, the user id — is identical. So the boundary cannot come from
the image; it has to come from what each container is *given* at run time.

## Decision

1. **Secrets are files, never environment values.** Each secret is a Docker secret mounted at
   `/run/secrets/<name>` and referenced as `ATLAS_*_FILE`. `environment:` is visible in
   `docker inspect`, in `/proc/1/environ`, and in any crash dump; a file is visible only to the
   process that mounts it. The compose file declares five secrets, all sourced from
   `/etc/atlas/secrets/` — outside the repository, so a checkout cannot contain one.
2. **One key per capability, handed to the smallest set of processes.**

   | Secret | Processes |
   | --- | --- |
   | `indodax_api_key`, `indodax_api_secret` | `execution` only |
   | `order_hmac_key` | `risk-engine` (signs) and `execution` (re-checks) |
   | `llm_api_key` | `agents` only |
   | `telegram_bot_token` | `api-gateway` only |

   `risk-engine` holding the signing key is deliberate: it can mint a verdict but it holds no
   venue key, so the worst it can do is produce an authoritative opinion.
3. **Non-secret configuration is shared explicitly.** `x-common-env` (mode, DSN, Redis URL, log
   level) is merged per service with YAML merge keys. A service receives nothing by inheritance
   it did not name — the earlier arrangement used one anchor for every service, which is exactly
   how a key ends up in nine containers.
4. **Egress is declared per service** as `x-egress: {enforced_by, hosts}`. The annotation is the
   contract; `docker-network` means the service is attached to a network whose firewall rules
   permit only those hosts, `none-required` means it makes no outbound calls at all. Only
   `execution` and `ingest-market` may reach the exchange.

## What this does *not* enforce

Docker cannot express a DNS allowlist. The `hosts` lists are enforced by the VPS firewall
(`vps-firewall`) and, for services with no outbound calls, by attaching no route at all. The
test asserts the declaration is present and correctly scoped; it cannot assert the firewall
rule exists, because that lives outside the repository. That gap is the same class of gap as
the runtime DB role (see `research.md` R17): a deployment check, listed as a pre-Live gate in
`tasks.md` T097a.

## Consequences

- Adding a credential means editing the compose file, the ADR table, and the test — three places
  that disagree loudly if they drift.
- A service that needs a new outbound host must declare it, so an accidental `httpx` call to a
  new origin shows up as a deliberate diff rather than as ambient freedom.
- Rotation is per secret file: replace `/etc/atlas/secrets/<name>`, restart the consumers only.
  Nothing is baked into an image, so no rebuild is needed.
