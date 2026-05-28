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
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

import yaml

from bkk.config import load_rc, rc_files
from bkk.index.catalog import CATALOG_SCHEMA_VERSION
from bkk.index.merge import discover_bundles, is_stale
from bkk.index.schema import SCHEMA_VERSION

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
    p.add_argument("--json", action="store_true", dest="json_out",
                   help="emit JSON instead of text")
    return p


def run(argv: list[str] | None = None) -> int:
    rc = load_rc()
    g = rc.get("global", {})
    idx_rc = rc.get("index", {})
    serve_rc = rc.get("serve", {})
    info_rc = rc.get("info", {})

    parser = build_parser()
    args = parser.parse_args(argv)

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

    corpus_data = _collect_corpus(corpus, prefix=None)
    index_data = _collect_index(index_path, corpus)
    catalog_data = _collect_catalog(catalog_path)
    config_data = _collect_config(rc)
    bundles_data = (
        _collect_bundles(corpus, index_path, prefix=args.prefix)
        if want_bundles else None
    )

    report = {
        "corpus": corpus_data,
        "index": index_data,
        "catalog": catalog_data,
        "config": config_data,
    }
    if bundles_data is not None:
        report["bundles"] = bundles_data

    if args.json_out:
        print(json.dumps(report, indent=2, default=str))
    else:
        _render_text(report)
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


def _render_text(report: dict) -> None:
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


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(run())
