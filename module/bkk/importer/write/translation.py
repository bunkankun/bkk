"""Writer for translation bundles.

Emits the Markdown-with-YAML-header form described in ``bunkankun.md``
§"Translations". The bundle is two-tier:

- a bundle entry-point ``<bundle-id>.md`` carries the full manifest as
  YAML front-matter plus a human-readable juan TOC in the body;
- each per-juan ``<bundle-id>_NNN.md`` carries only juan-level metadata
  (``canonical_identifier``, ``bundle``, ``juan_seq``, ``juan_label``,
  ``hash``, ``markers``) plus the Pandoc-style attribute-span body.

Layout under ``<out-root>``::

    translations/<source-text-id>/<lang>/<bundle-id>/
      <bundle-id>.md            # bundle manifest + juan TOC
      <bundle-id>_<NNN>.md      # one per source juan
      <bundle-id>.source.yaml   # raw teiHeader sidecar (round-trip)

With ``by_section=True`` a 4-char section prefix slips between
``translations/`` and the text id.

The storage form is Markdown; the **canonical form** used for hashing is
the parsed structure (segments as dicts, manifest as a dict), serialized
via RFC 8785 JCS. Per-juan hashes feed the bundle manifest's ``juan:``
list; the bundle hash is over ``{manifest_with_zero_hash, segments:
[flat list across juans]}``. The ``markers:`` list in each juan file is
storage-form only — it does not participate in the canonical hash.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml as _yaml

from ..hashing import ZERO_HASH, sha256_jcs
from ..ir import TranslationBundle, TranslationSegment
from ..source import section_prefix
from .yaml_writer import dump, marker_to_flow


def write_translation(
    bundle: TranslationBundle,
    out_root: Path,
    *,
    source_bundle_root: Path | None = None,
    by_section: bool = False,
) -> dict:
    """Emit ``bundle`` as Markdown-with-YAML-header files.

    The bundle directory is ``<out_root>/translations/[<section>/]
    <source-text-id>/<lang>/<bundle_id>/``.

    ``source_bundle_root``, when given, is the directory under which to
    look for the source bundle's manifest (so we can copy its hash into
    ``source.hash``). When omitted or unresolvable, ``source.hash`` is
    set to ``null`` and a warning is printed to stderr.

    Returns a summary dict ``{bundle_id, out_root, juans, bundle_path,
    source_hash_resolved}``.
    """
    bundle_dir = translation_bundle_dir(
        out_root,
        source_text_id=bundle.source_text_id,
        language=bundle.language,
        bundle_id=bundle.bundle_id,
        by_section=by_section,
    )
    bundle_dir.mkdir(parents=True, exist_ok=True)

    juan_groups = _group_segments_by_juan(bundle.segments)

    source_hash = _resolve_source_hash(
        bundle.source_text_id, source_bundle_root,
    )
    if source_hash is None and bundle.source_text_id:
        print(
            f"warning: {bundle.bundle_id}: source bundle "
            f"{bundle.source_text_id} not found under "
            f"{source_bundle_root}; source.hash left null",
            file=sys.stderr,
        )

    # Step 1: per-juan hashes feed the manifest's juan list.
    juan_entries: list[dict] = []
    juan_specs: list[tuple[str, list[TranslationSegment], str]] = []
    for seq, (label, segs) in enumerate(juan_groups, start=1):
        filename = f"{bundle.bundle_id}_{label}.md"
        jhash = _juan_hash(segs, bundle_language=bundle.language)
        juan_entries.append(marker_to_flow({
            "seq": seq,
            "label": label,
            "file": filename,
            "hash": jhash,
        }))
        juan_specs.append((label, segs, jhash))

    # Step 2: build manifest with ZERO_HASH, then compute the spec-aligned
    # bundle hash over {manifest_without_hash, flat segments}.
    manifest = _build_manifest(
        bundle, juan_entries, source_hash=source_hash,
    )
    manifest["hash"] = _bundle_hash(
        manifest, juan_groups, bundle_language=bundle.language,
    )

    # Step 3: write the bundle entry-point .md.
    bundle_md_path = bundle_dir / f"{bundle.bundle_id}.md"
    bundle_md_path.write_text(
        _render_bundle_md(manifest, juan_specs, bundle.bundle_id),
        encoding="utf-8",
    )

    # Step 4: write each per-juan .md.
    for seq, ((label, segs, jhash)) in enumerate(juan_specs, start=1):
        juan_md = _render_juan_md(
            bundle_id=bundle.bundle_id,
            seq=seq,
            label=label,
            juan_hash=jhash,
            segs=segs,
            bundle_language=bundle.language,
        )
        (bundle_dir / f"{bundle.bundle_id}_{label}.md").write_text(
            juan_md, encoding="utf-8",
        )

    if bundle.source_info:
        source_path = bundle_dir / f"{bundle.bundle_id}.source.yaml"
        source_path.write_text(dump(bundle.source_info), encoding="utf-8")

    return {
        "bundle_id": bundle.bundle_id,
        "out_root": str(bundle_dir),
        "juans": [
            {"seq": i, "label": label}
            for i, (label, _, _) in enumerate(juan_specs, start=1)
        ],
        "bundle_path": str(bundle_md_path),
        "source_hash_resolved": source_hash is not None,
    }


# ---------- path layout ----------------------------------------------------


def translation_bundle_dir(
    out_root: Path,
    *,
    source_text_id: str | None,
    language: str | None,
    bundle_id: str,
    by_section: bool,
) -> Path:
    """Compute the canonical output directory for a translation bundle.

    Single source of truth for the
    ``<out_root>/translations/[<section>/]<source-text-id>/<lang>/<bundle-id>/``
    layout — the writer uses it to place files, and the CLI uses it to
    decide whether a bundle already exists for ``--on-exists skip``.
    """
    parts = ["translations"]
    if by_section and source_text_id:
        parts.append(section_prefix(source_text_id))
    parts.extend([
        source_text_id or "_unknown",
        language or "_unknown",
        bundle_id,
    ])
    return out_root.joinpath(*parts)


# ---------- juan grouping --------------------------------------------------


def _group_segments_by_juan(
    segments: list[TranslationSegment],
) -> list[tuple[str, list[TranslationSegment]]]:
    """Group segments by ``juan_label``, preserving first-seen order.

    Numeric labels are zero-padded to 3 digits in the returned label
    (so a label like ``"5"`` lands at ``KR..._005.md`` and sorts naturally
    alongside ``"014"``); non-numeric labels (``"_unknown"``) pass
    through unchanged.
    """
    order: list[str] = []
    buckets: dict[str, list[TranslationSegment]] = {}
    for seg in segments:
        label = seg.juan_label
        if label.isdigit():
            label = label.zfill(3)
        if label not in buckets:
            order.append(label)
            buckets[label] = []
        buckets[label].append(seg)
    return [(lbl, buckets[lbl]) for lbl in order]


# ---------- bundle entry-point render --------------------------------------


def _render_bundle_md(
    manifest: dict,
    juan_specs: list[tuple[str, list[TranslationSegment], str]],
    bundle_id: str,
) -> str:
    """Render ``<bundle-id>.md``: YAML manifest + juan TOC body."""
    header = dump(manifest)
    title = manifest.get("title")
    lines: list[str] = []
    if isinstance(title, str) and title:
        lines.append(f"# {title}")
        lines.append("")
    lines.append("## Juan")
    lines.append("")
    for label, _segs, _h in juan_specs:
        lines.append(f"- [{label}]({bundle_id}_{label}.md)")
    body = "\n".join(lines) + "\n"
    return f"---\n{header}---\n{body}"


# ---------- per-juan render ------------------------------------------------


def _render_juan_md(
    *,
    bundle_id: str,
    seq: int,
    label: str,
    juan_hash: str,
    segs: list[TranslationSegment],
    bundle_language: str,
) -> str:
    """Render one ``<bundle-id>_NNN.md``: juan-level YAML + span body."""
    markers, ref_per_seg = _build_markers(segs, bundle_language=bundle_language)
    header_obj: dict = {
        "canonical_identifier": (
            f"bkk:translation/{bundle_id}/v1#juan/{label}"
        ),
        "bundle": f"bkk:translation/{bundle_id}/v1",
        "juan_seq": seq,
        "juan_label": label,
        "hash": juan_hash,
        "markers": markers,
    }
    header_yaml = dump(header_obj)
    body_lines = [
        _render_span(seg, refs)
        for seg, refs in zip(segs, ref_per_seg)
    ]
    body = "\n".join(body_lines) + "\n"
    return f"---\n{header_yaml}---\n{body}"


def _build_markers(
    segs: list[TranslationSegment], *, bundle_language: str,
) -> tuple[list, list[list[str]]]:
    """Build the storage-form ``markers:`` list and parallel body-ref lists.

    Returns ``(markers, ref_per_seg)`` where ``markers[i]`` is a flow-style
    dict for the YAML list and ``ref_per_seg[i]`` is the list of body
    ``@<ref>`` tokens for the same segment. Collisions on the body ref
    (i.e. first-occurrence location reused later in the same juan) are
    disambiguated with ``-N`` suffixes; the original ``corresp`` value is
    preserved verbatim.
    """
    markers: list = []
    ref_per_seg: list[list[str]] = []
    seen_refs: set[str] = set()
    next_suffix: dict[str, int] = {}

    for seg in segs:
        body_refs: list[str] = []
        for loc in seg.corresp:
            if loc not in seen_refs:
                ref = loc
            else:
                n = next_suffix.get(loc, 2)
                while f"{loc}-{n}" in seen_refs:
                    n += 1
                ref = f"{loc}-{n}"
                next_suffix[loc] = n + 1
            seen_refs.add(ref)
            body_refs.append(ref)
        ref_per_seg.append(body_refs)

        entry: dict = {}
        entry["ref"] = body_refs[0] if len(body_refs) == 1 else list(body_refs)
        entry["corresp"] = list(seg.corresp)
        if seg.lang and seg.lang != bundle_language:
            entry["lang"] = seg.lang
        if seg.resp:
            entry["resp"] = seg.resp
        if seg.modified:
            entry["modified"] = seg.modified
        markers.append(marker_to_flow(entry))

    return markers, ref_per_seg


def _render_span(seg: TranslationSegment, refs: list[str]) -> str:
    """Render a single body span: ``[text]{@r1 @r2 …}``."""
    text = _escape_span_text(seg.text)
    ref_tokens = " ".join(f"@{r}" for r in refs)
    return f"[{text}]{{{ref_tokens}}}"


def _escape_span_text(text: str) -> str:
    """Escape characters that would otherwise terminate the span."""
    out: list[str] = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch in ("[", "]"):
            out.append("\\" + ch)
        elif ch in ("\r", "\n"):
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


# ---------- canonical / hash form ------------------------------------------


def _segment_canonical(seg: TranslationSegment, *,
                       bundle_language: str) -> dict:
    """Parsed-form dict used for hashing — independent of storage formatting.

    Mirrors the storage-form attribute-omission rules so hash and rendered
    Markdown stay consistent: same-language ``lang`` is omitted.
    """
    d: dict = {"corresp": list(seg.corresp), "text": seg.text}
    if seg.lang and seg.lang != bundle_language:
        d["lang"] = seg.lang
    if seg.resp:
        d["resp"] = seg.resp
    if seg.modified:
        d["modified"] = seg.modified
    return d


def _juan_hash(segs: list[TranslationSegment], *,
               bundle_language: str) -> str:
    """Hash a per-juan segment list over its canonical (JCS) form."""
    payload = {
        "segments": [
            _segment_canonical(s, bundle_language=bundle_language) for s in segs
        ],
    }
    return sha256_jcs(payload)


def _bundle_hash(
    manifest: dict,
    juan_groups: list[tuple[str, list[TranslationSegment]]],
    *,
    bundle_language: str,
) -> str:
    """Spec-aligned bundle hash: JCS over header + ordered segments.

    Drops the ``hash`` field from the manifest dict (rather than zeroing
    in place) and flattens segments across juans in juan-seq order.
    """
    payload = {
        **{k: _plain(v) for k, v in manifest.items() if k != "hash"},
        "segments": [
            _segment_canonical(s, bundle_language=bundle_language)
            for _, segs in juan_groups for s in segs
        ],
    }
    return sha256_jcs(payload)


def _plain(value):
    """Strip storage-form flow markers so JCS sees a plain dict/list."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


# ---------- manifest build / source hash -----------------------------------


def _build_manifest(
    bundle: TranslationBundle,
    juan_entries: list,
    *,
    source_hash: str | None,
) -> dict:
    """Compose the bundle manifest dict ready for hashing.

    ``hash`` is placed last but left as ``ZERO_HASH``; the caller patches
    it in once the bundle hash is computed.
    """
    m: dict = {
        "canonical_identifier": f"bkk:translation/{bundle.bundle_id}/v1",
        "canonical_location": "",
        "source": {
            "canonical_identifier": (
                f"bkk:krp/{bundle.source_text_id}/v1"
                if bundle.source_text_id else ""
            ),
            "hash": source_hash,
        },
        "language": bundle.language,
    }
    for k in ("title", "original_title", "responsibility",
              "publication", "license", "date"):
        if k in bundle.metadata:
            m[k] = bundle.metadata[k]
    m["juan"] = juan_entries
    m["hash"] = ZERO_HASH
    return m


def _resolve_source_hash(
    source_text_id: str,
    source_bundle_root: Path | None,
) -> str | None:
    """Look up the source bundle's manifest hash, if available.

    Looks at ``<source_bundle_root>/<source_text_id>/<source_text_id>.manifest.yaml``.
    Returns ``None`` when the file doesn't exist or its hash field is missing.
    """
    if not source_text_id or source_bundle_root is None:
        return None
    candidate = (
        source_bundle_root / source_text_id / f"{source_text_id}.manifest.yaml"
    )
    if not candidate.exists():
        return None
    try:
        with candidate.open(encoding="utf-8") as fh:
            data = _yaml.safe_load(fh)
    except Exception:  # noqa: BLE001
        return None
    if isinstance(data, dict):
        h = data.get("hash")
        if isinstance(h, str) and h:
            return h
    return None
