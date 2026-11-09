"""T062: the credential boundary is a property of the deployment, so it is tested as one.

Nothing here starts a container. The compose file is the artefact that decides which process
receives which secret, so the assertions read it: a key that appears in the wrong service's
environment is a boundary violation whether or not an image was ever built (FR-064, FR-066,
constitution I and VIII).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "infra" / "compose" / "docker-compose.yml"
DOCKERFILE = ROOT / "infra" / "compose" / "Dockerfile.service"

APP_SERVICES = (
    "ingest-market",
    "ingest-news",
    "feature-engine",
    "agents",
    "risk-engine",
    "execution",
    "stop-monitor",
    "backtester",
    "api-gateway",
)

VENUE_MATERIAL = re.compile(r"INDODAX|VENUE_(API_)?(KEY|SECRET)|BINANCE|CCXT")
HMAC_MATERIAL = re.compile(r"ORDER_HMAC")
LLM_MATERIAL = re.compile(r"LLM_(API_)?KEY|OPENAI|ANTHROPIC")
CREDENTIAL_SUFFIX = re.compile(r"(_KEY|_SECRET|_TOKEN|_PASSWORD)$")


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return yaml.safe_load(COMPOSE.read_text())


def _services(compose: dict[str, Any]) -> dict[str, Any]:
    return compose["services"]


def _environment(service: dict[str, Any]) -> dict[str, str]:
    environment = service.get("environment") or {}
    if isinstance(environment, list):
        pairs = [item.split("=", 1) for item in environment]
        return {key: value for key, value in pairs}
    return {str(key): str(value) for key, value in environment.items()}


def _credential_vars(compose: dict[str, Any], pattern: re.Pattern[str]) -> dict[str, list[str]]:
    holders: dict[str, list[str]] = {}
    for name in APP_SERVICES:
        service = _services(compose).get(name)
        assert service is not None, f"{name} is missing from the compose file"
        names = [key for key in _environment(service) if pattern.search(key)]
        if names:
            holders[name] = names
    return holders


def test_only_execution_holds_venue_credentials(
    compose: dict[str, Any],
) -> None:
    holders = _credential_vars(compose, VENUE_MATERIAL)
    assert set(holders) == {"execution"}, f"venue material leaked into {sorted(holders)}"


def test_the_order_signing_key_is_shared_by_exactly_two_services(
    compose: dict[str, Any],
) -> None:
    holders = _credential_vars(compose, HMAC_MATERIAL)
    assert set(holders) == {"execution", "risk-engine"}


def test_agents_hold_only_the_llm_key(compose: dict[str, Any]) -> None:
    holders = _credential_vars(compose, LLM_MATERIAL)
    assert set(holders) == {"agents"}, "the LLM key belongs to agents and nowhere else"


def test_the_gateway_holds_no_order_or_venue_material(compose: dict[str, Any]) -> None:
    environment = _environment(_services(compose)["api-gateway"])
    forbidden = [
        key for key in environment if VENUE_MATERIAL.search(key) or HMAC_MATERIAL.search(key)
    ]
    assert not forbidden


def test_every_credential_reaches_the_process_as_a_file_not_a_value(
    compose: dict[str, Any],
) -> None:
    """A secret in `environment:` lands in `docker inspect` and in every crash dump."""
    for name in APP_SERVICES:
        for key, value in _environment(_services(compose)[name]).items():
            if CREDENTIAL_SUFFIX.search(key):
                assert key.endswith("_FILE"), f"{name}: {key} must be a *_FILE path"
                assert value.startswith("/run/secrets/"), (
                    f"{name}: {key} points outside /run/secrets"
                )


def test_only_the_venue_facing_services_may_egress_to_the_exchange(compose: dict[str, Any]) -> None:
    for name in APP_SERVICES:
        egress = _services(compose)[name].get("x-egress")
        assert egress is not None, f"{name} declares no x-egress allowlist"
        hosts = egress.get("hosts", [])
        assert isinstance(hosts, list)
        reaches_venue = any("indodax" in host for host in hosts)
        assert reaches_venue == (name in {"execution", "ingest-market"}), (
            f"{name} may{' ' if reaches_venue else ' not '}reach the exchange"
        )


def test_every_service_states_where_it_is_allowed_to_talk(compose: dict[str, Any]) -> None:
    for name in APP_SERVICES:
        egress = _services(compose)[name]["x-egress"]
        assert "enforced_by" in egress, f"{name} does not say what enforces its allowlist"
        assert egress["enforced_by"] in {"vps-firewall", "docker-network", "none-required"}


def test_the_image_definition_carries_no_secret_material() -> None:
    text = DOCKERFILE.read_text()
    for forbidden in ("secrets/", ".env", "INDODAX", "api_key", "API_SECRET"):
        assert forbidden not in text, f"Dockerfile.service references {forbidden}"
    assert "ARG SERVICE" in text and "COPY services/${SERVICE}" in text
