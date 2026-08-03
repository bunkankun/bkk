"""Punctuation-comparison report support.

This module powers the ``punc-report`` workflow:

- :func:`make_punc_report_input` inspects a bundle, discovers every available
  set of punctuation (the bundle's own core markers plus any ``llm-punctuation``
  sidecars) for a selected juan/bucket, and produces an *input recipe* that a
  generic template can render.
- :func:`build_punctuation_report` consumes such an input recipe and produces
  the intersected, fixed-width comparison groups the template iterates over.

The generated input file is intentionally editable: a user may delete or
reorder punctuation sets before rendering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from bkk.index.merge import find_bundle
from bkk.marker_assets import effective_markers_for_bucket, load_marker_asset

VALID_BUCKETS = ("front", "body", "back")
DEFAULT_WIDTH = 40
CORE_SIGIL = "base"
CORE_LABEL = "Bundle punctuation"
REFERENCE_ROLE = "llm-punctuation"
_PUNCTUATION_SUFFIX = ".punctuation.yaml"

_SLUG_RE = re.compile(r"[^0-9a-z]+")


class PuncReportError(RuntimeError):
    """Raised when a punctuation report cannot be generated."""


@dataclass
class PunctuationSet:
    sigil: str
    label: str
    source: str  # "core" | "llm-punctuation"
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "sigil": self.sigil,
            "label": self.label,
            "source": self.source,
        }
        if self.model is not None:
            out["model"] = self.model
        return out


@dataclass
class ReportLine:
    sigil: str
    label: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"sigil": self.sigil, "label": self.label, "text": self.text}


@dataclass
class ReportGroup:
    textid: str
    juan_seq: int
    bucket: str
    offset: int
    end: int
    lines: list[ReportLine] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "textid": self.textid,
            "juan_seq": self.juan_seq,
            "bucket": self.bucket,
            "offset": self.offset,
            "end": self.end,
            "lines": [line.to_dict() for line in self.lines],
        }


# --------------------------------------------------------------------------
# bundle / manifest helpers
# --------------------------------------------------------------------------


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PuncReportError(f"file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise PuncReportError(f"expected a YAML mapping in {path}")
    return data


def _bundle_dir(corpus_root: Path, textid: str) -> Path:
    bundle = find_bundle(corpus_root, textid)
    if bundle is None:
        raise PuncReportError(
            f"bundle not found for textid {textid!r} under {corpus_root}"
        )
    return bundle


def _manifest(bundle_dir: Path, textid: str) -> dict[str, Any]:
    return _load_yaml_mapping(bundle_dir / f"{textid}.manifest.yaml")


def _all_juan_seqs(manifest: dict[str, Any]) -> list[int]:
    parts = (manifest.get("assets") or {}).get("parts") or []
    seqs = [
        p.get("seq")
        for p in parts
        if isinstance(p, dict) and isinstance(p.get("seq"), int)
    ]
    return sorted(set(seqs))


def _juan_filename(manifest: dict[str, Any], seq: int) -> str | None:
    parts = (manifest.get("assets") or {}).get("parts") or []
    for entry in parts:
        if isinstance(entry, dict) and entry.get("seq") == seq:
            filename = entry.get("filename")
            if isinstance(filename, str):
                return filename
    return None


def _load_juan(bundle_dir: Path, manifest: dict[str, Any], seq: int) -> dict[str, Any] | None:
    filename = _juan_filename(manifest, seq)
    if filename is None:
        return None
    path = bundle_dir / filename
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else None


def _bucket_text(juan: dict[str, Any], bucket: str) -> str:
    body = juan.get(bucket)
    if not isinstance(body, dict):
        return ""
    text = body.get("text")
    return text if isinstance(text, str) else ""


def _core_punctuation_markers(
    bundle_dir: Path, manifest: dict[str, Any], juan: dict[str, Any], seq: int, bucket: str,
) -> list[dict[str, Any]]:
    marker_asset = load_marker_asset(bundle_dir, manifest, seq)
    return [
        m
        for m in effective_markers_for_bucket(juan, bucket, marker_asset)
        if isinstance(m, dict) and m.get("type") == "punctuation"
    ]


# --------------------------------------------------------------------------
# llm-punctuation sidecar discovery
# --------------------------------------------------------------------------


def _scope_bundle_dir(bundle_dir: Path, textid: str) -> Path:
    if bundle_dir.name == textid:
        return bundle_dir
    if bundle_dir.parent.name == "editions" and bundle_dir.parent.parent.name == textid:
        return bundle_dir.parent.parent
    return bundle_dir


def _bundle_relative_dir(bundle_dir: Path, textid: str, corpus_root: Path | None) -> Path:
    source_dir = _scope_bundle_dir(bundle_dir, textid)
    if corpus_root is not None:
        try:
            return source_dir.resolve().relative_to(Path(corpus_root).resolve())
        except ValueError:
            pass
    section = textid[:4]
    if source_dir.parent.name == section:
        return Path(section) / textid
    return Path(textid)


def _model_from_filename(textid: str, seq: int, name: str) -> str | None:
    prefix = f"{textid}_{seq:03d}"
    if not name.startswith(prefix) or not name.endswith(_PUNCTUATION_SUFFIX):
        return None
    stem = name[len(prefix): -len(_PUNCTUATION_SUFFIX)]
    if stem.startswith("-"):
        parts = stem.split(".", 1)
        if len(parts) != 2:
            return None
        stem = parts[1]
    elif stem.startswith("."):
        stem = stem[1:]
    return stem or None


def _punctuation_root_is_assets(value: str | Path | None) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in ("", "assets"))


def discover_llm_sidecars(
    corpus_root: Path,
    bundle_dir: Path,
    manifest: dict[str, Any],
    textid: str,
    seq: int,
    *,
    punctuation_root: str | Path | None = None,
) -> list[tuple[str | None, Path]]:
    """Return ``(model, path)`` for every ``llm-punctuation`` sidecar for a juan.

    Combines manifest-declared references with a filesystem scan of the
    external ``punctuation_root`` (when configured) and any in-bundle
    ``*.punctuation.yaml`` assets.
    """
    out: list[tuple[str | None, Path]] = []
    seen: set[str] = set()

    def add(model: str | None, path: Path) -> None:
        if not path.is_file():
            return
        key = str(path.resolve(strict=False))
        if key in seen:
            return
        seen.add(key)
        out.append((model, path))

    refs = (manifest.get("assets") or {}).get("references") or []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        if ref.get("role") != REFERENCE_ROLE or ref.get("seq") != seq:
            continue
        filename = ref.get("filename") or ref.get("name")
        if not isinstance(filename, str):
            continue
        model = ref.get("model")
        if not isinstance(model, str) or not model:
            model = _model_from_filename(textid, seq, Path(filename).name)
        add(model, bundle_dir / filename)

    # In-bundle sidecars not necessarily declared as references.
    pattern = f"{textid}_{seq:03d}*{_PUNCTUATION_SUFFIX}"
    for path in sorted(bundle_dir.rglob(pattern)):
        add(_model_from_filename(textid, seq, path.name), path)

    if not _punctuation_root_is_assets(punctuation_root):
        root = Path(punctuation_root)  # type: ignore[arg-type]
        external_dir = root / _bundle_relative_dir(bundle_dir, textid, corpus_root)
        if external_dir.is_dir():
            for path in sorted(external_dir.glob(pattern)):
                add(_model_from_filename(textid, seq, path.name), path)

    return out


def _sidecar_punctuation_markers(path: Path, bucket: str) -> list[dict[str, Any]]:
    data = _load_yaml_mapping(path)
    markers_obj = data.get("markers")
    if not isinstance(markers_obj, dict):
        return []
    raw = markers_obj.get(bucket)
    if not isinstance(raw, list):
        return []
    return [
        m for m in raw
        if isinstance(m, dict) and m.get("type") == "punctuation"
    ]


# --------------------------------------------------------------------------
# sigils
# --------------------------------------------------------------------------


def _slug(value: str) -> str:
    slug = _SLUG_RE.sub("", value.strip().lower())
    return slug or "set"


def _unique_sigil(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    n = 2
    while f"{base}-{n}" in used:
        n += 1
    sigil = f"{base}-{n}"
    used.add(sigil)
    return sigil


# --------------------------------------------------------------------------
# input-recipe generation
# --------------------------------------------------------------------------


def make_punc_report_input(
    *,
    corpus_root: Path,
    textid: str,
    juans: list[int] | None = None,
    bucket: str = "body",
    width: int = DEFAULT_WIDTH,
    punctuation_root: str | Path | None = None,
) -> dict[str, Any]:
    """Inspect a bundle and build a punctuation-report input recipe dict."""
    if bucket not in VALID_BUCKETS:
        raise PuncReportError(f"bad bucket {bucket!r}; expected one of {VALID_BUCKETS}")
    if width <= 0:
        raise PuncReportError("width must be a positive integer")

    bundle_dir = _bundle_dir(corpus_root, textid)
    manifest = _manifest(bundle_dir, textid)

    available = _all_juan_seqs(manifest)
    if juans is None:
        selected = available
    else:
        selected = sorted(set(juans))
        missing = [s for s in selected if s not in available]
        if missing:
            raise PuncReportError(
                f"juan(s) {missing} not found in {textid}; available: {available}"
            )
    if not selected:
        raise PuncReportError(f"no juans found for {textid}")

    has_core = False
    models: list[str] = []
    seen_models: set[str] = set()
    for seq in selected:
        juan = _load_juan(bundle_dir, manifest, seq)
        if juan is None:
            continue
        if _core_punctuation_markers(bundle_dir, manifest, juan, seq, bucket):
            has_core = True
        for model, _path in discover_llm_sidecars(
            corpus_root, bundle_dir, manifest, textid, seq,
            punctuation_root=punctuation_root,
        ):
            key = model or ""
            if key in seen_models:
                continue
            seen_models.add(key)
            models.append(model or "model")

    used_sigils: set[str] = set()
    sets: list[PunctuationSet] = []
    if has_core:
        sets.append(
            PunctuationSet(
                sigil=_unique_sigil(CORE_SIGIL, used_sigils),
                label=CORE_LABEL,
                source="core",
            )
        )
    for model in models:
        sets.append(
            PunctuationSet(
                sigil=_unique_sigil(_slug(model), used_sigils),
                label=model,
                source=REFERENCE_ROLE,
                model=model,
            )
        )

    if not sets:
        raise PuncReportError(
            f"no punctuation sets found for {textid} "
            f"(bucket={bucket!r}, juans={selected})"
        )

    return {
        "kind": "bkk.recipe-input/v1",
        "for_template": "punc-report",
        "target": {
            "textid": textid,
            "juans": selected,
            "bucket": bucket,
        },
        "layout": {"width": width},
        "sets": [s.to_dict() for s in sets],
    }


# --------------------------------------------------------------------------
# report building
# --------------------------------------------------------------------------


def _injections(markers: list[dict[str, Any]], text_len: int) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for m in markers:
        off = m.get("offset")
        content = m.get("content")
        if not isinstance(off, int) or not isinstance(content, str) or not content:
            continue
        if off < 0 or off > text_len:
            continue
        out.append((off, content))
    out.sort(key=lambda p: p[0])
    return out


def _render_window(
    text: str, injections: list[tuple[int, str]], start: int, end: int, text_len: int,
) -> str:
    """``text[start:end]`` with punctuation inserted before each char at its offset.

    A punctuation marker at offset ``O`` renders immediately before the base
    character at ``O`` and belongs to the window that contains that character
    (``start <= O < end``). Trailing punctuation at ``O == text_len`` renders
    in the final window.
    """
    parts: list[str] = []
    cursor = start
    include_trailing = end >= text_len
    for off, content in injections:
        if off < start:
            continue
        if off > end:
            break
        if off == end and not (off == text_len and include_trailing):
            break
        if off > cursor:
            parts.append(text[cursor:off])
            cursor = off
        parts.append(content)
    if cursor < end:
        parts.append(text[cursor:end])
    return "".join(parts)


def _resolve_set_markers(
    corpus_root: Path,
    bundle_dir: Path,
    manifest: dict[str, Any],
    textid: str,
    seq: int,
    bucket: str,
    juan: dict[str, Any],
    pset: dict[str, Any],
    *,
    punctuation_root: str | Path | None,
    warnings: list[str],
) -> list[dict[str, Any]]:
    source = pset.get("source")
    sigil = pset.get("sigil") or "?"
    if source == "core":
        return _core_punctuation_markers(bundle_dir, manifest, juan, seq, bucket)
    if source == REFERENCE_ROLE:
        model = pset.get("model")
        sidecars = discover_llm_sidecars(
            corpus_root, bundle_dir, manifest, textid, seq,
            punctuation_root=punctuation_root,
        )
        match: Path | None = None
        for m, path in sidecars:
            if m == model or (model in (None, "model") and m is None):
                match = path
                break
        if match is None:
            warnings.append(
                f"set {sigil!r} (model {model!r}): no sidecar for {textid} juan {seq}"
            )
            return []
        try:
            return _sidecar_punctuation_markers(match, bucket)
        except PuncReportError as exc:
            warnings.append(f"set {sigil!r}: {exc}")
            return []
    warnings.append(f"set {sigil!r}: unknown source {source!r}")
    return []


def build_punctuation_report(
    input_recipe: dict[str, Any],
    *,
    corpus_root: Path,
    punctuation_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build the intersected fixed-width comparison groups for a template."""
    target = input_recipe.get("target")
    if not isinstance(target, dict):
        raise PuncReportError("input recipe requires a 'target' mapping")
    textid = target.get("textid")
    if not isinstance(textid, str) or not textid:
        raise PuncReportError("input recipe target requires a 'textid'")
    bucket = target.get("bucket", "body")
    if bucket not in VALID_BUCKETS:
        raise PuncReportError(f"bad bucket {bucket!r}; expected one of {VALID_BUCKETS}")

    layout = input_recipe.get("layout") or {}
    width = layout.get("width", DEFAULT_WIDTH) if isinstance(layout, dict) else DEFAULT_WIDTH
    if not isinstance(width, int) or width <= 0:
        raise PuncReportError("layout.width must be a positive integer")

    sets_raw = input_recipe.get("sets")
    if not isinstance(sets_raw, list) or not sets_raw:
        raise PuncReportError("input recipe requires a non-empty 'sets' list")
    sets = [s for s in sets_raw if isinstance(s, dict)]

    bundle_dir = _bundle_dir(corpus_root, textid)
    manifest = _manifest(bundle_dir, textid)

    available = _all_juan_seqs(manifest)
    juans_raw = target.get("juans")
    if juans_raw is None:
        juans = available
    elif isinstance(juans_raw, list):
        juans = [s for s in juans_raw if isinstance(s, int)]
    else:
        raise PuncReportError("target.juans must be a list of integers")

    warnings: list[str] = []
    groups: list[ReportGroup] = []

    for seq in juans:
        juan = _load_juan(bundle_dir, manifest, seq)
        if juan is None:
            warnings.append(f"juan {seq}: not found in {textid}")
            continue
        text = _bucket_text(juan, bucket)
        if not text:
            continue
        text_len = len(text)

        set_injections: list[tuple[dict[str, Any], list[tuple[int, str]]]] = []
        for pset in sets:
            markers = _resolve_set_markers(
                corpus_root, bundle_dir, manifest, textid, seq, bucket, juan, pset,
                punctuation_root=punctuation_root, warnings=warnings,
            )
            set_injections.append((pset, _injections(markers, text_len)))

        for start in range(0, text_len, width):
            end = min(start + width, text_len)
            group = ReportGroup(
                textid=textid, juan_seq=seq, bucket=bucket, offset=start, end=end,
            )
            for pset, injections in set_injections:
                group.lines.append(
                    ReportLine(
                        sigil=pset.get("sigil") or "?",
                        label=pset.get("label") or pset.get("sigil") or "?",
                        text=_render_window(text, injections, start, end, text_len),
                    )
                )
            groups.append(group)

    return {
        "target": {"textid": textid, "juans": juans, "bucket": bucket},
        "width": width,
        "sets": [
            {"sigil": s.get("sigil"), "label": s.get("label")}
            for s in sets
        ],
        "groups": [g.to_dict() for g in groups],
        "warnings": warnings,
    }
