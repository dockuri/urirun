# Author: Tom Sapletta · https://tom.sapletta.com
# Part of the ifURI solution.

"""Small host dashboard for planfile tasks, nodes and urirun activity."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import ssl
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urirun.node.mesh import EventHub, _sse_initial_cursor, _sse_event_matches, _sse_frame
from .twin_bridge import TWIN_EVENT_HUB
from .dashboard_http import _json_response, _html_response, _asset_response, _js_sdk_response, _read_json, _file_response, _remote_file_response
from urllib.parse import parse_qs, unquote, urlparse

from .document_sync import (
    DOCUMENT_SYNC_URI as _DOCUMENT_SYNC_URI,
    DocumentSyncDeps,
    document_archive_pdfs as _document_archive_pdfs_impl,
    document_archive_root as _document_archive_root,
    document_index_path as _document_index_path,
    load_document_index as _load_document_index,
    save_document_index as _save_document_index,
    prune_orphaned_documents as _prune_orphaned_documents,
    document_sync_auto_retry_enabled as _document_sync_auto_retry_enabled,
    document_sync_default_dest_root as _document_sync_default_dest_root,
    document_sync_default_node as _document_sync_default_node,
    document_sync_dest_from_prompt as _document_sync_dest_from_prompt,
    document_sync_retry_payload_from_urifix as _document_sync_retry_payload_from_urifix,
    document_sync_verification as _document_sync_verification_impl,
    needs_screen_document_capture as _needs_screen_document_capture,
    sync_documents_to_node as _sync_documents_to_node_impl,
    artifact_schema_known as _artifact_schema_known,
    document_schema_fields as _document_schema_fields,
    file_sha256 as _file_sha256,
    docid_for_file as _docid_for_file,
    find_duplicate_document as _find_duplicate_document,
    archive_scanned_document as _archive_scanned_document_impl,
    _DOCUMENT_INDEX_LOCK,
    _transaction_fingerprint,
    _fingerprint_match_count,
    merge_metadata_fields as _merge_metadata_fields,
    enrich_archived_record as _enrich_archived_record,
    backfill_scanned_id_log as _backfill_scanned_id_log,
    route_scanned_documents as _route_scanned_documents_impl,
)
from .android_node import phone_web_nodes
from .discovery import (
    add_node_aliases as _add_node_aliases_impl,
    alias_map_from_dict as _alias_map_from_dict_impl,
    alias_map_from_list as _alias_map_from_list_impl,
    classify_route_run as _classify_route_run_impl,
    host_config as _host_config_impl,
    iter_node_alias_values as _iter_node_alias_values_impl,
    known_nodes_file_data as _known_nodes_file_data_impl,
    known_nodes_file_urls as _known_nodes_file_urls_impl,
    merge_known_nodes_into_config as _merge_known_nodes_into_config_impl,
    node_alias_map_from_config_doc as _node_alias_map_from_config_doc_impl,
    node_alias_map_from_context as _node_alias_map_from_context_impl,
    node_alias_map_from_env as _node_alias_map_from_env_impl,
    node_alias_map_from_known_nodes_file as _node_alias_map_from_known_nodes_file_impl,
    node_alias_map_from_node_urls as _node_alias_map_from_node_urls_impl,
    node_alias_map_from_value as _node_alias_map_from_value_impl,
    node_dicts_from_url_map as _node_dicts_from_url_map_impl,
    node_spec_aliases as _node_spec_aliases_impl,
    node_test_routes as _node_test_routes_impl,
    node_url_map_from_value as _node_url_map_from_value_impl,
    normalize_known_node_url as _normalize_known_node_url_impl,
    prompt_node_match as _prompt_node_match_impl,
    route_inputs_example as _route_inputs_example_impl,
    url_map_from_dict as _url_map_from_dict_impl,
    url_map_from_list as _url_map_from_list_impl,
)
from .fs_transfer import (
    deploy_fs_file_transfer_fallback as _deploy_fs_file_transfer_fallback_impl,
    ensure_node_uri_routes as _ensure_node_uri_routes_impl,
    fs_file_transfer_binding as _fs_file_transfer_binding_impl,
    fs_file_transfer_fallback_bindings as _fs_file_transfer_fallback_bindings_impl,
    node_has_route as _node_has_route_impl,
    route_key as _route_key_impl,

    compact_remote_run as _compact_remote_run,
    remote_write_error as _remote_write_error,
    remote_read_error as _remote_read_error,
    node_client as _node_client,
    node_token_for as _node_token_for,
)
from .node_dispatch import run_node_uri as _run_node_uri
from .node_types import (
    annotate_node_types as _annotate_node_types_impl,
    node_type_profile as _node_type_profile_impl,
    node_type_profiles as _node_type_profiles_impl,
    node_type_tags as _node_type_tags_impl,
    normalize_node_type as _normalize_node_type_impl,
)
from .object_registry import (
    annotate_node_tokens as _annotate_node_tokens_impl,
    host_object as _host_object_impl,
    host_registry_routes as _host_registry_routes_impl,
    local_entry_point_host_routes as _local_entry_point_host_routes_impl,
    service_contacts as _service_contacts_impl,
    uri_objects as _uri_objects_impl,
    mirror_node_to_nodes_file as _mirror_node_to_nodes_file,
    uri_action_catalog as _uri_action_catalog,
    node_api_slug as _node_api_slug,
    node_api_secret_ref as _node_api_secret_ref,
    store_node_api_secret as _store_node_api_secret,
    extract_raw_secret as _extract_raw_secret,
    extract_secret_ref as _extract_secret_ref,
    build_auth_extra_fields as _build_auth_extra_fields,
    normalize_node_api_auth as _normalize_node_api_auth,
    default_api_items as _default_api_items,
    api_item_fields as _api_item_fields,
    normalize_api_item as _normalize_api_item,
    normalize_node_apis as _normalize_node_apis,
    derive_node_capabilities as _derive_node_capabilities,
    build_node_entry as _build_node_entry,
    persist_node_to_config as _persist_node_to_config,
    node_remove_from_mirror as _node_remove_from_mirror,
    configured_node_api_parts as _configured_node_api_parts,
    configured_node_api_lookup as _configured_node_api_lookup_impl,
    configured_api_secret as _configured_api_secret,
    apply_auth_header as _apply_auth_header,
    configured_api_headers as _configured_api_headers,
    join_api_url as _join_api_url,
    configured_api_response_body as _configured_api_response_body,
    build_request_body as _build_request_body,
    execute_http_request as _execute_http_request,
    resolve_http_method_and_url as _resolve_http_method_and_url,
    connector_hint as _connector_hint,
    connector_required_response as _connector_required_response,
    configured_api_call as _configured_api_call,
    apply_uri_overrides as _apply_uri_overrides,
    resolve_node_api_identifiers as _resolve_node_api_identifiers,
    _SCHEME_CONNECTOR_PACKAGES,
    node_kinds_path as _node_kinds_path,
    node_kinds as _node_kinds,
    set_node_kind as _set_node_kind,
    node_remove_kind as _node_remove_kind,
    annotate_node_kinds as _annotate_node_kinds,
    node_add as _node_add_impl,
    node_remove as _node_remove_impl,
    node_envelope_error as _node_envelope_error_impl,
    probe_node_token as _probe_node_token_impl,
    node_set_token as _node_set_token_impl,
)
from .connector_admin import (
    CONNECTOR_DOCKER_TIMEOUT as _CONNECTOR_DOCKER_TIMEOUT,
    connector_install as _connector_install_impl,
    _connector_install_node as _connector_install_node_impl,
    connector_pip_tail as _connector_pip_tail,
    refresh_connector_schemes as _refresh_connector_schemes,
    env_check_error as _env_check_error,
    docker_install_target as _docker_install_target,
    run_docker_check as _run_docker_check,
    parse_bindings_output as _parse_bindings_output,
    connector_env_check as connector_env_check,
)
from .artifacts_admin import (
    artifact_delete_roots as _artifact_delete_roots,
    artifact_file_delete_allowed as _artifact_file_delete_allowed,
    payload_bool as _payload_bool,
    global_document_metadata_paths as _global_document_metadata_paths,
    safe_artifact_sidecar_path as _safe_artifact_sidecar_path,
    artifact_delete_candidate_paths as _artifact_delete_candidate_paths,
    delete_one_artifact_file as _delete_one_artifact_file,
    delete_artifact_files as _delete_artifact_files,
    artifact_visual_path as _artifact_visual_path,
    artifact_file_exists as _artifact_file_exists,
    artifact_dedupe_key as _artifact_dedupe_key,
    artifact_dedupe_rank as _artifact_dedupe_rank,
    merge_artifact_group as _merge_artifact_group,
    preview_url as _preview_url,
    public_artifact as _public_artifact,
    public_artifacts as _public_artifacts,
    attachment_visual_path as _attachment_visual_path,
    apply_attachment_file_fields as _apply_attachment_file_fields,
    apply_attachment_visual_fields as _apply_attachment_visual_fields,
    public_chat_attachment as _public_chat_attachment,
    public_chat_attachments as _public_chat_attachments,
    dedupe_public_artifacts as _dedupe_public_artifacts,
    visible_public_artifacts as _visible_public_artifacts,
    collect_attachments as _collect_attachments,
    iter_orphan_candidates as _iter_orphan_candidates,
    cleanup_one_sidecar as _cleanup_one_sidecar,
    artifacts_delete as _artifacts_delete_impl,
    artifacts_dedupe_rows as _artifacts_dedupe_rows_impl,
    artifacts_cleanup_orphan_sidecars as _artifacts_cleanup_orphan_sidecars_impl,
)
from .html_templates import (
    INDEX_HTML, NODE_TYPES_DOC_HTML, SCANNER_HTML,
    docs_nodes_html as _docs_nodes_html_impl,
)
from .scanner_bridge import (
    PAGE_ACTION_LOCK as _SCANNER_PAGE_ACTION_LOCK,
    PAGE_ACTION_QUEUES as _SCANNER_PAGE_ACTION_QUEUES,
    SCANNER_BEST_LOCK as _SCANNER_BEST_LOCK,
    SCANNER_BEST_SESSIONS as _SCANNER_BEST_SESSIONS,
    SCANNER_LIVE_STREAMS as _SCANNER_LIVE_STREAMS,
    ScannerBridgeDeps,
    crop_overlay_attachment as _crop_overlay_attachment_impl,
    page_action_enqueue as _page_action_enqueue_impl,
    page_action_poll as _page_action_poll_impl,
    page_action_result as _page_action_result_impl,
    public_scanner_candidate as _public_scanner_candidate_impl,
    is_autonomous_scanner_prompt as _is_autonomous_scanner_prompt_impl,
    is_camera_start_prompt as _is_camera_start_prompt_impl,
    is_scanner_artifact as _is_scanner_artifact_impl,
    is_phone_scanner_prompt as _is_phone_scanner_prompt_impl,
    latest_scanner_page_status as _latest_scanner_page_status_impl,
    nl_text as _nl_text_impl,
    register_document_artifact as _register_document_artifact_impl,
    scanner_artifact_item as _scanner_artifact_item_impl,
    scanner_artifact_doc_meta as _scanner_artifact_doc_meta_impl,
    bounded as _bounded,
    crop_dimensions as _crop_dimensions,
    crop_geometry_score as _crop_geometry_score,
    crop_quality_score as _crop_quality_score,
    doctype_quality_score as _doctype_quality_score,
    document_frame_quality as _document_frame_quality,
    frame_visual_metrics as _frame_visual_metrics,
    metadata_quality_score as _metadata_quality_score,
    ocr_quality_score as _ocr_quality_score,
    orientation_summary as _orientation_summary,
    scanner_best_take as _scanner_best_take,
    scanner_best_update as _scanner_best_update,
    scanner_staging_dir as _scanner_staging_dir,
    staging_keep_paths as _staging_keep_paths,
    prune_scanner_staging as _prune_scanner_staging_impl,
    visual_quality_score as _visual_quality_score,
    scanner_live_state_from_streams as _scanner_live_state_from_streams_impl,
    scanner_live_store_locked as _scanner_live_store_locked,
    scanner_public_candidate_for_live as _scanner_public_candidate_for_live_impl,
    scanner_status_from_log as _scanner_status_from_log_impl,
    scanner_session as _scanner_session_impl,
    scanner_result_content as _scanner_result_content_impl,
    scanner_service_live_views as _scanner_service_live_views_impl,
    scanner_flow_result as _scanner_flow_result_impl,
    torch_enabled_from_prompt as _torch_enabled_from_prompt_impl,
    uri_event as _uri_event_impl,
    best_candidate_paths as _best_candidate_paths,
    best_crop_and_ocr as _best_crop_and_ocr,
    best_finish_store_failure as _best_finish_store_failure_impl,
    best_quality_rejected as _best_quality_rejected,
    best_series_not_found as _best_series_not_found,
    capture_display_path as _capture_display_path,
    capture_quality_ok as _capture_quality_ok,
    cleanup_duplicate_scan_files as _cleanup_duplicate_scan_files,
    decode_capture_image as _decode_capture_image,
    resolve_best_candidate as _resolve_best_candidate,
    store_best_finish as _store_best_finish,
    capture_candidate_result as _capture_candidate_result_impl,
    capture_reject_result as _capture_reject_result_impl,
    scanner_live_state as _scanner_live_state_impl,
    draw_crop_box as _draw_crop_box,
    draw_overlay_label as _draw_overlay_label,
    scanner_crop_overlay as _scanner_crop_overlay,
    auto_crop_receipt as _auto_crop_receipt,
    capture_ocr_and_detect as _capture_ocr_and_detect_impl,
    refresh_best_ocr as _refresh_best_ocr_impl,
    ensure_best_overlay as _ensure_best_overlay_impl,
    scanner_capture as _scanner_capture_impl,
    scanner_best_finish as _scanner_best_finish_impl,
    register_scanner_result as _register_scanner_result_impl,
)
# Backward-compat alias — tests and older callers used _PAGE_ACTION_QUEUES.
_PAGE_ACTION_QUEUES = _SCANNER_PAGE_ACTION_QUEUES
from .service_control import (
    chat_service_restart_argv as _chat_service_restart_argv_impl,
    free_port_from_matching_processes as _free_port_from_matching_processes_impl,
    free_port_from_old_dashboard as _free_port_from_old_dashboard_impl,
    is_android_node_process as _is_android_node_process_impl,
    is_chat_process as _is_chat_process_impl,
    is_dashboard_process as _is_dashboard_process_impl,
    is_scanner_process as _is_scanner_process_impl,
    port_holder_pids as _port_holder_pids_impl,
    port_holder_pids as _port_holder_pids,  # monkeypatch-friendly alias  # noqa: F401
    process_cmdline as _process_cmdline_impl,
    process_cmdline as _process_cmdline,  # monkeypatch-friendly alias  # noqa: F401
    restart_chat_service as _restart_chat_service_impl,
    schedule_restart_command as _schedule_restart_command_impl,
    service_lifecycle_aliases as _service_lifecycle_aliases_impl,
    service_restart_argv as _service_restart_argv_impl,
    service_status as _service_status_impl,
    stop_service_pids as _stop_service_pids_impl,
)
from ._host_port import (
    _free_port_from_matching_processes,
    _is_dashboard_process,
    _free_port_from_old_dashboard,
    _free_port_from_old_scanner,
    _free_port_from_old_chat,
    _free_port_from_old_android_node,
)
from .widgets import (
    query_value as _widget_query_value,
    scanner_stream_summary as _scanner_stream_summary_impl,
    select_service_view as _select_service_view_impl,
)

try:
    import docid.dedup as _docid_dedup_check  # noqa: F401
    _DOCID_DEDUP_IMPORT_ERROR = None
except Exception as _err:  # noqa: BLE001
    _DOCID_DEDUP_IMPORT_ERROR = _err

from .scanner_service import (
    _SERVICE_LOCK,
    _SERVICE_SERVERS,
    _SERVICE_THREADS,
    phone_scanner_service_id as _phone_scanner_service_id,
    startup_phone_qr as _startup_phone_qr_impl,
    phone_node_qr as _phone_node_qr_impl,
    ensure_phone_scanner_service as _ensure_phone_scanner_service_impl,
    restart_phone_scanner_service as _restart_phone_scanner_service_impl,
)
# Monkeypatch-friendly aliases (auto-sync moved these to scanner_bridge with _impl suffix)
_is_phone_scanner_prompt = _is_phone_scanner_prompt_impl  # noqa: F401
_torch_enabled_from_prompt = _torch_enabled_from_prompt_impl  # noqa: F401
page_action_poll = _page_action_poll_impl  # noqa: F401
_LAST_STAGING_PRUNE: float = 0.0


def _prune_scanner_staging(*, min_interval: float = 60.0) -> int:
    """Wrapper: injects _scanner_staging_dir; owns throttle via module-level _LAST_STAGING_PRUNE."""
    global _LAST_STAGING_PRUNE
    import time as _time
    now = _time.time()
    if now - _LAST_STAGING_PRUNE < min_interval:
        return 0
    _LAST_STAGING_PRUNE = now
    return _prune_scanner_staging_impl(_scanner_staging_dir, min_interval=0.0)


def _archive_scanned_document(**kwargs):
    """Wrapper: injects the patchable _docid_for_file so tests can monkeypatch it."""
    return _archive_scanned_document_impl(**kwargs, docid_fn=_docid_for_file)


def artifacts_delete(project, artifact_dir, payload, db=None):
    return _artifacts_delete_impl(_host_db(), project, db, payload)


def artifacts_dedupe_rows(project, artifact_dir, payload, db=None):
    return _artifacts_dedupe_rows_impl(_host_db(), project, db, payload)


def artifacts_cleanup_orphan_sidecars(project, artifact_dir, payload, db=None):
    return _artifacts_cleanup_orphan_sidecars_impl(_host_db(), project, db, payload)


def _docs_nodes_html() -> str:
    return _docs_nodes_html_impl(_node_type_profiles_impl())


def _service_view_from_query(project: str, query: dict[str, list[str]]) -> dict:
    target = _widget_query_value(query, "target") or _widget_query_value(query, "service") or "service:phone-scanner"
    view_id = _widget_query_value(query, "id")
    data = service_live_views(project, limit=int(_first(query, "limit", "8") or 8))
    return _select_service_view_impl(data, target=target, view_id=view_id, utc_now=_utc_now)


def _standalone_service_html(project: str, query: dict[str, list[str]]) -> str:
    from urirun_widgets.render import service_widget_html
    return service_widget_html(_service_view_from_query(project, query))


def _standalone_service_svg(project: str, query: dict[str, list[str]]) -> str:
    from urirun_widgets.render import render_svg
    view = _service_view_from_query(project, query)
    width = max(320, min(1200, int(_first(query, "width", "720") or 720)))
    height = max(120, min(600, int(_first(query, "height", "180") or 180)))
    return render_svg(view, width=width, height=height)


from ._chat_message import chat_message as _chat_message


def _add_chat_message(db: str | None, message: dict) -> dict | None:
    try:
        return _host_db().add_log(db, "chat", "message", message)
    except Exception:  # noqa: BLE001
        return None





from . import document_metadata as _document_metadata
from .document_metadata import (  # noqa: F401,E402 - re-export shim placed where the funcs were
    _LLM_DOC_TYPES,
    _LLM_FIELDS_SPEC,
    _coerce_amount,
    _document_type,
    _extract_document_metadata,
    _llm_api_key_ref,
    _llm_complete_metadata,
    _llm_env_file,
    _llm_extract_metadata,
    _llm_model,
    _load_env_file,
    _local_image_ocr,
    _local_image_ocr_llm,
    _local_image_ocr_tesseract,
    _normalize_llm_doc_fields,
    _normalized_document_text,
    _ocr_connector_envelope,
    _ocr_text_ok,
    _parse_amount,
    _parse_contractor,
    _parse_document_date,
    _parse_llm_json_object,
    _truthy_env,
    shutil_which,
)


def _sync_document_metadata_hooks() -> None:
    for name in (
        "_llm_extract_metadata",
        "_llm_model",
        "_llm_api_key_ref",
        "_llm_complete_metadata",
        "_local_image_ocr_tesseract",
        "_local_image_ocr_llm",
        "_ocr_connector_envelope",
        "_ocr_text_ok",
        "_truthy_env",
        "shutil_which",
    ):
        setattr(_document_metadata, name, globals()[name])


def _extract_document_metadata(ocr_text: str, *, captured_at: str | None = None,
                               image_path: str | None = None, use_llm: bool = True) -> dict:
    _sync_document_metadata_hooks()
    return _document_metadata._extract_document_metadata(
        ocr_text,
        captured_at=captured_at,
        image_path=image_path,
        use_llm=use_llm,
    )


def _local_image_ocr(path: str, backend: str | None = None) -> dict:
    _sync_document_metadata_hooks()
    return _document_metadata._local_image_ocr(path, backend=backend)


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _node_alias_map_from_context(config: str | None, node_urls: list[str] | None = None) -> dict[str, str]:
    try:
        config_doc = _host_config(config, node_urls)
    except Exception:
        config_doc = None
    return _node_alias_map_from_context_impl(
        config_doc,
        node_urls,
        default_node=_document_sync_default_node(),
    )
def _node_url_from_config(config: str | None, node_urls: list[str] | None, node: str) -> str | None:
    try:
        return str(_mesh().node_url(_host_config(config, node_urls), node)).rstrip("/")
    except (Exception, SystemExit):  # mesh.node_url exits for unknown nodes.
        return None


def node_test_routes(project: str, db: str | None, config: str | None, payload: dict, *,
                     node_urls: list[str] | None = None, token: str | None = None,
                     identity: str | None = None) -> dict:
    return _node_test_routes_impl(
        payload,
        node_url_from_config=lambda node: _node_url_from_config(config, node_urls, node),
        node_token_for=lambda node: _node_token_for(node, token),
        node_client=_node_client,
        token=token,
        identity=identity,
    )


def _ensure_node_uri_routes(
    node_url: str,
    required_uris: list[str],
    *,
    node: str,
    token: str | None = None,
    identity: str | None = None,
    timeout: float = 120.0,
    roots: Any = None,
) -> dict:
    return _ensure_node_uri_routes_impl(
        node_url,
        required_uris,
        node=node,
        node_client=_node_client,
        token=token,
        identity=identity,
        timeout=timeout,
        roots=roots,
    )


def _document_sync_deps() -> DocumentSyncDeps:
    return DocumentSyncDeps(
        document_archive_root=_document_archive_root,
        default_node=_document_sync_default_node,
        default_dest_root=_document_sync_default_dest_root,
        node_url_from_config=_node_url_from_config,
        archive_pdfs=_document_archive_pdfs_impl,
        verification=lambda files, results, source_root, read_back: _document_sync_verification_impl(
            files, results, source_root=source_root, read_back=read_back
        ),
        ensure_node_uri_routes=_ensure_node_uri_routes,
        run_node_uri=_run_node_uri,
        compact_remote_run=_compact_remote_run,
        remote_write_error=_remote_write_error,
        remote_read_error=_remote_read_error,
        utc_now=_utc_now,
        host_db=_host_db,
        chat_message=_chat_message,
        add_chat_message=_add_chat_message,
    )


def sync_documents_to_node(
    project: str,
    db: str | None,
    config: str | None,
    payload: dict,
    *,
    node_urls: list[str] | None = None,
    token: str | None = None,
    identity: str | None = None,
) -> dict:
    # Use the per-node management token (set via the dashboard Nodes view, stored in keyring) when
    # present, so node:// route provisioning on the target is authorized; else the host-wide token.
    node_name = str((payload or {}).get("node") or (payload or {}).get("targetNode") or "").strip()
    token = _node_token_for(node_name, token)
    return _sync_documents_to_node_impl(
        project,
        db,
        config,
        payload,
        deps=_document_sync_deps(),
        node_urls=node_urls,
        token=token,
        identity=identity,
    )


from .document_sync import reconcile_document_index  # noqa: F401 - re-export

from .decision_loop import (
    decision_loop_status as _decision_loop_status,
    decision_loop_next_intent as _decision_loop_next_intent,
    decision_loop_observation as _decision_loop_observation,
    decision_loop_for_document_sync as _decision_loop_for_document_sync,
    general_path_next_intent as _general_path_next_intent,
)
from .scanner_net import (  # noqa: F401,E402 - re-export shim placed where the funcs were
    _ensure_tls_cert,
    _lan_host,
    _phone_scanner_external_status,
    _phone_scanner_url,
    _probe_scanner_url,
    _public_base_url,
    _scanner_autonomy_params,
    _scanner_page_url,
    _url_host,
    _write_qr_png,
)


def startup_phone_qr(project: str, db: str | None, *, scheme: str, host: str, port: int,
                     qr_url: str | None = None, content_prefix: str = "Phone scanner QR ready") -> dict:
    return _startup_phone_qr_impl(
        project, db,
        scheme=scheme, host=host, port=port, qr_url=qr_url, content_prefix=content_prefix,
        host_db_fn=_host_db,
        preview_url_fn=_preview_url,
        chat_message_fn=_chat_message,
        add_chat_message_fn=_add_chat_message,
    )


def ensure_phone_scanner_service(
    project: str,
    db: str | None,
    config: str | None = None,
    node_urls: list[str] | None = None,
    token: str | None = None,
    identity: str | None = None,
    *,
    host: str | None = None,
    port: int | None = None,
    tls_cert: str | None = None,
    tls_key: str | None = None,
) -> dict:
    return _ensure_phone_scanner_service_impl(
        project, db, config, node_urls, token, identity,
        host=host, port=port, tls_cert=tls_cert, tls_key=tls_key,
        serve_fn=serve,
        startup_phone_qr_fn=startup_phone_qr,
        host_db_fn=_host_db,
    )


def _latest_scanner_page_status(db: str | None) -> dict:
    try:
        logs = _host_db().recent_logs(db, stream="page-action", limit=80)
    except Exception:  # noqa: BLE001
        return {}
    return _latest_scanner_page_status_impl(logs)


def _artifact_meta_dict(artifact: dict) -> dict:
    m = artifact.get("meta")
    return m if isinstance(m, dict) else {}


def _artifact_display_path(meta: dict, path: str) -> str:
    return str(meta.get("displayImage") or meta.get("displayPath") or path)


def _artifact_any_file_exists(path: str, display_path: str) -> bool:
    return _artifact_file_exists(path) or _artifact_file_exists(display_path)


def _recent_scanner_artifacts(db: str | None, project: str, limit: int = 6) -> list[dict]:
    try:
        artifacts = _host_db().list_artifacts(db, limit=80)
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    cap = max(1, int(limit))
    for artifact in artifacts:
        kind = str(artifact.get("kind") or "")
        uri = str(artifact.get("uri") or "")
        meta = _artifact_meta_dict(artifact)
        if not _is_scanner_artifact_impl(kind, uri, meta):
            continue
        path = str(artifact.get("path") or "")
        display_path = _artifact_display_path(meta, path)
        if not _artifact_any_file_exists(path, display_path):
            continue
        doc = _scanner_artifact_doc_meta_impl(artifact)
        out.append(_scanner_artifact_item_impl(artifact, kind, uri, path, display_path, doc, project, preview_url=_preview_url))
        if len(out) >= cap:
            break
    return out


def service_live_views(project: str, db: str | None = None, limit: int = 8) -> dict:
    scanner = _scanner_live_state_impl(project, limit=limit, preview_url=_preview_url)
    service = next((item for item in _service_contacts() if item.get("id") == "service:phone-scanner"), {})
    recent_artifacts = _recent_scanner_artifacts(db, project, limit=6)
    camera_status = _latest_scanner_page_status(db)
    return _scanner_service_live_views_impl(
        scanner,
        service,
        recent_artifacts,
        camera_status,
        utc_now=_utc_now,
    )


def _scanner_bridge_deps() -> ScannerBridgeDeps:
    return ScannerBridgeDeps(
        preview_url=_preview_url,
        register_artifact=lambda db, kind, uri, path, meta: _host_db().register_artifact(
            db, kind, uri, path, meta
        ),
        chat_message=_chat_message,
        add_chat_message=_add_chat_message,
        add_log=lambda db, stream, event, detail: _host_db().add_log(db, stream, event, detail),
    )


def uri_event(db: str | None, query: dict) -> dict:
    """Thin wrapper: injects _scanner_bridge_deps() for tests that patch _host_db."""
    return _uri_event_impl(_scanner_bridge_deps(), db, query)


def _register_scanner_result(project: str, db: "str | None", **kwargs) -> dict:
    """Wrapper so tests can monkeypatch host_dashboard._register_scanner_result."""
    return _register_scanner_result_impl(_scanner_bridge_deps(), project, db, **kwargs)


def scanner_capture(project: str, db: str | None, payload: dict) -> dict:
    return _scanner_capture_impl(
        project, db, payload,
        deps=_scanner_bridge_deps(),
        archive_fn=_archive_scanned_document,
        local_image_ocr_fn=_local_image_ocr,
        extract_document_metadata_fn=_extract_document_metadata,
        truthy_env_fn=_truthy_env,
        auto_crop_receipt_fn=_auto_crop_receipt,
    )


def scanner_best_finish(project: str, db: str | None, payload: dict) -> dict:
    return _scanner_best_finish_impl(
        project, db, payload,
        deps=_scanner_bridge_deps(),
        archive_fn=_archive_scanned_document,
        local_image_ocr_fn=_local_image_ocr,
        truthy_env_fn=_truthy_env,
        auto_crop_receipt_fn=_auto_crop_receipt,
        extract_document_metadata_fn=_extract_document_metadata,
    )


def page_action_enqueue(
    db: str | None,
    *,
    target: str,
    uri: str,
    payload: dict | None = None,
    mode: str = "execute",
    source: str = "host",
) -> dict:
    return _page_action_enqueue_impl(
        _scanner_bridge_deps(),
        db,
        target=target,
        uri=uri,
        payload=payload,
        mode=mode,
        source=source,
        uri_mode=_uri_mode,
        utc_now=_utc_now,
    )


def _uri_action_lookup(uri: str) -> dict | None:
    for item in _uri_action_catalog():
        if item["uri"] == uri:
            return item
    aliases = {
        "scanner://host/capture": "scanner://host/capture/command/run",
        "scanner://host/best/finish": "scanner://host/best/command/finish",
        "scanner://host/session": "scanner://host/session/command/log",
        "scanner://page/start-button": "scanner://page/ui/button/start-camera/command/click",
        "scanner://page/torch": "scanner://page/camera/command/torch",
        "scanner://page/torch-button": "scanner://page/ui/button/torch/command/click",
        "dashboard://host/actions/query/list": "scanner://host/actions/query/list",
        **_service_lifecycle_aliases_impl("phone-scanner"),
        "scanner://host/service/command/restart": "dashboard://host/service/phone-scanner/command/restart",
        **_service_lifecycle_aliases_impl("chat"),
        **_service_lifecycle_aliases_impl("android-node"),
        "webpage://host/service/command/restart": "dashboard://host/service/android-node/command/restart",
        "document://host/archive/sync": "document://host/archive/command/sync-to-node",
        "api://host/node-api/command/request": "configured://host/node-api/command/request",
        "api://host/node-api/query/status": "configured://host/node-api/query/status",
    }
    target = aliases.get(uri)
    if target:
        return _uri_action_lookup(target)
    return None


def _uri_mode(value: Any) -> str:
    mode = str(value or "execute").strip().lower()
    if mode in {"execute", "exec", "run"}:
        return "execute"
    return "dry-run"


def _service_restart_argv(payload: dict, *, service: str, env_prefix: str, default_unit: str) -> tuple[list[str] | None, dict]:
    return _service_restart_argv_impl(
        payload,
        service=service,
        env_prefix=env_prefix,
        default_unit=default_unit,
    )


def restart_chat_service(
    payload: dict,
    *,
    project: str = ".",
    db: str | None = None,
    config: str | None = None,
    node_urls: list[str] | None = None,
    token: str | None = None,
    identity: str | None = None,
) -> dict:
    return _restart_chat_service_impl(
        payload,
        project=project,
        db=db,
        config=config,
        node_urls=node_urls,
        token=token,
        identity=identity,
    )


def restart_phone_scanner_service(
    project: str,
    db: str | None,
    config: str | None = None,
    node_urls: list[str] | None = None,
    token: str | None = None,
    identity: str | None = None,
    payload: dict | None = None,
) -> dict:
    return _restart_phone_scanner_service_impl(
        project, db, config, node_urls, token, identity, payload=payload,
        ensure_fn=ensure_phone_scanner_service,
        free_port_fn=_free_port_from_old_scanner,
        external_status_fn=_phone_scanner_external_status,
        service_restart_argv_fn=_service_restart_argv_impl,
        schedule_restart_fn=_schedule_restart_command_impl,
    )


def _uri_simulated_result(uri: str, mode: str, action_payload: dict, action: dict | None) -> dict:
    return {
        "ok": True,
        "uri": uri,
        "invokedUri": uri,
        "mode": mode,
        "simulated": True,
        "dryRun": True,
        "action": action or {"uri": uri},
        "payload": action_payload,
        "wouldRun": {
            "uri": uri,
            "layer": (action or {}).get("layer"),
            "kind": (action or {}).get("kind"),
            "sideEffects": (action or {}).get("sideEffects", []),
        },
    }


_INPROCESS_BINDINGS_GROUP = "urirun.bindings"


def _result_artifact_class(value: Any) -> str | None:
    """Classify a connector result via the shared ``urirun.tag`` contract.

    Returns ``"widget"`` for a live view (``live=True``), ``"artifact"`` for a frozen
    output (``live=False``), or ``None`` when the result is untagged (no ``live`` key) so
    the host falls back to its own taxonomy. This is how the host *consumes* the contract:
    a live view is routed to the chat-stream (never the frozen artifact store) and a frozen
    output to the artifact catalogue, by the connector's own declaration rather than a guess.
    """
    if not isinstance(value, dict) or "live" not in value:
        return None
    return "widget" if value.get("live") else "artifact"


def register_tagged_artifact(db: str | None, *, uri: str, result: Any, meta: dict | None = None) -> dict | None:
    """Route a tagged connector result per the shared ``urirun.tag`` contract.

    * frozen **artifact** (``live=False``) pointing at an on-disk ``path`` -> cataloged in
      the artifact store under its connector-declared ``kind``;
    * live **widget** (``live=True``) -> never stored (it is a self-updating view);
    * untagged result -> left alone (the host's own taxonomy decides elsewhere).

    Returns the registered artifact row, or ``None`` when nothing was stored. Best-effort:
    never raises, so a catalog hiccup cannot fail the connector call.
    """
    if _result_artifact_class(result) != "artifact":
        return None
    path = str((result or {}).get("path") or "")
    if not path or not Path(path).expanduser().is_file():
        return None
    kind = str(result.get("kind") or "artifact")
    try:
        return _host_db().register_artifact(db, kind, uri, path, meta or {})
    except Exception:  # noqa: BLE001
        return None


def _inprocess_result_value(env: dict, urirun_mod) -> object:
    try:
        return urirun_mod.result_data(env)
    except Exception:  # noqa: BLE001
        result = env.get("result")
        return result.get("value") if isinstance(result, dict) else None


def _inprocess_response(env: dict, uri: str, value: object, db: str | None) -> dict:
    result_val = value if value is not None else env.get("result")
    inner_failed = isinstance(result_val, dict) and result_val.get("ok") is False
    ok = bool(env.get("ok")) and not inner_failed
    registered = register_tagged_artifact(db, uri=uri, result=value) if ok and db else None
    err_msg = None
    if not ok:
        err_msg = (env.get("error") or {}).get("message")
        if err_msg is None and isinstance(result_val, dict):
            err_msg = result_val.get("error")
    return {"ok": ok, "invokedUri": uri, "result": result_val,
            "artifactClass": _result_artifact_class(value),
            "registeredArtifact": registered, "error": err_msg}


def _run_inprocess_connector_uri(uri: str, action_payload: dict, db: str | None = None) -> dict | None:
    """Execute an installed in-process connector URI (widget://, artifact://, …) through the
    urirun runtime and return its unwrapped handler value. Returns None when no connector owns
    the route, so :func:`uri_invoke` can fall back to its legacy "unsupported URI action" error.

    This is what lets the dashboard pull connector output over a URI request rather than baking
    it into the page — e.g. the chat-stream widgets are loaded from
    ``widget://host/bundle/query/js`` instead of being inlined in INDEX_HTML."""
    try:
        import urirun
        from urirun.runtime import discovery

        registry = discovery.registry_for_uri(uri, _INPROCESS_BINDINGS_GROUP)
        env = urirun.run(uri, registry, payload=dict(action_payload or {}),
                         mode="execute", policy={"allowExecute": True})
    except Exception as exc:  # noqa: BLE001 - a connector error must not crash the API
        return {"ok": False, "invokedUri": uri, "error": str(exc)}
    if not env.get("ok") and (env.get("error") or {}).get("category") == "NOT_FOUND":
        return None  # no connector owns this route → let the caller raise the legacy error
    # Route by the tag contract: a frozen artifact with a path gets cataloged under its
    # declared kind; a live widget never does. No-op for today's in-process traffic
    # (widget://, artifact:// registry/schema queries are untagged), correct for any
    # connector that returns a frozen file artifact over this path.
    return _inprocess_response(env, uri, _inprocess_result_value(env, urirun), db)


from .dispatch import make_local_dispatch_uri as _make_local_dispatch_uri


_UNROUTED = object()  # sentinel: _uri_invoke_route matched no built-in route (distinct from a handler returning None)

_SVC_PORT_MAP = {"phone-scanner": 8196, "chat": 8194, "android-node": 8195}
_SVC_IS_MAP: dict = {}  # populated lazily to avoid circular imports at module load


def _svc_port(name: str) -> int:
    env_key = f"URIRUN_{name.replace('-', '_').upper()}_PORT"
    return int(os.environ.get(env_key, str(_SVC_PORT_MAP[name])))


def _svc_is_map() -> dict:
    if not _SVC_IS_MAP:
        _SVC_IS_MAP.update({
            "phone-scanner": _is_scanner_process_impl,
            "chat": _is_chat_process_impl,
            "android-node": _is_android_node_process_impl,
        })
    return _SVC_IS_MAP


def _svc_start_fn(name: str, project: str, db, config, node_urls, token, identity, payload: dict):
    """Start a named host service (no-op if already running).
    Each service has its own start/ensure implementation; this dispatches to it."""
    if name == "phone-scanner":
        return ensure_phone_scanner_service(project, db, config,
                                            node_urls=node_urls, token=token, identity=identity)
    if name == "chat":
        return restart_chat_service(payload, project=project, db=db, config=config,
                                    node_urls=node_urls, token=token, identity=identity)
    if name == "android-node":
        return restart_android_node_service(payload)
    return {"ok": False, "service": name, "error": f"no start handler for service '{name}'"}


def _svc_restart_fn(name: str, project: str, db, config, node_urls, token, identity, payload: dict):
    """Restart a named host service (stop then start, regardless of running state)."""
    if name == "phone-scanner":
        return restart_phone_scanner_service(project, db, config,
                                             node_urls=node_urls, token=token, identity=identity,
                                             payload=payload)
    if name == "chat":
        return restart_chat_service(payload, project=project, db=db, config=config,
                                    node_urls=node_urls, token=token, identity=identity)
    if name == "android-node":
        return restart_android_node_service(payload)
    return {"ok": False, "service": name, "error": f"no restart handler for service '{name}'"}


def _service_lifecycle_dispatch(
    uri: str, project: str, db, config, node_urls, token, identity, payload: dict
):
    """Handle all four standard lifecycle verbs for the three named host services:
    query/status, command/start, command/stop, command/restart."""
    for svc in _SVC_PORT_MAP:
        if uri == f"dashboard://host/service/{svc}/query/status":
            status = _service_status_impl(_svc_port(svc), _svc_is_map()[svc])
            return {"ok": True, "service": svc, **status}
        if uri == f"dashboard://host/service/{svc}/command/stop":
            result = _stop_service_pids_impl(_svc_port(svc), _svc_is_map()[svc])
            return {"ok": True, "service": svc, **result}
        if uri == f"dashboard://host/service/{svc}/command/start":
            status = _service_status_impl(_svc_port(svc), _svc_is_map()[svc])
            if status["running"]:
                return {"ok": True, "service": svc, "started": False,
                        "detail": f"{svc} is already running", **status}
            return _svc_start_fn(svc, project, db, config, node_urls, token, identity, payload)
        if uri == f"dashboard://host/service/{svc}/command/restart":
            return _svc_restart_fn(svc, project, db, config, node_urls, token, identity, payload)
    return _UNROUTED


def _uri_invoke_route(effective_uri: str, *, project: str, db: str | None, config: str | None,
                      action_payload: dict, node_urls: list[str] | None, token: str | None,
                      identity: str | None):
    """Route a concrete (execute-mode, non-page) dashboard/scanner URI to its handler.

    Returns the handler's result, or None when the URI is not a built-in dashboard action (the
    caller then tries an in-process connector)."""
    if effective_uri in {"scanner://host/capture/command/run", "scanner://host/capture"}:
        return scanner_capture(project, db, action_payload)
    if effective_uri in {"scanner://host/best/command/finish", "scanner://host/best/finish"}:
        return scanner_best_finish(project, db, action_payload)
    if effective_uri in {"scanner://host/session/command/log", "scanner://host/session"}:
        return _scanner_session_impl(_scanner_bridge_deps(), db, action_payload)
    # Legacy alias: dashboard://host/phone-scanner/command/start → canonical start URI
    if effective_uri == "dashboard://host/phone-scanner/command/start":
        effective_uri = "dashboard://host/service/phone-scanner/command/start"
    lifecycle = _service_lifecycle_dispatch(effective_uri, project, db, config,
                                            node_urls, token, identity, action_payload)
    if lifecycle is not _UNROUTED:
        return lifecycle
    if effective_uri == "configured://host/node-api/command/request":
        return configured_node_api_request(config, node_urls, action_payload, uri=effective_uri)
    if effective_uri == "configured://host/node-api/query/status":
        return configured_node_api_request(config, node_urls, action_payload, uri=effective_uri, status_only=True)
    if effective_uri.startswith(("api://", "device://")):
        return configured_node_api_request(config, node_urls, action_payload, uri=effective_uri)
    if effective_uri in {"document://host/archive/command/sync-to-node", "document://host/archive/sync"}:
        return sync_documents_to_node(
            project,
            db,
            config,
            action_payload,
            node_urls=node_urls,
            token=token,
            identity=identity,
        )
    if effective_uri in {
        "document://host/archive/command/route-scans",
        "proc://fin/scanner/route/v1",
        "proc://fin/scanner/route-scan/v1",
    }:
        return _route_scanned_documents_impl(action_payload)
    return _UNROUTED


def _uri_invoke_page_action(uri: str, mode: str, payload: dict, action_payload: dict, db: str | None) -> dict:
    """Enqueue a `layer=page` URI action for the scanner page (or reject if it must run locally)."""
    source = str(payload.get("source") or action_payload.get("source") or "").strip().lower()
    if source in {"page", "scanner-page"}:
        raise ValueError(f"page URI action must be handled locally by the scanner page: {uri}")
    return page_action_enqueue(
        db,
        target=str(action_payload.get("target") or "scanner"),
        uri=uri,
        payload=action_payload,
        mode=mode,
        source=str(payload.get("source") or "uri-invoke"),
    )


def _finalize_uri_result(result, uri: str) -> dict:
    """Annotate a handler result with invokedUri and the urirun.tag artifact class."""
    if isinstance(result, dict):
        result.setdefault("invokedUri", uri)
        # Surface the artifact/widget class when the result carries the urirun.tag contract.
        tag_class = _result_artifact_class(result)
        if tag_class is not None:
            result.setdefault("artifactClass", tag_class)
        return result
    return {"ok": True, "invokedUri": uri, "result": result}


def _uri_invoke_fallback(effective_uri: str, uri: str, *, config: str | None,
                         node_urls: list[str] | None, action_payload: dict, db: str | None) -> dict:
    """Unrouted URI: try an in-process connector, then a configured node-API, else raise."""
    dispatched = _run_inprocess_connector_uri(effective_uri, action_payload, db=db)
    if dispatched is not None:
        return dispatched
    if effective_uri.startswith(("media://", "camera://", "ssh://", "fs://")):
        configured = configured_node_api_request(config, node_urls, action_payload, uri=effective_uri)
        if configured.get("error") not in {"node is required", "configured API not found"}:
            return _finalize_uri_result(configured, uri)
    raise ValueError(f"unsupported URI action: {uri}")


def _uri_action_payload(payload: dict) -> dict:
    p = payload.get("payload")
    return p if isinstance(p, dict) else {}


def _uri_effective(action: dict | None, uri: str) -> str:
    return str(action.get("uri") if action else uri)


def _uri_mode_from_payload(payload: dict, action_payload: dict) -> str:
    return _uri_mode(payload.get("mode") or payload.get("runMode") or action_payload.get("mode"))


def uri_invoke(
    project: str,
    db: str | None,
    config: str | None,
    payload: dict,
    *,
    node_urls: list[str] | None = None,
    token: str | None = None,
    identity: str | None = None,
) -> dict:
    uri = str(payload.get("uri") or "").strip()
    action_payload = _uri_action_payload(payload)
    mode = _uri_mode_from_payload(payload, action_payload)
    if not uri:
        raise ValueError("uri is required")
    action = _uri_action_lookup(uri)
    effective_uri = _uri_effective(action, uri)

    if uri in {"scanner://host/actions/query/list", "dashboard://host/actions/query/list"}:
        return {"ok": True, "mode": mode, "actions": _uri_action_catalog(), "invokedUri": uri}

    if mode != "execute":
        return _uri_simulated_result(uri, mode, action_payload, action)

    if action and action.get("layer") == "page":
        return _uri_invoke_page_action(uri, mode, payload, action_payload, db)

    result = _uri_invoke_route(
        effective_uri, project=project, db=db, config=config, action_payload=action_payload,
        node_urls=node_urls, token=token, identity=identity,
    )
    if result is _UNROUTED:
        return _uri_invoke_fallback(effective_uri, uri, config=config, node_urls=node_urls,
                                    action_payload=action_payload, db=db)
    return _finalize_uri_result(result, uri)


from .dashboard_api import (  # noqa: E402,F401
    _first,
    _host_db,
    _mesh,
    _planfile_adapter,
    _host_config,
    _safe_tickets,
    _task_counts,
    _lan_qr_profile,
    chat_history,
    chat_delete_messages,
    task_create,
    _api_summary,
    _api_objects,
    _api_node_types,
    _api_tasks,
    _api_checks,
    _api_logs,
    _api_artifacts,
    _api_chat_history,
    _api_services_live,
    _api_scanner_live,
    _api_nodes_or_routes,
    _api_twin_flows,
    _api_twin_state,
    _API_ROUTES,
    _dashboard_api_response,
)


def _service_contacts() -> list[dict]:
    scanner_port = int(os.environ.get("URIRUN_PHONE_SCANNER_PORT", "8196"))
    scanner_state = _phone_scanner_external_status(scanner_port)
    service_entries: list[dict] = []
    with _SERVICE_LOCK:
        for service_id, server in _SERVICE_SERVERS.items():
            thread = _SERVICE_THREADS.get(service_id)
            service_entries.append({
                "service_id": service_id,
                "alive": bool(thread is not None and thread.is_alive()),
                "server_name": getattr(server, "server_name", ""),
            })
    return _service_contacts_impl(
        scanner_port=scanner_port,
        scanner_state=scanner_state,
        service_entries=service_entries,
        phone_scanner_url=_phone_scanner_url,
        phone_scanner_status=_phone_scanner_external_status,
    )


def _summary_infra(
    config: str | None, node_urls: list[str] | None, db: str | None
) -> tuple:
    """Initialise host_db and mesh, run discovery; return infrastructure objects and resolved paths."""
    host_db = _host_db()
    mesh = _mesh()
    try:
        discovered = mesh.discover_mesh(_host_config(config, node_urls))
    except Exception as exc:  # noqa: BLE001
        discovered = {"nodes": [], "routes": [], "serviceMap": {}, "error": str(exc)}
    db_path = str(host_db.db_path(db))
    config_path = str(mesh.host_config_path(config))
    return host_db, discovered, db_path, config_path


def _summary_task_data(project: str) -> tuple:
    """Load sprint tickets; return (task_error, task_counts, ticket_count)."""
    tickets, task_error = _safe_tickets(project, sprint="all")
    return task_error, _task_counts(tickets), len(tickets)


def _summary_db_records(host_db, db: str | None, project: str) -> tuple:
    """Fetch recent checks, artifacts and logs from the host DB."""
    checks = host_db.recent_checks(db, limit=10)
    artifacts = _public_artifacts(host_db.list_artifacts(db, limit=10), project)
    logs = host_db.recent_logs(db, limit=10)
    return checks, artifacts, logs


def _summary_annotated_nodes(discovered: dict) -> list:
    """Extract nodes from discovery result and apply all annotation passes."""
    nodes = discovered.get("nodes") or []
    _annotate_node_tokens_impl(nodes, _node_token_for)
    _annotate_node_kinds(nodes)
    _annotate_node_types_impl(nodes)
    _merge_live_webpage_nodes(nodes)
    return nodes


def _summary_host_data(project: str, nodes: list, services: list, routes: list) -> tuple:
    """Build host route list, host object and URI objects dict."""
    host_routes = _host_registry_routes_impl(_uri_action_catalog())
    host_routes.extend(_local_entry_point_host_routes_impl())
    host = _host_object_impl(project, host_routes)
    objects = _uri_objects_impl(
        project=project,
        host_routes=host_routes,
        nodes=nodes,
        services=services,
        routes=routes,
    )
    return host_routes, host, objects


def summary(project: str, db: str | None, config: str | None, node_urls: list[str] | None = None) -> dict:
    host_db, discovered, db_path, config_path = _summary_infra(config, node_urls, db)
    task_error, task_counts, ticket_count = _summary_task_data(project)
    checks, artifacts, logs = _summary_db_records(host_db, db, project)
    nodes = _summary_annotated_nodes(discovered)
    routes = discovered.get("routes") or []
    services = _service_contacts()
    host_routes, host, objects = _summary_host_data(project, nodes, services, routes)
    return {
        "ok": True,
        "project": str(Path(project).expanduser().resolve()),
        "db": db_path,
        "config": config_path,
        "taskError": task_error,
        "taskCounts": task_counts,
        "ticketCount": ticket_count,
        "nodeCount": len(nodes),
        "nodesOnline": len([node for node in nodes if node.get("reachable")]),
        "routeCount": len(routes),
        "serviceCount": len(services),
        "host": host,
        "hostRoutes": host_routes,
        "lan": _lan_qr_profile(),
        "nodeTypes": _node_type_profiles_impl(),
        "objects": objects,
        "nodes": nodes,
        "services": services,
        "routes": routes,
        "checks": checks,
        "artifacts": artifacts,
        "logs": logs,
    }

# POST-path sub-handlers and auxiliary functions extracted to _dashboard_post_handlers.
from ._dashboard_post_handlers import (  # noqa: E402
    node_add, node_remove,
    _safe_api, _configured_api_status_response, configured_node_api_request,
    restart_android_node_service, _merge_live_webpage_nodes, phone_node_qr,
    _node_envelope_error, _probe_node_token, node_set_token, chat_ask, task_action,
    connector_test, documents_reconcile,
    _handle_get, _handle_post_connectors, _handle_post_nodes, _handle_post_scanner,
    _handle_post_chat, _handle_post_tasks, _handle_post_artifacts, _handle_post,
)

def create_handler(
    project: str,
    db: str | None = None,
    config: str | None = None,
    node_urls: list[str] | None = None,
    token: str | None = None,
    identity: str | None = None,
):
    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self):
            _json_response(self, 200, {"ok": True})

        def do_GET(self):
            if getattr(self.server, "urirun_scheme", "http") == "https" and not isinstance(self.connection, ssl.SSLSocket):
                host_hdr = self.headers.get("Host") or f"127.0.0.1:{self.server.server_address[1]}"
                self.send_response(302)
                self.send_header("Location", f"https://{host_hdr}{self.path}")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                return
            parsed = urlparse(self.path)
            try:
                _handle_get(self, parsed, project, db, config, node_urls, token, identity)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            except Exception as exc:  # noqa: BLE001
                import traceback
                print(f"[scanner-http-error] GET {self.path} failed: {exc}\n{traceback.format_exc()}", flush=True)
                try:
                    _json_response(self, 500, {"ok": False, "error": str(exc)})
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return

        def do_POST(self):
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            try:
                _handle_post(self, parsed, parts, project, db, config, node_urls, token, identity)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            except Exception as exc:  # noqa: BLE001
                import traceback
                print(f"[scanner-http-error] POST {self.path} failed: {exc}\n{traceback.format_exc()}", flush=True)
                try:
                    _json_response(self, 400, {"ok": False, "error": str(exc)})
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return

        def log_message(self, fmt, *args: Any):
            msg = fmt % args
            # Log all requests except repetitive noise
            if ("GET /api/scanner/live" in msg or "GET /api/page/actions/poll" in msg) and "200" in msg:
                return
            print(f"[scanner-http] {self.address_string()} {msg}", flush=True)

    return Handler


def serve(
    project: str = ".",
    db: str | None = None,
    config: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8194,
    node_urls: list[str] | None = None,
    token: str | None = None,
    identity: str | None = None,
    tls_cert: str | None = None,
    tls_key: str | None = None,
    startup_qr: bool = False,
    qr_url: str | None = None,
) -> ThreadingHTTPServer:
    # Load `<project>/.env` so the in-process NL planner sees LLM_MODEL / OPENROUTER_API_KEY
    # without the launcher having to `set -a; . .env`. The real environment always wins
    # (the file never clobbers an already-set variable), mirroring `host ask`.
    from urirun.host.env_loader import load_project_env
    loaded_env = load_project_env(project)
    if loaded_env:
        print(json.dumps({
            "event": "urirun.host_dashboard.dotenv_loaded",
            "keys": sorted(loaded_env),
        }), flush=True)
    # Starting a new dashboard auto-replaces the old one holding this port (parent+worker), so
    # `serve` never dies on "Address already in use".
    _free_port_from_old_dashboard(int(port))
    server = ThreadingHTTPServer((host, port), create_handler(project, db=db, config=config, node_urls=node_urls,
                                                             token=token, identity=identity))
    scheme = "http"
    if tls_cert and tls_key:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(os.path.expanduser(tls_cert), os.path.expanduser(tls_key))
        scheme = "https"
        orig_get_request = server.get_request

        def secure_get_request():
            sock, addr = orig_get_request()
            try:
                sock.settimeout(3.0)
                peek = sock.recv(1, socket.MSG_PEEK)
                if peek == b"\x16":
                    tls_sock = context.wrap_socket(sock, server_side=True)
                    tls_sock.settimeout(None)
                    return tls_sock, addr
                else:
                    sock.settimeout(None)
                    return sock, addr
            except Exception:
                try:
                    sock.close()
                except Exception:
                    pass
                raise

        server.get_request = secure_get_request
    setattr(server, "urirun_scheme", scheme)
    qr = startup_phone_qr(project, db, scheme=scheme, host=host, port=server.server_address[1], qr_url=qr_url) if startup_qr else None
    print(json.dumps({
        "event": "urirun.host_dashboard.started",
        "url": f"{scheme}://{host}:{server.server_address[1]}/",
        "qrUrl": qr.get("url") if qr else None,
        "project": str(Path(project).resolve()),
    }), flush=True)
    # Periodic QR panel in the shell: scannable QR for the LAN URL (phone) + clickable local URL.
    # On with --startup-qr or URIRUN_QR_SHELL=1; cadence via URIRUN_QR_INTERVAL (default 300s).
    if startup_qr or str(os.environ.get("URIRUN_QR_SHELL", "")).strip().lower() in ("1", "true", "yes", "on"):
        try:
            from urirun._shell_qr import start_qr_panel
            start_qr_panel(scheme, int(server.server_address[1]), local_host=host or "127.0.0.1")
        except Exception:  # noqa: BLE001 - the QR panel is a convenience; never block serving
            pass
    _warn_stale_local_deps()
    return server


def _warn_stale_local_deps() -> None:
    """Surface dependency drift at EVERY service start (best-effort, never fatal).

    Stale local installs bite silently — the service runs old library code while the
    repo shows new sources (the recurring "works in repo, old behavior in service"
    class). Emitting the doctor's live verdicts here makes every restart an audit:
    the operator sees SHADOW/STALE lines in the startup log the moment drift appears,
    instead of days later mid-debug. Fix: scripts/dev_deps_doctor.py --fix."""
    try:
        import importlib.util as _ilu  # noqa: PLC0415
        doctor_path = Path(__file__).resolve().parents[2] / "scripts" / "dev_deps_doctor.py"
        if not doctor_path.is_file():
            return
        spec = _ilu.spec_from_file_location("_dev_deps_doctor", doctor_path)
        doctor = _ilu.module_from_spec(spec)
        spec.loader.exec_module(doctor)
        site = doctor._site_packages()
        if site is None:
            return
        stale = [h for h in doctor.local_install_health(site)
                 if h["verdict"].startswith("STALE")]
        owners = doctor.editable_owners(site)
        shadowed = []
        for name, owner in owners.items():
            resolved = doctor.resolve(name)
            if resolved is not None and resolved.resolve() != owner.resolve():
                shadowed.append({"name": name, "owner": str(owner), "loads": str(resolved)})
        if stale or shadowed:
            print(json.dumps({
                "event": "urirun.host_dashboard.stale_local_deps",
                "stale": stale,
                "shadowed": shadowed,
                "fix": "venv/bin/python adapters/python/scripts/dev_deps_doctor.py --fix",
            }), flush=True)
    except Exception:  # noqa: BLE001 - startup audit must never block serving
        pass


def command(args) -> int:
    if args.dashboard_command == "serve":
        host = args.host or "127.0.0.1"
        port = int(args.port or 8194)
        startup_qr = bool(getattr(args, "startup_qr", False)) and not bool(getattr(args, "no_startup_qr", False))
        server = serve(project=args.project, db=args.db, config=args.config, host=host, port=port,
                       node_urls=getattr(args, "node_url", None),
                       token=getattr(args, "token", None) or os.environ.get("URIRUN_NODE_TOKEN"),
                       identity=getattr(args, "identity", None),
                       tls_cert=getattr(args, "tls_cert", None),
                       tls_key=getattr(args, "tls_key", None),
                       startup_qr=startup_qr,
                       qr_url=getattr(args, "qr_url", None))
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            return 130
        return 0
    if args.dashboard_command == "url":
        host = args.host or "127.0.0.1"
        port = int(args.port or 8194)
        print(f"http://{host}:{port}/")
        return 0
    return 1


def default_host() -> str:
    return socket.gethostbyname(socket.gethostname())
