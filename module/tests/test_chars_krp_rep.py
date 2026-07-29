from __future__ import annotations

import subprocess
from pathlib import Path

from bkk.chars import cli
from bkk.chars.krp_rep import apply_krp_replacements, load_krp_replacement_list


def _write_refs(refs: Path) -> None:
    refs.mkdir()
    (refs / "normlist-2016-02-05.txt").write_text(
        "KR0001\t所\nKR0002\t若\n",
        encoding="utf-8",
    )
    (refs / "replist-2016-02-05.txt").write_text(
        "KR0001\t𫠦\nKR0003\t說\n",
        encoding="utf-8",
    )


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _write_repo(root: Path, text_id: str, *, branch: str = "master") -> Path:
    repo = root / text_id[:4] / text_id
    repo.mkdir(parents=True)
    _git("init", "-b", "master", cwd=repo)
    juan = repo / f"{text_id}_001.txt"
    juan.write_text("甲&KR0001;乙&KR9999;\n", encoding="utf-8")
    _git("add", juan.name, cwd=repo)
    if branch != "master":
        _git("checkout", "-b", branch, cwd=repo)
    return repo


def test_apply_krp_replacements_preserves_unmapped() -> None:
    new_text, count, unresolved = apply_krp_replacements(
        "甲&KR0001;&KR9999;",
        {"KR0001": "所"},
    )

    assert new_text == "甲所&KR9999;"
    assert count == 1
    assert unresolved == {"KR9999": 1}


def test_load_krp_replacement_list_preserves_replacement_spacing(tmp_path: Path) -> None:
    path = tmp_path / "list.txt"
    path.write_text("KR0132\t𦒿 \n", encoding="utf-8")

    assert load_krp_replacement_list(path)["KR0132"] == "𦒿 "


def test_krp_rep_dry_run_uses_normlist_on_master(tmp_path: Path, capsys) -> None:
    refs = tmp_path / "refs"
    _write_refs(refs)
    repo = _write_repo(tmp_path / "krp", "KR0a0001")

    rc = cli.run([
        "krp-rep",
        "--repo",
        str(repo),
        "--refs-dir",
        str(refs),
    ])

    assert rc == 1
    assert (repo / "KR0a0001_001.txt").read_text(encoding="utf-8") == (
        "甲&KR0001;乙&KR9999;\n"
    )
    out = capsys.readouterr().out
    assert "using normlist-2016-02-05.txt" in out
    assert "would replace 1 occurrence" in out
    assert "dry-run only; pass --write" in out


def test_krp_rep_write_uses_replist_off_master(tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    _write_refs(refs)
    repo = _write_repo(tmp_path / "krp", "KR0a0001", branch="WYG")

    rc = cli.run([
        "krp-rep",
        "--repo",
        str(repo),
        "--refs-dir",
        str(refs),
        "--write",
    ])

    assert rc == 1
    assert (repo / "KR0a0001_001.txt").read_text(encoding="utf-8") == (
        "甲𫠦乙&KR9999;\n"
    )


def test_krp_rep_resolves_text_id_under_krp_root(tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    _write_refs(refs)
    krp_root = tmp_path / "krp"
    repo = _write_repo(krp_root, "KR0a0001")

    rc = cli.run([
        "krp-rep",
        "--krp-root",
        str(krp_root),
        "--text-id",
        "KR0a0001",
        "--refs-dir",
        str(refs),
        "--write",
    ])

    assert rc == 1
    assert (repo / "KR0a0001_001.txt").read_text(encoding="utf-8") == (
        "甲所乙&KR9999;\n"
    )


def test_krp_rep_includes_untracked_new_files(tmp_path: Path) -> None:
    refs = tmp_path / "refs"
    _write_refs(refs)
    repo = _write_repo(tmp_path / "krp", "KR0a0001")
    new_file = repo / "KR0a0001_002.txt"
    new_file.write_text("新&KR0001;\n", encoding="utf-8")

    rc = cli.run([
        "krp-rep",
        "--repo",
        str(repo),
        "--refs-dir",
        str(refs),
        "--write",
    ])

    assert rc == 1
    assert new_file.read_text(encoding="utf-8") == "新所\n"
