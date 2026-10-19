# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, unquote


def _resolve_version() -> str:
    """Version of the installed ``urirun`` distribution, with a source fallback."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("urirun")
    except Exception:  # pragma: no cover - metadata missing in source checkouts
        return "0.4.199"


__version__ = _resolve_version()

URI_RE = re.compile(r'^(?P<scheme>[a-z][a-z0-9+.-]*)://(?P<target>[^/?#]+)(?P<path>/[^?#]*)?(?:\?(?P<query>[^#]*))?(?:#(?P<fragment>.*))?$', re.I)


def _patch_router_effect_of() -> None:
    """Tolerate older router wheels missing names used by urirun-flow."""
    try:
        from urirun_connector_router import routing as router_routing
    except Exception:
        return
    if hasattr(router_routing, "effect_of"):
        pass
    else:
        alias = getattr(router_routing, "effect_of_route", None)
        if callable(alias):
            router_routing.effect_of = alias
        else:
            def _effect_of(uri: str, *_args, **_kwargs) -> str:
                text = str(uri or "")
                if "/query/" in text:
                    return "read"
                if "/command/" in text:
                    return "write"
                return "unknown"

            router_routing.effect_of = _effect_of
    if not hasattr(router_routing, "execution_layers"):
        def _execution_layers(_route: dict | None = None, *_args, **_kwargs) -> list[str]:
            return ["host", "node"]

        router_routing.execution_layers = _execution_layers


# Patch is applied lazily (see test_router_compat and places that need router compat)
# to keep bare `import urirun` slim (no connector imports at facade load time).
# _patch_router_effect_of()


def parse_uri(uri: str):
    m = URI_RE.match(str(uri))
    if not m:
        raise ValueError(f"Invalid URI: {uri}")
    path = m.group('path') or '/'
    segments = [unquote(s) for s in path.split('/') if s]
    return {
        'package': m.group('scheme'),
        'target': unquote(m.group('target')),
        'segments': segments,
        'query': dict(parse_qsl(m.group('query') or '')),
        'fragment': m.group('fragment') or None,
        'raw': uri,
    }

def build_invocation(descriptor: dict):
    function_name = '_'.join(descriptor['segments'][:2])
    args = [descriptor['target'], *descriptor['segments'][2:]]
    descriptor = dict(descriptor)
    descriptor['functionName'] = function_name
    descriptor['args'] = args
    return descriptor

def dispatch(uri: str, registry: dict, payload=None):
    descriptor = parse_uri(uri)
    invocation = build_invocation(descriptor)
    mod = registry.get(invocation['package'])
    if mod is None:
        raise KeyError(f"Unknown package: {invocation['package']}")
    fn = getattr(mod, invocation['functionName'], None) if not isinstance(mod, dict) else mod.get(invocation['functionName'])
    if not callable(fn):
        raise KeyError(f"Unknown function: {invocation['package']}.{invocation['functionName']}")
    return fn(*invocation['args'], payload, invocation)


def command(uri: str, **options):
    """Declare a v2 URI command with the public top-level decorator API.

    This is the preferred spelling for connector authors:

        import urirun

        @urirun.command("demo://host/http/query/status")
        def status(url: str):
            return ["demo", "{url}"]

    ``urirun.v2.uri_command`` remains supported for existing code.
    """
    from urirun.v2 import uri_command

    return uri_command(uri, **options)


def shell(uri: str, **options):
    """Declare a v2 shell-template URI route with the top-level API."""
    from urirun.v2 import uri_shell

    return uri_shell(uri, **options)


def handler(uri: str, **options):
    """Register a typed function as the in-process handler for a URI route.

    The function signature becomes the input schema and the runtime calls it in
    the same process (``local-function`` adapter) instead of spawning argv::

        @urirun.handler("llm://host/chat/command/complete")
        def complete(prompt: str, model: str = "llama3") -> dict:
            return urirun.ok(response=...)

    Pass ``isolated=True`` to run the handler in a fresh process via the shared
    ``python -m urirun.exec`` runner (crash containment / untrusted code / a heavy
    import kept off the host) — still one declaration, no per-connector shim.
    """
    from urirun.v2 import uri_handler

    return uri_handler(uri, **options)


_EXAMPLE_BY_TYPE = {"string": "example", "integer": 1, "number": 1.0, "boolean": True, "array": [], "object": {}}


def _example_payload(schema: dict | None) -> dict:
    """Derive a sample payload from a JSON Schema: defaults, then required fields."""
    schema = schema or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    payload = {}
    for name, spec in props.items():
        if isinstance(spec, dict) and "default" in spec:
            payload[name] = spec["default"]
        elif name in required:
            spec_type = spec.get("type") if isinstance(spec, dict) else None
            payload[name] = _EXAMPLE_BY_TYPE.get(spec_type, "example")
    return payload


def ok(**fields) -> dict:
    """Build a success result envelope: ``{"ok": True, **fields}``."""
    return {"ok": True, **fields}


def fail(error, **fields) -> dict:
    """Build a failure result envelope: ``{"ok": False, "error": str(error), **fields}``."""
    return {"ok": False, "error": str(error), **fields}


def plan(**fields) -> dict:
    """Build a dry-run plan envelope: ``{"ok": True, "dryRun": True, **fields}``."""
    return {"ok": True, "dryRun": True, **fields}


def tag(result: dict, kind: str, *, live: bool = False) -> dict:
    """Stamp a connector result with the artifact/widget contract and return it.

    ``kind`` names the output (e.g. ``photo``/``scan``/``text``/``crop``/``document``/
    ``stream``) and ``live`` declares its nature:

    * ``live=False`` -- a frozen, immutable **artifact** (a captured frame, a rendered
      PDF, a finished crop). The host catalogs these in the artifact store.
    * ``live=True`` -- a self-updating **widget** / live view (an open camera stream).

    A UI renders by ``live``, not by media type: a captured frame and a recorded clip
    are both artifacts; only the open stream is a widget. This is the single shared
    contract so every connector declares its output the same way instead of inventing
    ad-hoc fields. No-ops on a non-dict (e.g. an already-built failure envelope)."""
    if isinstance(result, dict):
        result["kind"] = kind
        result["live"] = bool(live)
    return result


def policy(allow=None, deny=None, secret_allow=None, policy_file=None) -> dict | None:
    """Build an execution policy from allow/deny/secret-allow globs (and an optional
    policy file) — the public builder, so callers no longer reach into
    ``urirun.runtime._runtime.build_policy``.

    Returns ``None`` when nothing is specified (the runtime's default). Example::

        urirun.run(uri, registry, payload, mode="execute",
                   policy=urirun.policy(allow=["time://*"]))
    """
    from urirun.runtime._runtime import build_policy

    return build_policy(policy_file, allow, deny, secret_allow)


def resolve_secret(value, secret_allow="") -> str:
    """Resolve a credential argument that may be a secret *reference* (the shared connector
    helper). ``value`` may be a literal, a ``getv://NAME`` / ``secret://...`` reference, or a
    ``{getv:..}`` / ``{secret:..}`` placeholder; references resolve under ``secret_allow``
    (glob list or comma/space-separated string), deny-by-default. Empty ``value`` -> ''.

    Local-function connectors receive credentials as params (the runtime only auto-injects
    secrets into ``fetch`` adapters), so each calls this at the credential boundary instead of
    reading the value from ``os.environ`` itself. See ``urirun.runtime.secrets.resolve_secret``.
    """
    from urirun.runtime import secrets as _secrets

    return _secrets.resolve_secret(value, secret_allow)


def action_space(registry: dict) -> list[dict]:
    """The routes an agent/LLM can choose from — ``{uri, kind, label, inputs,
    required}`` per route, including the input schema (unlike ``list_routes``,
    which omits it). Lets callers use the public API instead of reaching into the
    runtime to re-derive an agent's action space."""
    from urirun.runtime import _registry as reglib

    space = []
    for route in reglib.flatten_registry_document(registry):
        entry = route.get("routeEntry") or {}
        # compiled registries carry the schema under config.inputSchema; bindings
        # docs carry it at the top level — accept either.
        schema = entry.get("inputSchema") or (entry.get("config") or {}).get("inputSchema") or {}
        uri = route["uri"]
        space.append({
            "uri": uri,
            "kind": "query" if "/query/" in uri else "command",
            "label": (entry.get("meta") or {}).get("label", ""),
            "inputs": list((schema.get("properties") or {}).keys()),
            "required": schema.get("required", []),
        })
    space.sort(key=lambda item: item["uri"])
    return space


def result_data(env: dict):
    """Extract a connector's result payload from a run envelope, regardless of the
    adapter that produced it: ``local-function`` returns under ``result.value``;
    ``argv``/shell print JSON to ``result.stdout``; ``fetch``/dry-run carry the
    data on ``result`` directly. Saves consumers from unwrapping each shape by hand.
    """
    if not isinstance(env, dict):
        return env
    result = env.get("result")
    if not isinstance(result, dict):
        return result if result is not None else env
    if "value" in result:  # local-function executor
        return result["value"]
    stdout = result.get("stdout")  # argv / shell executor
    if isinstance(stdout, str) and stdout.strip():
        try:
            return json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            return {"stdout": stdout}
    return result  # fetch / dry-run / other


_DEGRADED_MODES = {"mock", "simulated", "degraded", "stub", "placeholder", "fake", "sample"}


def result_degraded(env: dict):
    """Return a short reason string if a connector result signals it ran in a
    degraded / simulated mode — a mock driver, a stub, a placeholder — instead of
    doing the real thing; else ``None``.

    The convention a connector sets so tools (and ``host probe``) don't mistake a
    plausible placeholder for real work: any of ``degraded: true``,
    ``simulated: true``, or a ``mode``/``driver`` field whose value is a known
    placeholder (``mock``/``stub``/``placeholder``/...). E.g. a browser connector
    that couldn't reach a real engine returning ``{"ok": true, "driver": "mock"}``
    is reported degraded even though ``ok`` is true."""
    data = result_data(env) if isinstance(env, dict) and "result" in env else env
    if not isinstance(data, dict):
        return None
    if data.get("degraded"):
        return "degraded"
    if data.get("simulated"):
        return "simulated"
    for field in ("mode", "driver"):
        value = data.get(field)
        if isinstance(value, str) and value.lower() in _DEGRADED_MODES:
            return f"{field}={value}"
    return None


def _step_uri_and_payload(step):
    """Extract (uri, payload) from a step that is either a dict or a sequence."""
    if isinstance(step, dict):
        return step["uri"], (step.get("payload") or {})
    return step[0], ((step[1] if len(step) > 1 else None) or {})


def _step_allow_policy(allow, scheme: str) -> list:
    """Return the allow list: caller-supplied list or a default scheme wildcard."""
    return list(allow) if allow else [f"{scheme}://*"]


def _step_ok(env: dict, data) -> bool:
    """Compute whether a step succeeded from its envelope and unwrapped data."""
    return bool(env.get("ok")) and (data.get("ok", True) if isinstance(data, dict) else True)


def _step_record(uri: str, ok: bool, data, step) -> dict:
    """Build the per-step output record."""
    rec = {"uri": uri, "ok": ok, "data": data}
    if isinstance(step, dict) and "id" in step:
        rec["id"] = step["id"]
    return rec


def run_steps(steps, registry: dict, *, execute: bool = True, allow=None, stop_on_error: bool = True):
    """Run a list of ``{uri, payload}`` steps against a registry and return one
    ``{uri, ok, data}`` (plus ``id`` when given) per step — the loop every agent /
    flow example was re-writing by hand. ``data`` is already unwrapped via
    :func:`result_data`; ``ok`` combines the envelope and the connector's own ``ok``.

    Policy defaults to ``<scheme>://*`` per step (so ``query`` reads and the
    matching ``command`` routes run); pass ``allow=[...]`` to use one policy for
    all steps. With ``stop_on_error`` (default) execution halts at the first failed
    step. ``execute=False`` dry-runs (validate + plan, no side effects)::

        for r in urirun.run_steps(flow["steps"], registry, execute=True):
            print(r["uri"], r["ok"], r["data"])
    """
    mode = "execute" if execute else "dry-run"
    out = []
    for step in steps:
        uri, payload = _step_uri_and_payload(step)
        scheme = uri.split("://", 1)[0]
        env = run(uri, registry, payload, mode=mode,
                  policy=policy(allow=_step_allow_policy(allow, scheme)))
        data = result_data(env)
        ok = _step_ok(env, data)
        out.append(_step_record(uri, ok, data, step))
        if stop_on_error and not ok:
            break
    return out


def tool_binding(uri: str, argv, properties: dict | None = None, *, label: str = "",
                 required=None, kind: str | None = None) -> dict:
    """Build a single ``argv-template`` binding (``{uri: entry}``) for a CLI tool —
    the ``_route(...)`` helper every example was redefining. ``argv`` is the command
    (use ``"{name}"`` placeholders), ``properties`` the JSON-Schema properties for
    the inputs. ``kind`` defaults to ``query``/``command`` from the URI. Merge several
    into a bindings document and ``compile_registry`` it::

        b = {}
        b.update(urirun.tool_binding("time://host/clock/query/now", [py, tool, "now"], {}))
        b.update(urirun.tool_binding("log://host/run/command/write",
                 [py, tool, "log", "--text", "{text}"], {"text": {"type": "string"}}, required=["text"]))
        registry = urirun.compile_registry({"version": "urirun.bindings.v2", "bindings": b})
    """
    schema = {"type": "object", "additionalProperties": False, "properties": properties or {}}
    if required:
        schema["required"] = list(required)
    if kind is None:
        kind = "query" if "/query/" in uri else "command"
    return {uri: {"adapter": "argv-template", "kind": kind, "argv": list(argv),
                  "inputSchema": schema, "meta": {"label": label}, "uri": uri}}


def connector_bindings(*, routes=None, connector=None, additional_properties: bool | None = False):
    """Export v2 bindings generated from ``@urirun.command`` decorators."""
    from urirun.v2 import connector_bindings as _connector_bindings

    return _connector_bindings(
        routes=routes,
        connector=connector,
        additional_properties=additional_properties,
    )


def entry_point_bindings(group: str = "urirun.bindings"):
    """Load bindings exposed by installed connector entry points."""
    from urirun.v2 import entry_point_bindings as _entry_point_bindings

    return _entry_point_bindings(group=group)


def entry_point_binding_document(group: str = "urirun.bindings", generated_at: str | None = None):
    """Build a v2 binding document from installed connector entry points."""
    from urirun.v2 import entry_point_binding_document as _entry_point_binding_document

    return _entry_point_binding_document(group=group, generated_at=generated_at)


def entry_point_registry(
    group: str = "urirun.bindings",
    generated_at: str | None = None,
    on_conflict: str = "keep",
):
    """Compile installed connector entry points into a registry document."""
    from urirun.v2 import entry_point_registry as _entry_point_registry

    return _entry_point_registry(
        group=group,
        generated_at=generated_at,
        on_conflict=on_conflict,
    )


def error_bindings(target: str = "local") -> dict:
    """Return built-in ``error://`` bindings for registry/flow use."""
    from urirun import errors

    return errors.bindings(target=target)


def compat_report() -> dict:
    """Return migration status for legacy compatibility modules."""
    from urirun import compat

    return compat.report()


def compile_registry(doc, generated_at: str | None = None, on_conflict: str = "keep"):
    """Compile a v2 binding document through the stable top-level API."""
    from urirun.v2 import compile_registry as _compile_registry

    return _compile_registry(doc, generated_at=generated_at, on_conflict=on_conflict)


def list_routes(registry: dict, policy: dict | None = None) -> list[dict]:
    """List routes from a compiled registry through the stable top-level API."""
    from urirun.v2 import list_routes as _list_routes

    return _list_routes(registry, policy=policy)


def validate_binding_document(doc) -> dict:
    """Validate a v2 binding document through the stable top-level API."""
    from urirun.v2 import validate_binding_document as _validate_binding_document

    return _validate_binding_document(doc)


def make_dispatch(registry: dict, mode: str = "execute", fallback=None):
    """Return a two-tier ``dispatch(uri, payload) → dict`` callable.

    Tier 1 — mesh (v2_service.call): covers served nodes via *registry*.
    Tier 2 — *fallback(uri, payload)*: called only when Tier 1 returns NOT_FOUND.
    Pass ``None`` to skip Tier 2 (default: in-process connector discovery).

    This is the canonical way to build a dispatch callable for flow execution,
    twin connector handlers, and dashboard wiring — a single seam so routing
    strategy can be swapped in tests without touching callers::

        dispatch = urirun.make_dispatch(mesh_registry, mode="execute")
        result = dispatch("kvm://laptop/ui/command/click", {"text": "Post"})
    """
    from urirun.runtime.v2_service import make_dispatch as _make_dispatch

    return _make_dispatch(registry, mode, fallback=fallback)


def normalize_dispatch_request(raw: dict, *, default_mode: str = "dry-run") -> dict:
    """Coerce an incoming request body to canonical ``{uri, payload, mode}``.

    Tolerates ``execute: bool`` alongside ``mode: str`` — the shapes different
    transports have historically sent.  Use at transport boundaries (HTTP handler,
    MCP tools/call, gRPC) before calling ``urirun.run`` or ``urirun.make_dispatch``."""
    from urirun.runtime.dispatch_protocol import normalize_request as _norm
    return _norm(raw, default_mode=default_mode)


def run(
    uri: str,
    registry: dict,
    payload=None,
    mode: str = "dry-run",
    policy: dict | None = None,
    confirm: bool = False,
    executors: dict | None = None,
) -> dict:
    """Run a URI route through the stable top-level API."""
    from urirun.v2 import run as _run

    return _run(
        uri,
        registry,
        payload=payload,
        mode=mode,
        policy=policy,
        confirm=confirm,
        executors=executors,
    )


def _parse_uri_invocations(
    address: str = "",
    payload: Any = None,
    *,
    jsonl: str | None = None,
) -> tuple[list[tuple[str, dict]], bool]:
    """Normalize (address, payload, jsonl) into a list of (uri, payload_dict) steps and batch flag."""
    raw_addr = (address or "").strip()
    inline_text = jsonl
    if inline_text is None and isinstance(payload, str):
        inline_text = payload
        payload = None
    if inline_text is None and payload is None and raw_addr:
        parts = re.split(r"[\s\r\n]+", raw_addr, maxsplit=1)
        if len(parts) == 2 and URI_RE.match(parts[0]) and parts[1].lstrip().startswith(("{", "[")):
            raw_addr, inline_text = parts[0], parts[1]

    if inline_text is not None:
        lines = [ln.strip() for ln in str(inline_text).splitlines() if ln.strip()]
        if not lines:
            return ([(raw_addr, {})], False) if raw_addr else ([], True)
        steps: list[tuple[str, dict]] = []
        is_batch = len(lines) > 1 or jsonl is not None or "\n" in str(inline_text).strip()
        for ln in lines:
            item = json.loads(ln)
            if isinstance(item, list):
                is_batch = True
                for sub in item:
                    s_uri, s_pay = _extract_step_item(raw_addr, sub)
                    steps.append((s_uri, s_pay))
            else:
                s_uri, s_pay = _extract_step_item(raw_addr, item)
                steps.append((s_uri, s_pay))
        return steps, is_batch

    if isinstance(payload, list):
        return [_extract_step_item(raw_addr, item) for item in payload], True

    s_uri, s_pay = _extract_step_item(raw_addr, payload if payload is not None else {})
    return [(s_uri, s_pay)], False


def _extract_step_item(default_uri: str, item: Any) -> tuple[str, dict]:
    if not isinstance(item, dict):
        if not default_uri:
            raise ValueError("URI address is required")
        return default_uri, {"value": item}
    step_uri = str(item.get("uri") or default_uri).strip()
    if not step_uri:
        raise ValueError("URI address is required either as argument or 'uri' field in JSONL item")
    if "payload" in item and isinstance(item["payload"], dict):
        return step_uri, dict(item["payload"])
    if "uri" in item and not default_uri:
        return step_uri, {k: v for k, v in item.items() if k != "uri"}
    return step_uri, dict(item)


_DEFAULT_EP_REGISTRY_FN = entry_point_registry
_CACHED_EP_REGISTRY: tuple[int, str, dict] | None = None
_CACHED_PY_HANDLERS: dict[tuple[str, str], Any] = {}


def _ep_cache_fingerprint() -> str:
    import sys
    import sysconfig
    from pathlib import Path

    sp = Path(sysconfig.get_path("purelib") or sys.prefix)
    try:
        st = sp.stat()
        return f"{sys.prefix}:{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return sys.prefix


def _load_or_build_default_ep_registry(fp: str) -> dict:
    import contextlib
    import io
    import os
    import pickle
    from pathlib import Path

    cache_file = Path(os.environ.get("URIRUN_EP_CACHE_FILE") or (Path.home() / ".cache" / "urirun" / "ep_registry_v2.pkl"))
    if os.environ.get("URIRUN_DISABLE_EP_CACHE") != "1" and cache_file.is_file():
        try:
            cached_fp, cached_reg = pickle.loads(cache_file.read_bytes())  # noqa: S301
            if cached_fp == fp and isinstance(cached_reg, dict):
                return cached_reg
        except Exception:
            pass
    if os.environ.get("URIRUN_VERBOSE_ENTRYPOINTS") == "1":
        reg = entry_point_registry()
    else:
        with contextlib.redirect_stderr(io.StringIO()):
            reg = entry_point_registry()
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(pickle.dumps((fp, reg), protocol=pickle.HIGHEST_PROTOCOL))
    except Exception:
        pass
    return reg


def _get_cached_entry_point_registry() -> dict:
    global _CACHED_EP_REGISTRY
    fn_id = id(entry_point_registry)
    fp = _ep_cache_fingerprint() if entry_point_registry is _DEFAULT_EP_REGISTRY_FN else "custom"
    if _CACHED_EP_REGISTRY is not None and _CACHED_EP_REGISTRY[0] == fn_id and _CACHED_EP_REGISTRY[1] == fp:
        return _CACHED_EP_REGISTRY[2]
    reg = _load_or_build_default_ep_registry(fp) if entry_point_registry is _DEFAULT_EP_REGISTRY_FN else entry_point_registry()
    _CACHED_EP_REGISTRY = (fn_id, fp, reg)
    return reg


def _normalize_status(out: Any) -> Any:
    if isinstance(out, dict) and "status" not in out:
        out["status"] = "ok" if (out.get("ok", True) is not False and "error" not in out) else "error"
    return out


def _try_inproc_route(step_uri: str, descriptor: dict, reg: dict, merged_payload: dict, mode: str, raw_envelope: bool) -> tuple[bool, Any]:
    import contextlib
    import importlib
    import inspect
    import os
    import sys

    if mode != "execute" or os.environ.get("URIRUN_FORCE_SUBPROCESS") == "1":
        return False, None
    routes = reg.get("routes") if isinstance(reg, dict) else None
    if not isinstance(routes, dict):
        return False, None
    scheme = descriptor["package"]
    scheme_routes = routes.get(scheme)
    if not isinstance(scheme_routes, dict):
        return False, None
    segs = descriptor["segments"]
    if len(segs) < 2:
        return False, None
    res_node = scheme_routes.get(segs[0])
    route = res_node.get(segs[1]) if isinstance(res_node, dict) else None
    if not isinstance(route, dict) or route.get("adapter") not in ("local-function", "local-function-subprocess"):
        return False, None
    py_spec = route.get("python")
    if not isinstance(py_spec, dict) or not py_spec.get("module") or not py_spec.get("export"):
        return False, None
    key = (str(py_spec["module"]), str(py_spec["export"]))
    fn = _CACHED_PY_HANDLERS.get(key)
    if fn is None:
        try:
            fn = getattr(importlib.import_module(key[0]), key[1])
            _CACHED_PY_HANDLERS[key] = fn
        except Exception:
            return False, None
    call_kwargs = dict(merged_payload)
    try:
        params = inspect.signature(fn).parameters
        if not any(p.kind == p.VAR_KEYWORD for p in params.values()):
            call_kwargs = {k: v for k, v in call_kwargs.items() if k in params}
    except (TypeError, ValueError):
        pass
    with contextlib.redirect_stdout(sys.stderr):
        out = fn(**call_kwargs)
    if raw_envelope:
        return True, {"uri": step_uri, "mode": mode, "kind": route.get("kind"), "adapter": route.get("adapter"), "ok": True, "status": "ok", "result": out}
    return True, _normalize_status(out)


def _invoke_single_uri(
    step_uri: str,
    step_payload: dict,
    *,
    registry: dict | None = None,
    node_url: str | None = None,
    mode: str = "execute",
    allow: list[str] | None = None,
    raw_envelope: bool = False,
):
    import os
    from pathlib import Path
    import sys
    import urllib.request

    descriptor = parse_uri(step_uri)
    merged_payload = {**descriptor["query"], **(step_payload or {})}
    scheme = descriptor["package"]

    if registry is None and scheme in ("proc", "willman"):
        try:
            cand = Path.home() / "github" / "dockuri" / "dockuri" / "python"
            if cand.is_dir() and str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
            import dockuri_uri
            return dockuri_uri.uri(step_uri, merged_payload)
        except Exception:
            pass

    effective_node = node_url or os.environ.get("URIRUN_NODE_URL")
    if effective_node and registry is None:
        req_body = json.dumps({"uri": step_uri, "payload": merged_payload, "mode": mode}).encode("utf-8")
        req = urllib.request.Request(
            f"{effective_node.rstrip('/')}/call",
            data=req_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            env = json.loads(resp.read().decode("utf-8"))
        return env if raw_envelope else _normalize_status(result_data(env))

    reg = registry if registry is not None else _get_cached_entry_point_registry()
    if isinstance(reg, dict) and "routes" not in reg and "bindings" not in reg and scheme in reg:
        return dispatch(step_uri, reg, merged_payload)
    if isinstance(reg, dict) and "bindings" in reg and "routes" not in reg:
        reg = compile_registry(reg)

    if isinstance(reg, dict) and isinstance(reg.get("routes"), dict):
        s_routes = reg["routes"].get(scheme)
        if isinstance(s_routes, dict):
            if len(descriptor["segments"]) == 1:
                step_uri = f"{scheme}://host/{descriptor['target']}/{descriptor['segments'][0]}"
                descriptor = parse_uri(step_uri)
            if descriptor["segments"][:2] == ["doctor", "query"] and not (isinstance(s_routes.get("doctor"), dict) and "query" in s_routes["doctor"]):
                doc_out = {"ok": True, "status": "ready", "connector": f"urirun-connector-{scheme}", "scheme": scheme, "configured": True, "resources": sorted(s_routes.keys())}
                return {"uri": step_uri, "mode": mode, "kind": "query", "adapter": "builtin-doctor", "ok": True, "status": "ok", "result": doc_out} if raw_envelope else doc_out

    handled, fast_out = _try_inproc_route(step_uri, descriptor, reg, merged_payload, mode, raw_envelope)
    if handled:
        return fast_out

    pol = policy(allow=_step_allow_policy(allow, scheme))
    env = run(step_uri, reg, payload=merged_payload, mode=mode, policy=pol)
    return env if raw_envelope else _normalize_status(result_data(env))


def uri(
    address: str = "",
    payload: Any = None,
    *,
    jsonl: str | None = None,
    registry: dict | None = None,
    node_url: str | None = None,
    mode: str = "execute",
    allow: list[str] | None = None,
    raw: bool = False,
):
    """Universal URI process invoker for single JSON or batch JSONL calls."""
    steps, is_batch = _parse_uri_invocations(address, payload, jsonl=jsonl)
    results = [
        _invoke_single_uri(
            s_uri,
            s_pay,
            registry=registry,
            node_url=node_url,
            mode=mode,
            allow=allow,
            raw_envelope=raw,
        )
        for s_uri, s_pay in steps
    ]
    return results if is_batch else (results[0] if results else {})


def uri_jsonl(address: str, lines: Any, **kwargs: Any) -> list[Any]:
    """Stream JSONL lines or dicts into a URI process."""
    res = uri(address, list(lines) if not isinstance(lines, str) else lines, **kwargs)
    return res if isinstance(res, list) else [res]



def uri_cli(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``uri <full-uri> [json|jsonl]``."""
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        prog="uri",
        description="Universal dockuri URI process runner (single JSON or batch JSONL).",
    )
    parser.add_argument("address", nargs="?", default="", help="Full URI address or 'URI JSON/JSONL'")
    parser.add_argument("payload", nargs="?", default=None, help="Inline JSON or JSONL payload, or '-' for stdin")
    parser.add_argument("--dry-run", action="store_true", help="Plan/validate without executing side effects")
    parser.add_argument("--raw", action="store_true", help="Return full runtime execution envelope")
    parser.add_argument("--jsonl", action="store_true", help="Emit JSONL (one JSON object per line)")
    parser.add_argument("--node-url", default=None, help="Remote urirun node URL (or URIRUN_NODE_URL)")
    args = parser.parse_args(argv)

    payload_text = args.payload
    if payload_text == "-" or (payload_text is None and not sys.stdin.isatty()):
        stdin_data = sys.stdin.read()
        if stdin_data.strip():
            payload_text = stdin_data
        elif payload_text == "-":
            payload_text = ""

    if not args.address and not payload_text:
        parser.print_help(sys.stderr)
        return 2

    mode = "dry-run" if args.dry_run else "execute"
    out = uri(
        args.address,
        payload_text,
        node_url=args.node_url,
        mode=mode,
        raw=args.raw,
    )
    if args.jsonl and isinstance(out, list):
        for item in out:
            print(json.dumps(item, ensure_ascii=False))
    else:
        print(json.dumps(out, ensure_ascii=False))
    return 0


from ._connector import (  # noqa: E402
    Connector,
    connector,
    load_manifest,
    connector_emit,
    connector_cli,
    connector_main,
)

