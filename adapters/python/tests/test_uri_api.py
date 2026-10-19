import json
import urirun


def _demo_registry() -> dict:
    return {
        "plesk": {
            "site_command": lambda target, action, payload, _inv: {
                "ok": True,
                "target": target,
                "action": action,
                "domain": payload.get("domain"),
                "source": payload.get("source"),
            }
        }
    }


def test_uri_single_dict_payload():
    reg = _demo_registry()
    res = urirun.uri(
        "plesk://host/site/command/sync",
        {"domain": "heyasa.com", "source": "./dist"},
        registry=reg,
    )
    assert res == {
        "ok": True,
        "target": "host",
        "action": "sync",
        "domain": "heyasa.com",
        "source": "./dist",
    }


def test_uri_single_inline_string():
    reg = _demo_registry()
    res = urirun.uri(
        'plesk://host/site/command/sync {"domain":"get.heyasa.com","source":"./dist"}',
        registry=reg,
    )
    assert res["ok"] is True
    assert res["domain"] == "get.heyasa.com"


def test_uri_batch_jsonl():
    reg = _demo_registry()
    jsonl_text = (
        '{"domain":"heyasa.com","source":"./dist"}\n'
        '{"domain":"get.heyasa.com","source":"./dist"}\n'
    )
    res = urirun.uri("plesk://host/site/command/sync", jsonl_text, registry=reg)
    assert isinstance(res, list)
    assert len(res) == 2
    assert [item["domain"] for item in res] == ["heyasa.com", "get.heyasa.com"]


def test_uri_cli_dry_run(capsys, monkeypatch):
    reg = urirun.compile_registry(
        {
            "version": "urirun.bindings.v2",
            "bindings": urirun.tool_binding(
                "demo://host/site/command/sync",
                ["echo", "{domain}"],
                {"domain": {"type": "string"}},
                required=["domain"],
            ),
        }
    )
    monkeypatch.setattr(urirun, "entry_point_registry", lambda *a, **kw: reg)
    rc = urirun.uri_cli(
        [
            "demo://host/site/command/sync",
            '{"domain":"heyasa.com"}\n{"domain":"get.heyasa.com"}',
            "--dry-run",
            "--jsonl",
        ]
    )
    assert rc == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert len(lines) == 2
    assert lines[0]["command"] == ["echo", "heyasa.com"]
    assert lines[1]["command"] == ["echo", "get.heyasa.com"]


def test_uri_jsonl_and_two_segment_normalization():
    reg = {
        "routes": {
            "demo": {
                "doctor": {
                    "query": {
                        "kind": "query",
                        "adapter": "local-function-subprocess",
                        "python": {"module": "json", "export": "dumps"},
                    }
                }
            }
        }
    }
    res = urirun.uri("demo://doctor/query", {"obj": {"ok": True}}, registry=reg)
    assert json.loads(res) == {"ok": True}
    batch = urirun.uri_jsonl("plesk://host/site/command/sync", [{"domain": "a.com"}], registry=_demo_registry())
    assert len(batch) == 1
    assert batch[0]["domain"] == "a.com"


def test_uri_disk_cache_and_status_normalization(tmp_path, monkeypatch):
    cache_path = tmp_path / "ep_reg.pkl"
    monkeypatch.setenv("URIRUN_EP_CACHE_FILE", str(cache_path))
    urirun._CACHED_EP_REGISTRY = None
    reg1 = urirun._load_or_build_default_ep_registry("fp-test")
    assert cache_path.is_file()
    reg2 = urirun._load_or_build_default_ep_registry("fp-test")
    assert reg2.get("routes") == reg1.get("routes")
    assert urirun._normalize_status({"ok": True}) == {"ok": True, "status": "ok"}
    assert urirun._normalize_status({"ok": False}) == {"ok": False, "status": "error"}


