"""Shared state for the validator: parsed YAML files + the running Report."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .report import Report


@dataclass
class LoadedFile:
    """A YAML file we tried to load."""
    path: Path
    rel: str           # path relative to the bundle dir, for messages
    data: Any = None
    parse_error: str | None = None
    exists: bool = True


def _load_yaml(bundle_dir: Path, path: Path) -> LoadedFile:
    rel = str(path.relative_to(bundle_dir))
    if not path.exists():
        return LoadedFile(path=path, rel=rel, exists=False)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return LoadedFile(path=path, rel=rel, parse_error=str(exc))
    return LoadedFile(path=path, rel=rel, data=data)


@dataclass
class EditionFiles:
    short: str
    dir: Path
    manifest: LoadedFile
    juans: dict[int, LoadedFile] = field(default_factory=dict)


@dataclass
class ValidationContext:
    bundle_dir: Path
    text_id: str
    report: Report
    master_manifest: LoadedFile
    master_juans: dict[int, LoadedFile] = field(default_factory=dict)
    annotations: dict[int, LoadedFile] = field(default_factory=dict)
    pua_map: LoadedFile | None = None
    editions: dict[str, EditionFiles] = field(default_factory=dict)


def load_context(bundle_dir: Path) -> ValidationContext:
    """Load every YAML file referenced by the manifest layout.

    Files that are missing or unparseable are recorded as `LoadedFile`s with
    `exists=False` or `parse_error` set; rules surface those as findings.
    """
    bundle_dir = Path(bundle_dir).resolve()
    text_id = bundle_dir.name
    report = Report(bundle=str(bundle_dir))

    master_path = bundle_dir / f"{text_id}.manifest.yaml"
    master = _load_yaml(bundle_dir, master_path)

    ctx = ValidationContext(
        bundle_dir=bundle_dir,
        text_id=text_id,
        report=report,
        master_manifest=master,
    )

    # Master juans + annotations referenced by the manifest.
    if isinstance(master.data, dict):
        assets = master.data.get("assets") or {}
        for part in assets.get("parts") or []:
            if not isinstance(part, dict):
                continue
            seq = part.get("seq")
            fname = part.get("filename")
            if isinstance(seq, int) and isinstance(fname, str):
                ctx.master_juans[seq] = _load_yaml(
                    bundle_dir, bundle_dir / fname,
                )
        for ann in assets.get("annotations") or []:
            if not isinstance(ann, dict):
                continue
            seq = ann.get("seq")
            fname = ann.get("filename")
            if isinstance(seq, int) and isinstance(fname, str):
                ctx.annotations[seq] = _load_yaml(
                    bundle_dir, bundle_dir / fname,
                )

    # PUA-map (optional).
    pua_path = bundle_dir / "PUA-map.yaml"
    if pua_path.exists():
        ctx.pua_map = _load_yaml(bundle_dir, pua_path)

    # Editions on disk.
    editions_dir = bundle_dir / "editions"
    if editions_dir.is_dir():
        for sub in sorted(editions_dir.iterdir()):
            if not sub.is_dir():
                continue
            short = sub.name
            ed_manifest = _load_yaml(
                bundle_dir, sub / f"{text_id}-{short}.manifest.yaml",
            )
            ed = EditionFiles(short=short, dir=sub, manifest=ed_manifest)
            if isinstance(ed_manifest.data, dict):
                assets = ed_manifest.data.get("assets") or {}
                for part in assets.get("parts") or []:
                    if not isinstance(part, dict):
                        continue
                    seq = part.get("seq")
                    fname = part.get("filename")
                    if isinstance(seq, int) and isinstance(fname, str):
                        ed.juans[seq] = _load_yaml(
                            bundle_dir, sub / fname,
                        )
            ctx.editions[short] = ed

    return ctx
