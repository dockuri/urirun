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
