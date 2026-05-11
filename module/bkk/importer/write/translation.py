"""Writer for translation bundles.

Emits the Markdown-with-YAML-header form described in
``bunkankun.md`` §"Translations". Layout under ``<out-root>``::

    translations/<bundle-id>/
      <bundle-id>.manifest.yaml      # YAML header + juan list
      <bundle-id>_<NNN>.md           # one Markdown file per source juan
      <bundle-id>.source.yaml        # raw teiHeader sidecar (round-trip)

The storage form is Markdown; the **canonical form** used for hashing is
the parsed structure (segments as dicts, manifest as a dict), serialized
via RFC 8785 JCS. This decouples reformatting of the .md from the bundle
hash, exactly as juan files decouple YAML formatting from juan hashes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml as _yaml

from ..hashing import ZERO_HASH, manifest_hash, sha256_jcs
from ..ir import TranslationBundle, TranslationSegment
from .yaml_writer import dump


# Span-attribute keys emitted on each segment (when set). Order matters
# only for the storage form's readability; the hash is over the parsed
# structure so attribute ordering does not affect it.
_SPAN_ATTR_ORDER = ("corresp", "lang", "resp", "modified")


def write_translation(
    bundle: TranslationBundle,
    out_root: Path,
    *,
    source_bundle_root: Path | None = None,
) -> dict:
    """Emit ``bundle`` under ``<out_root>/translations/<bundle_id>/``.

    ``source_bundle_root``, when given, is the directory under which to
    look for the source bundle's manifest (so we can copy its hash into
    ``source.hash``). When omitted or unresolvable, ``source.hash`` is
    set to ``null`` and a warning is printed to stderr.

    Returns a summary dict ``{bundle_id, out_root, juans, manifest_path,
    source_hash_resolved}``.
    """
    bundle_dir = out_root / "translations" / bundle.bundle_id
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

    juan_entries: list[dict] = []
    for seq, (label, segs) in enumerate(juan_groups, start=1):
        filename = f"{bundle.bundle_id}_{label}.md"
        juan_path = bundle_dir / filename
        md_text = _render_juan_markdown(segs, bundle_language=bundle.language)
        juan_path.write_text(md_text, encoding="utf-8")
        juan_entries.append({
            "seq": seq,
            "label": label,
            "file": filename,
            "hash": _juan_hash(segs, bundle_language=bundle.language),
        })

    manifest = _build_manifest(
        bundle, juan_entries, source_hash=source_hash,
    )
    manifest["hash"] = manifest_hash(manifest)
    manifest_path = bundle_dir / f"{bundle.bundle_id}.manifest.yaml"
    manifest_path.write_text(dump(manifest), encoding="utf-8")

    if bundle.source_info:
        source_path = bundle_dir / f"{bundle.bundle_id}.source.yaml"
        source_path.write_text(dump(bundle.source_info), encoding="utf-8")

    return {
        "bundle_id": bundle.bundle_id,
        "out_root": str(bundle_dir),
        "juans": juan_entries,
        "manifest_path": str(manifest_path),
        "source_hash_resolved": source_hash is not None,
    }


# ---------- helpers --------------------------------------------------------


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


def _render_juan_markdown(segs: list[TranslationSegment], *,
                          bundle_language: str) -> str:
    """Render one per-juan .md file: one Pandoc span per non-empty segment."""
    lines = [_render_span(s, bundle_language=bundle_language) for s in segs]
    return "\n".join(lines) + "\n"


def _render_span(seg: TranslationSegment, *, bundle_language: str) -> str:
    """Render a single Pandoc-style attribute span for ``seg``."""
    attrs = _segment_attrs(seg, bundle_language=bundle_language)
    pieces = []
    for k in _SPAN_ATTR_ORDER:
        if k in attrs:
            pieces.append(_attr_token(k, attrs[k]))
    text = _escape_span_text(seg.text)
    return f"[{text}]{{{' '.join(pieces)}}}"


def _segment_attrs(seg: TranslationSegment, *,
                   bundle_language: str) -> dict[str, str]:
    """Collect non-empty span attributes from a segment in storage form.

    ``lang`` is emitted only when it differs from the bundle language, so
    same-language segs (the common case) stay terse. A mismatch is
    preserved verbatim — useful for spans that quote source text inline.
    """
    attrs: dict[str, str] = {"corresp": " ".join(seg.corresp)}
    if seg.lang and seg.lang != bundle_language:
        attrs["lang"] = seg.lang
    if seg.resp:
        attrs["resp"] = seg.resp
    if seg.modified:
        attrs["modified"] = seg.modified
    return attrs


def _attr_token(key: str, value: str) -> str:
    """Render ``key=value``; quote when needed."""
    if any(c in value for c in ' \t"\\}=') or not value:
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'{key}="{escaped}"'
    return f"{key}={value}"


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


def _segment_canonical(seg: TranslationSegment, *,
                       bundle_language: str) -> dict:
    """Parsed-form dict used for hashing — independent of storage formatting.

    Mirrors the writer's attribute-omission rules so the hash and the
    rendered Markdown are consistent: same-language ``lang`` is omitted.
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


def _build_manifest(
    bundle: TranslationBundle,
    juan_entries: list[dict],
    *,
    source_hash: str | None,
) -> dict:
    """Compose the manifest dict ready for hashing.

    ``hash`` is placed last but left as ``ZERO_HASH``; the caller
    re-computes and patches it in via :func:`manifest_hash`.
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
