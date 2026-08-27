from __future__ import annotations

import pytest

from scripts.dev import (
    parse_args,
    proxy_host,
    resolve_api_host,
    resolve_api_port,
    shared_environment,
)


def test_shared_environment_sets_backend_and_frontend_to_same_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORBIDDENLAND_API_PORT", "9999")
    monkeypatch.setenv("FORBIDDENLAND_API_PROXY_TARGET", "http://old-target:1")

    environment = shared_environment("127.0.0.1", resolve_api_port())

    assert environment["FORBIDDENLAND_API_HOST"] == "127.0.0.1"
    assert environment["FORBIDDENLAND_API_PORT"] == "9999"
    assert environment["FORBIDDENLAND_API_PROXY_TARGET"] == "http://127.0.0.1:9999"


def test_shared_environment_uses_loopback_proxy_for_all_interface_binding() -> None:
    environment = shared_environment("0.0.0.0", 9092)

    assert environment["FORBIDDENLAND_API_HOST"] == "0.0.0.0"
    assert environment["FORBIDDENLAND_API_PROXY_TARGET"] == "http://127.0.0.1:9092"


def test_cli_port_overrides_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORBIDDENLAND_API_PORT", "9999")

    assert parse_args(["--api-port", "9093"]).api_port == 9093
    assert resolve_api_port(9093) == 9093


def test_cli_host_and_environment_host_are_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORBIDDENLAND_API_HOST", "0.0.0.0")

    assert resolve_api_host() == "0.0.0.0"
    assert parse_args(["--api-host", "localhost"]).api_host == "localhost"
    assert shared_environment(resolve_api_host(), 9092)["FORBIDDENLAND_API_HOST"] == "0.0.0.0"


@pytest.mark.parametrize("value", ["0", "65536", "not-a-port"])
def test_invalid_api_port_is_rejected(value: str) -> None:
    with pytest.raises((ValueError, SystemExit)):
        resolve_api_port(int(value) if value.isdigit() else value)  # type: ignore[arg-type]


def test_proxy_host_normalizes_wildcard_hosts() -> None:
    assert proxy_host("0.0.0.0") == "127.0.0.1"
    assert proxy_host("::") == "127.0.0.1"


def test_proxy_host_brackets_ipv6_literals_for_http_urls() -> None:
    assert proxy_host("::1") == "[::1]"
