"""HTTP execution layer for configured node APIs.

Functions here make outbound HTTP calls to nodes registered in the host config.
They are separated from ``object_registry`` so the data-model layer (node objects,
route building, capability derivation) stays import-free of HTTP machinery.

``configured_api_call`` is the main entry point; every other function is a building
block or utility used by it.
"""
from __future__ import annotations

import base64
import json
from urllib.parse import parse_qsl, urlencode, urlunsplit, urlsplit
from typing import Any
import urllib.error
import urllib.request


# ─── scheme → connector package registry ─────────────────────────────────────

_SCHEME_CONNECTOR_PACKAGES: dict[str, str] = {
    "media": "urirun-connector-media",
    "camera": "urirun-connector-camera",
    "ssh": "urirun-connector-ssh",
    "rtsp": "urirun-connector-rtsp",
    "smb": "urirun-connector-smb",
    "nfs": "urirun-connector-nfs",
    "serial": "urirun-connector-serial",
    "modbus": "urirun-connector-modbus",
    "mqtt": "urirun-connector-mqtt",
    "websocket": "urirun-connector-websocket",
    "ws": "urirun-connector-websocket",
}


# ─── auth helpers ─────────────────────────────────────────────────────────────

def configured_api_secret(auth: dict, *, allow: list[str] | None = None) -> str:
    secret_ref = str(auth.get("secretRef") or auth.get("credentialRef") or "")
    if not secret_ref:
        return ""
    import urirun  # noqa: PLC0415
    return urirun.resolve_secret(secret_ref, secret_allow=allow or [secret_ref])


def apply_auth_header(headers: dict, auth: dict, auth_type: str, secret: str) -> str | None:
    if auth_type in {"bearer", "oauth", "token"}:
        headers.setdefault("Authorization", f"Bearer {secret}")
    elif auth_type in {"api-key", "apikey", "key", "header", "custom-header"}:
        default_hdr = "X-API-Key" if auth_type in {"api-key", "apikey", "key"} else "Authorization"
        headers.setdefault(str(auth.get("headerName") or auth.get("header") or default_hdr), secret)
    elif auth_type == "basic":
        user = str(auth.get("username") or "")
        encoded = base64.b64encode(f"{user}:{secret}".encode("utf-8")).decode("ascii")
        headers.setdefault("Authorization", f"Basic {encoded}")
    else:
        return f"unsupported auth type {auth_type!r}"
    return None


def _merge_api_headers(api: dict, payload: dict) -> dict:
    """Merge static headers from api config and per-call payload headers."""
    headers: dict = (
        {str(k): str(v) for k, v in (api.get("headers") or {}).items()}
        if isinstance(api.get("headers"), dict) else {}
    )
    if isinstance(payload.get("headers"), dict):
        headers.update({str(k): str(v) for k, v in (payload.get("headers") or {}).items()})
    return headers


def _apply_configured_auth(headers: dict, api: dict) -> str | None:
    """Resolve auth secret and attach the appropriate Authorization header in-place.

    Returns an error string on failure, or ``None`` on success / no-auth-needed.
    """
    auth = api.get("auth") if isinstance(api.get("auth"), dict) else {}
    auth_type = str(auth.get("type") or "").strip().lower()
    if not auth_type or auth_type in {"none", "no", "false"}:
        return None
    try:
        secret = configured_api_secret(auth)
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    if not secret:
        return None
    return apply_auth_header(headers, auth, auth_type, secret)


def configured_api_headers(api: dict, payload: dict) -> tuple[dict, str | None]:
    headers = _merge_api_headers(api, payload)
    error = _apply_configured_auth(headers, api)
    return headers, error


# ─── URL / request building ───────────────────────────────────────────────────

def join_api_url(base: str, extra_path: str = "", query: dict | None = None) -> str:
    if extra_path.startswith(("http://", "https://")):
        url = extra_path
    else:
        left = str(base or "").rstrip("/")
        right = str(extra_path or "").lstrip("/")
        url = f"{left}/{right}" if right else left
    if query:
        parts = urlsplit(url)
        current = dict(parse_qsl(parts.query, keep_blank_values=True))
        current.update({str(k): str(v) for k, v in query.items() if v is not None})
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(current), parts.fragment))
    return url


def configured_api_response_body(raw: bytes, content_type: str) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if "json" in content_type.lower():
        try:
            return json.loads(text or "{}")
        except json.JSONDecodeError:
            return text
    return text


def build_request_body(payload: dict, headers: dict) -> bytes | None:
    body_value = payload.get("json") if "json" in payload else payload.get("body")
    if body_value is None:
        return None
    if isinstance(body_value, (dict, list)):
        headers.setdefault("Content-Type", "application/json")
        return json.dumps(body_value).encode("utf-8")
    return str(body_value).encode("utf-8")


def resolve_http_method_and_url(node: dict, api: dict, payload: dict) -> tuple[str | None, str, str | None]:
    method = str(payload.get("method") or ("POST" if "body" in payload or "json" in payload else "GET")).upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        return None, "", f"unsupported HTTP method {method!r}"
    query = payload.get("query") if isinstance(payload.get("query"), dict) else None
    url = join_api_url(str(api.get("url") or node.get("url") or ""), str(payload.get("path") or payload.get("url") or ""), query)
    return method, url, None


# ─── HTTP execution ───────────────────────────────────────────────────────────

def _with_remediation(env: dict, *, uri: str = "") -> dict:
    """Attach a structured ``RemediationClass`` to a failed configured-API envelope.

    Configured-API failures previously carried only a bare ``error``/``status``; routing them
    through the shared ``node_dispatch.classify_error`` taxonomy gives dashboard/chat the SAME
    next-steps (install command, auth enroll, offline hint) they get from host→node dispatch.
    Success envelopes pass through untouched. Lazy import keeps node_api importable standalone.
    """
    if env.get("ok") or env.get("remediation"):
        return env
    from urirun.host.node_dispatch import classify_error  # noqa: PLC0415 - avoid import cycle

    raw = env.get("error")
    err: dict = dict(raw) if isinstance(raw, dict) else ({"message": str(raw)} if raw else {})
    if env.get("status") is not None:
        err.setdefault("message", f"HTTP {env['status']}")
        err.setdefault("code", str(env["status"]))
    hint = env.get("connectorHint")
    if isinstance(hint, dict):  # connector_hint() returns {scheme, package, installCommand, ...}
        err.setdefault("installCommand", hint.get("installCommand"))
        err.setdefault("connectorHint", hint.get("scheme") or hint.get("package"))
    elif hint:
        err.setdefault("connectorHint", str(hint))
    env["remediation"] = classify_error(err, node=str(env.get("node") or ""), uri=uri).to_dict()
    return env


def execute_http_request(node: dict, api: dict, method: str, url: str,
                         raw_body: bytes | None, headers: dict, timeout: float) -> dict:
    request = urllib.request.Request(url, data=raw_body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
            return {
                "ok": 200 <= int(response.status) < 400,
                "node": node.get("name"),
                "apiId": api.get("id"),
                "method": method,
                "url": url,
                "status": int(response.status),
                "contentType": content_type,
                "data": configured_api_response_body(raw, content_type),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        content_type = exc.headers.get("Content-Type", "") if exc.headers else ""
        return _with_remediation({
            "ok": False,
            "node": node.get("name"),
            "apiId": api.get("id"),
            "method": method,
            "url": url,
            "status": int(exc.code),
            "contentType": content_type,
            "data": configured_api_response_body(raw, content_type),
        })
    except urllib.error.URLError as exc:
        # Connection refused / DNS / timeout — previously propagated uncaught and crashed the
        # caller (uri_invoke). Return a classified envelope instead so the API is treated like
        # any unreachable host→node target.
        return _with_remediation({
            "ok": False,
            "node": node.get("name"),
            "apiId": api.get("id"),
            "method": method,
            "url": url,
            "error": {"type": "URLError", "message": str(getattr(exc, "reason", exc))},
        })
    except Exception as exc:  # noqa: BLE001 - never let a configured-API call throw past here
        return _with_remediation({
            "ok": False,
            "node": node.get("name"),
            "apiId": api.get("id"),
            "method": method,
            "url": url,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        })


# ─── connector hint / connector_required ─────────────────────────────────────

def connector_hint(scheme: str) -> dict:
    known = scheme in _SCHEME_CONNECTOR_PACKAGES
    package = _SCHEME_CONNECTOR_PACKAGES.get(scheme) or f"urirun-connector-{scheme}"
    hint: dict = {
        "scheme": scheme,
        "package": package,
        "installCommand": f"pip install {package}",
        "deployCommand": "urirun host deploy --merge <node_url>",
        "reason": (f"The {scheme}:// scheme requires a dedicated connector that implements its protocol. "
                   "Install the connector package, deploy it to the node, then the route goes live."),
    }
    if not known:
        hint["speculative"] = True
    return hint


def connector_required_response(scheme: str, node_name: str, safe_api: dict) -> dict:
    return _with_remediation({
        "ok": False,
        "error": "connector_required",
        "message": f"{scheme}:// execution needs a dedicated connector; configured API metadata is available",
        "scheme": scheme,
        "node": node_name,
        "api": safe_api,
        "connectorHint": connector_hint(scheme),
    }, uri=f"{scheme}://{node_name}")


# ─── main entry point ─────────────────────────────────────────────────────────

def configured_api_call(node: dict, api: dict, payload: dict) -> dict:
    api_kind = str(api.get("kind") or "").strip().lower()
    if api_kind not in {"http", "https", "rest", "openapi", "web", "panel"}:
        return _with_remediation({
            "ok": False,
            "node": node.get("name"),
            "error": "connector_required",
            "message": f"{api_kind or 'unknown'} interfaces require a dedicated connector/service",
            "api": {k: v for k, v in api.items() if k != "auth"},
            "connectorHint": connector_hint(api_kind),
        })
    method, url, method_error = resolve_http_method_and_url(node, api, payload)
    if method_error:
        return _with_remediation({"ok": False, "node": node.get("name"), "error": method_error})
    headers, auth_error = configured_api_headers(api, payload)
    if auth_error:
        return _with_remediation({"ok": False, "node": node.get("name"), "error": auth_error})
    raw_body = build_request_body(payload, headers)
    timeout = float(payload.get("timeout") or 20)
    return execute_http_request(node, api, method, url, raw_body, headers, timeout)
