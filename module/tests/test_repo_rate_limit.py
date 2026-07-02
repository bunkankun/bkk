from __future__ import annotations

import json
import subprocess
from pathlib import Path

from bkk.repo import cli


def _cp(returncode: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["gh"], returncode, stdout, stderr)


def test_gh_backoff_uses_secondary_floor_before_retrying(monkeypatch):
    calls = {"n": 0}
    slept: list[float] = []

    def fake_run(cmd, *, cwd=None):
        calls["n"] += 1
        if calls["n"] < 3:
            return _cp(1, stderr="HTTP 403: You have exceeded a secondary rate limit")
        return _cp(0, stdout="created")

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(cli.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(cli.random, "uniform", lambda a, b: 0.0)

    r = cli._run_gh_with_rate_limit_backoff(
        ["gh", "repo", "create"], initial_wait_s=10, max_wait_s=900, max_retries=8,
    )

    assert r.returncode == 0
    assert calls["n"] == 3
    # Secondary/content-creation blocks use a minutes-long floor, not the
    # short exponential seed, so we do not extend the block by retrying early.
    assert slept == [300, 300]


def test_gh_backoff_treats_too_many_repositories_as_secondary(monkeypatch):
    calls = {"n": 0}
    slept: list[float] = []
    msg = (
        "GraphQL: You have created too many repositories, too quickly. "
        "Please try again later. (createRepository)"
    )

    def fake_run(cmd, *, cwd=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _cp(1, stderr=msg)
        return _cp(0, stdout="created")

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(cli.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(cli.random, "uniform", lambda a, b: 0.0)

    r = cli._run_gh_with_rate_limit_backoff(
        ["gh", "repo", "create"], initial_wait_s=10, max_wait_s=900, max_retries=2,
    )

    assert r.returncode == 0
    assert calls["n"] == 2
    assert slept == [300]


def test_gh_backoff_gives_up_after_max_retries(monkeypatch):
    calls = {"n": 0}
    slept: list[float] = []

    def fake_run(cmd, *, cwd=None):
        calls["n"] += 1
        return _cp(1, stderr="API rate limit exceeded")

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(cli.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(cli.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(cli, "_gh_primary_reset_wait_s", lambda max_wait_s: None)

    r = cli._run_gh_with_rate_limit_backoff(
        ["gh", "repo", "create"], initial_wait_s=5, max_wait_s=40, max_retries=3,
    )

    assert r.returncode == 1
    # 1 initial try + 3 retries.
    assert calls["n"] == 4
    # Backoff doubles but is capped at max_wait_s.
    assert slept == [5, 10, 20]
    assert "next retry would wait about 40s" in r.stderr


def test_gh_backoff_does_not_retry_non_rate_limit_error(monkeypatch):
    calls = {"n": 0}

    def fake_run(cmd, *, cwd=None):
        calls["n"] += 1
        return _cp(1, stderr="name already exists on this account")

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(
        cli.time, "sleep",
        lambda s: (_ for _ in ()).throw(AssertionError("must not sleep")),
    )

    r = cli._run_gh_with_rate_limit_backoff(["gh", "repo", "create"])
    assert r.returncode == 1
    assert calls["n"] == 1


def test_reset_hint_parser():
    # Epoch-style reset value is converted to a relative wait.
    epoch_wait = cli._reset_hint_s(f"rate limit; reset {int(cli.time.time()) + 120}")
    assert epoch_wait is not None
    assert 100 <= epoch_wait <= 130

    # "retry after N" seconds is honored directly.
    retry_wait = cli._reset_hint_s("secondary rate limit, retry after 45 seconds")
    assert retry_wait == 45

    assert cli._reset_hint_s("secondary rate limit") is None


def test_primary_rate_limit_uses_gh_rate_limit_reset(monkeypatch):
    slept: list[float] = []
    now = 1_000_000
    reset = now + 1200
    calls = {"n": 0}

    def fake_run(cmd, *, cwd=None):
        calls["n"] += 1
        if cmd == ["gh", "repo", "create"]:
            return _cp(1, stderr="API rate limit exceeded")
        if cmd == ["gh", "api", "rate_limit"]:
            return _cp(
                0,
                stdout=json.dumps(
                    {"resources": {"core": {"remaining": 0, "reset": reset}}}
                ),
            )
        raise AssertionError(cmd)

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(cli.time, "time", lambda: now)
    monkeypatch.setattr(cli.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(cli.random, "uniform", lambda a, b: 0.0)

    r = cli._run_gh_with_rate_limit_backoff(
        ["gh", "repo", "create"], initial_wait_s=10, max_wait_s=3600, max_retries=1,
    )

    assert r.returncode == 1
    assert slept == [1200]


def test_run_loads_rate_limit_backoff_config(tmp_path, monkeypatch):
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    # Isolate the module-level config so run()'s in-place update doesn't leak.
    monkeypatch.setattr(cli, "_GH_BACKOFF", dict(cli._GH_BACKOFF))
    monkeypatch.setattr(
        cli,
        "load_rc",
        lambda: {
            "repo": {
                "corpus": str(corpus),
                "rate_limit_initial_wait_s": 11,
                "rate_limit_secondary_floor_s": 22,
                "rate_limit_max_wait_s": 33,
                "rate_limit_max_retries": 4,
            }
        },
    )
    monkeypatch.setattr(cli, "_action_clone", lambda *args: 0)

    assert cli.run(["clone", "--all", "--dry-run"]) == 0
    assert cli._GH_BACKOFF["initial_wait_s"] == 11.0
    assert cli._GH_BACKOFF["secondary_floor_s"] == 22.0
    assert cli._GH_BACKOFF["max_wait_s"] == 33.0
    assert cli._GH_BACKOFF["max_retries"] == 4


def test_publish_retries_on_rate_limit_after_long_secondary_wait(tmp_path, monkeypatch):
    bundle = tmp_path / "KR1a0001"
    bundle.mkdir()
    (bundle / f"{bundle.name}.manifest.yaml").write_text("metadata: {}\n", encoding="utf-8")
    (bundle / ".git").mkdir()

    slept: list[float] = []
    seq = iter(
        [
            _cp(1),  # git remote get-url origin → no origin yet
            _cp(1, stderr="You have exceeded a secondary rate limit"),  # first create
            _cp(0, stdout="created"),  # retried create
        ]
    )

    def fake_run(cmd, *, cwd=None):
        return next(seq)

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(cli.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(cli.random, "uniform", lambda a, b: 0.0)

    result = cli._action_publish(
        bundle, org="bkkbooks", visibility="public", create_delay_s=0, dry_run=False,
    )

    assert result == "ok"
    assert slept == [300]


def test_publish_reports_expected_delay_when_repo_create_block_persists(
    tmp_path, monkeypatch,
):
    bundle = tmp_path / "KR1a0001"
    bundle.mkdir()
    (bundle / f"{bundle.name}.manifest.yaml").write_text("metadata: {}\n", encoding="utf-8")
    (bundle / ".git").mkdir()

    msg = (
        "GraphQL: You have created too many repositories, too quickly. "
        "Please try again later. (createRepository)"
    )
    seq = iter(
        [
            _cp(1),  # git remote get-url origin → no origin yet
            _cp(1, stderr=msg),
            _cp(1, stderr=msg),
        ]
    )

    def fake_run(cmd, *, cwd=None):
        return next(seq)

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(cli, "_GH_BACKOFF", dict(cli._GH_BACKOFF, max_retries=1))

    result = cli._action_publish(
        bundle, org="bkkbooks", visibility="public", create_delay_s=0, dry_run=False,
    )

    assert result.startswith("error: gh repo create: gh secondary/content-creation limit")
    assert "next retry would wait about 300s" in result
    assert "You have created too many repositories, too quickly" in result
