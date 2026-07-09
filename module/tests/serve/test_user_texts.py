from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path
import time

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from bkk.serve import create_app
from bkk.serve.config import ServeConfig
from bkk.serve.routers import user_texts


def _client(tmp_path: Path) -> TestClient:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    return TestClient(create_app(ServeConfig(
        corpus_root=corpus,
        index_path=corpus / "_corpus.bkkx",
        user_texts_root=tmp_path / "user-texts",
    )))


def _login(client: TestClient, login: str = "alice"):
    session = client.app.state.bkk.sessions.create(
        login=login,
        name=login.title(),
        avatar_url=None,
        html_url=f"https://github.com/{login}",
        access_token=f"{login}-token",
        workspace={
            "repo": f"{login}/BKK-Workspace",
            "branch": login,
            "html_url": f"https://github.com/{login}/BKK-Workspace",
            "private": True,
        },
    )
    client.cookies.set("bkk_session", session.id)
    return session


@pytest.fixture(autouse=True)
def _no_remote_repo_lookup(monkeypatch):
    monkeypatch.setattr(user_texts, "_repo_exists", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def _no_real_git_push(monkeypatch):
    def fake_git(cwd: Path, *args: str):
        if args[:1] == ("rev-parse",):
            return subprocess.CompletedProcess(args, 0, "commit-sha\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(user_texts, "_git", fake_git)


def test_preview_requires_login(tmp_path: Path):
    client = _client(tmp_path)
    response = client.post(
        "/user-texts/preview",
        json={"format": "krp", "paste": "甲乙¶"},
    )
    assert response.status_code == 401


def test_missing_registry_is_an_expected_first_user_state(client, monkeypatch):
    session = _login(client)
    calls = []

    def missing(method, path, token, **kwargs):
        calls.append(kwargs)
        raise HTTPException(
            status_code=502,
            detail={"github_status": 404, "body": {"message": "Not Found"}},
        )

    monkeypatch.setattr(user_texts, "_github_json", missing)
    assert user_texts._load_registry(session) == []
    assert calls == [{"expected_statuses": {404}}]


def test_krp_preview_suggests_first_owner_local_id(tmp_path: Path):
    client = _client(tmp_path)
    _login(client)
    response = client.post(
        "/user-texts/preview",
        json={"format": "krp", "paste": "#+TITLE: 測試\n甲乙丙¶"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["detected_text_id"] is None
    assert body["suggested_text_id"] == "KR9a0001"
    assert body["title"] == "測試"
    assert body["first_seq"] == 1


def test_krp_preview_canonicalizes_title(tmp_path: Path):
    client = _client(tmp_path)
    _login(client)
    response = client.post(
        "/user-texts/preview",
        json={"format": "krp", "paste": "#+TITLE: 学习\n甲乙丙¶"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["title"] == "學習"


def test_krp_preview_skips_existing_user_repo_id(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    _login(client)

    def fake_repo_exists(token: str, owner: str, repo: str):
        return {"full_name": f"{owner}/{repo}"} if repo == "KR9a0001" else None

    monkeypatch.setattr(user_texts, "_repo_exists", fake_repo_exists)
    response = client.post(
        "/user-texts/preview",
        json={"format": "krp", "paste": "#+TITLE: 既有\nKR9a0001¶"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["detected_text_id"] == "KR9a0001"
    assert body["suggested_text_id"] == "KR9a0002"


def test_krp_preview_without_text_id_does_not_scan_corpus(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    _login(client)

    def fail_if_called():
        raise AssertionError("preview should not consult the corpus cache here")

    monkeypatch.setattr(client.app.state.bkk.cache, "get", fail_if_called)
    response = client.post(
        "/user-texts/preview",
        json={"format": "krp", "paste": "#+TITLE: 短文\n甲乙丙¶"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["suggested_text_id"] == "KR9a0001"


def test_large_pasted_krp_preview_avoids_bundle_yaml_roundtrip(tmp_path: Path):
    client = _client(tmp_path)
    _login(client)
    source = "#+TITLE: 長篇\n" + ("甲，" * 20_000) + "¶"

    started = time.perf_counter()
    response = client.post(
        "/user-texts/preview",
        json={"format": "krp", "paste": source},
    )
    elapsed = time.perf_counter() - started

    assert response.status_code == 200, response.text
    assert response.json()["title"] == "長篇"
    assert elapsed < 6.0


def test_tls_preview_keeps_detected_id(tmp_path: Path):
    client = _client(tmp_path)
    _login(client)
    xml = """<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="KR9a0042">
      <teiHeader><fileDesc><titleStmt><title>測試書</title></titleStmt>
      <publicationStmt><p/></publicationStmt><sourceDesc><p>
      <idno type="kanripo">KR9a0042</idno></p></sourceDesc></fileDesc></teiHeader>
      <text><body><div><head><seg xml:id="KR9a0042_T_001-h">題</seg></head>
      <p><seg xml:id="KR9a0042_T_001-1a.1">甲乙</seg></p></div></body></text>
      </TEI>"""
    response = client.post(
        "/user-texts/preview",
        json={"format": "tls", "files": [{"name": "KR9a0042.xml", "content": xml}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["detected_text_id"] == "KR9a0042"
    assert response.json()["title"] == "測試書"


def test_cbeta_preview_uses_kr9_when_source_has_no_kr_id(tmp_path: Path):
    client = _client(tmp_path)
    _login(client)
    xml = """<TEI xmlns="http://www.tei-c.org/ns/1.0"
      xmlns:cb="http://www.cbeta.org/ns/1.0" xml:id="T01n0001">
      <teiHeader><fileDesc><titleStmt><title>測試經</title></titleStmt>
      <publicationStmt><p/></publicationStmt><sourceDesc><p/></sourceDesc>
      </fileDesc></teiHeader>
      <text><body><cb:juan fun="open" n="1"/><p>甲乙丙</p></body></text>
      </TEI>"""
    response = client.post(
        "/user-texts/preview",
        json={"format": "cbeta", "files": [{"name": "T01n0001.xml", "content": xml}]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["detected_text_id"] is None
    assert response.json()["suggested_text_id"] == "KR9a0001"
    assert response.json()["title"] == "測試經"


def test_create_is_owner_scoped_catalogued_readable_and_indexed(
    tmp_path: Path, monkeypatch,
):
    client = _client(tmp_path)
    _login(client)
    preview = client.post(
        "/user-texts/preview",
        json={"format": "krp", "paste": "#+TITLE: 私人文本\n甲乙丙丁¶"},
    ).json()

    counter = {"blob": 0}

    def fake_github(method, path, token, **kwargs):
        if method == "POST" and path == "/user/repos":
            return {
                "full_name": "alice/KR9a0001",
                "html_url": "https://github.com/alice/KR9a0001",
            }
        if method == "POST" and path.endswith("/git/blobs"):
            counter["blob"] += 1
            return {"sha": f"blob-{counter['blob']}"}
        if method == "POST" and path.endswith("/git/trees"):
            return {"sha": "tree-sha"}
        if method == "POST" and path.endswith("/git/commits"):
            return {"sha": "commit-sha"}
        if method == "POST" and path.endswith("/git/refs"):
            return {}
        if method == "GET" and "/contents/settings/user-texts.json" in path:
            raise HTTPException(
                status_code=502,
                detail={"github_status": 404, "body": {"message": "Not Found"}},
            )
        if method == "PUT" and "/contents/settings/user-texts.json" in path:
            return {"content": {"sha": "registry-sha"}}
        raise AssertionError((method, path, kwargs))

    monkeypatch.setattr(user_texts, "_github_json", fake_github)
    created = client.post(
        "/user-texts",
        json={
            "preview_token": preview["preview_token"],
            "text_id": "KR9a0001",
            "title": "私人文本",
            "author": "Alice",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["repository"] == "alice/KR9a0001"

    listed = client.get("/user-texts").json()["texts"]
    assert listed[0]["index_status"] in {"pending", "ready"}

    categories = client.get("/catalog/categories").json()["categories"]
    kr9 = next(node for node in categories if node["code"] == "KR9")
    assert kr9["bundle_count"] == 1
    assert kr9["subcategories"][0]["code"] == "KR9a"
    original_cache_get = client.app.state.bkk.cache.get
    monkeypatch.setattr(
        client.app.state.bkk.cache,
        "get",
        lambda: (_ for _ in ()).throw(
            AssertionError("catalog browse should not scan the corpus snapshot")
        ),
    )
    by_category = client.get(
        "/catalog", params={"tags.kr-categories": "KR9a"},
    ).json()
    monkeypatch.setattr(client.app.state.bkk.cache, "get", original_cache_get)
    assert [item["textid"] for item in by_category["matches"]] == ["KR9a0001"]

    manifest = client.get("/bundles/KR9a0001/manifest")
    assert manifest.status_code == 200
    assert client.get("/texts/KR9a0001/manifest").status_code == 200
    juan = client.get("/bundles/KR9a0001/juan/1")
    assert juan.status_code == 200
    assert juan.json()["body"]["text"] == "甲乙丙丁"

    client.cookies.clear()
    assert client.get("/bundles/KR9a0001/manifest").status_code == 404

    _login(client, "bob")
    assert client.get("/bundles/KR9a0001/manifest").status_code == 404
    assert client.get("/catalog").json()["matches"] == []


def test_delete_user_text_route_is_not_available(
    tmp_path: Path, monkeypatch,
):
    client = _client(tmp_path)
    _login(client)
    preview = client.post(
        "/user-texts/preview",
        json={"format": "krp", "paste": "#+TITLE: 刪除測試\n甲乙丙丁¶"},
    ).json()

    registry: dict[str, object] | None = None
    registry_sha: str | None = None
    calls: list[tuple[str, str]] = []

    def fake_github(method, path, token, **kwargs):
        nonlocal registry, registry_sha
        calls.append((method, path))
        if method == "POST" and path == "/user/repos":
            return {
                "full_name": "alice/KR9a0002",
                "html_url": "https://github.com/alice/KR9a0002",
            }
        if method == "POST" and path.endswith("/git/blobs"):
            return {"sha": "blob-sha"}
        if method == "POST" and path.endswith("/git/trees"):
            return {"sha": "tree-sha"}
        if method == "POST" and path.endswith("/git/commits"):
            return {"sha": "commit-sha"}
        if method == "POST" and path.endswith("/git/refs"):
            return {}
        if method == "GET" and "/contents/settings/user-texts.json" in path:
            if registry is None:
                raise HTTPException(
                    status_code=502,
                    detail={"github_status": 404, "body": {"message": "Not Found"}},
                )
            return {
                "sha": registry_sha,
                "content": base64.b64encode(
                    json.dumps(registry, ensure_ascii=False).encode("utf-8")
                ).decode("ascii"),
            }
        if method == "PUT" and "/contents/settings/user-texts.json" in path:
            content = json.loads(base64.b64decode(kwargs["json"]["content"]).decode("utf-8"))
            registry = content
            registry_sha = "registry-sha"
            return {"content": {"sha": registry_sha}}
        raise AssertionError((method, path, kwargs))

    monkeypatch.setattr(user_texts, "_github_json", fake_github)
    created = client.post(
        "/user-texts",
        json={
            "preview_token": preview["preview_token"],
            "text_id": "KR9a0002",
            "title": "刪除測試",
        },
    )
    assert created.status_code == 201, created.text
    bundle_dir = tmp_path / "user-texts" / "alice" / "KR9a0002"
    assert bundle_dir.exists()
    assert client.get("/user-texts").json()["texts"]

    response = client.delete("/user-texts/KR9a0002")
    assert response.status_code in {404, 405}
    assert bundle_dir.exists()
    assert client.get("/user-texts").json()["texts"]
    assert registry is not None
    assert len(registry["texts"]) == 1
    assert ("DELETE", "/repos/alice/KR9a0002") not in calls


def test_sync_removes_user_text_after_manual_github_delete(
    tmp_path: Path, monkeypatch,
):
    client = _client(tmp_path)
    _login(client)
    preview = client.post(
        "/user-texts/preview",
        json={"format": "krp", "paste": "#+TITLE: 手動刪除\n甲乙丙丁¶"},
    ).json()

    registry: dict[str, object] | None = None
    registry_sha: str | None = None
    calls: list[tuple[str, str]] = []

    def fake_github(method, path, token, **kwargs):
        nonlocal registry, registry_sha
        calls.append((method, path))
        if method == "POST" and path == "/user/repos":
            return {
                "full_name": "alice/KR9a0003",
                "html_url": "https://github.com/alice/KR9a0003",
            }
        if method == "POST" and path.endswith("/git/blobs"):
            return {"sha": "blob-sha"}
        if method == "POST" and path.endswith("/git/trees"):
            return {"sha": "tree-sha"}
        if method == "POST" and path.endswith("/git/commits"):
            return {"sha": "commit-sha"}
        if method == "POST" and path.endswith("/git/refs"):
            return {}
        if method == "GET" and path == "/repos/alice/KR9a0003/git/ref/heads/main":
            raise HTTPException(
                status_code=502,
                detail={"github_status": 404, "body": {"message": "Not Found"}},
            )
        if method == "GET" and "/contents/settings/user-texts.json" in path:
            if registry is None:
                raise HTTPException(
                    status_code=502,
                    detail={"github_status": 404, "body": {"message": "Not Found"}},
                )
            return {
                "sha": registry_sha,
                "content": base64.b64encode(
                    json.dumps(registry, ensure_ascii=False).encode("utf-8")
                ).decode("ascii"),
            }
        if method == "PUT" and "/contents/settings/user-texts.json" in path:
            content = json.loads(base64.b64decode(kwargs["json"]["content"]).decode("utf-8"))
            registry = content
            registry_sha = "registry-sha"
            return {"content": {"sha": "registry-sha"}}
        raise AssertionError((method, path, kwargs))

    monkeypatch.setattr(user_texts, "_github_json", fake_github)
    created = client.post(
        "/user-texts",
        json={
            "preview_token": preview["preview_token"],
            "text_id": "KR9a0003",
            "title": "手動刪除",
        },
    )
    assert created.status_code == 201, created.text
    assert client.get("/user-texts").json()["texts"]
    bundle_dir = tmp_path / "user-texts" / "alice" / "KR9a0003"
    assert bundle_dir.exists()

    synced = client.post("/user-texts/sync")
    assert synced.status_code == 200, synced.text
    assert synced.json()["results"] == [{"text_id": "KR9a0003", "status": "removed"}]
    assert not bundle_dir.exists()
    assert client.get("/user-texts").json()["texts"] == []
    assert registry is not None
    assert registry["texts"] == []
    assert ("DELETE", "/repos/alice/KR9a0003") not in calls


def test_private_manifest_does_not_sync_before_serving(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    _login(client)
    preview = client.post(
        "/user-texts/preview",
        json={"format": "krp", "paste": "#+TITLE: 立即顯示\n甲乙丙丁¶"},
    ).json()

    def fake_github(method, path, token, **kwargs):
        if method == "POST" and path == "/user/repos":
            return {
                "full_name": "alice/KR9a0009",
                "html_url": "https://github.com/alice/KR9a0009",
            }
        if method == "GET" and "/contents/settings/user-texts.json" in path:
            raise HTTPException(
                status_code=502,
                detail={"github_status": 404, "body": {"message": "Not Found"}},
            )
        if method == "PUT" and "/contents/settings/user-texts.json" in path:
            return {"content": {"sha": "registry-sha"}}
        raise AssertionError((method, path, kwargs))

    monkeypatch.setattr(user_texts, "_github_json", fake_github)
    created = client.post(
        "/user-texts",
        json={
            "preview_token": preview["preview_token"],
            "text_id": "KR9a0009",
            "title": "立即顯示",
        },
    )
    assert created.status_code == 201, created.text

    def fail_sync(*args, **kwargs):
        raise AssertionError("manifest load should not sync private user texts")

    monkeypatch.setattr("bkk.serve.routers.user_texts._sync_registered_texts", fail_sync)
    manifest = client.get("/bundles/KR9a0009/manifest")
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["metadata"]["title"] == "立即顯示"


def test_create_canonicalizes_title(tmp_path: Path, monkeypatch):
    client = _client(tmp_path)
    _login(client)
    preview = client.post(
        "/user-texts/preview",
        json={"format": "krp", "paste": "#+TITLE: 学习\n甲乙丙丁¶"},
    ).json()

    def fake_github(method, path, token, **kwargs):
        if method == "POST" and path == "/user/repos":
            return {
                "full_name": "alice/KR9a0010",
                "html_url": "https://github.com/alice/KR9a0010",
            }
        if method == "GET" and "/contents/settings/user-texts.json" in path:
            raise HTTPException(
                status_code=502,
                detail={"github_status": 404, "body": {"message": "Not Found"}},
            )
        if method == "PUT" and "/contents/settings/user-texts.json" in path:
            return {"content": {"sha": "registry-sha"}}
        raise AssertionError((method, path, kwargs))

    monkeypatch.setattr(user_texts, "_github_json", fake_github)
    created = client.post(
        "/user-texts",
        json={
            "preview_token": preview["preview_token"],
            "text_id": "KR9a0010",
            "title": "学习",
        },
    )
    assert created.status_code == 201, created.text
    manifest = client.get("/bundles/KR9a0010/manifest")
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["metadata"]["title"] == "學習"


def test_catalog_categories_for_user_texts_do_not_scan_corpus(
    tmp_path: Path, monkeypatch,
):
    client = _client(tmp_path)
    _login(client)
    preview = client.post(
        "/user-texts/preview",
        json={"format": "krp", "paste": "#+TITLE: 類別\n甲乙丙¶"},
    ).json()

    def fake_github(method, path, token, **kwargs):
        if method == "POST" and path == "/user/repos":
            return {
                "full_name": "alice/KR9a0011",
                "html_url": "https://github.com/alice/KR9a0011",
            }
        if method == "GET" and "/contents/settings/user-texts.json" in path:
            raise HTTPException(
                status_code=502,
                detail={"github_status": 404, "body": {"message": "Not Found"}},
            )
        if method == "PUT" and "/contents/settings/user-texts.json" in path:
            return {"content": {"sha": "registry-sha"}}
        raise AssertionError((method, path, kwargs))

    def fake_git(cwd: Path, *args: str):
        if args[:1] == ("rev-parse",):
            return subprocess.CompletedProcess(args, 0, "commit-sha\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(user_texts, "_github_json", fake_github)
    monkeypatch.setattr(user_texts, "_git", fake_git)
    created = client.post(
        "/user-texts",
        json={
            "preview_token": preview["preview_token"],
            "text_id": "KR9a0011",
            "title": "類別",
        },
    )
    assert created.status_code == 201, created.text

    state = client.app.state.bkk

    def fail_cache():
        raise AssertionError("catalog categories should not scan the corpus snapshot")

    monkeypatch.setattr(state.cache, "get", fail_cache)
    body = client.get("/catalog/categories").json()
    kr9 = next(node for node in body["categories"] if node["code"] == "KR9")
    assert kr9["bundle_count"] == 1
    assert kr9["subcategories"][0]["code"] == "KR9a"


def test_create_initializes_empty_repo_with_git_push(
    tmp_path: Path, monkeypatch,
):
    client = _client(tmp_path)
    _login(client)
    preview = client.post(
        "/user-texts/preview",
        json={"format": "krp", "paste": "#+TITLE: 初始化\n甲乙丙丁¶"},
    ).json()

    calls = []
    git_calls = []

    def fake_github(method, path, token, **kwargs):
        calls.append((method, path))
        if method == "POST" and path == "/user/repos":
            return {
                "full_name": "alice/KR9a0002",
                "html_url": "https://github.com/alice/KR9a0002",
            }
        if method == "GET" and "/contents/settings/user-texts.json" in path:
            raise HTTPException(
                status_code=502,
                detail={"github_status": 404, "body": {"message": "Not Found"}},
            )
        if method == "PUT" and "/contents/settings/user-texts.json" in path:
            return {"content": {"sha": "registry-sha"}}
        raise AssertionError((method, path, kwargs))

    def fake_git(cwd: Path, *args: str):
        git_calls.append((Path(cwd).name, args))
        ok = type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})
        if args[:2] in {
            ("init", "-b"),
            ("config", "user.email"),
            ("config", "user.name"),
        }:
            return ok()
        if args[:1] in {("add",), ("commit",), ("push",)}:
            return ok()
        if args[:3] == ("remote", "add", "origin"):
            return ok()
        if args[:1] == ("rev-parse",):
            return type("CP", (), {"returncode": 0, "stdout": "commit-sha\n", "stderr": ""})()
        raise AssertionError((cwd, args))

    monkeypatch.setattr(user_texts, "_github_json", fake_github)
    monkeypatch.setattr(user_texts, "_git", fake_git)
    created = client.post(
        "/user-texts",
        json={
            "preview_token": preview["preview_token"],
            "text_id": "KR9a0002",
            "title": "初始化",
        },
    )

    assert created.status_code == 201, created.text
    assert ("POST", "/user/repos") in calls
    assert any(step[1][:2] == ("init", "-b") for step in git_calls)
    assert any(step[1][:1] == ("push",) for step in git_calls)
