# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

"""POST-path sub-handlers and auxiliary functions extracted from host_dashboard.py."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from .dashboard_http import _json_response, _read_json
from ._chat_message import chat_message as _chat_message
from .chat_orchestrator import chat_ask as _chat_ask_impl, ChatDeps
from .dashboard_api import (
    _first, _host_db, _mesh, _planfile_adapter, _host_config,
    task_create, chat_delete_messages, _dashboard_api_response,
)
from .fs_transfer import node_client as _node_client, node_token_for as _node_token_for
from .connector_admin import connector_install as _connector_install_impl, connector_env_check
from .artifacts_admin import (
    artifacts_delete as _artifacts_delete_impl,
    artifacts_dedupe_rows as _artifacts_dedupe_rows_impl,
    artifacts_cleanup_orphan_sidecars as _artifacts_cleanup_orphan_sidecars_impl,
    preview_url as _preview_url,
)
from .object_registry import (
    configured_node_api_lookup as _configured_node_api_lookup_impl,
    configured_api_call as _configured_api_call,
    connector_required_response as _connector_required_response,
    node_add as _node_add_impl,
    node_remove as _node_remove_impl,
    node_envelope_error as _node_envelope_error_impl,
    node_set_token as _node_set_token_impl,
    probe_node_token as _probe_node_token_impl,
    resolve_node_api_identifiers as _resolve_node_api_identifiers,
)
from .node_types import (
    node_type_tags as _node_type_tags_impl,
    normalize_node_type as _normalize_node_type_impl,
)
from .android_node import (
    node_forget_webpage as _node_forget_webpage,
    start_android_node_service,
    restart_android_node_service as _restart_android_node_service_impl,
    merge_live_webpage_nodes as _merge_live_webpage_nodes_impl,
    phone_web_nodes,
)
from ._host_port import _free_port_from_matching_processes
from .scanner_bridge import (
    page_action_result as _page_action_result_impl,
    scanner_session as _scanner_session_impl,
)
from .scanner_service import phone_node_qr as _phone_node_qr_impl
from .document_sync import reconcile_document_index

# Circular imports — safe: all listed names are bound in host_dashboard.py
# BEFORE the `from ._dashboard_post_handlers import ...` statement (before line 1273).
from urirun.host.host_dashboard import (
    _add_chat_message,
    _utc_now,
    _node_alias_map_from_context,
    _node_url_from_config,
    node_test_routes,
    sync_documents_to_node,
    ensure_phone_scanner_service,
    _scanner_bridge_deps,
    scanner_capture,
    scanner_best_finish,
    page_action_enqueue,
    uri_invoke,
)




def node_add(config: str | None, payload: dict) -> dict:
    return _node_add_impl(config, payload, normalize_node_type=_normalize_node_type_impl, node_type_tags=_node_type_tags_impl)


def node_remove(config: str | None, payload: dict) -> dict:
    return _node_remove_impl(config, payload, forget_webpage=_node_forget_webpage)


def _safe_api(api: dict) -> dict:
    return {k: v for k, v in api.items() if k != "auth"}


def _configured_api_status_response(node_name: str, api: dict) -> dict:
    auth = api.get("auth") or {}
    return {"ok": True, "node": node_name, "api": _safe_api(api), "authConfigured": bool(auth.get("secretRef"))}


def configured_node_api_request(config: str | None, node_urls: list[str] | None, payload: dict,
                                *, uri: str | None = None, status_only: bool = False) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    scheme, node_name, api_id, uri_status_only = _resolve_node_api_identifiers(payload, uri)
    if not node_name:
        return {"ok": False, "error": "node is required"}
    _hc = _host_config(config, node_urls)
    node, api, error = _configured_node_api_lookup_impl(_hc, node_name=node_name, api_id=api_id)
    if error or node is None or api is None:
        return {"ok": False, "error": error or "configured API not found"}
    if status_only or uri_status_only:
        return _configured_api_status_response(node_name, api)
    if scheme in {"media", "camera", "ssh", "fs"}:
        return _connector_required_response(scheme, node_name, _safe_api(api))
    return _configured_api_call(node, api, payload)




def restart_android_node_service(payload: dict | None = None) -> dict:
    return _restart_android_node_service_impl(payload, free_port_fn=_free_port_from_matching_processes)


def _merge_live_webpage_nodes(nodes: list) -> None:
    """Wrapper so tests can monkeypatch host_dashboard.phone_web_nodes."""
    import urirun.host.android_node as _an
    _orig = _an.phone_web_nodes
    _an.phone_web_nodes = phone_web_nodes
    try:
        _merge_live_webpage_nodes_impl(nodes)
    finally:
        _an.phone_web_nodes = _orig


def phone_node_qr(project: str, db: str | None, payload: dict) -> dict:
    return _phone_node_qr_impl(
        project, db, payload,
        host_db_fn=_host_db,
        preview_url_fn=_preview_url,
        chat_message_fn=_chat_message,
        add_chat_message_fn=_add_chat_message,
        ensure_android_node_fn=start_android_node_service,
    )


def _node_envelope_error(envelope: dict) -> str:
    return _node_envelope_error_impl(envelope)


def _probe_node_token(name: str, config: str | None, *, token: str | None = None,
                      identity: str | None = None, node_urls: list[str] | None = None,
                      timeout: float = 8.0) -> dict:
    return _probe_node_token_impl(
        name,
        node_url_fn=lambda n: _node_url_from_config(config, node_urls, n),
        token=token, identity=identity, timeout=timeout,
    )


def node_set_token(config: str | None, payload: dict, *, identity: str | None = None,
                   node_urls: list[str] | None = None) -> dict:
    return _node_set_token_impl(
        config, payload,
        node_url_fn=lambda n: _node_url_from_config(config, node_urls, n),
        identity=identity,
    )


def chat_ask(project: str, db: str | None, config: str | None, payload: dict, node_urls: list[str] | None = None,
             token: str | None = None, identity: str | None = None) -> dict:
    # Support ticket context for per-ticket LLM history
    ticket = payload.get("ticket") or (payload.get("context") or {}).get("ticket")
    if ticket:
        payload = dict(payload)
        payload["ticket"] = str(ticket)
    return _chat_ask_impl(project, db, config, payload, node_urls, token, identity, deps=ChatDeps(
        host_db_fn=_host_db,
        mesh_fn=_mesh,
        host_config_fn=_host_config,
        node_alias_map_fn=_node_alias_map_from_context,
        add_chat_message_fn=_add_chat_message,
        page_action_enqueue_fn=page_action_enqueue,
        ensure_phone_scanner_fn=ensure_phone_scanner_service,
        sync_documents_fn=sync_documents_to_node,
    ))


def task_action(project: str, ticket_id: str, action: str, payload: dict) -> dict:
    planfile_adapter = _planfile_adapter()
    handlers = {
        "start": lambda: planfile_adapter.start_ticket(project, ticket_id, assigned_to=payload.get("assigned_to")),
        "complete": lambda: planfile_adapter.complete_ticket(
            project, ticket_id, note=payload.get("note"), result=payload.get("result"), artifacts=payload.get("artifacts")
        ),
        "block": lambda: planfile_adapter.block_ticket(
            project,
            ticket_id,
            reason=str(payload.get("reason") or "Blocked from dashboard"),
            note=payload.get("note"),
        ),
        "ready": lambda: planfile_adapter.ready_ticket(project, ticket_id, note=payload.get("note")),
        "fail": lambda: planfile_adapter.fail_ticket(project, ticket_id, str(payload.get("error") or "failed from dashboard")),
    }
    handler = handlers.get(action)
    if handler is None:
        raise ValueError(f"unsupported task action: {action}")
    return {"ok": True, "ticket": handler()}







def connector_test(project: str, db: str | None, config: str | None, payload: dict, *,
                   node_urls: list[str] | None = None, token: str | None = None,
                   identity: str | None = None) -> dict:
    """Smoke-test a connector route on the host by really invoking it (mode=execute) through the
    same uri_invoke dispatch a chat/URI run uses. Use a read-only query route for a safe probe.
    Testing on a remote node/docker uses the dedicated /api/nodes/test-routes path instead."""
    payload = payload if isinstance(payload, dict) else {}
    uri = str(payload.get("uri") or "").strip()
    if not uri:
        return {"ok": False, "error": "test uri is required (e.g. uuid://host/id/query/v4)"}
    invoke_payload = {
        "uri": uri,
        "mode": payload.get("mode") or "execute",
        "payload": payload.get("payload") if isinstance(payload.get("payload"), dict) else {},
        "source": "connector-test",
    }
    try:
        return uri_invoke(project, db, config, invoke_payload,
                          node_urls=node_urls, token=token, identity=identity)
    except Exception as exc:  # noqa: BLE001 - surface route/handler errors to the UI as a failed test
        return {"ok": False, "invokedUri": uri, "error": str(exc)}
def documents_reconcile(project: str, db: str | None, payload: dict | None = None) -> dict:
    """Prune document-index entries whose artifacts are gone from disk.

    Index-only and non-destructive: existing files are never touched. Returns the
    summary report from :func:`reconcile_document_index` and logs it.
    """
    result = reconcile_document_index()
    try:
        _host_db().add_log(db, "documents", "reconcile-index", result)
    except Exception:  # noqa: BLE001
        pass
    return result


def _handle_get(handler, parsed, project, db, config, node_urls, token, identity):
    from ._dashboard_get_handlers import (  # noqa: PLC0415 - lazy avoids host_dashboard↔_dashboard_get_handlers cycle
        _handle_get_static, _handle_get_services, _handle_get_api,
    )
    if _handle_get_static(handler, parsed, project):
        return
    if _handle_get_services(handler, parsed, project):
        return
    if parsed.path == "/api/nodes/doctor":
        from .node_health import node_doctor as _node_doctor  # noqa: PLC0415
        query = parse_qs(parsed.query)
        node_name = str((_first(query, "node") or "")).strip()
        if not node_name:
            _json_response(handler, 400, {"ok": False, "error": "?node= required"})
            return
        node_url = _node_url_from_config(config, node_urls, node_name) or ""
        if not node_url:
            _json_response(handler, 404, {"ok": False, "error": f"node '{node_name}' not configured"})
            return
        _json_response(handler, 200, _node_doctor(
            node_url, node_name=node_name, token=token, identity=identity))
        return
    if _handle_get_api(handler, parsed, project, db):
        return
    status, payload = _dashboard_api_response(parsed.path, project, db, config, parse_qs(parsed.query), node_urls=node_urls)
    _json_response(handler, status, payload)


def _handle_post_connectors(handler, parsed, project, db, config, node_urls, token, identity) -> bool:
    if parsed.path == "/api/connectors/install":
        payload = _read_json(handler)
        _json_response(handler, 200, _connector_install_impl(project, payload, config=config, node_urls=node_urls, token=token, identity=identity,
                                                            node_url_from_config=_node_url_from_config, node_token_for=_node_token_for, node_client=_node_client))
        return True
    if parsed.path == "/api/connectors/docker-check":
        payload = _read_json(handler)
        _json_response(handler, 200, connector_env_check(payload))
        return True
    if parsed.path == "/api/connectors/test":
        payload = _read_json(handler)
        _json_response(handler, 200, connector_test(project, db, config, payload,
                                                     node_urls=node_urls, token=token, identity=identity))
        return True
    return False


def _handle_post_nodes(handler, parsed, project, db, config, node_urls, token, identity) -> bool:
    if parsed.path == "/api/nodes/test-routes":
        payload = _read_json(handler)
        _json_response(handler, 200, node_test_routes(project, db, config, payload,
                                                       node_urls=node_urls, token=token, identity=identity))
        return True
    if parsed.path in {"/api/nodes/add", "/api/nodes/api/add"}:
        payload = _read_json(handler)
        _json_response(handler, 200, _node_add_impl(config, payload, normalize_node_type=_normalize_node_type_impl, node_type_tags=_node_type_tags_impl))
        return True
    if parsed.path in {"/api/nodes/remove", "/api/nodes/delete"}:
        payload = _read_json(handler)
        _json_response(handler, 200, _node_remove_impl(config, payload, forget_webpage=_node_forget_webpage))
        return True
    if parsed.path == "/api/nodes/api/request":
        payload = _read_json(handler)
        _json_response(handler, 200, configured_node_api_request(config, node_urls, payload))
        return True
    if parsed.path == "/api/nodes/phone-qr":
        payload = _read_json(handler)
        _json_response(handler, 200, phone_node_qr(project, db, payload))
        return True
    if parsed.path == "/api/nodes/phone-service/start":
        payload = _read_json(handler)
        _json_response(handler, 200, start_android_node_service(payload))
        return True
    if parsed.path == "/api/nodes/token":
        payload = _read_json(handler)
        _json_response(handler, 200, node_set_token(config, payload, identity=identity, node_urls=node_urls))
        return True
    if parsed.path == "/api/nodes/doctor":
        from .node_health import node_doctor as _node_doctor  # noqa: PLC0415
        payload = _read_json(handler)
        node_name = str(payload.get("node") or "")
        node_url = _node_url_from_config(config, node_urls, node_name) if node_name else ""
        if not node_url:
            _json_response(handler, 400, {"ok": False, "error": "node name required"})
            return True
        _json_response(handler, 200, _node_doctor(
            node_url, node_name=node_name, token=token, identity=identity))
        return True
    return False


def _handle_post_scanner(handler, parsed, project, db, config, node_urls, token, identity) -> bool:
    if parsed.path == "/api/uri/invoke":
        payload = _read_json(handler)
        if not payload.get("source"):
            ref_path = urlparse(handler.headers.get("Referer", "") or "").path
            if ref_path == "/scanner":
                payload["source"] = "scanner-page"
        _json_response(
            handler,
            200,
            uri_invoke(project, db, config, payload, node_urls=node_urls, token=token, identity=identity),
        )
        return True
    if parsed.path == "/api/uri/event":
        _json_response(handler, 200, {"ok": True})
        return True
    if parsed.path == "/api/page/actions/result":
        payload = _read_json(handler)
        _json_response(handler, 200, _page_action_result_impl(_scanner_bridge_deps(), db, payload, utc_now=_utc_now))
        return True
    if parsed.path == "/api/scanner/capture":
        payload = _read_json(handler)
        _json_response(handler, 200, scanner_capture(project, db, payload))
        return True
    if parsed.path == "/api/scanner/best/finish":
        payload = _read_json(handler)
        _json_response(handler, 200, scanner_best_finish(project, db, payload))
        return True
    if parsed.path == "/api/scanner/session":
        payload = _read_json(handler)
        _json_response(handler, 200, _scanner_session_impl(_scanner_bridge_deps(), db, payload))
        return True
    return False


def _handle_post_chat(handler, parsed, project, db, config, node_urls, token, identity) -> bool:
    if parsed.path == "/api/chat/ask":
        payload = _read_json(handler)
        _json_response(handler, 200, chat_ask(project, db, config, payload, node_urls=node_urls,
                                               token=token, identity=identity))
        return True
    if parsed.path == "/api/chat/messages/delete":
        payload = _read_json(handler)
        _json_response(handler, 200, chat_delete_messages(db, payload))
        return True
    return False


def _handle_post_tasks(handler, parsed, parts, project) -> bool:
    if parsed.path == "/api/tasks/create":
        _json_response(handler, 200, task_create(project, _read_json(handler)))
        return True
    if len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks":
        _json_response(handler, 200, task_action(project, parts[2], parts[3], _read_json(handler)))
        return True
    return False


def _handle_post_artifacts(handler, parsed, project, db) -> bool:
    if parsed.path == "/api/artifacts/delete":
        _json_response(handler, 200, _artifacts_delete_impl(_host_db(), project, db, _read_json(handler)))
        return True
    if parsed.path == "/api/artifacts/dedupe":
        _json_response(handler, 200, _artifacts_dedupe_rows_impl(_host_db(), project, db, _read_json(handler)))
        return True
    if parsed.path == "/api/artifacts/cleanup-orphans":
        _json_response(handler, 200, _artifacts_cleanup_orphan_sidecars_impl(_host_db(), project, db, _read_json(handler)))
        return True
    if parsed.path == "/api/documents/reconcile":
        _json_response(handler, 200, documents_reconcile(project, db, _read_json(handler)))
        return True
    return False


def _work_approve(project, body: dict) -> dict:
    """Approve a BLOCKED work item → run its declared action, right now, in the background.

    Generic: every blocked item in the work-plan may carry ``approve.cmd`` (a shell command) and
    ``approve.label``. The browser sends only the item's URI; the command is read SERVER-SIDE from
    the plan (never from the request), so approving can't inject arbitrary commands. The run gets
    a durable record (meta + log + exit code) so the /work Runs panel can show its progress."""
    uri = str((body or {}).get("uri") or "").strip()
    if not uri:
        return {"ok": False, "error": "no item URI given"}
    try:
        from urirun_connector_view.core import _load_plan
        plan = _load_plan()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"work view not installed: {exc}"}
    item = next((b for b in (plan.get("blocked") or []) if b.get("uri") == uri), None)
    if item is None:
        return {"ok": False, "error": f"no blocked item matches {uri}"}
    action = item.get("approve") or {}
    cmd = action.get("cmd")
    if not cmd:
        return {"ok": False, "error": "this blocked item declares no approve action (add approve.cmd to the plan)"}
    # Run in the DASHBOARD process (the user's, with their credentials) — human-in-the-loop.
    from .work_runs import start_run
    meta = start_run(project, uri, cmd, label=action.get("label") or "")
    return {"ok": True, "started": True, "uri": uri, "run": meta["id"], "log": meta["log"],
            "message": action.get("label") or "Approved — running in the background."}


def _handle_post_work_ops(handler, parsed, project) -> bool:
    """/api/work/{approve,debug/snapshot,koru} — split out of _handle_post_work to keep its CC low."""
    if parsed.path == "/api/work/approve":
        _json_response(handler, 200, _work_approve(project, _read_json(handler)))
        return True
    if parsed.path == "/api/work/debug/snapshot":
        body = _read_json(handler)
        try:
            from urirun_connector_view.debug import snapshot_create  # optional
            _json_response(handler, 200,
                           snapshot_create(repo=str(project), label=str((body or {}).get("label") or "")))
        except Exception as exc:  # noqa: BLE001
            _json_response(handler, 200, {"ok": False, "error": f"debug surface unavailable: {exc}"})
        return True
    if parsed.path == "/api/work/koru":
        body = _read_json(handler) or {}
        from .work_queue import ensure_running
        try:
            _json_response(handler, 200, ensure_running(lane=str(body.get("lane") or "queue")))
        except Exception as exc:  # noqa: BLE001
            _json_response(handler, 200, {"ok": False, "error": str(exc)})
        return True
    return False


def _handle_post_work_person(handler, parsed, project) -> bool:
    """/api/work/person/{toggle,mode} — split out of _handle_post_work to keep its CC low."""
    if parsed.path == "/api/work/person/toggle":
        body = _read_json(handler) or {}
        pid = str(body.get("id") or "").strip()
        enabled = body.get("enabled")
        disabled_until = body.get("disabled_until")
        from . import ticket_meta
        try:
            ok = ticket_meta.set_digital_person_enabled(pid, bool(enabled) if enabled is not None else True, disabled_until)
            _json_response(handler, 200, {"ok": ok, "id": pid, "enabled": enabled, "disabled_until": disabled_until})
        except Exception as exc:  # noqa: BLE001
            _json_response(handler, 200, {"ok": False, "error": str(exc)})
        return True
    if parsed.path == "/api/work/person/mode":
        body = _read_json(handler) or {}
        pid = str(body.get("id") or "").strip()
        mode = str(body.get("mode") or "real")
        from . import ticket_meta
        try:
            ok = ticket_meta.set_digital_person_mode(pid, mode)
            _json_response(handler, 200, {"ok": ok, "id": pid, "mode": ticket_meta.get_digital_person_mode(pid)})
        except Exception as exc:  # noqa: BLE001
            _json_response(handler, 200, {"ok": False, "error": str(exc)})
        return True
    return False


def _handle_post_work(handler, parsed, project) -> bool:
    if _handle_post_work_ops(handler, parsed, project):
        return True
    if _handle_post_work_person(handler, parsed, project):
        return True
    if _handle_post_work_console(handler, parsed, project):
        return True
    return False


def _work_console_ops(project, path, body) -> dict:
    from .work_console import confirm_op, reject_op
    op_id = str(body.get("id") or "").strip()
    return confirm_op(project, op_id) if path.endswith("confirm") else reject_op(op_id)


def _work_console_shell(project, path, body) -> dict:
    from .work_console import run_shell
    return run_shell(project, str(body.get("cmd") or ""))


def _work_console_ticket(project, path, body) -> dict:
    from .work_queue import ticket_action
    return ticket_action(str(body.get("id") or ""), str(body.get("action") or ""),
                         str(body.get("note") or ""))


def _work_console_ticket_create(project, path, body) -> dict:
    from .work_queue import create_ticket  # NL „nowy ticket" z panelu /work → kolejka
    name = str(body.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name wymagane"}
    return create_ticket(name, description=str(body.get("description") or ""),
                         priority=str(body.get("priority") or "normal"),
                         node=str(body.get("node") or ""),
                         labels=body.get("labels"), criteria=body.get("criteria"))


def _work_console_ticket_edit(project, path, body) -> dict:
    from .work_queue import ticket_edit_full
    return ticket_edit_full(
        str(body.get("id") or ""),
        name=str(body.get("name") or ""),
        description=str(body.get("description") or ""),
        llm=body.get("llm"),
        node=body.get("node"),
        allow=body.get("allow"),
        deny=body.get("deny"),
        schedule=body.get("schedule"),
        owner=body.get("owner"),
        llm_model=body.get("llm_model"),
        assigned_person=body.get("assigned_person"),
    )


def _work_console_unblocks(project, path, body) -> dict:
    from urirun_connector_grants import unblock_ledger as ul
    action = str(body.get("action") or "revoke").strip()
    key = str(body.get("key") or body.get("ticket") or "").strip()
    if not key:
        return {"ok": False, "error": "key lub ticket wymagane"}
    if action == "revoke-ticket":
        return ul.revoke_unblock(key)
    if action == "revoke":
        return ul.revoke_unblock_key(key) if ":" in key else ul.revoke_unblock(key)
    return {"ok": False, "error": f"unknown action {action!r}"}


def _work_console_ticket_archive(project, path, body) -> dict:
    from .work_queue import ticket_action
    return ticket_action(str(body.get("id") or ""), "archive", str(body.get("note") or ""))


def _work_console_ticket_unarchive(project, path, body) -> dict:
    from .work_queue import ticket_action
    return ticket_action(str(body.get("id") or ""), "unarchive", "")


def _work_console_cron(project, path, body) -> dict:
    from . import cron_admin
    return cron_admin.action(body)


def _work_console_watchdog(project, path, body) -> dict:
    from . import watchdog_admin
    return watchdog_admin.action(body)


def _work_console_agents(project, path, body) -> dict:
    from . import agent_admin
    return agent_admin.action(project, body)


def _work_console_task_new(project, path, body) -> dict:
    """Człowiek dodaje zadanie z panelu: nazwa/opis/node/priorytet + acceptance_criteria."""
    from .work_queue import create_ticket
    b = body or {}
    return create_ticket(name=str(b.get("name") or ""), description=str(b.get("description") or ""),
                         priority=str(b.get("priority") or "normal"), node=str(b.get("node") or ""),
                         labels=b.get("labels"), criteria=b.get("criteria"))


def _work_console_system(project, path, body) -> dict:
    from .work_queue import _project
    try:
        from urirun_connector_watchdog import core as wd
    except ImportError:
        return {"ok": False, "error": "install urirun-connector-watchdog"}
    return wd.system_remediate(project=_project())


def _work_console_signal(project, path, body) -> dict:
    """signal:// z panelu: send (reversible — inverse=delete) / delete."""
    try:
        from urirun_connector_signal import core as sig
    except ImportError:
        return {"ok": False, "error": "install urirun-connector-signal"}
    b = body or {}
    act = str(b.get("action") or "send")
    if act == "delete":
        return sig.message_command_delete(id=str(b.get("id") or ""))
    return sig.message_command_send(to=str(b.get("to") or ""), message=str(b.get("message") or ""))


def _work_console_verify(project, path, body) -> dict:
    """verify:// — seed/uruchom acceptance_criteria (done-validation) ticketu."""
    from .work_queue import _project
    try:
        from urirun_connector_verify import core as _vf
    except ImportError:
        return {"ok": False, "error": "install urirun-connector-verify"}
    b = body or {}
    if str(b.get("action")) == "seed":
        return _vf.ticket_command_seed(id=str(b.get("id") or ""), checks=b.get("checks"))
    return _vf.ticket_query_check(id=str(b.get("id") or ""), checks=b.get("checks"), cwd=str(_project()))


def _work_console_loop(project, path, body) -> dict:
    """Uruchom cykl pętli korekcyjnej. apply=True stosuje SAFE akcje; auto_agent=True odblokowuje run-agent."""
    from .work_queue import _project
    try:
        from urirun_connector_loop import core as _loop
    except ImportError:
        return {"ok": False, "error": "install urirun-connector-loop"}
    return _loop.cycle_command_run(project=_project(), apply=bool((body or {}).get("apply")),
                                   auto_agent=bool((body or {}).get("auto_agent")))


def _work_console_action(project, path, body) -> dict:
    """Unijny dispatcher: {op, ...params} → właściwy handler POST (sterowanie całą stroną przez 1 endpoint)."""
    from .work_api import op_path
    op = str((body or {}).get("op") or "").strip()
    target = op_path(op)
    inner = {k: v for k, v in (body or {}).items() if k != "op"}
    if op == "koru":
        from .work_queue import ensure_running
        return ensure_running(lane=str(inner.get("lane") or "queue"))
    fn = _WORK_CONSOLE_ROUTES.get(target) if target else None
    if not fn:
        return {"ok": False, "error": f"unknown op {op!r}"}
    return fn(project, target, inner)


_WORK_CONSOLE_ROUTES = {
    "/api/work/ops/confirm": _work_console_ops, "/api/work/ops/reject": _work_console_ops,
    "/api/work/shell": _work_console_shell, "/api/work/ticket": _work_console_ticket,
    "/api/work/ticket/create": _work_console_ticket_create,
    "/api/work/ticket/edit": _work_console_ticket_edit, "/api/work/cron": _work_console_cron,
    "/api/work/watchdog": _work_console_watchdog, "/api/work/agents": _work_console_agents,
    "/api/work/loop": _work_console_loop, "/api/work/verify": _work_console_verify,
    "/api/work/task/new": _work_console_task_new, "/api/work/signal": _work_console_signal,
    "/api/work/system": _work_console_system, "/api/work/unblocks": _work_console_unblocks,
    "/api/work/action": _work_console_action,
    "/api/work/ticket/archive": _work_console_ticket_archive,
    "/api/work/ticket/unarchive": _work_console_ticket_unarchive,
}


def _handle_post_work_console(handler, parsed, project) -> bool:
    """Operator-console POST surfaces (confirm/reject an operation, run a shell command, act on a
    ticket). Dispatch table keeps this under the CC gate; each handler returns an envelope."""
    fn = _WORK_CONSOLE_ROUTES.get(parsed.path)
    if fn is None:
        return False
    body = _read_json(handler) or {}
    try:
        _json_response(handler, 200, fn(project, parsed.path, body))
    except Exception as exc:  # noqa: BLE001
        _json_response(handler, 200, {"ok": False, "error": str(exc)})
    return True


def _handle_post(handler, parsed, parts, project, db, config, node_urls, token, identity):
    if _handle_post_work(handler, parsed, project):
        return
    if _handle_post_tasks(handler, parsed, parts, project):
        return
    if _handle_post_artifacts(handler, parsed, project, db):
        return
    if _handle_post_connectors(handler, parsed, project, db, config, node_urls, token, identity):
        return
    if _handle_post_nodes(handler, parsed, project, db, config, node_urls, token, identity):
        return
    if _handle_post_scanner(handler, parsed, project, db, config, node_urls, token, identity):
        return
    if _handle_post_chat(handler, parsed, project, db, config, node_urls, token, identity):
        return
    _json_response(handler, 404, {"ok": False, "error": "not found"})

