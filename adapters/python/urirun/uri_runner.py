"""Universal URI process invoker (`uri(...)`, `uri_jsonl(...)`, `uri_cli(...)`) and entry-point cache."""

from __future__ import annotations

import contextlib
import importlib
import inspect
import io
import json
import os
from pathlib import Path
import pickle
import re
import sys
import sysconfig
from typing import Any
import urllib.request


def _parse_uri_invocations(
    address: str = "",
    payload: Any = None,
    *,
    jsonl: str | None = None,
    uri_re: re.Pattern[str] | None = None,
) -> tuple[list[tuple[str, dict]], bool]:
    """Normalize (address, payload, jsonl) into a list of (uri, payload_dict) steps and batch flag."""
    import urirun

    pat = uri_re or urirun.URI_RE
    raw_addr = (address or "").strip()
    inline_text = jsonl
    if inline_text is None and isinstance(payload, str):
        inline_text, payload = payload, None
    if inline_text is None and payload is None and raw_addr:
        parts = re.split(r"[\s\r\n]+", raw_addr, maxsplit=1)
        if len(parts) == 2 and pat.match(parts[0]) and parts[1].lstrip().startswith(("{", "[")):
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
                steps.extend(_extract_step_item(raw_addr, sub) for sub in item)
            else:
                steps.append(_extract_step_item(raw_addr, item))
        return steps, is_batch

    if isinstance(payload, list):
        return [_extract_step_item(raw_addr, item) for item in payload], True

    return [_extract_step_item(raw_addr, payload if payload is not None else {})], False


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


_CACHED_PY_HANDLERS: dict[tuple[str, str], Any] = {}


def _ep_cache_fingerprint() -> str:
    sp = Path(sysconfig.get_path("purelib") or sys.prefix)
    try:
        st = sp.stat()
        return f"{sys.prefix}:{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return sys.prefix


def _load_or_build_default_ep_registry(fp: str) -> dict:
    import urirun

    cache_file = Path(os.environ.get("URIRUN_EP_CACHE_FILE") or (Path.home() / ".cache" / "urirun" / "ep_registry_v2.pkl"))
    if os.environ.get("URIRUN_DISABLE_EP_CACHE") != "1" and cache_file.is_file():
        try:
            cached_fp, cached_reg = pickle.loads(cache_file.read_bytes())  # noqa: S301
            if cached_fp == fp and isinstance(cached_reg, dict):
                return cached_reg
        except Exception:
            pass
    if os.environ.get("URIRUN_VERBOSE_ENTRYPOINTS") == "1":
        reg = urirun.entry_point_registry()
    else:
        with contextlib.redirect_stderr(io.StringIO()):
            reg = urirun.entry_point_registry()
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(pickle.dumps((fp, reg), protocol=pickle.HIGHEST_PROTOCOL))
    except Exception:
        pass
    return reg


def _get_cached_entry_point_registry() -> dict:
    import urirun

    ep_fn = urirun.entry_point_registry
    default_fn = getattr(urirun, "_DEFAULT_EP_REGISTRY_FN", ep_fn)
    fn_id = id(ep_fn)
    fp = _ep_cache_fingerprint() if ep_fn is default_fn else "custom"
    cached = getattr(urirun, "_CACHED_EP_REGISTRY", None)
    if cached is not None and cached[0] == fn_id and cached[1] == fp:
        return cached[2]
    reg = _load_or_build_default_ep_registry(fp) if ep_fn is default_fn else ep_fn()
    urirun._CACHED_EP_REGISTRY = (fn_id, fp, reg)
    return reg


def _normalize_status(out: Any) -> Any:
    if isinstance(out, dict) and "status" not in out:
        out["status"] = "ok" if (out.get("ok", True) is not False and "error" not in out) else "error"
    return out


def _try_inproc_route(step_uri: str, descriptor: dict, reg: dict, merged_payload: dict, mode: str, raw_envelope: bool) -> tuple[bool, Any]:
    if mode != "execute" or os.environ.get("URIRUN_FORCE_SUBPROCESS") == "1":
        return False, None
    routes = reg.get("routes") if isinstance(reg, dict) else None
    scheme_routes = routes.get(descriptor["package"]) if isinstance(routes, dict) else None
    segs = descriptor["segments"]
    if not isinstance(scheme_routes, dict) or len(segs) < 2:
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
) -> Any:
    import urirun

    descriptor = urirun.parse_uri(step_uri)
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
        req = urllib.request.Request(f"{effective_node.rstrip('/')}/call", data=req_body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            env = json.loads(resp.read().decode("utf-8"))
        return env if raw_envelope else _normalize_status(urirun.result_data(env))

    reg = registry if registry is not None else _get_cached_entry_point_registry()
    if isinstance(reg, dict) and "routes" not in reg and "bindings" not in reg and scheme in reg:
        return urirun.dispatch(step_uri, reg, merged_payload)
    if isinstance(reg, dict) and "bindings" in reg and "routes" not in reg:
        reg = urirun.compile_registry(reg)

    if isinstance(reg, dict) and isinstance(reg.get("routes"), dict):
        s_routes = reg["routes"].get(scheme)
        if isinstance(s_routes, dict):
            if len(descriptor["segments"]) == 1:
                step_uri = f"{scheme}://host/{descriptor['target']}/{descriptor['segments'][0]}"
                descriptor = urirun.parse_uri(step_uri)
            if descriptor["segments"][:2] == ["doctor", "query"] and not (isinstance(s_routes.get("doctor"), dict) and "query" in s_routes["doctor"]):
                doc_out = {"ok": True, "status": "ready", "connector": f"urirun-connector-{scheme}", "scheme": scheme, "configured": True, "resources": sorted(s_routes.keys())}
                return {"uri": step_uri, "mode": mode, "kind": "query", "adapter": "builtin-doctor", "ok": True, "status": "ok", "result": doc_out} if raw_envelope else doc_out

    handled, fast_out = _try_inproc_route(step_uri, descriptor, reg, merged_payload, mode, raw_envelope)
    if handled:
        return fast_out

    pol = urirun.policy(allow=urirun._step_allow_policy(allow, scheme))
    env = urirun.run(step_uri, reg, payload=merged_payload, mode=mode, policy=pol)
    return env if raw_envelope else _normalize_status(urirun.result_data(env))


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
) -> Any:
    """Universal URI process invoker for single JSON or batch JSONL calls."""
    steps, is_batch = _parse_uri_invocations(address, payload, jsonl=jsonl)
    results = [_invoke_single_uri(s_uri, s_pay, registry=registry, node_url=node_url, mode=mode, allow=allow, raw_envelope=raw) for s_uri, s_pay in steps]
    return results if is_batch else (results[0] if results else {})


def uri_jsonl(address: str, lines: Any, **kwargs: Any) -> list[Any]:
    """Stream JSONL lines or dicts into a URI process."""
    res = uri(address, list(lines) if not isinstance(lines, str) else lines, **kwargs)
    return res if isinstance(res, list) else [res]


def uri_cli(argv: list[str] | None = None) -> int:
    """CLI entrypoint for ``uri <full-uri> [json|jsonl]``."""
    import argparse

    parser = argparse.ArgumentParser(prog="uri", description="Universal dockuri URI process runner (single JSON or batch JSONL).")
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

    out = uri(args.address, payload_text, node_url=args.node_url, mode="dry-run" if args.dry_run else "execute", raw=args.raw)
    if args.jsonl and isinstance(out, list):
        for item in out:
            print(json.dumps(item, ensure_ascii=False))
    else:
        print(json.dumps(out, ensure_ascii=False))
    return 0
