from __future__ import annotations

try:
    # prefer the separately-installed package (dev/connector installs)
    from urirun_connector_scanner.document_sync import *  # noqa: F401, F403
    from urirun_connector_scanner.document_sync import (  # noqa: F401
        _DOCUMENT_INDEX_LOCK,
        _transaction_fingerprint,
        _fingerprint_match_count,
    )
except ImportError:
    # Bundled fallback — same implementation, kept in sync with urirun-connector-scanner
    import io
    import textwrap
    import struct
    import base64
    import json
    import hashlib
    import os
    import re
    import threading
    import time
    import unicodedata
    from dataclasses import dataclass
    from pathlib import Path
    from typing import Any, Callable
    from urllib.parse import quote

    _DOCUMENT_INDEX_LOCK = threading.Lock()

    _DEFAULT_MISSING_LIMIT = 50


    def _verification_check(name: str, *, ok: bool, expected: int, actual: int, **meta: Any) -> dict:
        row: dict[str, Any] = {"check": name, "ok": bool(ok), "expected": int(expected), "actual": int(actual)}
        row.update({k: v for k, v in meta.items() if v is not None})
        return row


    def file_transfer_verification(
        *,
        contract: str,
        expected: list[str],
        uploaded: list[str],
        verified: list[str],
        mode: str,
        missing_limit: int = _DEFAULT_MISSING_LIMIT,
    ) -> dict:
        """Verification contract for file-copy style URI flows (moved from contracts.py)."""
        expected_set = list(expected)
        uploaded_set = set(uploaded)
        verified_set = set(verified)
        missing = [rel for rel in expected_set if rel not in verified_set]
        checks = [
            _verification_check("write_ack_for_every_expected_file",
                                ok=len(uploaded_set) == len(expected_set),
                                expected=len(expected_set), actual=len(uploaded_set)),
            _verification_check("sha256_verified_for_every_expected_file",
                                ok=len(verified_set) == len(expected_set),
                                expected=len(expected_set), actual=len(verified_set), mode=mode),
        ]
        return {
            "contract": contract, "ok": all(c["ok"] for c in checks), "mode": mode,
            "expectedFiles": len(expected_set), "uploadedFiles": len(uploaded_set),
            "verifiedFiles": len(verified_set), "failedFiles": len(missing),
            "missing": missing[:missing_limit],
            "truncatedMissing": max(0, len(missing) - missing_limit),
            "checks": checks,
        }


    # --------------------------------------------------------------------------- #
    # Configuration — reads env vars that locate the document archive and sync defaults
    # --------------------------------------------------------------------------- #

    def document_archive_root() -> Path:
        return Path(os.environ.get("URIRUN_DOCUMENT_DIR", "~/.urirun/documents")).expanduser().resolve()


    def document_index_path() -> Path:
        configured = os.environ.get("URIRUN_DOCUMENT_INDEX")
        return Path(configured).expanduser().resolve() if configured else document_archive_root() / "index.json"


    def document_sync_default_dest_root() -> str:
        return os.environ.get("URIRUN_DOCUMENT_SYNC_DEST", "~/Downloads/urirun-scans")


    def document_sync_default_node() -> str:
        return os.environ.get("URIRUN_DOCUMENT_SYNC_NODE", "").strip()


    # --------------------------------------------------------------------------- #
    # Pure utilities — no host_dashboard dependencies
    # --------------------------------------------------------------------------- #

    def archive_month(extracted: dict) -> str:
        """The YYYY-MM archive bucket from the document's date, or the current month."""
        if re.match(r"^20\d{2}-\d{2}", str(extracted.get("date", ""))):
            return str(extracted["date"])[:7]
        return time.strftime("%Y-%m", time.gmtime())


    def pdf_text(value: Any) -> str:
        text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
        text = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        return text


    def pdf_stream(data: bytes) -> bytes:
        return b"<< /Length " + str(len(data)).encode("ascii") + b" >>\nstream\n" + data + b"\nendstream"


    def document_files_exist(item: dict) -> bool:
        """True if the document still has at least one on-disk artifact (PDF or JSON sidecar)."""
        for key in ("pdfPath", "path", "jsonPath"):
            value = item.get(key)
            if value and Path(str(value)).expanduser().is_file():
                return True
        return False


    # --------------------------------------------------------------------------- #
    # Document filename / schema utilities
    # --------------------------------------------------------------------------- #

    def filename_part(value: str, *, default: str, max_len: int = 48) -> str:
        folded = unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")
        folded = re.sub(r"[^A-Za-z0-9._+-]+", "-", folded).strip(".-_").lower()
        folded = re.sub(r"-{2,}", "-", folded)
        return (folded or default)[:max_len].strip(".-_") or default


    def canonical_document_filename(meta: dict) -> str:
        doc_type = filename_part(str(meta.get("type") or ""), default="dokument", max_len=18)
        doc_date = filename_part(str(meta.get("date") or ""), default=time.strftime("%Y-%m-%d", time.gmtime()), max_len=10)
        contractor = filename_part(str(meta.get("contractor") or ""), default="kontrahent-nieznany", max_len=42)
        amount = str(meta.get("amount") or "").strip()
        currency = str(meta.get("currency") or "").strip().upper()
        amount_part = f"{amount}-{currency}" if amount and currency else amount or "kwota-nieznana"
        amount_part = filename_part(amount_part, default="kwota-nieznana", max_len=24)
        return f"{doc_type}_{doc_date}_{contractor}_{amount_part}.pdf"


    def document_filename_with_id(filename: str, doc_id: str) -> str:
        path = Path(filename)
        doc_part = filename_part(doc_id, default="doc-id", max_len=36)
        if doc_part and doc_part in path.stem:
            return filename
        return f"{path.stem}_{doc_part}{path.suffix or '.pdf'}"


    def artifact_schema_known(type_id: str) -> bool | None:
        """Whether ``type_id`` matches a registered urirun-artifacts schema id.

        Returns None when the registry is not installed (validation skipped).
        """
        normalized = str(type_id or "").strip().lower()
        if not normalized:
            return None
        try:
            import urirun_artifacts  # noqa: F401
            from urirun_artifacts import registry
            known = {str(i).strip().lower() for i in registry.all_ids()}
        except Exception:  # noqa: BLE001
            return None
        return normalized in known


    def document_schema_fields(doc_type: str) -> dict:
        known = artifact_schema_known(doc_type)
        return {
            "schemaKnown": known,
            "schemaId": str(doc_type or "").strip().lower() if known else None,
        }


    def needs_screen_document_capture(prompt: str) -> bool:
        text_value = prompt.casefold()
        wants_screen = any(word in text_value for word in ("zrzut", "screenshot", "screen capture", "zrzuty ekranu"))
        wants_document = any(word in text_value for word in ("pdf", "dokument", "document", "faktur", "rachunek", "paragon"))
        return wants_screen and wants_document

    DOCUMENT_SYNC_URI = "document://host/archive/command/sync-to-node"

    _DEFAULT_SYNC_TIMEOUT = 120.0
    _MAX_FILE_BYTES = 25_000_000  # 25 MB read-back verification ceiling
    _UPLOAD_TIMEOUT_S = 30.0      # preflight route-check per-request timeout cap


    def truthy_env(name: str, default: str = "0") -> bool:
        return str(os.environ.get(name, default)).strip().lower() in {"1", "true", "yes", "on"}


    def document_sync_auto_retry_enabled(payload: dict) -> bool:
        for key in ("autoRetry", "auto_retry", "autoRepair", "auto_repair"):
            if key in payload:
                return boolish(payload.get(key), default=True)
        return truthy_env("URIRUN_DOCUMENT_SYNC_AUTO_RETRY", "1")


    def _urifix_auto_retry(urifix: dict) -> bool:
        diagnosis = urifix.get("diagnosis") if isinstance(urifix.get("diagnosis"), dict) else {}
        if urifix.get("repaired") or diagnosis.get("canAutoRetry"):
            return True
        return any(bool(item.get("automatic")) for item in (urifix.get("recovery") or []) if isinstance(item, dict))


    def _validated_sync_retry_payload(retry: dict, sync_node: str) -> dict | None:
        if str(retry.get("uri") or "") != DOCUMENT_SYNC_URI:
            return None
        if str(retry.get("mode") or "").casefold() != "execute":
            return None
        retry_payload = retry.get("payload")
        if not isinstance(retry_payload, dict):
            return None
        node_url = str(retry_payload.get("node_url") or retry_payload.get("nodeUrl") or "").strip()
        if not node_url:
            return None
        retry_node = str(retry_payload.get("node") or retry_payload.get("targetNode") or sync_node).strip()
        if sync_node and retry_node and retry_node != sync_node:
            return None
        return dict(retry_payload)


    def document_sync_retry_payload_from_urifix(urifix: dict | None, *, sync_node: str) -> dict | None:
        if not isinstance(urifix, dict):
            return None
        if not _urifix_auto_retry(urifix):
            return None
        retry = urifix.get("retry")
        if not isinstance(retry, dict):
            return None
        return _validated_sync_retry_payload(retry, sync_node)


    def document_sync_dest_from_prompt(prompt: str) -> str:
        text_value = prompt.casefold()
        if "download" in text_value or "pobrane" in text_value:
            return os.environ.get("URIRUN_DOCUMENT_SYNC_DEST", "~/Downloads/urirun-scans")
        return document_sync_default_dest_root()


    @dataclass(frozen=True)
    class DocumentSyncDeps:
        document_archive_root: Callable[[], str | Path]
        default_node: Callable[[], str]
        default_dest_root: Callable[[], str]
        node_url_from_config: Callable[[str | None, list[str] | None, str], str | None]
        archive_pdfs: Callable[[Path], list[Path]]
        verification: Callable[[list[Path], list[dict], Path, bool], dict]
        ensure_node_uri_routes: Callable[..., dict]
        run_node_uri: Callable[..., dict]
        compact_remote_run: Callable[[dict], dict]
        remote_write_error: Callable[..., str]
        remote_read_error: Callable[..., str]
        utc_now: Callable[[], str]
        host_db: Callable[[], Any]
        chat_message: Callable[..., dict]
        add_chat_message: Callable[[str | None, dict], Any]


    def boolish(value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().casefold() not in {"", "0", "false", "no", "off"}


    def document_archive_pdfs(root: Path) -> list[Path]:
        if not root.is_dir():
            return []
        return sorted(
            path for path in root.glob("*/*.pdf")
            if path.is_file() and path.parent.name != "no_invoice"
        )


    def document_sync_verification(
        files: list[Path],
        results: list[dict],
        *,
        source_root: Path,
        read_back: bool,
    ) -> dict:
        expected = [path.relative_to(source_root).as_posix() for path in files]
        uploaded = [item["relativePath"] for item in results if item.get("writeOk")]
        verified = [item["relativePath"] for item in results if item.get("verified")]
        mode = "read-back-sha256" if read_back else "write-ack-sha256"
        return file_transfer_verification(
            contract="document-sync.v1",
            expected=expected,
            uploaded=uploaded,
            verified=verified,
            mode=mode,
        )


    def _log_and_chat_report(
        db: str | None,
        deps: DocumentSyncDeps,
        report: dict,
        *,
        node: str,
        content: str,
    ) -> dict:
        try:
            deps.host_db().add_log(db, "document-sync", "sync-to-node", report)
        except Exception:
            pass
        message = deps.chat_message(
            "system",
            content,
            detail={
                **report,
                "selectedTargets": ["host", f"node:{node}"],
            },
        )
        deps.add_chat_message(db, message)
        report["message"] = message
        return report


    @dataclass(frozen=True)
    class _SyncParams:
        source_root: Path
        node: str
        node_url: str
        dest_root: str
        overwrite: bool
        make_dirs: bool
        timeout: float
        fs_uri: str
        fs_read_uri: str
        read_back: bool
        verify_max_bytes: int
        ensure_routes: bool
        connector_roots: Any


    def _resolve_node_params(
        payload: dict,
        config: str | None,
        deps: DocumentSyncDeps,
        node_urls: list[str] | None,
    ) -> tuple[str, str]:
        """Resolve and validate the (node, node_url) pair from payload + host config."""
        node = str(payload.get("node") or payload.get("targetNode") or deps.default_node()).strip()
        if not node:
            raise ValueError("node is required: pass payload.node, select a node target, or set URIRUN_DOCUMENT_SYNC_NODE")
        node_url = str(payload.get("node_url") or payload.get("nodeUrl") or "").strip()
        if not node_url:
            node_url = deps.node_url_from_config(config, node_urls, node) or ""
        if not node_url:
            raise ValueError("node_url is required when the target node is not present in host config")
        return node, node_url


    def _parse_sync_params(
        payload: dict,
        config: str | None,
        deps: DocumentSyncDeps,
        node_urls: list[str] | None,
    ) -> _SyncParams:
        source_root = Path(
            payload.get("source_root") or payload.get("sourceRoot") or deps.document_archive_root()
        ).expanduser().resolve()
        node, node_url = _resolve_node_params(payload, config, deps, node_urls)
        fs_target = str(payload.get("fs_target") or payload.get("fsTarget") or "host").strip() or "host"
        return _build_sync_params(payload, deps, source_root=source_root, node=node,
                                  node_url=node_url, fs_target=fs_target)


    def _build_sync_params(payload: dict, deps: DocumentSyncDeps, *, source_root: Path, node: str,
                           node_url: str, fs_target: str) -> _SyncParams:
        """Assemble the _SyncParams from the resolved required fields plus optional payload settings."""
        return _SyncParams(
            source_root=source_root,
            node=node,
            node_url=node_url.rstrip("/"),
            dest_root=str(payload.get("dest_root") or payload.get("destRoot") or deps.default_dest_root()).rstrip("/"),
            overwrite=bool(payload.get("overwrite", True)),
            make_dirs=bool(payload.get("make_dirs", payload.get("makeDirs", True))),
            timeout=float(payload.get("timeout", _DEFAULT_SYNC_TIMEOUT) or _DEFAULT_SYNC_TIMEOUT),
            fs_uri=f"fs://{fs_target}/file/command/write-b64",
            fs_read_uri=f"fs://{fs_target}/file/query/read-b64",
            read_back=boolish(payload.get("verify_read_back", payload.get("verifyReadBack", payload.get("verify"))), True),
            verify_max_bytes=int(payload.get("verify_max_bytes") or payload.get("verifyMaxBytes") or _MAX_FILE_BYTES),
            ensure_routes=boolish(payload.get("ensure_routes", payload.get("ensureRoutes")), True),
            connector_roots=payload.get("connector_roots", payload.get("connectorRoots", payload.get("roots"))),
        )


    def _check_preflight(
        params: _SyncParams,
        files: list[Path],
        deps: DocumentSyncDeps,
        token: str | None,
        identity: str | None,
    ) -> tuple[dict | None, dict | None, str | None]:
        """Check that the remote node exposes required fs routes before transferring files."""
        if not params.ensure_routes or not files:
            return None, None, None
        required_routes = [params.fs_uri, params.fs_read_uri] if params.read_back else [params.fs_uri]
        try:
            preflight: dict = deps.ensure_node_uri_routes(
                params.node_url,
                required_routes,
                node=params.node,
                token=token,
                identity=identity,
                timeout=min(params.timeout, _UPLOAD_TIMEOUT_S),
                roots=params.connector_roots,
            )
        except Exception as exc:  # noqa: BLE001
            preflight = {"ok": False, "error": str(exc), "requiredRoutes": required_routes}
        if preflight.get("ok"):
            return preflight, None, None
        missing = preflight.get("missingAfter") or preflight.get("missingBefore") or required_routes
        preflight_error = (
            "remote node is missing required fs transfer route(s): "
            f"{', '.join(str(r) for r in missing)}"
        )
        if preflight.get("error"):
            preflight_error += f" ({preflight['error']})"
        verification = deps.verification(files, [], params.source_root, params.read_back)
        report = {
            "ok": False,
            "uri": "document://host/archive/command/sync-to-node",
            "sourceRoot": str(params.source_root),
            "node": params.node,
            "nodeUrl": params.node_url,
            "fsUri": params.fs_uri,
            "fsReadUri": params.fs_read_uri,
            "destRoot": params.dest_root,
            "total": len(files),
            "uploaded": 0,
            "copied": 0,
            "failed": len(files),
            "skipped": 0,
            "failedReasons": {preflight_error: len(files)},
            "verification": verification,
            "preflight": preflight,
            "results": [],
            "updatedAt": deps.utc_now(),
        }
        content = (
            f"Document sync to {params.node} blocked: 0/{len(files)} PDFs"
            f" -> {params.dest_root} ({preflight_error})"
        )
        return preflight, report, content


    def _upload_file(
        source: Path,
        params: _SyncParams,
        deps: DocumentSyncDeps,
        token: str | None,
        identity: str | None,
    ) -> dict:
        """Upload one file to the remote node; returns a result item dict."""
        rel = source.relative_to(params.source_root)
        dest_path = f"{params.dest_root}/{rel.as_posix()}"
        data = source.read_bytes()
        sha256 = hashlib.sha256(data).hexdigest()
        item: dict = {
            "source": str(source),
            "relativePath": rel.as_posix(),
            "dest": dest_path,
            "bytes": len(data),
            "sha256": sha256,
        }
        try:
            run = deps.run_node_uri(
                params.node_url,
                params.fs_uri,
                {
                    "path": dest_path,
                    "bytes_b64": base64.b64encode(data).decode("ascii"),
                    "overwrite": params.overwrite,
                    "make_dirs": params.make_dirs,
                },
                token=token,
                identity=identity,
                timeout=params.timeout,
            )
            value = run.get("value") if isinstance(run.get("value"), dict) else {}
            remote_sha = value.get("sha256")
            write_ok = bool(run.get("ok") and value.get("ok", True) and remote_sha == sha256)
            item.update({
                "ok": write_ok,
                "writeOk": write_ok,
                "verified": False,
                "remotePath": value.get("path"),
                "remoteSha256": remote_sha,
                "overwritten": value.get("overwritten"),
                "renamed": value.get("renamed"),
            })
            if not write_ok:
                item["remote"] = deps.compact_remote_run(run)
                item["error"] = deps.remote_write_error(run, run.get("value"), expected_sha=sha256, remote_sha=remote_sha)
        except Exception as exc:  # noqa: BLE001
            item.update({"ok": False, "writeOk": False, "verified": False, "error": str(exc)})
        return item


    def _read_back_file(
        item: dict,
        params: _SyncParams,
        deps: DocumentSyncDeps,
        token: str | None,
        identity: str | None,
    ) -> None:
        """Verify one uploaded file by reading it back from the node. Mutates item in place."""
        remote_path = str(item.get("remotePath") or item.get("dest") or "")
        try:
            run = deps.run_node_uri(
                params.node_url,
                params.fs_read_uri,
                {
                    "path": remote_path,
                    "max_bytes": max(params.verify_max_bytes, int(item.get("bytes") or 0)),
                },
                token=token,
                identity=identity,
                timeout=params.timeout,
            )
            value = run.get("value") if isinstance(run.get("value"), dict) else {}
            read_sha = value.get("sha256")
            read_bytes = value.get("bytes")
            verified = bool(
                run.get("ok")
                and value.get("ok", True)
                and read_sha == item.get("sha256")
                and (read_bytes in (None, item.get("bytes")))
            )
            item.update({
                "ok": verified,
                "verified": verified,
                "readBackPath": value.get("path"),
                "readBackSha256": read_sha,
                "readBackBytes": read_bytes,
            })
            if not verified:
                item["readBack"] = deps.compact_remote_run(run)
                item["error"] = deps.remote_read_error(
                    run,
                    run.get("value"),
                    expected_sha=str(item.get("sha256") or ""),
                    remote_sha=read_sha,
                )
        except Exception as exc:  # noqa: BLE001
            item.update({"ok": False, "verified": False, "error": str(exc)})


    def sync_documents_to_node(
        project: str,
        db: str | None,
        config: str | None,
        payload: dict,
        *,
        deps: DocumentSyncDeps,
        node_urls: list[str] | None = None,
        token: str | None = None,
        identity: str | None = None,
    ) -> dict:
        params = _parse_sync_params(payload, config, deps, node_urls)
        files = deps.archive_pdfs(params.source_root)

        preflight, early_report, early_content = _check_preflight(params, files, deps, token, identity)
        if early_report is not None:
            return _log_and_chat_report(db, deps, early_report, node=params.node, content=early_content)  # type: ignore[arg-type]

        results: list[dict] = []
        uploaded = 0
        failed_reasons: dict[str, int] = {}
        for source in files:
            item = _upload_file(source, params, deps, token, identity)
            if item.get("writeOk"):
                uploaded += 1
            elif "error" in item:
                failed_reasons[item["error"]] = failed_reasons.get(item["error"], 0) + 1
            results.append(item)

        copied = 0
        for item in results:
            if not item.get("writeOk"):
                continue
            if not params.read_back:
                item["verified"] = True
                copied += 1
                continue
            _read_back_file(item, params, deps, token, identity)
            if item.get("verified"):
                copied += 1
            elif "error" in item:
                failed_reasons[item["error"]] = failed_reasons.get(item["error"], 0) + 1

        verification = deps.verification(files, results, params.source_root, params.read_back)
        failed = len(files) - copied
        report = {
            "ok": bool(verification.get("ok")),
            "uri": "document://host/archive/command/sync-to-node",
            "sourceRoot": str(params.source_root),
            "node": params.node,
            "nodeUrl": params.node_url,
            "fsUri": params.fs_uri,
            "fsReadUri": params.fs_read_uri,
            "destRoot": params.dest_root,
            "total": len(files),
            "uploaded": uploaded,
            "copied": copied,
            "failed": failed,
            "skipped": 0,
            "failedReasons": failed_reasons,
            "verification": verification,
            "preflight": preflight,
            "results": results,
            "updatedAt": deps.utc_now(),
        }
        status = "completed" if report["ok"] else "finished with errors"
        top_reason = max(failed_reasons.items(), key=lambda kv: kv[1])[0] if failed_reasons else ""
        reason_suffix = f" ({top_reason})" if top_reason else ""
        content = f"Document sync to {params.node} {status}: {copied}/{len(files)} PDFs -> {params.dest_root}{reason_suffix}"
        return _log_and_chat_report(db, deps, report, node=params.node, content=content)


    def scanned_id_log_path() -> Path:
        configured = os.environ.get("URIRUN_SCANNED_ID_LOG")
        return Path(configured).expanduser().resolve() if configured else document_archive_root() / "scanned.id.jsonl"


    def load_document_index() -> dict:
        path = document_index_path()
        if not path.is_file():
            return {"version": 1, "documents": []}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {"version": 1, "documents": []}
        if not isinstance(data, dict):
            return {"version": 1, "documents": []}
        docs = data.get("documents")
        if not isinstance(docs, list):
            data["documents"] = []
        data.setdefault("version", 1)
        return data


    def save_document_index(index: dict) -> None:
        path = document_index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        index["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)


    def prune_orphaned_documents(index: dict) -> list[dict]:
        """Drop index entries whose PDF and JSON sidecar are both gone from disk."""
        docs = index.get("documents")
        if not isinstance(docs, list):
            return []
        kept: list[dict] = []
        pruned: list[dict] = []
        for item in docs:
            if isinstance(item, dict) and not document_files_exist(item):
                pruned.append(item)
            else:
                kept.append(item)
        if pruned:
            index["documents"] = kept
        return pruned


    def iter_scanned_id_log() -> list[dict]:
        path = scanned_id_log_path()
        if not path.is_file():
            return []
        out: list[dict] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    out.append(item)
        except Exception:  # noqa: BLE001
            return out
        return out


    def append_scanned_id_log(entry: dict) -> None:
        path = scanned_id_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


    def existing_scanned_id(*, doc_id: str, source_sha256: str, text_sha256: str) -> dict | None:
        duplicate: dict | None = None
        for item in iter_scanned_id_log():
            same_doc = bool(doc_id and item.get("docId") == doc_id)
            same_source = bool(source_sha256 and item.get("sourceSha256") == source_sha256)
            same_text = bool(text_sha256 and item.get("textSha256") == text_sha256)
            if same_doc or same_source or same_text:
                duplicate = item
        return duplicate


    def scanned_log_entry(item: dict) -> dict:
        """Build a scanned-id-log entry from an existing document-index record."""
        pdf_path = str(item.get("pdfPath") or item.get("path") or "")
        return {
            "version": 1,
            "event": "indexed",
            "scannedAt": item.get("createdAt") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "docId": str(item.get("docId") or "").strip(),
            "docIdProvider": item.get("docIdProvider"),
            "docIdSource": item.get("docIdSource"),
            "duplicate": False,
            "uri": item.get("uri"),
            "pdfPath": pdf_path,
            "jsonPath": item.get("jsonPath"),
            "fileName": Path(pdf_path).name if pdf_path else "",
            "originalPath": item.get("originalPath"),
            "cropPath": item.get("cropPath"),
            "sourceSha256": str(item.get("sourceSha256") or "").strip(),
            "textSha256": str(item.get("textSha256") or "").strip(),
            "ocrBackend": item.get("ocrBackend"),
            "ocrChars": item.get("ocrChars"),
            "metadata": {
                "type": item.get("type"),
                "date": item.get("date"),
                "contractor": item.get("contractor"),
                "amount": item.get("amount"),
                "currency": item.get("currency"),
            },
        }


    def scanned_entry_seen(entry: dict, seen: dict[str, set[str]]) -> bool:
        """True when any of the entry's identity keys is already in the seen-bucket of that key."""
        return any(entry[key] and entry[key] in bucket for key, bucket in seen.items())


    def scanned_seen_buckets(existing: list[dict]) -> dict[str, set[str]]:
        """Index the existing scanned-id log by each identity key for O(1) duplicate checks."""
        return {
            "docId": {str(i.get("docId") or "") for i in existing if i.get("docId")},
            "sourceSha256": {str(i.get("sourceSha256") or "") for i in existing if i.get("sourceSha256")},
            "textSha256": {str(i.get("textSha256") or "") for i in existing if i.get("textSha256")},
        }


    def backfill_scanned_id_log(index: dict) -> None:
        docs = [item for item in index.get("documents", []) if isinstance(item, dict)]
        if not docs:
            return
        seen = scanned_seen_buckets(iter_scanned_id_log())
        for item in docs:
            entry = scanned_log_entry(item)
            if scanned_entry_seen(entry, seen):
                continue
            append_scanned_id_log(entry)
            for key, bucket in seen.items():
                if entry[key]:
                    bucket.add(entry[key])


    def write_document_pdf(image_path: str | Path, pdf_path: str | Path, *, metadata: dict, ocr_text: str) -> None:
        from PIL import Image, ImageOps

        source = Path(image_path).expanduser().resolve()
        target = Path(pdf_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened).convert("RGB")
            try:
                from urirun_connector_smart_crop import orient_document_image

                image, _orientation = orient_document_image(image, auto_orient=True, prefer_portrait=True)
            except Exception:  # noqa: BLE001
                pass
            image_bytes = io.BytesIO()
            image.save(image_bytes, format="JPEG", quality=92, optimize=True)
            jpeg = image_bytes.getvalue()
            image_width, image_height = image.size

        page_width = 595.0
        page_height = 842.0
        margin = 36.0
        scale = min((page_width - margin * 2) / image_width, (page_height - margin * 2) / image_height)
        draw_width = image_width * scale
        draw_height = image_height * scale
        draw_x = (page_width - draw_width) / 2.0
        draw_y = (page_height - draw_height) / 2.0
        image_content = f"q {draw_width:.2f} 0 0 {draw_height:.2f} {draw_x:.2f} {draw_y:.2f} cm /Im0 Do Q".encode("ascii")

        header_lines = [
            f"Document ID: {metadata.get('docId', '')}",
            f"Type: {metadata.get('type', '')}",
            f"Date: {metadata.get('date', '')}",
            f"Contractor: {metadata.get('contractor', '')}",
            f"Amount: {metadata.get('amount', '')} {metadata.get('currency', '')}".strip(),
            f"Source: {metadata.get('sourcePath', '')}",
            "",
            "OCR text:",
        ]
        text_lines = header_lines
        for paragraph in (ocr_text or "").splitlines():
            if not paragraph.strip():
                text_lines.append("")
                continue
            text_lines.extend(textwrap.wrap(paragraph.strip(), width=92) or [""])
        text_lines = text_lines[:66]
        ops = ["BT /F1 10 Tf 12 TL 44 792 Td"]
        for line in text_lines:
            ops.append(f"({pdf_text(line)}) Tj T*")
        ops.append("ET")
        text_content = "\n".join(ops).encode("ascii", "ignore")

        info = (
            f"<< /Title ({pdf_text(target.stem)}) "
            f"/Creator ({pdf_text('urirun host dashboard')}) "
            f"/Subject ({pdf_text(metadata.get('docId', ''))}) "
            f"/Keywords ({pdf_text('urirun,ocr,document,' + str(metadata.get('type', '')))}) >>"
        ).encode("ascii", "ignore")

        objects: list[bytes] = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R 7 0 R] /Count 2 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>",
            pdf_stream(image_content),
            (
                b"<< /Type /XObject /Subtype /Image /Width "
                + str(image_width).encode("ascii")
                + b" /Height "
                + str(image_height).encode("ascii")
                + b" /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length "
                + str(len(jpeg)).encode("ascii")
                + b" >>\nstream\n"
                + jpeg
                + b"\nendstream"
            ),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 6 0 R >> >> /Contents 8 0 R >>",
            pdf_stream(text_content),
            info,
        ]

        pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for idx, body in enumerate(objects, start=1):
            offsets.append(len(pdf))
            pdf.extend(f"{idx} 0 obj\n".encode("ascii"))
            pdf.extend(body)
            pdf.extend(b"\nendobj\n")
        xref_offset = len(pdf)
        pdf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
        for offset in offsets[1:]:
            pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        pdf.extend(
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 9 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
        )
        target.write_bytes(bytes(pdf))


    def sidecar_text(item: dict) -> str:
        """OCR text for an archived record, read from its JSON sidecar (the index omits it)."""
        json_path = item.get("jsonPath")
        if not json_path:
            return ""
        try:
            path = Path(str(json_path)).expanduser()
            if not path.is_file():
                return ""
            data = json.loads(path.read_text(encoding="utf-8"))
            return str(data.get("text") or "") if isinstance(data, dict) else ""
        except Exception:  # noqa: BLE001
            return ""


    def unique_document_path(directory: Path, filename: str, doc_id: str) -> Path:
        candidate = directory / filename
        if not candidate.exists():
            return candidate
        suffix = filename_part(doc_id[-10:], default="doc", max_len=12)
        alternative = directory / f"{candidate.stem}_{suffix}{candidate.suffix}"
        counter = 2
        while alternative.exists():
            alternative = directory / f"{candidate.stem}_{suffix}-{counter}{candidate.suffix}"
            counter += 1
        return alternative


    def existing_document(index: dict, *, doc_id: str, source_sha256: str, text_sha256: str) -> dict | None:
        for item in index.get("documents", []):
            if not isinstance(item, dict):
                continue
            same_doc = item.get("docId") == doc_id
            same_source = bool(source_sha256 and item.get("sourceSha256") == source_sha256)
            same_text = bool(text_sha256 and item.get("textSha256") == text_sha256)
            if same_doc or same_source or same_text:
                return item
        return None


    def existing_document_meta(duplicate: dict) -> dict:
        """The duplicate record's metadata dict, or a flat projection of its top-level fields."""
        if isinstance(duplicate.get("metadata"), dict):
            return duplicate["metadata"]
        return {key: duplicate.get(key) for key in ("type", "date", "contractor", "amount", "currency")}


    # --------------------------------------------------------------------------- #
    # Metadata merge helpers (moved from host_dashboard to keep archive logic here) #
    # --------------------------------------------------------------------------- #

    try:
        from docid.visual_fingerprint import FieldSource as _DocidFieldSource
        from docid.visual_fingerprint import merge_records as _docid_merge_records
    except Exception:  # noqa: BLE001
        _DocidFieldSource = None
        _docid_merge_records = None

    MERGE_METADATA_FIELDS = ("type", "date", "contractor", "amount", "currency")
    BLANK_METADATA_MARKERS: frozenset[str] = frozenset({
        "", "kwota-nieznana", "nieznana", "unknown", "n/a", "-", "kontrahent-nieznany"
    })


    def is_blank_metadata(value: Any) -> bool:
        return str(value or "").strip().lower() in BLANK_METADATA_MARKERS


    def merge_metadata_fields(old_meta: dict | None, new_meta: dict, *,
                              old_weight: float, new_weight: float) -> tuple[dict, list[str]]:
        """Fuse two scans of the same document into one best-of-both record."""
        old_meta = old_meta or {}
        try:
            if _DocidFieldSource is None or _docid_merge_records is None:
                raise RuntimeError("docid.visual_fingerprint unavailable")

            result = _docid_merge_records(
                [
                    _DocidFieldSource(fields={k: old_meta.get(k) for k in MERGE_METADATA_FIELDS},
                                      weight=max(old_weight, 0.0001), label="archived"),
                    _DocidFieldSource(fields={k: new_meta.get(k) for k in MERGE_METADATA_FIELDS},
                                      weight=max(new_weight, 0.0001), label="rescan"),
                ],
                fields=list(MERGE_METADATA_FIELDS),
            )
            merged = dict(new_meta)
            for key in MERGE_METADATA_FIELDS:
                value = result["fields"].get(key)
                if not is_blank_metadata(value):
                    merged[key] = value
            return merged, list(result.get("filledGaps") or [])
        except Exception:  # noqa: BLE001
            merged = dict(new_meta)
            filled: list[str] = []
            for key in MERGE_METADATA_FIELDS:
                if is_blank_metadata(merged.get(key)) and not is_blank_metadata(old_meta.get(key)):
                    merged[key] = old_meta.get(key)
                    filled.append(key)
            return merged, filled


    def enrich_archived_record(existing: dict, fused: dict, enriched_fields: list[str]) -> None:
        """Backfill an already-archived record with fields a re-scan recognized."""
        for key in enriched_fields:
            value = fused.get(key)
            if not is_blank_metadata(value):
                existing[key] = value
        existing["enrichedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        history = existing.get("enrichedFields")
        history = list(history) if isinstance(history, list) else []
        for key in enriched_fields:
            if key not in history:
                history.append(key)
        existing["enrichedFields"] = history

        json_path = existing.get("jsonPath")
        if not json_path:
            return
        try:
            jpath = Path(str(json_path)).expanduser()
            data = json.loads(jpath.read_text(encoding="utf-8")) if jpath.is_file() else {}
            if not isinstance(data, dict):
                return
            for key in enriched_fields:
                value = fused.get(key)
                if not is_blank_metadata(value):
                    data[key] = value
            data["enrichedAt"] = existing["enrichedAt"]
            data["enrichedFields"] = history
            jpath.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass


    # --------------------------------------------------------------------------- #
    # Dedup dispatch: document identity & matching                                 #
    # --------------------------------------------------------------------------- #

    try:
        from docid.dedup import (
            business_key as _dedup_business_key,
            dhash_distance as _dedup_dhash_distance,
            document_id as _dedup_document_id,
            document_matches as _dedup_document_matches,
            fingerprint_match_count as _dedup_fingerprint_match_count,
            image_dhash as _dedup_image_dhash,
            image_phash as _dedup_image_phash,
            metadata_completeness as _dedup_metadata_completeness,
            transaction_fingerprint as _dedup_transaction_fingerprint,
        )
    except Exception:  # noqa: BLE001
        _dedup_business_key = None
        _dedup_dhash_distance = None
        _dedup_document_id = None
        _dedup_document_matches = None
        _dedup_fingerprint_match_count = None
        _dedup_image_dhash = None
        _dedup_image_phash = None
        _dedup_metadata_completeness = None
        _dedup_transaction_fingerprint = None

    if _dedup_document_matches is not None:
        _document_matches = _dedup_document_matches
        _business_key = _dedup_business_key
        _metadata_completeness = _dedup_metadata_completeness
        _transaction_fingerprint = _dedup_transaction_fingerprint
        _fingerprint_match_count = _dedup_fingerprint_match_count
        _image_dhash = _dedup_image_dhash
        _image_phash = _dedup_image_phash
        _dhash_distance = _dedup_dhash_distance
    else:
        def _document_matches(existing: dict, *, doc_id: str, source_sha256: str, text_sha256: str,
                              fingerprint: dict, dhash: str, phash: str = "",
                              metadata: dict | None = None, text: str = "") -> str:
            if doc_id and existing.get("docId") == doc_id:
                return "docId"
            if source_sha256 and existing.get("sourceSha256") == source_sha256:
                return "sourceSha256"
            if text_sha256 and existing.get("textSha256") == text_sha256:
                return "textSha256"
            return ""

        def _business_key(meta: dict | None):
            return None

        def _metadata_completeness(meta: dict | None) -> int:
            return 0

        def _transaction_fingerprint(text: str) -> dict:
            return {}

        def _fingerprint_match_count(a: "dict | None", b: "dict | None") -> int:
            return 0

        def _image_dhash(path: "str | Path") -> str:
            return ""

        def _image_phash(path: "str | Path") -> str:
            return ""

        def _dhash_distance(a: str, b: str) -> int:
            return 999


    def file_sha256(path: "str | Path") -> str:
        digest = hashlib.sha256()
        with Path(path).expanduser().resolve().open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()


    def docid_for_file(path: "str | Path", ocr_text: str) -> dict:
        if _dedup_document_id is not None:
            try:
                from .document_metadata import normalized_document_text as _normalized_doc_text
            except Exception:  # noqa: BLE001
                _normalized_doc_text = None
            norm = _normalized_doc_text(ocr_text) if _normalized_doc_text else ocr_text
            return _dedup_document_id(path, ocr_text, normalized_text=norm)

        docid_error = ""
        docid_log = ""
        try:
            import contextlib
            import io as _io
            from docid import get_document_id  # type: ignore

            log_buffer = _io.StringIO()
            with contextlib.redirect_stdout(log_buffer), contextlib.redirect_stderr(log_buffer):
                value = str(get_document_id(str(Path(path).expanduser().resolve())) or "").strip()
            docid_log = log_buffer.getvalue().strip()
            if value:
                result = {"id": value, "provider": "docid", "source": "get_document_id"}
                if docid_log:
                    result["docidLog"] = docid_log[:240]
                return result
        except Exception as exc:  # noqa: BLE001
            docid_error = str(exc)

        try:
            from .document_metadata import normalized_document_text as _normalized_doc_text2
            normalized = _normalized_doc_text2(ocr_text)
        except Exception:  # noqa: BLE001
            normalized = ocr_text
        if len(normalized) >= 24:
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            source = "ocr-text"
        else:
            digest = file_sha256(path)
            source = "file-sha256"
        result = {"id": f"LOCAL-DOC-{digest[:16].upper()}", "provider": "local-fallback", "source": source}
        if docid_error:
            result["docidError"] = docid_error[:240]
        if docid_log:
            result["docidLog"] = docid_log[:240]
        return result


    def find_duplicate_document(index: dict, *, doc_id: str, source_sha256: str, text_sha256: str,
                                fingerprint: dict, dhash: str, phash: str = "",
                                metadata: dict | None = None, text: str = "") -> "dict | None":
        """Find an already-archived document that is the same as the incoming scan."""
        match: "dict | None" = None
        cand_key = _business_key(metadata) if (metadata and _business_key) else None
        for item in index.get("documents", []):
            if not isinstance(item, dict):
                continue
            existing = item
            if cand_key is not None and not item.get("text") and _business_key(item) == cand_key:
                existing = {**item, "text": sidecar_text(item)}
            reason = _document_matches(
                existing, doc_id=doc_id, source_sha256=source_sha256, text_sha256=text_sha256,
                fingerprint=fingerprint, dhash=dhash, phash=phash, metadata=metadata, text=text,
            )
            if reason:
                match = {**item, "_matchReason": reason}
        return match


    def metadata_completeness(meta: "dict | None") -> int:
        return _metadata_completeness(meta)


    def _cleanup_scan_files_lazy(paths: list) -> list[str]:
        """Lazy import of cleanup_duplicate_scan_files from scanner_bridge (avoids circular import)."""
        try:
            from .scanner_bridge import cleanup_duplicate_scan_files  # noqa: PLC0415
            return cleanup_duplicate_scan_files(paths)
        except Exception:  # noqa: BLE001
            return []


    def archive_redundant_duplicate(*, duplicate: dict, index_match: "dict | None", existing_meta: dict,
                                    extracted: dict, new_completeness: float, index: dict,
                                    docid_info: dict, doc_id: str, original_path: "Path",
                                    display_path: "Path", source_sha256: str, text_sha256: str,
                                    fingerprint: Any, dhash: Any, phash: Any) -> dict:
        """The new scan matches an already-archived document and is NOT more complete."""
        duplicate_path = duplicate.get("pdfPath") or duplicate.get("path")
        enriched_fields: list[str] = []
        if index_match is not None:
            fused, enriched_fields = merge_metadata_fields(
                existing_meta, extracted,
                old_weight=float(_metadata_completeness(existing_meta)) + 0.5,
                new_weight=float(new_completeness),
            )
            if enriched_fields:
                enrich_archived_record(duplicate, fused, enriched_fields)
                save_document_index(index)
        removed_scan_files = _cleanup_scan_files_lazy([original_path, display_path])
        duplicate_entry = {
            "version": 1,
            "event": "duplicate",
            "scannedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "docId": doc_id,
            "docIdProvider": docid_info.get("provider"),
            "docIdSource": docid_info.get("source"),
            "duplicate": True,
            "duplicateOf": duplicate.get("docId") or doc_id,
            "matchReason": duplicate.get("_matchReason") or "exact",
            "enrichedFields": enriched_fields or None,
            "pdfPath": duplicate_path,
            "jsonPath": duplicate.get("jsonPath"),
            "fileName": Path(str(duplicate_path)).name if duplicate_path else "",
            "existingFileExists": bool(duplicate_path and Path(str(duplicate_path)).expanduser().is_file()),
            "originalPath": str(original_path),
            "cropPath": str(display_path),
            "removedScanFiles": removed_scan_files,
            "sourceSha256": source_sha256,
            "textSha256": text_sha256,
            "fingerprint": fingerprint,
            "dhash": dhash,
            "phash": phash,
            "metadata": extracted,
        }
        append_scanned_id_log(duplicate_entry)
        return {
            "ok": True,
            "duplicate": True,
            "docId": doc_id,
            "docIdProvider": docid_info.get("provider"),
            "path": duplicate_path,
            "jsonPath": duplicate.get("jsonPath"),
            "duplicateOf": duplicate_entry["duplicateOf"],
            "matchReason": duplicate_entry["matchReason"],
            "enrichedFields": enriched_fields or None,
            "existingFileExists": duplicate_entry["existingFileExists"],
            "removedScanFiles": removed_scan_files,
            "metadata": extracted,
            "indexPath": str(document_index_path()),
            "scannedIdLogPath": str(scanned_id_log_path()),
        }


    def supersede_archived_document(*, duplicate: dict, existing_meta: dict, extracted: dict,
                                    new_completeness: float, root: "Path", month: str, doc_id: str,
                                    index: dict) -> tuple:
        """Supersede an archived document with a better scan."""
        extracted, merged_fields = merge_metadata_fields(
            existing_meta, extracted,
            old_weight=float(_metadata_completeness(existing_meta)),
            new_weight=float(new_completeness),
        )
        month = str(extracted["date"])[:7] if re.match(r"^20\d{2}-\d{2}", str(extracted.get("date", ""))) else month
        archive_dir = root / month
        filename = document_filename_with_id(canonical_document_filename(extracted), doc_id)
        superseded_of = duplicate.get("docId")
        _cleanup_scan_files_lazy([duplicate.get("originalPath"), duplicate.get("cropPath")])
        for stale in (duplicate.get("pdfPath") or duplicate.get("path"), duplicate.get("jsonPath")):
            try:
                if stale and Path(str(stale)).expanduser().is_file():
                    Path(str(stale)).expanduser().unlink()
            except OSError:
                pass
        index["documents"] = [
            item for item in index.get("documents", [])
            if isinstance(item, dict) and item.get("docId") != superseded_of
        ]
        return extracted, month, archive_dir, filename, superseded_of, merged_fields


    def archive_scanned_document(
        *,
        display_path: "Path",
        original_path: "Path",
        ocr: dict,
        crop: dict,
        source_sha256: str,
        captured_at: "str | None",
        metadata: "dict | None" = None,
        docid_fn: "Callable | None" = None,
    ) -> dict:
        from .document_metadata import _extract_document_metadata  # noqa: PLC0415
        ocr_text = str(ocr.get("text") or "")
        extracted = metadata if metadata is not None else _extract_document_metadata(
            ocr_text, captured_at=captured_at, image_path=str(original_path))
        docid_info = (docid_fn or docid_for_file)(display_path, ocr_text)
        doc_id = str(docid_info["id"])
        from .document_metadata import normalized_document_text as _normalized_doc_text3  # noqa: PLC0415
        normalized_text = _normalized_doc_text3(ocr_text)
        text_sha256 = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest() if normalized_text else ""
        fingerprint = _transaction_fingerprint(ocr_text)
        dhash = _image_dhash(display_path)
        phash = _image_phash(display_path)
        new_completeness = _metadata_completeness(extracted)
        month = archive_month(extracted)
        root = document_archive_root()
        archive_dir = root / month
        filename = document_filename_with_id(canonical_document_filename(extracted), doc_id)

        with _DOCUMENT_INDEX_LOCK:
            index = load_document_index()
            backfill_scanned_id_log(index)
            index_match = find_duplicate_document(
                index, doc_id=doc_id, source_sha256=source_sha256, text_sha256=text_sha256,
                fingerprint=fingerprint, dhash=dhash, phash=phash,
                metadata=extracted, text=ocr_text,
            )
            duplicate = index_match or existing_scanned_id(
                doc_id=doc_id, source_sha256=source_sha256, text_sha256=text_sha256,
            )
            superseded_of = None
            merged_fields: list[str] = []
            if duplicate:
                existing_meta = existing_document_meta(duplicate)
                can_supersede = index_match is not None and new_completeness > _metadata_completeness(existing_meta)
                if not can_supersede:
                    return archive_redundant_duplicate(
                        duplicate=duplicate, index_match=index_match, existing_meta=existing_meta,
                        extracted=extracted, new_completeness=new_completeness, index=index,
                        docid_info=docid_info, doc_id=doc_id, original_path=original_path,
                        display_path=display_path, source_sha256=source_sha256, text_sha256=text_sha256,
                        fingerprint=fingerprint, dhash=dhash, phash=phash,
                    )
                extracted, month, archive_dir, filename, superseded_of, merged_fields = supersede_archived_document(
                    duplicate=duplicate, existing_meta=existing_meta, extracted=extracted,
                    new_completeness=new_completeness, root=root, month=month, doc_id=doc_id, index=index,
                )

            archive_dir.mkdir(parents=True, exist_ok=True)
            pdf_path = unique_document_path(archive_dir, filename, doc_id)
            json_path = pdf_path.with_suffix(".json")
            pdf_meta = {
                **extracted,
                "docId": doc_id,
                "sourcePath": str(original_path),
                "cropPath": str(display_path),
            }
            write_document_pdf(display_path, pdf_path, metadata=pdf_meta, ocr_text=ocr_text)
            _schema_fields = document_schema_fields(extracted.get("type"))
            entry = {
                "docId": doc_id,
                "docIdProvider": docid_info.get("provider"),
                "docIdSource": docid_info.get("source"),
                "docIdError": docid_info.get("docidError"),
                "docIdLog": docid_info.get("docidLog"),
                "uri": f"document://host/{quote(doc_id, safe='')}",
                "pdfPath": str(pdf_path),
                "jsonPath": str(json_path),
                "originalPath": str(original_path),
                "cropPath": str(display_path),
                "sourceSha256": source_sha256,
                "textSha256": text_sha256,
                "fingerprint": fingerprint,
                "dhash": dhash,
                "phash": phash,
                "supersededOf": superseded_of,
                "mergedFields": merged_fields or None,
                "ocrBackend": ocr.get("backend"),
                "ocrChars": ocr.get("chars"),
                "text": ocr_text,
                "crop": crop,
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "schemaKnown": _schema_fields["schemaKnown"],
                "schemaId": _schema_fields["schemaId"],
                **extracted,
            }
            json_path.write_text(
                json.dumps({**entry, "ocr": ocr, "text": ocr_text}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            docs = [item for item in index.get("documents", []) if isinstance(item, dict) and item.get("docId") != doc_id]
            docs.append(entry)
            index["documents"] = docs
            save_document_index(index)
            append_scanned_id_log({
                "version": 1,
                "event": "superseded" if superseded_of else "scan",
                "scannedAt": entry["createdAt"],
                "docId": doc_id,
                "docIdProvider": docid_info.get("provider"),
                "docIdSource": docid_info.get("source"),
                "docIdError": docid_info.get("docidError"),
                "docIdLog": docid_info.get("docidLog"),
                "duplicate": False,
                "supersededOf": superseded_of,
                "mergedFields": merged_fields or None,
                "uri": entry["uri"],
                "pdfPath": str(pdf_path),
                "jsonPath": str(json_path),
                "fileName": pdf_path.name,
                "originalPath": str(original_path),
                "cropPath": str(display_path),
                "sourceSha256": source_sha256,
                "textSha256": text_sha256,
                "fingerprint": fingerprint,
                "dhash": dhash,
                "phash": phash,
                "ocrBackend": ocr.get("backend"),
                "ocrChars": ocr.get("chars"),
                "metadata": extracted,
            })
            remote_sync_result = None
            if not os.environ.get("PYTEST_CURRENT_TEST"):
                try:
                    target_node, target_url, target_token = resolve_node_endpoint("lenovo")
                    if target_url:
                        date_val = str(extracted.get("date") or "")
                        m_per = re.search(r"(\d{4})[-.](\d{2})", date_val)
                        period_dot = f"{m_per.group(1)}.{m_per.group(2)}" if m_per else time.strftime("%Y.%m")
                        doc_type = str(extracted.get("type") or "paragon").lower()
                        subfolder = "koszty" if doc_type in ("paragon", "rachunek", "faktura") else "inne"
                        remote_root = os.environ.get("URIRUN_REMOTE_DEST_ROOT", "~/Documents/Faktury")
                        remote_dest = f"{remote_root.rstrip('/')}/{period_dot}/{subfolder}"
                        res_pdf = upload_document_to_node(target_url, f"{remote_dest}/{pdf_path.name}", pdf_path.read_bytes(), token=target_token)
                        if json_path.is_file():
                            upload_document_to_node(target_url, f"{remote_dest}/{json_path.name}", json_path.read_bytes(), token=target_token)
                        remote_sync_result = {"node": target_node, "dest": remote_dest, "pdf": res_pdf.get("ok", False)}
                except Exception as sync_exc:
                    remote_sync_result = {"error": str(sync_exc)}
        return {
            "ok": True,
            "duplicate": False,
            "superseded": bool(superseded_of),
            "supersededOf": superseded_of,
            "docId": doc_id,
            "docIdProvider": docid_info.get("provider"),
            "path": str(pdf_path),
            "jsonPath": str(json_path),
            "uri": entry["uri"],
            "metadata": extracted,
            "remoteSync": remote_sync_result,
            "indexPath": str(document_index_path()),
            "scannedIdLogPath": str(scanned_id_log_path()),
        }


    def reconcile_document_index() -> dict:
        """Reconcile the document index with the filesystem by pruning orphaned entries."""
        with _DOCUMENT_INDEX_LOCK:
            index = load_document_index()
            before = len(index.get("documents", []))
            pruned = prune_orphaned_documents(index)
            if pruned:
                save_document_index(index)
        return {
            "ok": True,
            "indexPath": str(document_index_path()),
            "before": before,
            "after": before - len(pruned),
            "prunedCount": len(pruned),
            "pruned": [
                {"docId": p.get("docId"), "pdfPath": p.get("pdfPath"), "jsonPath": p.get("jsonPath")}
                for p in pruned
            ],
        }
