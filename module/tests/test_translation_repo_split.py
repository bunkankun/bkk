from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "split_translation_repos.py"
SPEC = importlib.util.spec_from_file_location("split_translation_repos", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
split_translation_repos = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = split_translation_repos
SPEC.loader.exec_module(split_translation_repos)


def _write_translation(root: Path, bundle_id: str = "KR1h0004-en-test") -> Path:
    bundle = root / "translations" / "KR1h" / "KR1h0004" / "en" / bundle_id
    bundle.mkdir(parents=True)
    (bundle / f"{bundle_id}.md").write_text(
        f"""---
canonical_identifier: bkk:translation/{bundle_id}/v1
source:
  canonical_identifier: bkk:krp/KR1h0004/v1
language: en
title: Test Translation
responsibility:
- {{role: translator, name: Tester}}
juan:
- {{seq: 1, label: '001', file: {bundle_id}_001.md, hash: 'sha256:0'}}
hash: sha256:0
---
# Test Translation
""",
        encoding="utf-8",
    )
    (bundle / f"{bundle_id}_001.md").write_text(
        """---
juan_seq: 1
juan_label: '001'
markers:
- {ref: 001-1a.3, corresp: [001-1a.3]}
---
[The Master said:]{@001-1a.3}
""",
        encoding="utf-8",
    )
    return bundle


def test_plan_maps_dense_bundle_to_dedicated_by_section_root(tmp_path: Path):
    source = tmp_path / "corpus"
    dest = tmp_path / "bkktranslations"
    _write_translation(source)

    plans = split_translation_repos.plan_translation_repos(
        source,
        dest,
        org="bkktranslations",
    )

    assert len(plans) == 1
    plan = plans[0]
    assert plan.bundle_id == "KR1h0004-en-test"
    assert plan.source_textid == "KR1h0004"
    assert plan.language == "en"
    assert plan.target_dir == dest / "KR1h" / "KR1h0004" / "en" / "KR1h0004-en-test"
    assert plan.remote == "bkktranslations/KR1h0004-en-test"


def test_dry_run_does_not_create_destination(tmp_path: Path, monkeypatch, capsys):
    source = tmp_path / "corpus"
    dest = tmp_path / "bkktranslations"
    _write_translation(source)
    monkeypatch.setattr(
        split_translation_repos,
        "load_rc",
        lambda: {"global": {"corpus": source, "translation_root": dest}},
    )

    rc = split_translation_repos.run(["--dry-run", "--no-github", "--yes"])

    out = capsys.readouterr()
    assert rc == 0
    assert "KR1h0004-en-test  plan: copy + git init/add/commit" in out.out
    assert not dest.exists()
