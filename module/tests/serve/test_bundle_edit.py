from __future__ import annotations

import base64
import copy

import yaml

from bkk.importer.hashing import manifest_hash, sha256_jcs, sha256_text
from bkk.marker_assets import marker_asset_hash
from bkk.serve.routers import bundle_edit


def _remote() -> dict:
    manifest = {
        "canonical_identifier": "bkk:test/TEST0001/v1",
        "assets": {
            "parts": [
                {"seq": 1, "filename": "TEST0001_001.yaml", "hash": "sha256:old"},
            ],
            "markers": [
                {"seq": 1, "filename": "assets/TEST0001_001.markers.yaml", "hash": "sha256:old"},
            ],
        },
        "table_of_contents": [
            {
                "label": "Head",
                "ref": {
                    "seq": 1,
                    "marker_id": "TEST0001_X_001-head",
                    "span": ["body", 0, 4],
                },
            },
        ],
        "hash": "sha256:old",
    }
    juan = {
        "canonical_identifier": "bkk:test/TEST0001/v1/juan/1",
        "seq": 1,
        "body": {
            "text": "甲乙丙丁",
            "hash": "sha256:old",
            "markers": [
                {
                    "type": "head",
                    "offset": 0,
                    "content": "Head",
                    "id": "TEST0001_X_001-head",
                },
            ],
        },
        "metadata": {"edition": {"short": "X"}},
        "hash": "sha256:old",
    }
    marker_asset = {
        "canonical_identifier": "bkk:test/TEST0001/v1/markers/1",
        "seq": 1,
        "markers": {
            "body": [
                {"type": "punctuation", "offset": 2, "content": "。", "id": ""},
            ],
        },
        "hash": "sha256:old",
    }
    return {
        "base_sha": "base",
        "manifest_path": "TEST0001.manifest.yaml",
        "manifest": manifest,
        "juan_path": "TEST0001_001.yaml",
        "juan": juan,
        "marker_path": "assets/TEST0001_001.markers.yaml",
        "marker_asset": marker_asset,
    }


def _request(**overrides):
    data = {
        "base_commit_sha": "base",
        "bucket": "body",
        "text": "甲新乙丙丁",
        "markers": [
            {
                "type": "head",
                "offset": 0,
                "content": "Head",
                "id": "TEST0001_X_001-renamed",
            },
            {"type": "punctuation", "offset": 3, "content": "。", "id": ""},
        ],
        "text_splices": [{"start": 1, "delete_count": 0, "insert": "新"}],
        "renamed_marker_ids": {
            "TEST0001_X_001-head": "TEST0001_X_001-renamed",
        },
    }
    data.update(overrides)
    return bundle_edit.BundleEditRequest.model_validate(data)


def _self_hash(value: dict) -> str:
    copy_value = copy.deepcopy(value)
    copy_value["hash"] = "sha256:" + "0" * 64
    return sha256_jcs(copy_value)


def test_prepare_files_rehashes_and_cascades_toc():
    files, removed = bundle_edit._prepare_files(_remote(), "TEST0001", 1, _request())
    assert removed == []

    juan = yaml.safe_load(files["TEST0001_001.yaml"])
    asset = yaml.safe_load(files["assets/TEST0001_001.markers.yaml"])
    manifest_text = files["TEST0001.manifest.yaml"]
    manifest = yaml.safe_load(manifest_text)

    assert juan["body"]["text"] == "甲新乙丙丁"
    assert juan["body"]["hash"] == sha256_text("甲新乙丙丁")
    assert juan["hash"] == _self_hash(juan)
    assert asset["markers"]["body"][0]["offset"] == 3
    assert asset["hash"] == marker_asset_hash(asset)
    assert manifest["assets"]["parts"][0]["hash"] == juan["hash"]
    assert manifest["assets"]["markers"][0]["hash"] == asset["hash"]
    assert manifest["table_of_contents"][0]["ref"]["marker_id"].endswith("-renamed")
    assert manifest["table_of_contents"][0]["ref"]["span"] == ["body", 0, 5]
    assert manifest["hash"] == manifest_hash(manifest)
    assert (
        "- {seq: 1, filename: TEST0001_001.yaml, hash: "
        f"'{juan['hash']}'}}"
    ) in manifest_text
    assert (
        "- {seq: 1, filename: assets/TEST0001_001.markers.yaml, hash: "
        f"'{asset['hash']}'}}"
    ) in manifest_text


def test_prepare_files_requires_toc_delete_acknowledgement():
    request = _request(
        markers=[{"type": "punctuation", "offset": 3, "content": "。", "id": ""}],
        renamed_marker_ids={},
    )
    try:
        bundle_edit._prepare_files(_remote(), "TEST0001", 1, request)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "requires acknowledgement" in str(getattr(exc, "detail", exc))
    else:
        raise AssertionError("expected referenced marker deletion to fail")

    request.acknowledge_toc_deletions = True
    files, removed = bundle_edit._prepare_files(_remote(), "TEST0001", 1, request)
    manifest = yaml.safe_load(files["TEST0001.manifest.yaml"])
    assert manifest["table_of_contents"] == []
    assert removed == ["TEST0001_X_001-head"]


def test_prepare_files_rejects_bad_splice_history():
    request = _request(text="different")
    try:
        bundle_edit._prepare_files(_remote(), "TEST0001", 1, request)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 422
        assert "splice history" in str(getattr(exc, "detail", exc))
    else:
        raise AssertionError("expected invalid splice history to fail")


def test_prepare_files_creates_external_marker_asset():
    remote = _remote()
    remote["marker_path"] = None
    remote["marker_asset"] = None
    remote["manifest"]["assets"].pop("markers")
    remote["juan"]["body"]["markers"] = []
    request = _request(
        markers=[{"type": "punctuation", "offset": 3, "content": "。", "id": ""}],
        renamed_marker_ids={},
        acknowledge_toc_deletions=True,
    )
    files, _removed = bundle_edit._prepare_files(remote, "TEST0001", 1, request)
    marker_path = "assets/TEST0001_001-X.markers.yaml"
    assert marker_path in files
    manifest = yaml.safe_load(files["TEST0001.manifest.yaml"])
    assert manifest["assets"]["markers"][0]["filename"] == marker_path
    asset = yaml.safe_load(files[marker_path])
    assert asset["markers"]["body"][0]["type"] == "punctuation"


def _login(client, *, admin: bool):
    session = client.app.state.bkk.sessions.create(
        login="alice",
        name=None,
        avatar_url=None,
        html_url=None,
        access_token="token",
        workspace={
            "repo": "alice/BKK-Workspace",
            "branch": "alice",
            "html_url": "",
            "private": True,
        },
        is_admin=admin,
    )
    client.cookies.set("bkk_session", session.id)


def test_edit_endpoint_requires_login(client):
    response = client.get("/bundles/TEST0001/juan/1/edit")
    assert response.status_code == 401


def test_edit_endpoint_allows_non_admin_user(client, monkeypatch):
    _login(client, admin=False)
    monkeypatch.setattr(bundle_edit, "_branch", lambda *args: "main")
    monkeypatch.setattr(bundle_edit, "_load_remote", lambda *args, **kwargs: _remote())
    response = client.get("/bundles/TEST0001/juan/1/edit")
    assert response.status_code == 200
    body = response.json()
    assert body["repository"] == "bkkbooks/TEST0001"
    assert body["base_commit_sha"] == "base"
    assert [marker["type"] for marker in body["buckets"]["body"]["markers"]] == [
        "head", "punctuation",
    ]
    assert body["toc_marker_ids"] == ["TEST0001_X_001-head"]


def test_admin_save_commits_directly(client, monkeypatch):
    _login(client, admin=True)
    monkeypatch.setattr(bundle_edit, "_branch", lambda *args: "main")
    monkeypatch.setattr(bundle_edit, "_head_sha", lambda *args: "base")
    monkeypatch.setattr(bundle_edit, "_load_remote", lambda *args, **kwargs: _remote())
    monkeypatch.setattr(
        bundle_edit, "_prepare_files",
        lambda *args: ({"TEST0001_001.yaml": "new"}, []),
    )
    calls = []

    def fake_commit(*args, **kwargs):
        calls.append((args, kwargs))
        return "commit123"

    monkeypatch.setattr(bundle_edit, "_commit_files", fake_commit)
    response = client.post(
        "/bundles/TEST0001/juan/1/edit",
        json=_request().model_dump(),
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "commit"
    assert response.json()["commit_sha"] == "commit123"
    assert calls[0][1]["create_branch"] is False


def test_non_admin_save_opens_pull_request(client, monkeypatch):
    _login(client, admin=False)
    monkeypatch.setattr(bundle_edit, "_branch", lambda *args: "main")
    monkeypatch.setattr(bundle_edit, "_head_sha", lambda *args: "base")
    monkeypatch.setattr(bundle_edit, "_load_remote", lambda *args, **kwargs: _remote())
    monkeypatch.setattr(
        bundle_edit, "_prepare_files",
        lambda *args: ({"TEST0001_001.yaml": "new"}, []),
    )
    monkeypatch.setattr(
        bundle_edit, "_ensure_fork", lambda *args: "alice/TEST0001",
    )
    monkeypatch.setattr(
        bundle_edit, "_commit_files", lambda *args, **kwargs: "commit456",
    )
    monkeypatch.setattr(
        bundle_edit,
        "_github_json",
        lambda *args, **kwargs: {"html_url": "https://github.test/pr/7", "number": 7},
    )
    response = client.post(
        "/bundles/TEST0001/juan/1/edit",
        json=_request().model_dump(),
    )
    assert response.status_code == 200
    assert response.json() == {
        "kind": "pull_request",
        "commit_sha": "commit456",
        "url": "https://github.test/pr/7",
        "pull_request_number": 7,
        "removed_toc_marker_ids": [],
    }


def test_save_rejects_stale_base(client, monkeypatch):
    _login(client, admin=True)
    monkeypatch.setattr(bundle_edit, "_branch", lambda *args: "main")
    monkeypatch.setattr(bundle_edit, "_head_sha", lambda *args: "newer")
    response = client.post(
        "/bundles/TEST0001/juan/1/edit",
        json=_request().model_dump(),
    )
    assert response.status_code == 409


def test_branch_auto_discovers_github_default(client, monkeypatch):
    calls = []

    def fake(method, path, token, **kwargs):
        calls.append((method, path, token))
        return {"default_branch": "main"}

    monkeypatch.setattr(bundle_edit, "_github_json", fake)
    state = client.app.state.bkk
    assert bundle_edit._branch(state, "token", "bkkbooks/TEST0001") == "main"
    assert calls == [("GET", "/repos/bkkbooks/TEST0001", "token")]


def test_fetch_file_falls_back_to_blob_for_large_contents(monkeypatch):
    encoded = base64.b64encode("甲乙".encode()).decode()
    calls = []

    def fake(method, path, token, **kwargs):
        calls.append(path)
        if "/contents/" in path:
            return {"type": "file", "sha": "blob-sha", "content": ""}
        return {"sha": "blob-sha", "content": encoded, "encoding": "base64"}

    monkeypatch.setattr(bundle_edit, "_github_json", fake)
    _payload, text = bundle_edit._fetch_file(
        "token", "bkkbooks/TEST0001", "TEST0001_001.yaml", "base",
    )
    assert text == "甲乙"
    assert calls[-1] == "/repos/bkkbooks/TEST0001/git/blobs/blob-sha"


def test_commit_files_builds_one_atomic_tree_and_commit(monkeypatch):
    calls = []
    blob_number = 0

    def fake(method, path, token, **kwargs):
        nonlocal blob_number
        calls.append((method, path, kwargs.get("json")))
        if method == "GET":
            return {"tree": {"sha": "tree-base"}}
        if path.endswith("/git/blobs"):
            blob_number += 1
            return {"sha": f"blob-{blob_number}"}
        if path.endswith("/git/trees"):
            return {"sha": "tree-new"}
        if path.endswith("/git/commits"):
            return {"sha": "commit-new"}
        return {}

    monkeypatch.setattr(bundle_edit, "_github_json", fake)
    commit = bundle_edit._commit_files(
        "token",
        "bkkbooks/TEST0001",
        "parent",
        "master",
        "Edit",
        {"a.yaml": "A", "b.yaml": "B", "obsolete.yaml": None},
        create_branch=False,
    )
    assert commit == "commit-new"
    tree_call = next(call for call in calls if call[1].endswith("/git/trees"))
    assert tree_call[2]["base_tree"] == "tree-base"
    assert tree_call[2]["tree"] == [
        {"path": "a.yaml", "mode": "100644", "type": "blob", "sha": "blob-1"},
        {"path": "b.yaml", "mode": "100644", "type": "blob", "sha": "blob-2"},
        {"path": "obsolete.yaml", "mode": "100644", "type": "blob", "sha": None},
    ]
    commit_calls = [
        call for call in calls
        if call[0] == "POST" and call[1].endswith("/git/commits")
    ]
    assert len(commit_calls) == 1
    assert calls[-1][0:2] == (
        "PATCH",
        "/repos/bkkbooks/TEST0001/git/refs/heads/master",
    )
