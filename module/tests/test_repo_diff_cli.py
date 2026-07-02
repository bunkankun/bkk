from __future__ import annotations

import subprocess
from pathlib import Path

from bkk.repo import cli


def _bundle(root: Path, textid: str) -> Path:
    path = root / textid[:4] / textid
    path.mkdir(parents=True)
    (path / f"{textid}.manifest.yaml").write_text("metadata: {}\n", encoding="utf-8")
    return path


def test_diff_reports_name_overlap_as_repos_vs_plain_bundles(
    tmp_path, monkeypatch, capsys,
):
    corpus = tmp_path / "corpus"
    repo_bundle = _bundle(corpus, "KR1a0001")
    _bundle(corpus, "KR1a0002")
    _bundle(corpus, "KR1a0003")
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
    _bundle(corpus, "KR1a0003")
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
