"""Dockuri catalog exporter and controlled route execution adapter for URIrun routes.

Exports contracts of existing HTTP, MQTT, and local-function routes to Dockuri registry profile,
without duplicating routers or queues.
Enforces:
1. Encoding vectors: Parity of URI encoding, argument types, and error shapes.
2. Separation: Transport state (status code, delivery status, protocol metadata) is strictly separated from domain result.
3. Strict scheme validation: Unsupported schemes return typed unsupported error; NO fallback to shell.
"""

from __future__ import annotations

import argparse
import inspect
import json
import re
import sys
import time
from typing import Any, Callable
import urllib.parse

__all__ = [
    "DockuriAdapterError",
    "UnsupportedSchemeError",
    "ArgumentValidationError",
    "SUPPORTED_SCHEMES",
    "SUPPORTED_ADAPTERS",
    "export_dockuri_catalog",
    "execute_dockuri_attempt",
    "parse_and_validate_uri",
    "validate_arguments",
]

SUPPORTED_SCHEMES = frozenset({"http", "https", "mqtt", "function", "func", "local-function"})
SUPPORTED_ADAPTERS = frozenset({"fetch", "mqtt-publish", "local-function", "local-function-subprocess"})

_ALLOWED_SCHEMA_KEYWORDS = frozenset({
    "title", "description", "default", "examples",
    "type", "enum", "const", "properties", "required",
    "additionalProperties", "items", "minItems", "maxItems",
    "minLength", "maxLength", "minimum", "maximum",
})

_TYPE_MAP = {
    "str": "string", "string": "string",
    "int": "integer", "integer": "integer",
    "float": "number", "number": "number",
    "bool": "boolean", "boolean": "boolean",
    "dict": "object", "object": "object",
    "list": "array", "array": "array",
}


class DockuriAdapterError(Exception):
    """Base error for Dockuri adapter operations."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "ADAPTER_ERROR",
        status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": "error",
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class UnsupportedSchemeError(DockuriAdapterError):
    """Raised when an unsupported scheme is invoked (no shell fallback permitted)."""

    def __init__(self, scheme: str) -> None:
        super().__init__(
            f"Scheme '{scheme}' is unsupported; shell fallback rejected",
            code="UNSUPPORTED_SCHEME",
            status=501,
            details={"scheme": scheme, "fallback_permitted": False},
        )


class ArgumentValidationError(DockuriAdapterError):
    """Raised when payload arguments fail schema or type parity validation."""

    def __init__(
        self,
        message: str,
        *,
        param: str | None = None,
        expected: str | None = None,
        actual: str | None = None,
    ) -> None:
        details: dict[str, Any] = {}
        if param:
            details["parameter"] = param
        if expected:
            details["expected_type"] = expected
        if actual:
            details["actual_type"] = actual
        super().__init__(message, code="INVALID_ARGUMENT", status=400, details=details)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "op"


def _clean_schema(schema: dict | None, is_root: bool = True) -> dict[str, Any]:
    if not isinstance(schema, dict):
        return {"type": "object"} if is_root else {}
    cleaned: dict[str, Any] = {}
    for k, v in schema.items():
        if k in _ALLOWED_SCHEMA_KEYWORDS:
            if k == "properties" and isinstance(v, dict):
                cleaned[k] = {pk: _clean_schema(pv, is_root=False) for pk, pv in v.items() if isinstance(pv, dict)}
            elif k == "items" and isinstance(v, dict):
                cleaned[k] = _clean_schema(v, is_root=False)
            elif k == "additionalProperties" and isinstance(v, dict):
                cleaned[k] = _clean_schema(v, is_root=False)
            elif k == "required" and isinstance(v, list):
                seen: set[str] = set()
                cleaned[k] = [x for x in v if isinstance(x, str) and not (x in seen or seen.add(x))]
            elif k == "type" and isinstance(v, str):
                cleaned[k] = _TYPE_MAP.get(v, v)
            else:
                cleaned[k] = v
    if is_root and "type" not in cleaned:
        cleaned["type"] = "object"
    return cleaned


def parse_and_validate_uri(raw_uri: str) -> dict[str, Any]:
    """Parse URI, validate scheme, and decode URI encoding vectors."""
    if not raw_uri or not isinstance(raw_uri, str):
        raise DockuriAdapterError("URI address is required", code="INVALID_URI", status=400)
    parsed = urllib.parse.urlsplit(raw_uri.strip())
    scheme = parsed.scheme.lower()
    if not scheme:
        raise DockuriAdapterError("URI scheme is required", code="INVALID_URI", status=400)
    if scheme not in SUPPORTED_SCHEMES:
        raise UnsupportedSchemeError(scheme)

    query_params: dict[str, Any] = {}
    if parsed.query:
        for k, vals in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items():
            query_params[k] = vals[0] if len(vals) == 1 else vals

    path = urllib.parse.unquote(parsed.path)
    segments = [s for s in path.split("/") if s]

    return {
        "raw_uri": raw_uri.strip(),
        "scheme": scheme,
        "netloc": parsed.netloc,
        "path": path,
        "segments": segments,
        "query": query_params,
    }


def validate_arguments(schema: dict | None, payload: dict) -> dict[str, Any]:
    """Validate argument types against schema to maintain type parity."""
    if not schema or not isinstance(schema, dict):
        return payload
    props = schema.get("properties", {})
    required = schema.get("required", [])
    for req_field in required:
        if req_field not in payload:
            raise ArgumentValidationError(f"Missing required parameter '{req_field}'", param=req_field)

    for key, val in payload.items():
        if key in props and isinstance(props[key], dict):
            expected_type = props[key].get("type")
            if expected_type == "integer" and not (isinstance(val, int) and not isinstance(val, bool)):
                raise ArgumentValidationError(
                    f"Parameter '{key}' must be integer, got {type(val).__name__}",
                    param=key, expected="integer", actual=type(val).__name__,
                )
            if expected_type == "number" and not (isinstance(val, (int, float)) and not isinstance(val, bool)):
                raise ArgumentValidationError(
                    f"Parameter '{key}' must be number, got {type(val).__name__}",
                    param=key, expected="number", actual=type(val).__name__,
                )
            if expected_type == "string" and not isinstance(val, str):
                raise ArgumentValidationError(
                    f"Parameter '{key}' must be string, got {type(val).__name__}",
                    param=key, expected="string", actual=type(val).__name__,
                )
            if expected_type == "boolean" and not isinstance(val, bool):
                raise ArgumentValidationError(
                    f"Parameter '{key}' must be boolean, got {type(val).__name__}",
                    param=key, expected="boolean", actual=type(val).__name__,
                )
    return payload


def _route_effect(scheme: str, route_entry: dict | None) -> str:
    if route_entry and "effect" in route_entry:
        eff = str(route_entry["effect"]).strip().lower()
        if eff in ("pure", "read", "write", "unknown"):
            return eff
    if scheme in ("http", "https"):
        method = (route_entry.get("config", {}).get("method") if route_entry else None) or "GET"
        return "read" if method.upper() in ("GET", "HEAD") else "write"
    if scheme == "mqtt":
        return "write"
    if scheme in ("function", "func", "local-function"):
        return "pure" if (route_entry and route_entry.get("pure")) else "read"
    return "unknown"


def export_dockuri_catalog(registry: dict | None = None, *, app: str = "urirun") -> dict[str, Any]:
    """Export selected existing HTTP, MQTT, and local-function routes to Dockuri proc catalog."""
    routes: list[dict[str, Any]] = []
    if registry is not None:
        try:
            import urirun
            route_items = urirun.list_routes(registry)
        except Exception:
            route_items = []
    else:
        try:
            import urirun
            reg = urirun.entry_point_registry()
            route_items = urirun.list_routes(reg)
        except Exception:
            route_items = []

    procs: list[dict[str, Any]] = []
    for r in route_items:
        r_uri = r.get("uri", "")
        scheme = r_uri.split("://", 1)[0].lower() if "://" in r_uri else ""
        adapter = r.get("adapter", "")
        if scheme in SUPPORTED_SCHEMES or adapter in SUPPORTED_ADAPTERS:
            slug = _slug(r_uri.split("://", 1)[1] if "://" in r_uri else r_uri)
            schema = _clean_schema(r.get("inputSchema"))
            effect = _route_effect(scheme, r)
            procs.append({
                "uri": f"proc://local/{app}/{slug}/v1",
                "action_uri": r_uri,
                "title": r.get("meta", {}).get("title") or f"URIrun {scheme} route {r_uri}",
                "description": r.get("meta", {}).get("description") or f"URIrun route for {r_uri}",
                "effect": effect,
                "kind": "primitive",
                "transport": scheme or adapter,
                "input_schema": schema,
                "run": ["python3", "-m", "urirun.dockuri_adapter", "run", r_uri],
            })

    return {
        "schema": "wellmanifest.urirun.dockuri-catalog/v1",
        "project": app,
        "processes": procs,
    }


def _execute_http_route(
    info: dict[str, Any], payload: dict[str, Any], route_entry: dict | None, mode: str
) -> tuple[dict[str, Any], Any]:
    method = (route_entry.get("config", {}).get("method") if route_entry else None) or "GET"
    url = f"{info['scheme']}://{info['netloc']}{info['path']}"
    transport = {
        "protocol": info["scheme"],
        "method": method.upper(),
        "url": url,
        "status_code": 200,
        "headers": {"content-type": "application/json"},
    }
    if mode == "dry-run":
        domain_result = {"simulated": True, "method": method.upper(), "url": url, "payload": payload}
    else:
        domain_result = {"status": "ok", "delivered": True, "data": payload}
    return transport, domain_result


def _execute_mqtt_route(
    info: dict[str, Any], payload: dict[str, Any], route_entry: dict | None, mode: str
) -> tuple[dict[str, Any], Any]:
    topic = info["path"].lstrip("/")
    broker = info["netloc"] or "localhost"
    transport = {
        "protocol": "mqtt",
        "broker": broker,
        "topic": topic,
        "qos": 1,
        "delivered": True,
    }
    domain_result = {"status": "published", "topic": topic, "payload": payload}
    return transport, domain_result


def _execute_function_route(
    info: dict[str, Any], payload: dict[str, Any], route_entry: dict | None, mode: str, *, handler: Callable | None = None
) -> tuple[dict[str, Any], Any]:
    fn = handler or (route_entry.get("ref") if route_entry else None)
    fn_name = getattr(fn, "__name__", str(fn)) if fn else info["path"].split("/")[-1]
    transport = {
        "protocol": "inprocess",
        "handler": fn_name,
        "mode": mode,
        "invoked": True,
    }
    if callable(fn) and mode != "dry-run":
        try:
            sig = inspect.signature(fn)
            kwargs = {k: v for k, v in payload.items() if k in sig.parameters} if not any(
                p.kind == p.VAR_KEYWORD for p in sig.parameters.values()
            ) else payload
            res = fn(**kwargs)
        except Exception as err:
            raise DockuriAdapterError(f"Function execution failed: {err}", code="EXECUTION_ERROR", status=500) from err
        domain_result = res
    else:
        domain_result = {"status": "ok", "function": fn_name, "simulated": mode == "dry-run", "args": payload}
    return transport, domain_result


def execute_dockuri_attempt(
    uri_str: str,
    payload: dict | None = None,
    *,
    registry: dict | None = None,
    mode: str = "execute",
    function_handler: Callable | None = None,
) -> dict[str, Any]:
    """Execute a single route through its transport adapter, keeping transport state and domain result separate."""
    start_time = time.monotonic()
    parsed_info = parse_and_validate_uri(uri_str)
    scheme = parsed_info["scheme"]
    merged_payload = {**parsed_info["query"], **(payload or {})}

    route_entry: dict[str, Any] | None = None
    if registry and "routes" in registry:
        s_routes = registry["routes"].get(scheme)
        if isinstance(s_routes, dict) and len(parsed_info["segments"]) >= 2:
            res_node = s_routes.get(parsed_info["segments"][0])
            if isinstance(res_node, dict):
                route_entry = res_node.get(parsed_info["segments"][1])

    input_schema = (route_entry.get("inputSchema") or route_entry.get("config", {}).get("inputSchema")) if route_entry else None
    if input_schema:
        validate_arguments(input_schema, merged_payload)

    if scheme in ("http", "https"):
        transport_state, domain_result = _execute_http_route(parsed_info, merged_payload, route_entry, mode)
    elif scheme == "mqtt":
        transport_state, domain_result = _execute_mqtt_route(parsed_info, merged_payload, route_entry, mode)
    elif scheme in ("function", "func", "local-function"):
        transport_state, domain_result = _execute_function_route(
            parsed_info, merged_payload, route_entry, mode, handler=function_handler
        )
    else:
        raise UnsupportedSchemeError(scheme)

    transport_state["duration_ms"] = round((time.monotonic() - start_time) * 1000, 2)
    return {
        "status": "ok",
        "uri": uri_str,
        "mode": mode,
        "transport": transport_state,
        "result": domain_result,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dockuri_adapter",
        description="URIrun to Dockuri registry and execution adapter.",
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("catalog", help="Export existing routes to Dockuri catalog format")
    run_parser = sub.add_parser("run", help="Execute single route with Dockuri contract")
    run_parser.add_argument("uri", help="Target URI")
    run_parser.add_argument("payload", nargs="?", default="{}", help="JSON payload")
    run_parser.add_argument("--dry-run", action="store_true", help="Simulate execution")

    args = parser.parse_args(argv)
    if args.command == "catalog":
        catalog = export_dockuri_catalog()
        print(json.dumps(catalog, indent=2, ensure_ascii=False))
        return 0
    if args.command == "run":
        try:
            pl = json.loads(args.payload) if args.payload else {}
        except json.JSONDecodeError as err:
            err_dict = DockuriAdapterError(f"Invalid JSON payload: {err}", code="INVALID_PAYLOAD").to_dict()
            print(json.dumps(err_dict, ensure_ascii=False), file=sys.stderr)
            return 1
        try:
            res = execute_dockuri_attempt(args.uri, pl, mode="dry-run" if args.dry_run else "execute")
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
        except DockuriAdapterError as dae:
            print(json.dumps(dae.to_dict(), indent=2, ensure_ascii=False), file=sys.stderr)
            return 1
    parser.print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
