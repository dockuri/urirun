"""Tests for URIrun Dockuri adapter.

Verifies:
1. URI encoding vectors, argument type parity, and error shape parity.
2. Clear separation between transport state and domain result.
3. Unsupported schemes return typed unsupported error with zero shell fallback.
4. Exporting existing routes to Dockuri proc catalog format.
5. CLI execution parity.
"""

from __future__ import annotations

import json
import pytest

from urirun.dockuri_adapter import (
    ArgumentValidationError,
    DockuriAdapterError,
    UnsupportedSchemeError,
    execute_dockuri_attempt,
    export_dockuri_catalog,
    main as cli_main,
    parse_and_validate_uri,
    validate_arguments,
)


def test_uri_encoding_vector_parity():
    """Verify that percent-encoded query params and path segments preserve exact parity."""
    raw_uri = "http://api.service.local:8080/v1/query%2Fitems?search=hello%20world&filter=%23active&count=10"
    parsed = parse_and_validate_uri(raw_uri)
    assert parsed["scheme"] == "http"
    assert parsed["netloc"] == "api.service.local:8080"
    assert parsed["path"] == "/v1/query/items"
    assert parsed["query"]["search"] == "hello world"
    assert parsed["query"]["filter"] == "#active"
    assert parsed["query"]["count"] == "10"


def test_argument_type_validation_parity():
    """Verify schema argument validation rejects type mismatches and missing required params."""
    schema = {
        "type": "object",
        "required": ["id", "count"],
        "properties": {
            "id": {"type": "string"},
            "count": {"type": "integer"},
            "enabled": {"type": "boolean"},
        },
    }

    # Valid payload
    payload = {"id": "item-123", "count": 5, "enabled": True}
    assert validate_arguments(schema, payload) == payload

    # Missing required parameter
    with pytest.raises(ArgumentValidationError) as exc_info:
        validate_arguments(schema, {"id": "item-123"})
    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert exc_info.value.details.get("parameter") == "count"

    # Type mismatch: boolean for integer
    with pytest.raises(ArgumentValidationError) as exc_info:
        validate_arguments(schema, {"id": "item-123", "count": True})
    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert exc_info.value.details.get("expected_type") == "integer"

    # Type mismatch: integer for string
    with pytest.raises(ArgumentValidationError) as exc_info:
        validate_arguments(schema, {"id": 999, "count": 5})
    assert exc_info.value.code == "INVALID_ARGUMENT"
    assert exc_info.value.details.get("expected_type") == "string"


def test_separation_of_transport_and_domain_result_http():
    """Verify HTTP execution keeps transport protocol metadata separate from domain result."""
    uri = "https://cloud.service.io/api/v1/resource?format=json"
    payload = {"query": "sample"}

    out = execute_dockuri_attempt(uri, payload, mode="execute")
    assert out["status"] == "ok"
    assert out["uri"] == uri
    assert "transport" in out
    assert "result" in out

    # Transport details
    transport = out["transport"]
    assert transport["protocol"] == "https"
    assert transport["status_code"] == 200
    assert transport["method"] == "GET"
    assert "duration_ms" in transport
    assert transport["duration_ms"] >= 0

    # Domain result details
    result = out["result"]
    assert result["status"] == "ok"
    assert result["data"]["format"] == "json"
    assert result["data"]["query"] == "sample"

    # Transport keys must not bleed into domain result
    assert "status_code" not in result
    assert "headers" not in result
    assert "protocol" not in result


def test_separation_of_transport_and_domain_result_mqtt():
    """Verify MQTT execution keeps broker/topic transport metadata separate from domain payload."""
    uri = "mqtt://iot.broker.net:1883/sensors/temperature"
    payload = {"celsius": 22.5, "device": "thermo-1"}

    out = execute_dockuri_attempt(uri, payload, mode="execute")
    assert out["status"] == "ok"

    transport = out["transport"]
    assert transport["protocol"] == "mqtt"
    assert transport["broker"] == "iot.broker.net:1883"
    assert transport["topic"] == "sensors/temperature"
    assert transport["delivered"] is True

    result = out["result"]
    assert result["status"] == "published"
    assert result["payload"] == payload
    assert "broker" not in result


def test_separation_of_transport_and_domain_result_function():
    """Verify local function execution keeps invocation metadata separate from function return value."""
    def custom_math_add(x: int, y: int) -> int:
        return x + y

    uri = "function://host/math/add"
    payload = {"x": 10, "y": 25}

    out = execute_dockuri_attempt(uri, payload, function_handler=custom_math_add, mode="execute")
    assert out["status"] == "ok"

    transport = out["transport"]
    assert transport["protocol"] == "inprocess"
    assert transport["handler"] == "custom_math_add"
    assert transport["invoked"] is True

    assert out["result"] == 35


def test_unsupported_scheme_rejection_no_shell_fallback():
    """Verify schemes outside HTTP/MQTT/function return typed unsupported error with NO shell fallback."""
    unsupported_schemes = [
        "shell://host/run/command",
        "sh://localhost/bin/cat",
        "exec://bin/ls",
        "unknown://service/action",
        "ftp://files.org/download",
    ]

    for bad_uri in unsupported_schemes:
        with pytest.raises(UnsupportedSchemeError) as exc_info:
            execute_dockuri_attempt(bad_uri, {"arg": "val"})

        err = exc_info.value
        assert err.code == "UNSUPPORTED_SCHEME"
        assert err.status == 501
        assert err.details.get("fallback_permitted") is False
        assert "shell fallback rejected" in err.message


def test_export_dockuri_catalog():
    """Verify route catalog export conforms to wellmanifest.urirun.dockuri-catalog/v1 format."""
    mock_registry = {
        "routes": {
            "http": {
                "api": {
                    "health": {
                        "kind": "http",
                        "adapter": "fetch",
                        "config": {"method": "GET", "url": "http://api.local/health"},
                        "inputSchema": {"type": "object", "properties": {"timeout": {"type": "integer"}}},
                    }
                }
            },
            "mqtt": {
                "events": {
                    "publish": {
                        "kind": "mqtt",
                        "adapter": "mqtt-publish",
                        "config": {"topicPrefix": "system/events"},
                        "inputSchema": {"type": "object", "properties": {"event": {"type": "string"}}},
                    }
                }
            },
        }
    }

    catalog = export_dockuri_catalog(registry=mock_registry, app="urirun")
    assert catalog["schema"] == "wellmanifest.urirun.dockuri-catalog/v1"
    assert catalog["project"] == "urirun"
    assert len(catalog["processes"]) >= 2

    procs = {p["action_uri"]: p for p in catalog["processes"]}
    assert "http://host/api/health" in procs or any("api" in p["uri"] for p in catalog["processes"])

    for proc in catalog["processes"]:
        assert proc["uri"].startswith("proc://local/urirun/")
        assert proc["kind"] == "primitive"
        assert proc["effect"] in ("pure", "read", "write", "unknown")
        assert "input_schema" in proc
        assert proc["run"][0] == "python3"
        assert proc["run"][2] == "urirun.dockuri_adapter"


def test_cli_execution_parity(capsys):
    """Verify CLI entrypoint returns clean JSON and proper exit codes."""
    # Test valid execution
    exit_code = cli_main(["run", "function://host/test/echo", '{"msg": "hello"}'])
    assert exit_code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["status"] == "ok"
    assert data["transport"]["protocol"] == "inprocess"
    assert data["result"]["args"]["msg"] == "hello"

    # Test unsupported scheme via CLI
    bad_exit = cli_main(["run", "shell://host/dangerous/cmd"])
    assert bad_exit == 1
    err_captured = capsys.readouterr()
    err_data = json.loads(err_captured.err)
    assert err_data["status"] == "error"
    assert err_data["code"] == "UNSUPPORTED_SCHEME"
    assert err_data["details"]["fallback_permitted"] is False

    # Test catalog command via CLI
    cat_exit = cli_main(["catalog"])
    assert cat_exit == 0
    cat_captured = capsys.readouterr()
    cat_data = json.loads(cat_captured.out)
    assert cat_data["schema"] == "wellmanifest.urirun.dockuri-catalog/v1"
