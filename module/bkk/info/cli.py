"""``bkk info`` — show corpus, index, catalog, and config summary.

Reports three blocks of high-signal status:

- **corpus**: path, bundle count, breakdown by section prefix
- **index**: path, schema version, size, per-table counts, available voices,
  stale-bundle count
- **catalog**: path, schema version, size, per-table counts, source CSV,
  date range
- **config**: which ``.bkkrc`` files were loaded and the merged values for the
  ``global``, ``index``, and ``info`` sections

With ``--bundles`` (or ``--prefix``), a fourth per-bundle table is appended.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import yaml

from bkk.config import load_rc, rc_files
from bkk.exporter.read_bundle import read_bundle
from bkk.importer.charset import is_allowed_body_char
from bkk.importer.classify import bucket_sections
from bkk.importer.pua import summarise_pua_codepoints
from bkk.index.catalog import CATALOG_SCHEMA_VERSION
from bkk.index.merge import discover_bundles, is_stale
from bkk.index.schema import SCHEMA_VERSION
from bkk.importer.hashing import manifest_hash
from bkk.importer.write.yaml_writer import dump as dump_bkk_yaml, marker_to_flow
from bkk.marker_assets import effective_markers_for_bucket, load_marker_asset

_INDEX_TABLES = (
    "bundle", "juan", "bucket", "witness", "variant", "voice_range",
    "toc", "trigram",
)
_CATALOG_TABLES = ("catalog_bundle", "catalog_section", "catalog_identifier")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bkk info",
        description="Show corpus, index, and config summary.",
    )
    p.add_argument("--corpus", type=Path, default=None,
                   help="corpus root (default: [info].corpus or [global].corpus)")
    p.add_argument("--index", type=Path, default=None, dest="index_path",
                   help="merged .bkkx path (default: [info].index, "
                        "[index].out, or <corpus>/_corpus.bkkx)")
    p.add_argument("--catalog", type=Path, default=None, dest="catalog_path",
                   help="catalog .bkkc path (default: [info].catalog, "
                        "[serve].catalog, or <corpus>/_catalog.bkkc)")
    p.add_argument("--bundles", action="store_true",
                   help="emit per-bundle table")
    p.add_argument("--prefix", default=None,
                   help="restrict per-bundle table to textids starting with "
                        "PREFIX (implies --bundles)")
    p.add_argument("--text-id", default=None, dest="text_id",
                   help="show a focused per-text dossier (suppresses other "
                        "blocks)")
    p.add_argument("--readme", action="store_true",
                   help="write the dossier to <bundle>/Readme.md "
                        "(requires --text-id)")
    p.add_argument("--fix-editions", action="store_true", dest="fix_editions",
                   help="append editions referenced by page-break markers but "
                        "missing from the manifest's editions list "
                        "(requires --text-id)")
    p.add_argument("--json", action="store_true", dest="json_out",
                   help="emit JSON instead of text")
    return p


def collect_info_report(
    *,
    corpus: Path,
    index_path: Path,
    catalog_path: Path,
    rc: dict | None = None,
    want_bundles: bool = False,
    prefix: str | None = None,
    text_id: str | None = None,
) -> dict:
    """Build the info report dict. Shared by the CLI and the ``/admin/info`` endpoint."""
    if text_id is not None:
        return {"text": _collect_text(text_id, corpus, catalog_path)}
    report = {
        "corpus": _collect_corpus(corpus, prefix=None),
        "index": _collect_index(index_path, corpus),
        "catalog": _collect_catalog(catalog_path),
        "config": _collect_config(rc) if rc is not None else {"files": [], "sections": {}},
    }
    if want_bundles or prefix is not None:
        report["bundles"] = _collect_bundles(corpus, index_path, prefix=prefix)
    return report


def run(argv: list[str] | None = None) -> int:
    rc = load_rc()
    g = rc.get("global", {})
    idx_rc = rc.get("index", {})
    serve_rc = rc.get("serve", {})
    info_rc = rc.get("info", {})

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.readme and args.text_id is None:
        parser.error("--readme requires --text-id")
    if args.fix_editions and args.text_id is None:
        parser.error("--fix-editions requires --text-id")

    corpus = args.corpus or info_rc.get("corpus") or g.get("corpus")
    if corpus is None:
        parser.error("corpus is required (or set global.corpus / info.corpus in .bkkrc)")
    corpus = Path(corpus)

    if args.index_path is not None:
        index_path = Path(args.index_path)
    else:
        index_path = Path(
            info_rc.get("index")
            or idx_rc.get("out")
            or corpus / "_corpus.bkkx"
        )
    if args.catalog_path is not None:
        catalog_path = Path(args.catalog_path)
    else:
        catalog_path = Path(
            info_rc.get("catalog")
            or serve_rc.get("catalog")
            or corpus / "_catalog.bkkc"
        )

    want_bundles = args.bundles or args.prefix is not None
    try:
        report = collect_info_report(
            corpus=corpus,
            index_path=index_path,
            catalog_path=catalog_path,
            rc=rc,
            want_bundles=want_bundles,
            prefix=args.prefix,
            text_id=args.text_id,
        )
    except LookupError as exc:
        print(f"bkk info: {exc}", file=sys.stderr)
        return 1

    if args.fix_editions:
        undeclared = report["text"].get("undeclaredEditions") or []
        bundle_dir = Path(report["text"]["path"])
        added = _fix_editions(bundle_dir, args.text_id, undeclared)
        if added:
            print(
                f"added {len(added)} edition(s) to manifest: {', '.join(added)}",
                file=sys.stderr,
            )
            # Re-collect so downstream rendering reflects the patched manifest.
            report = collect_info_report(
                corpus=corpus, index_path=index_path,
                catalog_path=catalog_path, rc=rc,
                want_bundles=want_bundles, prefix=args.prefix,
                text_id=args.text_id,
            )
        else:
            print("no undeclared editions to add", file=sys.stderr)

    if args.json_out:
        print(json.dumps(report, indent=2, default=str))
    else:
        _render_text(report)

    if "text" in report:
        undeclared = report["text"].get("undeclaredEditions") or []
        if undeclared:
            print(
                f"warning: {len(undeclared)} edition(s) referenced by page-break "
                f"markers but missing from manifest editions list: "
                f"{', '.join(undeclared)}",
                file=sys.stderr,
            )

    if args.readme:
        readme_path = Path(report["text"]["path"]) / "Readme.md"
        readme_path.write_text(_render_text_markdown(report["text"]), encoding="utf-8")
        print(f"wrote {readme_path}", file=sys.stderr)

    return 0


def _collect_corpus(corpus: Path, *, prefix: str | None) -> dict:
    if not corpus.is_dir():
        return {
            "path": str(corpus),
            "exists": False,
            "bundle_count": 0,
            "by_section": {},
        }
    bundles = discover_bundles(corpus, prefix=prefix)
    by_section: Counter[str] = Counter()
    for b in bundles:
        section = b.name[:3] if len(b.name) >= 3 else b.name
        by_section[section] += 1
    return {
        "path": str(corpus),
        "exists": True,
        "bundle_count": len(bundles),
        "by_section": dict(sorted(by_section.items())),
    }


def _collect_index(index_path: Path, corpus: Path) -> dict:
    if not index_path.exists():
        return {"path": str(index_path), "built": False}

    out: dict = {
        "path": str(index_path),
        "built": True,
        "size_bytes": index_path.stat().st_size,
    }
    try:
        conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            schema_version = int(meta.get("schema_version", "0") or "0")
            out["schema_version"] = schema_version
            out["schema_current"] = SCHEMA_VERSION
            out["schema_ok"] = schema_version == SCHEMA_VERSION

            counts: dict[str, int] = {}
            for table in _INDEX_TABLES:
                try:
                    row = conn.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()
                    counts[table] = int(row[0]) if row else 0
                except sqlite3.OperationalError:
                    counts[table] = None  # table absent (older schema)
            out["counts"] = counts

            try:
                voices = [
                    r[0] for r in conn.execute(
                        "SELECT DISTINCT name FROM voice_range "
                        "ORDER BY name"
                    )
                ]
                out["voices"] = voices
            except sqlite3.OperationalError:
                out["voices"] = []
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        out["error"] = f"could not open index: {exc}"
        return out

    if corpus.is_dir():
        stale = 0
        checked = 0
        for bundle in discover_bundles(corpus):
            per_bkkx = bundle / f"{bundle.name}.bkkx"
            if not per_bkkx.exists():
                continue
            checked += 1
            if is_stale(bundle, per_bkkx):
                stale += 1
        out["per_bundle_indices_checked"] = checked
        out["per_bundle_indices_stale"] = stale
    return out


def _collect_catalog(catalog_path: Path) -> dict:
    if not catalog_path.exists():
        return {"path": str(catalog_path), "built": False}

    out: dict = {
        "path": str(catalog_path),
        "built": True,
        "size_bytes": catalog_path.stat().st_size,
    }
    try:
        conn = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
        try:
            conn.row_factory = sqlite3.Row
            meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
            schema_version = int(meta.get("schema_version", "0") or "0")
            out["schema_version"] = schema_version
            out["schema_current"] = CATALOG_SCHEMA_VERSION
            out["schema_ok"] = schema_version == CATALOG_SCHEMA_VERSION
            if meta.get("source_csv"):
                out["source_csv"] = meta["source_csv"]

            counts: dict[str, int | None] = {}
            for table in _CATALOG_TABLES:
                try:
                    row = conn.execute(
                        f"SELECT COUNT(*) FROM {table}"
                    ).fetchone()
                    counts[table] = int(row[0]) if row else 0
                except sqlite3.OperationalError:
                    counts[table] = None
            out["counts"] = counts

            try:
                row = conn.execute(
                    "SELECT MIN(index_date), MAX(index_date) "
                    "FROM catalog_bundle WHERE index_date != 9999"
                ).fetchone()
                if row and row[0] is not None and row[1] is not None:
                    out["date_min"] = int(row[0])
                    out["date_max"] = int(row[1])
            except sqlite3.OperationalError:
                pass
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        out["error"] = f"could not open catalog index: {exc}"
    return out


def _collect_config(rc: dict) -> dict:
    files = [str(p) for p in rc_files()]
    sections: dict[str, dict] = {}
    for name in ("global", "info", "index", "serve"):
        if name in rc:
            sections[name] = {k: str(v) for k, v in rc[name].items()}
    return {"files": files, "sections": sections}


def _collect_bundles(corpus: Path, index_path: Path,
                     *, prefix: str | None) -> list[dict]:
    if not corpus.is_dir():
        return []
    bundles = discover_bundles(corpus, prefix=prefix)
    by_id = {b.name: b for b in bundles}

    per: dict[str, dict] = {
        textid: {
            "textid": textid,
            "path": str(path),
            "juans": None,
            "buckets": None,
            "witnesses": None,
            "editions": [],
        }
        for textid, path in by_id.items()
    }

    if index_path.exists():
        try:
            conn = sqlite3.connect(f"file:{index_path}?mode=ro", uri=True)
            try:
                for textid, n in conn.execute(
                    "SELECT textid, COUNT(*) FROM juan GROUP BY textid"
                ):
                    if textid in per:
                        per[textid]["juans"] = int(n)
                for textid, n in conn.execute(
                    "SELECT j.textid, COUNT(*) FROM bucket b "
                    "JOIN juan j ON b.juan_id = j.juan_id "
                    "GROUP BY j.textid"
                ):
                    if textid in per:
                        per[textid]["buckets"] = int(n)
                for textid, n in conn.execute(
                    "SELECT j.textid, COUNT(*) FROM witness w "
                    "JOIN bucket b ON w.bucket_id = b.bucket_id "
                    "JOIN juan j ON b.juan_id = j.juan_id "
                    "GROUP BY j.textid"
                ):
                    if textid in per:
                        per[textid]["witnesses"] = int(n)
                try:
                    rows = conn.execute(
                        "SELECT textid, editions FROM bundle"
                    ).fetchall()
                    for textid, editions_json in rows:
                        if textid in per:
                            per[textid]["editions"] = json.loads(editions_json)
                except sqlite3.OperationalError:
                    pass
            finally:
                conn.close()
        except sqlite3.DatabaseError:
            pass

    for textid, entry in per.items():
        if entry["editions"]:
            continue
        manifest = by_id[textid] / f"{textid}.manifest.yaml"
        if not manifest.is_file():
            continue
        try:
            with manifest.open() as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError):
            continue
        eds = data.get("editions") or []
        entry["editions"] = [
            e.get("short", "") if isinstance(e, dict) else str(e)
            for e in eds
        ]

    return [per[k] for k in sorted(per)]


def _collect_text(text_id: str, corpus: Path, catalog_path: Path) -> dict:
    """Build a per-text dossier: metadata, dates, edition list, juan/char stats,
    PUA usage, marker inventory, manifest mtime.

    Raises ``LookupError`` if the bundle is not found in ``corpus``.
    """
    if not corpus.is_dir():
        raise LookupError(f"corpus directory not found: {corpus}")
    matches = [b for b in discover_bundles(corpus) if b.name == text_id]
    if not matches:
        raise LookupError(f"text id not found in corpus: {text_id}")
    bundle_dir = matches[0]
    manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    if not manifest_path.is_file():
        raise LookupError(f"manifest missing: {manifest_path}")

    manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    manifest_metadata = manifest_data.get("metadata") or {}
    editions = [
        {
            "short": e.get("short", "") if isinstance(e, dict) else str(e),
            "label": e.get("label", "") if isinstance(e, dict) else "",
        }
        for e in (manifest_data.get("editions") or [])
    ]
    mtime = manifest_path.stat().st_mtime
    manifest_date = _dt.datetime.fromtimestamp(
        mtime, tz=_dt.timezone.utc,
    ).isoformat(timespec="seconds")

    catalog_fields = _read_catalog_row(catalog_path, text_id)

    bundle = read_bundle(bundle_dir)

    chars: dict[str, dict[str, int]] = {
        "front": {"total": 0, "unique": 0},
        "body": {"total": 0, "unique": 0},
        "back": {"total": 0, "unique": 0},
    }
    unique_by_bucket: dict[str, set[str]] = {"front": set(), "body": set(), "back": set()}
    markers_by_type: Counter[str] = Counter()
    images_by_edition: Counter[str] = Counter()
    editions_in_markers: set[str] = set()
    all_texts: list[str] = []

    for juan in bundle.juans:
        front, body, back = bucket_sections(juan.sections)
        for bucket_name, secs in (("front", front), ("body", body), ("back", back)):
            for sec in secs:
                filtered = [ch for ch in sec.text if is_allowed_body_char(ch)]
                chars[bucket_name]["total"] += len(filtered)
                unique_by_bucket[bucket_name].update(filtered)
                all_texts.append(sec.text)
                for marker in sec.markers:
                    markers_by_type[marker.type] += 1

    _count_images_by_edition(
        bundle_dir, manifest_path, manifest_data,
        images_by_edition, editions_in_markers,
    )
    for bucket_name, uniq in unique_by_bucket.items():
        chars[bucket_name]["unique"] = len(uniq)
    chars["total"] = sum(chars[b]["total"] for b in ("front", "body", "back"))

    pua_summary = summarise_pua_codepoints(text_id, all_texts) or {
        "total_unique": 0, "total_occurrences": 0,
    }

    out: dict = {
        "textid": text_id,
        "path": str(bundle_dir),
        "manifestDate": manifest_date,
        "title": catalog_fields.get("title") or manifest_metadata.get("title"),
        "titlePinyin": catalog_fields.get("titlePinyin"),
        "titleEnglish": catalog_fields.get("titleEnglish"),
        "notBefore": catalog_fields.get("notBefore"),
        "notAfter": catalog_fields.get("notAfter"),
        "indexYear": catalog_fields.get("indexYear"),
        "editions": _merge_editions(editions, editions_in_markers, images_by_edition),
        "undeclaredEditions": sorted(
            editions_in_markers - {e["short"] for e in editions}
        ),
        "juanCount": len(bundle.juans),
        "chars": chars,
        "puaChars": {
            "total_unique": pua_summary["total_unique"],
            "total_occurrences": pua_summary["total_occurrences"],
        },
        "markersByType": dict(sorted(markers_by_type.items())),
    }
    if not catalog_fields:
        out["catalogPresent"] = False
    return out


def _merge_editions(
    declared: list[dict],
    seen: set[str],
    images: Counter,
) -> list[dict]:
    out: list[dict] = [
        {**e, "imageCount": images.get(e["short"], 0), "declared": True}
        for e in declared
    ]
    declared_shorts = {e["short"] for e in declared}
    for short in sorted(seen - declared_shorts):
        out.append({
            "short": short, "label": "",
            "imageCount": images.get(short, 0), "declared": False,
        })
    return out


def _fix_editions(
    bundle_dir: Path,
    text_id: str,
    undeclared: list[str],
) -> list[str]:
    """Append undeclared edition shorts to the manifest's editions list.

    Returns the shorts that were added. Re-emits the manifest with the BKK
    yaml dumper (stable formatting) and recomputes the manifest hash.
    """
    if not undeclared:
        return []
    manifest_path = bundle_dir / f"{text_id}.manifest.yaml"
    if not manifest_path.is_file():
        raise LookupError(f"manifest missing: {manifest_path}")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    eds = manifest.get("editions") or []
    existing = {e.get("short") for e in eds if isinstance(e, dict)}
    added: list[str] = []
    for short in undeclared:
        if short in existing:
            continue
        eds.append({"short": short})
        added.append(short)
    if not added:
        return []
    manifest["editions"] = eds
    _normalize_for_write(manifest)
    manifest["hash"] = manifest_hash(manifest)
    manifest_path.write_text(dump_bkk_yaml(manifest), encoding="utf-8")
    return added


def _normalize_for_write(manifest: dict) -> None:
    """Wrap leaf-list entries in flow style so the round-trip is byte-stable.

    Mirrors the on-disk BKK manifest style: ``assets.parts`` and
    ``assets.markers`` use flow-style mappings per entry; everything else
    stays in the default block style.
    """
    assets = manifest.get("assets") or {}
    for key in ("parts", "markers"):
        lst = assets.get(key)
        if isinstance(lst, list):
            assets[key] = [
                marker_to_flow(e) if isinstance(e, dict) else e
                for e in lst
            ]


def _count_images_by_edition(
    bundle_dir: Path,
    manifest_path: Path,
    manifest_data: dict,
    counter: Counter,
    editions_seen: set[str],
) -> None:
    """Walk raw page-break markers and (a) count those carrying a non-empty
    ``image`` field per edition, and (b) record every edition short id that
    appears in a page-break marker id, regardless of image field.

    Reads juan + marker-asset yaml directly because ``read_bundle`` drops
    marker extras during bucket splitting.
    """
    parts = manifest_data.get("assets", {}).get("parts") or []
    for entry in parts:
        if not isinstance(entry, dict):
            continue
        filename = entry.get("filename")
        seq = entry.get("seq")
        if not isinstance(filename, str) or not isinstance(seq, int):
            continue
        juan_path = bundle_dir / filename
        if not juan_path.is_file():
            continue
        try:
            juan = yaml.safe_load(juan_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        marker_asset = load_marker_asset(manifest_path.parent, manifest_data, seq)
        for bucket_name in ("front", "body", "back"):
            for m in effective_markers_for_bucket(juan, bucket_name, marker_asset):
                if m.get("type") != "page-break":
                    continue
                mid = m.get("id") or ""
                ed_parts = mid.split("_", 2)
                if len(ed_parts) >= 2 and ed_parts[1]:
                    edition = ed_parts[1]
                    editions_seen.add(edition)
                    if m.get("image"):
                        counter[edition] += 1


def _read_catalog_row(catalog_path: Path, text_id: str) -> dict:
    if not catalog_path.exists():
        return {}
    try:
        conn = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    except sqlite3.DatabaseError:
        return {}
    try:
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT title, title_pinyin, title_english, "
                "not_before, not_after, index_date "
                "FROM catalog_bundle WHERE textid = ?",
                (text_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return {}
    finally:
        conn.close()
    if row is None:
        return {}
    return {
        "title": row["title"],
        "titlePinyin": row["title_pinyin"],
        "titleEnglish": row["title_english"],
        "notBefore": row["not_before"],
        "notAfter": row["not_after"],
        "indexYear": row["index_date"],
    }


def _render_text(report: dict) -> None:
    if "text" in report and len(report) == 1:
        _render_text_block(report["text"])
        return
    c = report["corpus"]
    print("corpus")
    print(f"  path:     {c['path']}")
    if not c["exists"]:
        print("  (directory does not exist)")
    else:
        print(f"  bundles:  {c['bundle_count']}")
        if c["by_section"]:
            print("  by section:")
            width = max(len(s) for s in c["by_section"])
            for section, n in c["by_section"].items():
                print(f"    {section:<{width}}  {n:>5}")

    i = report["index"]
    print()
    print("index")
    print(f"  path:     {i['path']}")
    if not i["built"]:
        print("  (not built — run `bkk index merge`)")
    elif "error" in i:
        print(f"  error:    {i['error']}")
    else:
        size_mb = i["size_bytes"] / (1024 * 1024)
        version_tag = "" if i.get("schema_ok") else (
            f" (expected {i['schema_current']})"
        )
        print(f"  size:     {size_mb:,.1f} MiB ({i['size_bytes']:,} bytes)")
        print(f"  schema:   v{i['schema_version']}{version_tag}")
        counts = i.get("counts", {})
        if counts:
            print("  counts:")
            width = max(len(t) for t in counts)
            for table, n in counts.items():
                val = "—" if n is None else f"{n:,}"
                print(f"    {table:<{width}}  {val:>12}")
        voices = i.get("voices") or []
        if voices:
            print(f"  voices:   {', '.join(voices)}")
        if "per_bundle_indices_checked" in i:
            checked = i["per_bundle_indices_checked"]
            stale = i["per_bundle_indices_stale"]
            print(f"  per-bundle: {checked} indexed, {stale} stale")

    cat = report["catalog"]
    print()
    print("catalog")
    print(f"  path:     {cat['path']}")
    if not cat["built"]:
        print("  (not built — run `bkk index catalog`)")
    elif "error" in cat:
        print(f"  error:    {cat['error']}")
    else:
        size_mb = cat["size_bytes"] / (1024 * 1024)
        version_tag = "" if cat.get("schema_ok") else (
            f" (expected {cat['schema_current']})"
        )
        print(f"  size:     {size_mb:,.1f} MiB ({cat['size_bytes']:,} bytes)")
        print(f"  schema:   v{cat['schema_version']}{version_tag}")
        if cat.get("source_csv"):
            print(f"  source:   {cat['source_csv']}")
        counts = cat.get("counts", {})
        if counts:
            print("  counts:")
            width = max(len(t) for t in counts)
            for table, n in counts.items():
                val = "—" if n is None else f"{n:,}"
                print(f"    {table:<{width}}  {val:>12}")
        if "date_min" in cat and "date_max" in cat:
            print(f"  dates:    {cat['date_min']}..{cat['date_max']}")

    cfg = report["config"]
    print()
    print("config")
    if not cfg["files"]:
        print("  (no .bkkrc files found)")
    else:
        print("  files (low → high precedence):")
        for f in cfg["files"]:
            print(f"    {f}")
    for name, values in cfg["sections"].items():
        if not values:
            continue
        print(f"  [{name}]")
        width = max(len(k) for k in values)
        for k, v in values.items():
            print(f"    {k:<{width}}  {v}")

    bundles = report.get("bundles")
    if bundles is None:
        return
    print()
    print(f"bundles ({len(bundles)})")
    if not bundles:
        return
    headers = ("textid", "juans", "buckets", "witnesses", "editions")
    rows = [
        (
            b["textid"],
            "—" if b["juans"] is None else str(b["juans"]),
            "—" if b["buckets"] is None else str(b["buckets"]),
            "—" if b["witnesses"] is None else str(b["witnesses"]),
            ",".join(b["editions"]) if b["editions"] else "",
        )
        for b in bundles
    ]
    widths = [
        max(len(h), max((len(r[i]) for r in rows), default=0))
        for i, h in enumerate(headers)
    ]
    sep = "  "
    print("  " + sep.join(h.ljust(widths[i]) for i, h in enumerate(headers)))
    print("  " + sep.join("-" * widths[i] for i in range(len(headers))))
    for r in rows:
        print("  " + sep.join(r[i].ljust(widths[i]) for i in range(len(r))))


def _render_text_block(t: dict) -> None:
    print(f"text  {t['textid']}")
    print(f"  path:           {t['path']}")
    print(f"  manifest date:  {t['manifestDate']}")

    def _show(label: str, value: object) -> None:
        if value is None or value == "":
            print(f"  {label:<14}  —")
        else:
            print(f"  {label:<14}  {value}")

    _show("title:", t.get("title"))
    _show("title (py):", t.get("titlePinyin"))
    _show("title (en):", t.get("titleEnglish"))

    nb = t.get("notBefore")
    na = t.get("notAfter")
    iy = t.get("indexYear")
    if nb is None and na is None and iy is None:
        print("  dates:          —")
    else:
        print(
            f"  dates:          notBefore={nb if nb is not None else '—'}  "
            f"notAfter={na if na is not None else '—'}  "
            f"indexYear={iy if iy is not None else '—'}"
        )

    eds = t.get("editions") or []
    if eds:
        labels = ", ".join(
            f"{e['short']}" + (f" ({e['label']})" if e.get("label") else "")
            for e in eds
        )
        print(f"  editions:       {labels}")
    else:
        print("  editions:       —")

    print(f"  juans:          {t['juanCount']}")

    chars = t["chars"]
    print(f"  chars (total):  {chars['total']:,}")
    print("  chars by bucket:")
    width = max(len(b) for b in ("front", "body", "back"))
    for bucket_name in ("front", "body", "back"):
        c = chars[bucket_name]
        print(
            f"    {bucket_name:<{width}}  total={c['total']:>10,}  "
            f"unique={c['unique']:>6,}"
        )

    pua = t["puaChars"]
    print(
        f"  PUA chars:      {pua['total_occurrences']:,} occurrences, "
        f"{pua['total_unique']:,} unique"
    )

    markers = t.get("markersByType") or {}
    if not markers:
        print("  markers:        —")
    else:
        print("  markers by type:")
        m_width = max(len(k) for k in markers)
        for k, n in markers.items():
            print(f"    {k:<{m_width}}  {n:>8,}")

    if t.get("catalogPresent") is False:
        print("  (no catalog row found — title/dates fall back to manifest)")


def _render_text_markdown(t: dict) -> str:
    lines: list[str] = []
    title = t.get("title") or t["textid"]
    lines.append(f"# {t['textid']} — {title}" if title != t["textid"] else f"# {t['textid']}")
    lines.append("")

    def _val(v: object) -> str:
        return "—" if v is None or v == "" else str(v)

    lines.append("## Identification")
    lines.append("")
    lines.append(f"- **Manifest date:** {t['manifestDate']}")
    lines.append(f"- **Title:** {_val(t.get('title'))}")
    lines.append(f"- **Title (pinyin):** {_val(t.get('titlePinyin'))}")
    lines.append(f"- **Title (English):** {_val(t.get('titleEnglish'))}")

    nb, na, iy = t.get("notBefore"), t.get("notAfter"), t.get("indexYear")
    if nb is None and na is None and iy is None:
        lines.append("- **Dates:** —")
    else:
        lines.append(
            f"- **Dates:** notBefore={_val(nb)}, notAfter={_val(na)}, "
            f"indexYear={_val(iy)}"
        )
    lines.append(f"- **Juans:** {t['juanCount']}")
    tid = t["textid"]
    lines.append(
        f"- **More information:** "
        f'<a href="https://ask.bunkankun.org/{tid[:3]}/{tid[:4]}/{tid}" '
        f'target="ask-bkk">{tid}</a>'
    )

    eds = t.get("editions") or []
    lines.append("")
    lines.append("## Editions")
    lines.append("")
    if not eds:
        lines.append("_None._")
    else:
        show_declared = any(not e.get("declared", True) for e in eds)
        if show_declared:
            lines.append("| Short | Label | Images | Declared |")
            lines.append("|---|---|---:|:---:|")
            for e in eds:
                declared = "yes" if e.get("declared", True) else "**no**"
                lines.append(
                    f"| {e['short']} | {_val(e.get('label'))} | "
                    f"{e.get('imageCount', 0):,} | {declared} |"
                )
        else:
            lines.append("| Short | Label | Images |")
            lines.append("|---|---|---:|")
            for e in eds:
                lines.append(
                    f"| {e['short']} | {_val(e.get('label'))} | "
                    f"{e.get('imageCount', 0):,} |"
                )

    if t.get("catalogPresent") is False:
        lines.append("")
        lines.append("> No catalog row found — title/dates fall back to manifest.")

    chars = t["chars"]
    lines.append("")
    lines.append("## Characters")
    lines.append("")
    lines.append(f"Total: **{chars['total']:,}**")
    lines.append("")
    lines.append("| Bucket | Total | Unique |")
    lines.append("|---|---:|---:|")
    for bucket_name in ("front", "body", "back"):
        c = chars[bucket_name]
        lines.append(f"| {bucket_name} | {c['total']:,} | {c['unique']:,} |")

    pua = t["puaChars"]
    lines.append("")
    lines.append(
        f"PUA: **{pua['total_occurrences']:,}** occurrences, "
        f"**{pua['total_unique']:,}** unique."
    )

    markers = t.get("markersByType") or {}
    lines.append("")
    lines.append("## Markers")
    lines.append("")
    if not markers:
        lines.append("_None._")
    else:
        lines.append("| Type | Count |")
        lines.append("|---|---:|")
        for k, n in markers.items():
            lines.append(f"| {k} | {n:,} |")

    lines.append("")
    return "\n".join(lines)


def write_readme(
    text_id: str,
    corpus: Path,
    catalog_path: Path,
    *,
    fix_editions: bool = True,
) -> tuple[Path, str | None]:
    """Generate ``<bundle>/Readme.md`` for ``text_id``; return (path, title).

    Mirrors ``bkk info --text-id ID --readme --fix-editions``: optionally
    appends undeclared editions to the manifest, then writes the dossier.
    """
    text = _collect_text(text_id, corpus, catalog_path)
    if fix_editions:
        undeclared = text.get("undeclaredEditions") or []
        bundle_dir = Path(text["path"])
        if _fix_editions(bundle_dir, text_id, undeclared):
            text = _collect_text(text_id, corpus, catalog_path)
    readme_path = Path(text["path"]) / "Readme.md"
    readme_path.write_text(_render_text_markdown(text), encoding="utf-8")
    return readme_path, text.get("title")


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
