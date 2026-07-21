"""End-to-end tests for the corpus merge."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

import pytest
import yaml

from bkk.index import Index, build_index, merge_bundles
from bkk.index.cli import run as cli_run
from bkk.index.merge import discover_bundles, is_stale, read_text_id_list


def _write_bundle(root: Path, textid: str, body_text: str,
                  variants: list[dict] | None = None,
                  editions: list[dict] | None = None) -> Path:
    bundle_dir = root / textid
    bundle_dir.mkdir(parents=True)
    (bundle_dir / f"{textid}_001.yaml").write_text(
        yaml.safe_dump({
            "canonical_identifier": f"bkk:test/{textid}/v1/juan/1",
            "seq": 1,
            "body": {
                "text": body_text,
                "hash": "sha256:0",
                "markers": [{"type": "variant", **v} for v in (variants or [])],
            },
            "hash": "sha256:0",
        }, allow_unicode=True),
        encoding="utf-8",
    )
    (bundle_dir / f"{textid}.manifest.yaml").write_text(
        yaml.safe_dump({
            "canonical_identifier": f"bkk:test/{textid}/v1",
            "editions": editions or [{"short": "X", "label": "x"}],
            "assets": {"parts": [
                {"seq": 1, "filename": f"{textid}_001.yaml", "hash": "sha256:0"},
            ]},
            "table_of_contents": [
                {"ref": {"seq": 1, "marker_id": f"{textid}_001-1a",
                         "span": ["body", 0, len(body_text)]},
                 "label": f"{textid} juan"},
            ],
        }, allow_unicode=True),
        encoding="utf-8",
    )
    return bundle_dir


def test_discover_bundles_and_prefix(tmp_path):
    _write_bundle(tmp_path, "KR1a0001", "abc")
    _write_bundle(tmp_path, "KR1a0002", "def")
    _write_bundle(tmp_path, "KR3a0001", "ghi")
    # Non-bundle directory: ignored.
    (tmp_path / "scratch").mkdir()

    assert [b.name for b in discover_bundles(tmp_path)] == [
        "KR1a0001", "KR1a0002", "KR3a0001",
    ]
    assert [b.name for b in discover_bundles(tmp_path, prefix="KR1a")] == [
        "KR1a0001", "KR1a0002",
    ]


def test_discover_bundles_sectioned_layout(tmp_path):
    """`bkk import --by-section` puts bundles under ``<corpus>/<section>/<id>/``.
    The discoverer should walk one level deeper to find them, and ``prefix``
    should still filter on the leaf text-id name."""
    _write_bundle(tmp_path / "KR1a", "KR1a0001", "abc")
    _write_bundle(tmp_path / "KR1a", "KR1a0002", "def")
    _write_bundle(tmp_path / "KR3a", "KR3a0001", "ghi")

    assert [b.name for b in discover_bundles(tmp_path)] == [
        "KR1a0001", "KR1a0002", "KR3a0001",
    ]
    assert [b.name for b in discover_bundles(tmp_path, prefix="KR1a")] == [
        "KR1a0001", "KR1a0002",
    ]


def test_discover_bundles_mixed_layout(tmp_path):
    """Flat and sectioned bundles can coexist in one corpus; the result is
    sorted by leaf id regardless of where the bundle physically sits."""
    _write_bundle(tmp_path, "KR1a0001", "abc")             # flat
    _write_bundle(tmp_path / "KR1a", "KR1a0002", "def")    # sectioned
    _write_bundle(tmp_path / "KR3a", "KR3a0001", "ghi")    # sectioned

    assert [b.name for b in discover_bundles(tmp_path)] == [
        "KR1a0001", "KR1a0002", "KR3a0001",
    ]


def test_discover_bundles_three_level_layout(tmp_path):
    """The devcorpus mixes a 2-level TLS layout with a 3-level KRP layout
    (``krp/<section>/<bundle>/``). Discovery must descend deep enough to
    find KRP bundles while still picking up the shallower TLS ones."""
    _write_bundle(tmp_path / "krp" / "KR1a", "KR1a0001", "abc")
    _write_bundle(tmp_path / "krp" / "KR1a", "KR1a0002", "def")
    _write_bundle(tmp_path / "tls", "KR3fc058", "ghi")

    assert [b.name for b in discover_bundles(tmp_path)] == [
        "KR1a0001", "KR1a0002", "KR3fc058",
    ]
    assert [b.name for b in discover_bundles(tmp_path, prefix="KR1a")] == [
        "KR1a0001", "KR1a0002",
    ]


def test_merge_unions_sectioned_bundles(tmp_path):
    """End-to-end: merge_bundles works on a sectioned corpus."""
    _write_bundle(tmp_path / "KR0a", "KR0a0001", "abcDEFghi")
    _write_bundle(tmp_path / "KR0a", "KR0a0002", "xyzDEFwvu")
    out = tmp_path / "corpus.bkkx"
    merge_bundles(tmp_path, out)

    with Index(out) as ix:
        assert ix.bundles == ["KR0a0001", "KR0a0002"]
        textids = sorted({h.textid for h in ix.search("DEF")})
    assert textids == ["KR0a0001", "KR0a0002"]


def test_merge_unions_two_bundles(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "abcDEFghi")
    _write_bundle(tmp_path, "KR0a0002", "xyzDEFwvu")
    out = tmp_path / "corpus.bkkx"
    merge_bundles(tmp_path, out)

    with Index(out) as ix:
        assert ix.bundles == ["KR0a0001", "KR0a0002"]
        hits = list(ix.search("DEF"))
    textids = sorted({h.textid for h in hits})
    assert textids == ["KR0a0001", "KR0a0002"]
    # Each hit reports the right master offset within its own juan.
    by_textid = {h.textid: h for h in hits}
    assert by_textid["KR0a0001"].master_offset == 3
    assert by_textid["KR0a0002"].master_offset == 3


def test_merge_preserves_variant_aware_search(tmp_path):
    body = "專然未嘗不盡天下之議"
    variants = [{"offset": 3, "length": 1, "content": "嘗", "SBCK": "甞"}]
    _write_bundle(tmp_path, "KRTEST001", body, variants,
                  editions=[{"short": "SBCK", "label": "SBCK"}])
    _write_bundle(tmp_path, "KRTEST002", "unrelated content")
    out = tmp_path / "corpus.bkkx"
    merge_bundles(tmp_path, out)

    with Index(out) as ix:
        master_hits = list(ix.search("嘗不盡"))
        witness_hits = list(ix.search("甞不盡"))

    assert len(master_hits) == 1
    assert len(witness_hits) == 1
    m, w = master_hits[0], witness_hits[0]
    assert m.textid == w.textid == "KRTEST001"
    assert m.master_offset == w.master_offset == 3
    assert w.matched_via == "SBCK"


def test_textid_scope_filter(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "shared text")
    _write_bundle(tmp_path, "KR0a0002", "shared text twice; shared again")
    out = tmp_path / "corpus.bkkx"
    merge_bundles(tmp_path, out)

    with Index(out) as ix:
        all_hits = list(ix.search("shared"))
        scoped = list(ix.search("shared", textid="KR0a0002"))
    assert {h.textid for h in all_hits} == {"KR0a0001", "KR0a0002"}
    assert {h.textid for h in scoped} == {"KR0a0002"}


def test_prefix_filter(tmp_path):
    _write_bundle(tmp_path, "KR1a0001", "needle here")
    _write_bundle(tmp_path, "KR3a0001", "needle elsewhere")
    out = tmp_path / "corpus.bkkx"
    merge_bundles(tmp_path, out, prefix="KR1a")
    with Index(out) as ix:
        assert ix.bundles == ["KR1a0001"]
        hits = list(ix.search("needle"))
    assert {h.textid for h in hits} == {"KR1a0001"}


def test_read_text_id_list_accepts_first_token_comments_and_short_ids(tmp_path):
    text_list = tmp_path / "list.txt"
    text_list.write_text(
        "\n".join([
            "# name: sample",
            "",
            "KR1a0001 7 A title",
            "1a2 short form",
            "KR1a0001 duplicate",
        ]),
        encoding="utf-8",
    )

    assert read_text_id_list(text_list) == ["KR1a0001", "KR1a0002"]


def test_read_text_id_list_rejects_non_kr_first_token(tmp_path):
    text_list = tmp_path / "list.txt"
    text_list.write_text("not-a-kr-id\n", encoding="utf-8")

    with pytest.raises(ValueError, match="expected a KR text id"):
        read_text_id_list(text_list)


def test_merge_text_id_subset_filter(tmp_path):
    _write_bundle(tmp_path, "KR1a0001", "needle here")
    _write_bundle(tmp_path, "KR1a0002", "needle elsewhere")
    _write_bundle(tmp_path, "KR3a0001", "needle outside")
    out = tmp_path / "corpus.bkkx"
    merge_bundles(tmp_path, out, text_ids=["KR1a0002", "KR1a0001"])

    with Index(out) as ix:
        assert ix.bundles == ["KR1a0001", "KR1a0002"]
        hits = list(ix.search("needle"))
    assert {h.textid for h in hits} == {"KR1a0001", "KR1a0002"}


def test_merge_text_id_subset_errors_on_missing_bundle_without_replacing_out(tmp_path):
    _write_bundle(tmp_path, "KR1a0001", "abc")
    out = tmp_path / "corpus.bkkx"
    out.write_text("keep", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match="KR1a0099"):
        merge_bundles(tmp_path, out, text_ids=["KR1a0001", "KR1a0099"])

    assert out.read_text(encoding="utf-8") == "keep"


def test_merge_text_id_subset_empty_list_does_not_mean_all_bundles(tmp_path):
    _write_bundle(tmp_path, "KR1a0001", "abc")
    out = tmp_path / "corpus.bkkx"

    with pytest.raises(FileNotFoundError, match="text id list"):
        merge_bundles(tmp_path, out, text_ids=[])

    assert not out.exists()


def test_stale_triggers_rebuild(tmp_path):
    bundle = _write_bundle(tmp_path, "KR0a0001", "first version")
    bkkx = bundle / "KR0a0001.bkkx"
    build_index(bundle, bkkx)
    assert not is_stale(bundle, bkkx)

    # Touch a juan file to be newer than the .bkkx.
    juan = bundle / "KR0a0001_001.yaml"
    later = time.time() + 5
    os.utime(juan, (later, later))
    assert is_stale(bundle, bkkx)

    # Rewrite with new body text and re-merge: the merge must rebuild and
    # surface the new content.
    juan.write_text(
        yaml.safe_dump({
            "canonical_identifier": "bkk:test/KR0a0001/v1/juan/1",
            "seq": 1,
            "body": {"text": "second version", "hash": "sha256:0", "markers": []},
            "hash": "sha256:0",
        }, allow_unicode=True),
        encoding="utf-8",
    )
    later2 = time.time() + 10
    os.utime(juan, (later2, later2))
    out = tmp_path / "corpus.bkkx"
    merge_bundles(tmp_path, out)
    with Index(out) as ix:
        first = list(ix.search("first"))
        second = list(ix.search("second"))
    assert first == []
    assert len(second) == 1


def test_old_schema_version_triggers_rebuild(tmp_path):
    """A .bkkx with an outdated schema_version is treated as stale so that
    merge_bundles rebuilds it instead of erroring inside _merge_one."""
    bundle = _write_bundle(tmp_path, "KR0a0099", "abc")
    bkkx = bundle / "KR0a0099.bkkx"
    build_index(bundle, bkkx)
    assert not is_stale(bundle, bkkx)

    # Downgrade the recorded schema_version on the existing artifact.
    conn = sqlite3.connect(str(bkkx))
    try:
        conn.execute("UPDATE meta SET value = '0' WHERE key = 'schema_version'")
        conn.commit()
    finally:
        conn.close()

    assert is_stale(bundle, bkkx)

    # merge_bundles must rebuild — not raise the version-mismatch ValueError.
    out = tmp_path / "corpus.bkkx"
    merge_bundles(tmp_path, out)
    with Index(out) as ix:
        assert len(list(ix.search("abc"))) == 1


def test_malformed_toc_span_skipped(tmp_path, caplog):
    """Old TOC entries with a 2-element span must be logged + skipped, not crash."""
    bundle = _write_bundle(tmp_path, "KR0a0100", "hello world")
    manifest_path = bundle / "KR0a0100.manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    # Inject a legacy-shaped span (2 elements) and a string-shaped one.
    manifest["table_of_contents"] = [
        {"ref": {"seq": 1, "marker_id": "good", "span": ["body", 0, 5]},
         "label": "good"},
        {"ref": {"seq": 1, "marker_id": "legacy-2el", "span": [0, 5]},
         "label": "legacy"},
        {"ref": {"seq": 1, "marker_id": "legacy-str", "span": ["body", "0:5"]},
         "label": "legacy"},
    ]
    manifest_path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True), encoding="utf-8"
    )

    out = tmp_path / "corpus.bkkx"
    with caplog.at_level("WARNING", logger="bkk.index"):
        merge_bundles(tmp_path, out)

    # Both malformed entries surfaced as warnings.
    msgs = "\n".join(r.message for r in caplog.records)
    assert "legacy-2el" in msgs
    assert "legacy-str" in msgs

    # Only the well-formed TOC entry made it into the index.
    conn = sqlite3.connect(str(out))
    try:
        rows = conn.execute(
            "SELECT marker_id FROM toc ORDER BY marker_id"
        ).fetchall()
    finally:
        conn.close()
    assert [r[0] for r in rows] == ["good"]


def test_no_build_errors_when_missing(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "abc")
    out = tmp_path / "corpus.bkkx"
    with pytest.raises(FileNotFoundError, match="--no-build"):
        merge_bundles(tmp_path, out, no_build=True)


def _write_bad_voice_bundle(root: Path, textid: str, body_text: str) -> Path:
    """Bundle with an out-of-range voice marker — build_index will raise."""
    bundle_dir = root / textid
    bundle_dir.mkdir(parents=True)
    (bundle_dir / f"{textid}_001.yaml").write_text(
        yaml.safe_dump({
            "canonical_identifier": f"bkk:test/{textid}/v1/juan/1",
            "seq": 1,
            "body": {
                "text": body_text,
                "hash": "sha256:0",
                "markers": [{
                    "type": "voice", "name": "root", "id": "v1",
                    "offset": 0, "length": len(body_text) + 99,
                }],
            },
            "hash": "sha256:0",
        }, allow_unicode=True),
        encoding="utf-8",
    )
    (bundle_dir / f"{textid}.manifest.yaml").write_text(
        yaml.safe_dump({
            "canonical_identifier": f"bkk:test/{textid}/v1",
            "editions": [{"short": "X", "label": "x"}],
            "assets": {"parts": [
                {"seq": 1, "filename": f"{textid}_001.yaml", "hash": "sha256:0"},
            ]},
            "table_of_contents": [
                {"ref": {"seq": 1, "marker_id": f"{textid}_001-1a",
                         "span": ["body", 0, len(body_text)]},
                 "label": f"{textid} juan"},
            ],
        }, allow_unicode=True),
        encoding="utf-8",
    )
    return bundle_dir


def test_merge_skips_bundle_that_fails_to_build(tmp_path, capsys):
    """A per-bundle build failure should not abort the whole merge run;
    the merge proceeds with the remaining bundles and the skipped ones are
    listed at the end."""
    _write_bundle(tmp_path, "KR0a0001", "abc")
    _write_bad_voice_bundle(tmp_path, "KR0a0002", "xyz")
    _write_bundle(tmp_path, "KR0a0003", "def")
    out = tmp_path / "corpus.bkkx"

    merge_bundles(tmp_path, out, progress=True)

    err = capsys.readouterr().err
    assert "[build 2/3] KR0a0002 SKIPPED" in err
    assert "skipped 1 bundle(s)" in err
    assert "KR0a0002: build:" in err

    # The two well-formed bundles still made it into the merged corpus.
    conn = sqlite3.connect(str(out))
    try:
        rows = [r[0] for r in conn.execute(
            "SELECT textid FROM bundle ORDER BY textid"
        )]
    finally:
        conn.close()
    assert rows == ["KR0a0001", "KR0a0003"]


def test_cli_merge_defaults_out_to_corpus_underscore(tmp_path, monkeypatch):
    """`bkk.index merge <corpus>` with no --out writes <corpus>/_corpus.bkkx."""
    _write_bundle(tmp_path, "KR0a0001", "abc")
    monkeypatch.setattr("bkk.config.load_rc", lambda: {})
    rc = cli_run(["merge", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "_corpus.bkkx").is_file()


def test_cli_merge_out_from_index_rc(tmp_path, monkeypatch):
    """`index.out` in .bkkrc beats the <corpus>/_corpus.bkkx default."""
    _write_bundle(tmp_path, "KR0a0001", "abc")
    elsewhere = tmp_path / "elsewhere" / "merged.bkkx"
    elsewhere.parent.mkdir()
    monkeypatch.setattr("bkk.config.load_rc", lambda: {"index": {"out": elsewhere}})
    rc = cli_run(["merge", str(tmp_path)])
    assert rc == 0
    assert elsewhere.is_file()
    assert not (tmp_path / "_corpus.bkkx").exists()


def test_cli_merge_corpus_from_global_rc(tmp_path, monkeypatch):
    """`global.corpus` in .bkkrc supplies the positional, --out defaults under it."""
    _write_bundle(tmp_path, "KR0a0001", "abc")
    monkeypatch.setattr("bkk.config.load_rc", lambda: {"global": {"corpus": tmp_path}})
    rc = cli_run(["merge"])
    assert rc == 0
    assert (tmp_path / "_corpus.bkkx").is_file()


def test_cli_merge_jobs_forwarded_to_per_bundle_builds(tmp_path, monkeypatch):
    bundle = _write_bundle(tmp_path, "KR0a0001", "abc")
    real_build_index = build_index
    calls = []

    def spy_build_index(bundle_dir, out_path=None, *, jobs=1):
        calls.append((Path(bundle_dir).name, jobs))
        return real_build_index(bundle_dir, out_path, jobs=jobs)

    monkeypatch.setattr("bkk.config.load_rc", lambda: {})
    monkeypatch.setattr("bkk.index.merge.build_index", spy_build_index)

    rc = cli_run(["merge", str(tmp_path), "--jobs", "2"])

    assert rc == 0
    assert calls == [(bundle.name, 2)]


def test_cli_merge_text_list_filters_to_listed_bundles(tmp_path, monkeypatch):
    _write_bundle(tmp_path, "KR1a0001", "needle here")
    _write_bundle(tmp_path, "KR1a0002", "needle elsewhere")
    _write_bundle(tmp_path, "KR3a0001", "needle outside")
    text_list = tmp_path / "list.txt"
    text_list.write_text("# name: subset\nKR1a0002 3 title\n", encoding="utf-8")
    out = tmp_path / "subset.bkkx"
    monkeypatch.setattr("bkk.config.load_rc", lambda: {})

    rc = cli_run([
        "merge", str(tmp_path),
        "--text-list", str(text_list),
        "--out", str(out),
    ])

    assert rc == 0
    with Index(out) as ix:
        assert ix.bundles == ["KR1a0002"]


def test_cli_merge_text_list_conflicts_with_prefix(tmp_path, monkeypatch):
    _write_bundle(tmp_path, "KR1a0001", "abc")
    text_list = tmp_path / "list.txt"
    text_list.write_text("KR1a0001\n", encoding="utf-8")
    monkeypatch.setattr("bkk.config.load_rc", lambda: {})

    with pytest.raises(SystemExit):
        cli_run([
            "merge", str(tmp_path),
            "--text-list", str(text_list),
            "--text-prefix", "KR1a",
        ])


def test_cli_build_all_builds_per_bundle_indices(tmp_path, monkeypatch):
    _write_bundle(tmp_path / "KR0a", "KR0a0001", "abc")
    _write_bundle(tmp_path / "KR0a", "KR0a0002", "def")
    monkeypatch.setattr("bkk.config.load_rc", lambda: {})

    rc = cli_run(["build", "--all", str(tmp_path), "--jobs", "2"])

    assert rc == 0
    assert (tmp_path / "KR0a" / "KR0a0001" / "KR0a0001.bkkx").is_file()
    assert (tmp_path / "KR0a" / "KR0a0002" / "KR0a0002.bkkx").is_file()


def test_cli_build_text_id_resolves_from_configured_corpus(tmp_path, monkeypatch):
    _write_bundle(tmp_path / "KR0a", "KR0a0001", "abc")
    monkeypatch.setattr("bkk.config.load_rc", lambda: {"global": {"corpus": tmp_path}})

    rc = cli_run(["build", "--text-id", "KR0a0001"])

    assert rc == 0
    assert (tmp_path / "KR0a" / "KR0a0001" / "KR0a0001.bkkx").is_file()


def test_cli_build_selector_conflicts_are_rejected(tmp_path, monkeypatch):
    bundle = _write_bundle(tmp_path, "KR0a0001", "abc")
    monkeypatch.setattr("bkk.config.load_rc", lambda: {})

    with pytest.raises(SystemExit):
        cli_run(["build", str(bundle), "--text-id", "KR0a0001"])
    with pytest.raises(SystemExit):
        cli_run(["build", "--all", str(tmp_path), "--out", str(tmp_path / "x.bkkx")])


def test_progress_emits_per_bundle_lines(tmp_path, capsys):
    _write_bundle(tmp_path, "KR0a0001", "abc")
    _write_bundle(tmp_path, "KR0a0002", "def")
    out = tmp_path / "corpus.bkkx"
    merge_bundles(tmp_path, out, progress=True)
    err = capsys.readouterr().err
    assert "[build 1/2] KR0a0001 built" in err
    assert "[build 2/2] KR0a0002 built" in err
    assert "[merge 1/2] KR0a0001" in err
    assert "[merge 2/2] KR0a0002" in err
    assert "done in" in err


def test_progress_silent_by_default(tmp_path, capsys):
    _write_bundle(tmp_path, "KR0a0001", "abc")
    out = tmp_path / "corpus.bkkx"
    merge_bundles(tmp_path, out)
    assert capsys.readouterr().err == ""


def test_cli_merge_section_filters_and_renames_out(tmp_path, monkeypatch):
    """`--section KR1a` filters by prefix and writes _KR1a.bkkx next to the default."""
    _write_bundle(tmp_path, "KR1a0001", "needle here")
    _write_bundle(tmp_path, "KR3a0001", "needle elsewhere")
    monkeypatch.setattr("bkk.config.load_rc", lambda: {})
    rc = cli_run(["merge", str(tmp_path), "--section", "KR1a"])
    assert rc == 0
    out = tmp_path / "_KR1a.bkkx"
    assert out.is_file()
    assert not (tmp_path / "_corpus.bkkx").exists()
    with Index(out) as ix:
        assert ix.bundles == ["KR1a0001"]


def test_cli_merge_section_uses_index_out_directory(tmp_path, monkeypatch):
    """`--section` places _<section>.bkkx alongside the configured index.out."""
    _write_bundle(tmp_path, "KR1a0001", "abc")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    rc_out = elsewhere / "merged.bkkx"
    monkeypatch.setattr("bkk.config.load_rc", lambda: {"index": {"out": rc_out}})
    rc = cli_run(["merge", str(tmp_path), "--section", "KR1a"])
    assert rc == 0
    assert (elsewhere / "_KR1a.bkkx").is_file()
    assert not rc_out.exists()


def test_cli_merge_section_and_prefix_conflict(tmp_path, monkeypatch):
    _write_bundle(tmp_path, "KR1a0001", "abc")
    monkeypatch.setattr("bkk.config.load_rc", lambda: {})
    with pytest.raises(SystemExit):
        cli_run(["merge", str(tmp_path), "--section", "KR1a", "--prefix", "KR1a"])


def test_cli_merge_explicit_out_wins(tmp_path, monkeypatch):
    """An explicit --out beats both index.out and the corpus-relative default."""
    _write_bundle(tmp_path, "KR0a0001", "abc")
    rc_out = tmp_path / "rc.bkkx"
    cli_out = tmp_path / "cli.bkkx"
    monkeypatch.setattr("bkk.config.load_rc", lambda: {"index": {"out": rc_out}})
    rc = cli_run(["merge", str(tmp_path), "--out", str(cli_out)])
    assert rc == 0
    assert cli_out.is_file()
    assert not rc_out.exists()
    assert not (tmp_path / "_corpus.bkkx").exists()


def test_bundle_table_provenance(tmp_path):
    _write_bundle(tmp_path, "KR0a0001", "abc",
                  editions=[{"short": "SBCK", "label": "s"}])
    _write_bundle(tmp_path, "KR0a0002", "def",
                  editions=[{"short": "WYG", "label": "w"}])
    out = tmp_path / "corpus.bkkx"
    merge_bundles(tmp_path, out)
    conn = sqlite3.connect(out)
    rows = conn.execute(
        "SELECT textid, editions, source_path, source_hash FROM bundle "
        "ORDER BY textid"
    ).fetchall()
    conn.close()
    assert [r[0] for r in rows] == ["KR0a0001", "KR0a0002"]
    assert "SBCK" in rows[0][1]
    assert "WYG" in rows[1][1]
    for textid, _, src_path, src_hash in rows:
        assert src_hash.startswith("sha256:")
        assert Path(src_path).name == f"{textid}.bkkx"


def test_ids_do_not_collide(tmp_path):
    # Both bundles will produce juan_id=1, bucket_id=1 in their per-bundle
    # files; the merger must shift them so the merged DB has unique PKs.
    _write_bundle(tmp_path, "KR0a0001", "abc",
                  variants=[{"offset": 1, "length": 1, "content": "b", "X": "B"}])
    _write_bundle(tmp_path, "KR0a0002", "def",
                  variants=[{"offset": 1, "length": 1, "content": "e", "X": "E"}])
    out = tmp_path / "corpus.bkkx"
    merge_bundles(tmp_path, out)
    conn = sqlite3.connect(out)
    juan_ids = [r[0] for r in conn.execute("SELECT juan_id FROM juan ORDER BY juan_id")]
    bucket_ids = [r[0] for r in conn.execute("SELECT bucket_id FROM bucket ORDER BY bucket_id")]
    witness_ids = [r[0] for r in conn.execute("SELECT witness_id FROM witness ORDER BY witness_id")]
    variant_ids = [r[0] for r in conn.execute("SELECT variant_id FROM variant ORDER BY variant_id")]
    conn.close()
    for ids in (juan_ids, bucket_ids, witness_ids, variant_ids):
        assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"
        assert len(ids) == 2
