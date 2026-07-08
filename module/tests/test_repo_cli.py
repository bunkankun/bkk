from __future__ import annotations

import subprocess
from pathlib import Path

from bkk.repo import cli


def _bundle(root: Path, textid: str, *, nested: bool = False) -> Path:
    if nested:
        path = root / "krp" / textid[:4] / textid
    else:
        path = root / textid[:4] / textid
    path.mkdir(parents=True)
    (path / f"{textid}.manifest.yaml").write_text("metadata: {}\n", encoding="utf-8")
    return path


def test_run_status_accepts_text_prefix(tmp_path, monkeypatch, capsys):
    corpus = tmp_path / "corpus"
    _bundle(corpus, "KR1a0001")
    _bundle(corpus, "KR1a0002")
    _bundle(corpus, "KR2a0001")
    monkeypatch.setattr(cli, "load_rc", lambda: {"repo": {"corpus": corpus}})

    rc = cli.run(["status", "--text-prefix", "KR1a"])

    out = capsys.readouterr()
    assert rc == 0
    assert "KR1a0001  not a repo" in out.out
    assert "KR1a0002  not a repo" in out.out
    assert "KR2a0001" not in out.out
    assert "deprecated" not in out.err


def test_run_status_legacy_prefix_warns(tmp_path, monkeypatch, capsys):
    corpus = tmp_path / "corpus"
    _bundle(corpus, "KR1a0001")
    monkeypatch.setattr(cli, "load_rc", lambda: {"repo": {"corpus": corpus}})

    rc = cli.run(["status", "KR1a"])

    out = capsys.readouterr()
    assert rc == 0
    assert "KR1a0001  not a repo" in out.out
    assert "positional <prefix> is deprecated" in out.err


def test_run_rejects_text_prefix_and_legacy_prefix_together(
    tmp_path, monkeypatch, capsys,
):
    corpus = tmp_path / "corpus"
    _bundle(corpus, "KR1a0001")
    monkeypatch.setattr(cli, "load_rc", lambda: {"repo": {"corpus": corpus}})

    rc = cli.run(["status", "KR1a", "--text-prefix", "KR1a"])

    assert rc == 2
    assert "provide only one" in capsys.readouterr().err


def test_reclone_replaces_only_local_bundles_that_exist_on_github(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    local_and_remote = _bundle(corpus, "KR1a0001")
    local_only = _bundle(corpus, "KR1a0002")
    (local_and_remote / "local.txt").write_text("local", encoding="utf-8")
    (local_only / "local.txt").write_text("keep", encoding="utf-8")

    monkeypatch.setattr(cli, "_list_remote_bundles", lambda org, prefix: ["KR1a0001"])

    def fake_run(cmd, *, cwd=None):
        assert cmd[:3] == ["gh", "repo", "clone"]
        target = Path(cmd[4])
        textid = cmd[3].split("/", 1)[1]
        target.mkdir(parents=True)
        (target / f"{textid}.manifest.yaml").write_text("metadata: {}\n", encoding="utf-8")
        (target / "remote.txt").write_text("remote", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cli, "_run", fake_run)

    rc = cli._action_reclone(corpus, "KR1a", False, "bkkbooks", False)

    assert rc == 0
    assert not (local_and_remote / "local.txt").exists()
    assert (local_and_remote / "remote.txt").read_text(encoding="utf-8") == "remote"
    assert (local_only / "local.txt").read_text(encoding="utf-8") == "keep"


def test_reclone_preserves_existing_nested_bundle_location(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    bundle = _bundle(corpus, "KR1a0001", nested=True)

    monkeypatch.setattr(cli, "_list_remote_bundles", lambda org, prefix: ["KR1a0001"])

    def fake_run(cmd, *, cwd=None):
        target = Path(cmd[4])
        target.mkdir(parents=True)
        (target / "KR1a0001.manifest.yaml").write_text("metadata: {}\n", encoding="utf-8")
        (target / "remote.txt").write_text("remote", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(cli, "_run", fake_run)

    rc = cli._action_reclone(corpus, "KR1a0001", False, "bkkbooks", False)

    assert rc == 0
    assert bundle.is_dir()
    assert (bundle / "remote.txt").is_file()
    assert not (corpus / "KR1a" / "KR1a0001").exists()


def test_reclone_dry_run_does_not_mutate_local_bundle(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    bundle = _bundle(corpus, "KR1a0001")
    (bundle / "local.txt").write_text("local", encoding="utf-8")

    monkeypatch.setattr(cli, "_list_remote_bundles", lambda org, prefix: ["KR1a0001"])

    def fail_run(cmd, *, cwd=None):  # pragma: no cover - documents intent if called
        raise AssertionError("dry-run must not clone")

    monkeypatch.setattr(cli, "_run", fail_run)

    rc = cli._action_reclone(corpus, "KR1a0001", False, "bkkbooks", True)

    assert rc == 0
    assert (bundle / "local.txt").read_text(encoding="utf-8") == "local"


def test_diff_reports_name_overlap_as_repos_vs_plain_bundles(
    tmp_path, monkeypatch, capsys,
):
    corpus = tmp_path / "corpus"
    repo_bundle = _bundle(corpus, "KR1a0001")
    plain_bundle = _bundle(corpus, "KR1a0002")
    local_only = _bundle(corpus, "KR1a0003")
    (repo_bundle / ".git").mkdir()

    monkeypatch.setattr(
        cli,
        "_list_remote_bundles",
        lambda org, prefix: ["KR1a0001", "KR1a0002", "KR1a0004"],
    )

    rc = cli._action_diff(
        corpus,
        "KR1a",
        False,
        rc={},
        org="bkkbooks",
        visibility="public",
        default_branch="main",
        create_delay_s=0,
        upload=False,
        download=False,
        check_origin=False,
        dry_run=False,
    )

    out = capsys.readouterr()
    assert rc == 1
    assert "local-only (1):\n  KR1a0003" in out.out
    assert "remote-only (1):\n  KR1a0004" in out.out
    assert "present in both by name (2):" in out.out
    assert "  local git repos: 1" in out.out
    assert "  plain bundles (not git repos): 1" in out.out
    assert "2 present in both (1 local git repos, 1 plain bundles)" in out.err


def test_diff_check_origin_reports_origin_mismatches(tmp_path, monkeypatch, capsys):
    corpus = tmp_path / "corpus"
    ok_bundle = _bundle(corpus, "KR1a0001")
    bad_bundle = _bundle(corpus, "KR1a0002")
    plain_bundle = _bundle(corpus, "KR1a0003")
    (ok_bundle / ".git").mkdir()
    (bad_bundle / ".git").mkdir()

    monkeypatch.setattr(
        cli,
        "_list_remote_bundles",
        lambda org, prefix: ["KR1a0001", "KR1a0002", "KR1a0003"],
    )

    def fake_run(cmd, *, cwd=None):
        assert cmd == ["git", "remote", "get-url", "origin"]
        if Path(cwd) == ok_bundle:
            return subprocess.CompletedProcess(
                cmd, 0, "https://github.com/bkkbooks/KR1a0001.git\n", "",
            )
        if Path(cwd) == bad_bundle:
            return subprocess.CompletedProcess(
                cmd, 0, "https://github.com/other/KR1a0002.git\n", "",
            )
        raise AssertionError(f"unexpected cwd: {cwd}")

    monkeypatch.setattr(cli, "_run", fake_run)

    rc = cli._action_diff(
        corpus,
        "KR1a",
        False,
        rc={},
        org="bkkbooks",
        visibility="public",
        default_branch="main",
        create_delay_s=0,
        upload=False,
        download=False,
        check_origin=True,
        dry_run=False,
    )

    out = capsys.readouterr()
    assert rc == 0
    assert "present in both by name (3):" in out.out
    assert "  local git repos: 2" in out.out
    assert "  plain bundles (not git repos): 1" in out.out
    assert "  origin matches bkkbooks/<textid>: 1" in out.out
    assert "  origin missing/mismatch: 1" in out.out
    assert "    KR1a0002: https://github.com/other/KR1a0002.git" in out.out
    assert "1 origin ok, 1 origin missing/mismatch" in out.err
