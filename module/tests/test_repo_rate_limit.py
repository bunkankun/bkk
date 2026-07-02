from __future__ import annotations

import subprocess
from pathlib import Path

from bkk.repo import cli


def _cp(returncode: int, stderr: str = "", stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["gh"], returncode, stdout, stderr)


def test_gh_backoff_retries_then_succeeds(monkeypatch):
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
    # Exponential backoff between the two failed attempts.
    assert slept == [10, 20]


def test_gh_backoff_gives_up_after_max_retries(monkeypatch):
    calls = {"n": 0}
    slept: list[float] = []

    def fake_run(cmd, *, cwd=None):
        calls["n"] += 1
        return _cp(1, stderr="API rate limit exceeded")

    monkeypatch.setattr(cli, "_run", fake_run)
    monkeypatch.setattr(cli.time, "sleep", lambda s: slept.append(s))
    monkeypatch.setattr(cli.random, "uniform", lambda a, b: 0.0)

    r = cli._run_gh_with_rate_limit_backoff(
        ["gh", "repo", "create"], initial_wait_s=5, max_wait_s=40, max_retries=3,
    )

    assert r.returncode == 1
    # 1 initial try + 3 retries.
    assert calls["n"] == 4
    # Backoff doubles but is capped at max_wait_s.
    assert slept == [5, 10, 20]


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


def test_rate_limit_wait_prefers_reset_hint():
    # Epoch-style reset value is converted to a relative wait.
    epoch_wait = cli._rate_limit_wait_s(
        f"rate limit; reset {int(cli.time.time()) + 120}", 999, max_wait_s=900,
    )
    assert 100 <= epoch_wait <= 130

    # "retry after N" seconds is honored directly.
    retry_wait = cli._rate_limit_wait_s(
        "secondary rate limit, retry after 45 seconds", 999, max_wait_s=900,
    )
    assert retry_wait == 45


def test_publish_retries_on_rate_limit(tmp_path, monkeypatch):
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
    assert len(slept) == 1
