"""Structural diff between generated output and a hand-crafted sample.

The sample at ``import/samples/<text-id>`` is a witness to the intended shape
rather than a byte-precise oracle, so this module reports differences as a
classified list rather than asserting equality.

A ``Divergence`` row carries:
- ``path``: dotted path to the field (e.g. ``editions/T/...yaml::front.hash``),
- ``kind``: ``hash`` / ``offset`` / ``shape`` / ``value`` / ``missing-key`` /
  ``extra-key`` / ``length`` / ``type``,
- ``status``: ``expected`` (we already know about this artifact) or
  ``unexpected``,
- ``note``: free-text describing the difference.

The set of "expected" patterns is encoded in :func:`_classify` and reflects
the sample's known hand-crafted artifacts (zeroed manifest hash, identical
front+body hash placeholders, body-marker offsets that count from front-end,
TOC rows with ``[body, 0, 0]``, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Divergence:
    file: str
    path: str
    kind: str
    status: str   # "expected" | "unexpected"
    sample: object = None
    ours: object = None
    note: str = ""


# Hashes that the sample uses as placeholders.
_PLACEHOLDER_HASHES = {
    "sha256:0000000000000000000000000000000000000000000000000000000000000000",
    "sha256:7dc7658d44765e59dc2e35ddc5238c254b6f98a7f92a1ee2fedd21f8929c3be3",
    "sha256:0dd7a674abd41b8fd210abf748b10e5121896611ea76f9fc41e98d2a98490147",
}


def _classify(file: str, path: str, kind: str, sample, ours) -> Divergence:
    """Decide whether a difference is an expected sample artifact or not."""
    note = ""
    status = "unexpected"

    # Placeholder hashes in the sample are known artifacts. Other hash
    # mismatches are downstream consequences of the sample's hand-crafted
    # text/marker shape (different offset space, dropped page-breaks, etc.)
    # and are expected for the same reason as the .markers divergences.
    if path.endswith(".hash") or path == "hash":
        if isinstance(sample, str) and sample in _PLACEHOLDER_HASHES:
            status = "expected"
            note = "sample uses a placeholder hash"
        elif isinstance(ours, str) and ours in _PLACEHOLDER_HASHES:
            status = "expected"
            note = "ours uses a placeholder hash (e.g. canonical_set)"
        elif (
            path.startswith(("body.", "front."))
            or path.startswith("juan[")
            or path.startswith("assets.parts[")
            or path == "hash"
        ):
            status = "expected"
            note = "hash follows divergent text/markers in the sample"

    # KRP: documentary editions in the sample don't carry a `front` block;
    # ours splits the opening lines per the opening-indent rule.
    elif path == "front" and sample is None:
        status = "expected"
        note = "sample omits front bucket on documentary editions"

    # Sample's body bucket inherits the front's closing paragraph-break and
    # uses bucket-global offsets, so every marker index/offset diverges.
    elif ".markers" in path or path.endswith(".markers"):
        status = "expected"
        note = "sample concatenates front+body in a single offset space"

    # body.text differs in detail (sample merges some punctuation into the
    # text stream where the source XML emits a literal char + <c/>).
    elif path.endswith(".text") and (path.startswith("body") or path.startswith("front")):
        status = "expected"
        note = "sample text content has hand-crafted edits"

    # Sample TOC spans diverge from spec-computed offsets; the sample also
    # reuses earlier head ids/labels for some 序 sections.
    elif "table_of_contents" in path:
        status = "expected"
        note = "sample TOC artifacts (placeholder spans, reused head ids)"

    # Metadata details differ (sample has hand-edited date, identifier sets).
    elif "metadata" in path:
        status = "expected"
        note = "metadata details differ between sample and importer output"

    # Annotation file: order, offsets, and per-entry content diverge.
    # Sample preserves swl-then-doc input order; we sort by offset per plan.
    # Sample's offsets are bucket-global; ours are per-bucket. Sample lacks
    # the ``bucket`` field we added.
    elif path.startswith("annotations[") or path == "annotations" or path == "source":
        status = "expected"
        note = "sample annotations differ in order, offset space, and shape"

    return Divergence(file=file, path=path, kind=kind, status=status,
                      sample=sample, ours=ours, note=note)


def _walk(file: str, path: str, sample, ours, out: list[Divergence]) -> None:
    if type(sample) != type(ours):
        # Allow int/float interchange.
        if isinstance(sample, (int, float)) and isinstance(ours, (int, float)):
            pass
        else:
            out.append(_classify(file, path, "type", sample, ours))
            return

    if isinstance(sample, dict):
        s_keys = set(sample.keys())
        o_keys = set(ours.keys())
        for k in s_keys - o_keys:
            out.append(_classify(file, f"{path}.{k}" if path else k,
                                 "missing-key", sample[k], None))
        for k in o_keys - s_keys:
            out.append(_classify(file, f"{path}.{k}" if path else k,
                                 "extra-key", None, ours[k]))
        for k in sorted(s_keys & o_keys):
            _walk(file, f"{path}.{k}" if path else k,
                  sample[k], ours[k], out)
    elif isinstance(sample, list):
        if len(sample) != len(ours):
            out.append(_classify(file, path, "length", len(sample), len(ours)))
        for i, (sv, ov) in enumerate(zip(sample, ours)):
            _walk(file, f"{path}[{i}]", sv, ov, out)
    else:
        if sample != ours:
            out.append(_classify(file, path, "value", sample, ours))


def diff_yaml_files(sample_path: Path, ours_path: Path) -> list[Divergence]:
    out: list[Divergence] = []
    try:
        sample = yaml.safe_load(sample_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        out.append(Divergence(
            file=str(ours_path.name), path="", kind="parse",
            status="expected", note=f"sample YAML invalid: {exc.__class__.__name__}",
        ))
        return out
    try:
        ours = yaml.safe_load(ours_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        out.append(Divergence(
            file=str(ours_path.name), path="", kind="parse",
            status="unexpected", note=f"output YAML invalid: {exc}",
        ))
        return out
    _walk(str(ours_path.name), "", sample, ours, out)
    return out


def diff_trees(sample_root: Path, ours_root: Path) -> list[Divergence]:
    """Diff every YAML file present on either side.

    Files matching ``*.source.yaml`` are skipped: the source sidecar is an
    auxiliary file (not part of the bundle) used by the future XML exporter,
    and the hand-crafted sample doesn't ship one.
    """
    def _included(p: Path) -> bool:
        return not p.name.endswith(".source.yaml")

    sample_files = {p.relative_to(sample_root): p
                    for p in sample_root.rglob("*.yaml") if _included(p)}
    ours_files = {p.relative_to(ours_root): p
                  for p in ours_root.rglob("*.yaml") if _included(p)}
    out: list[Divergence] = []
    all_keys = sorted(set(sample_files.keys()) | set(ours_files.keys()), key=str)
    for rel in all_keys:
        if rel not in sample_files:
            if str(rel).startswith("assets/") and rel.name.endswith(".markers.yaml"):
                out.append(Divergence(
                    file=str(rel), path="", kind="extra-key",
                    status="expected",
                    note="new-format marker asset absent from legacy sample",
                ))
                continue
            if (
                len(rel.parts) >= 3
                and rel.parts[0] == "editions"
                and rel.parts[2] == "assets"
                and rel.name.endswith(".markers.yaml")
            ):
                out.append(Divergence(
                    file=str(rel), path="", kind="extra-key",
                    status="expected",
                    note="new-format marker asset absent from legacy sample",
                ))
                continue
            out.append(Divergence(file=str(rel), path="", kind="extra-key",
                                  status="unexpected",
                                  note="present in output, missing in sample"))
            continue
        if rel not in ours_files:
            out.append(Divergence(file=str(rel), path="", kind="missing-key",
                                  status="unexpected",
                                  note="present in sample, missing from output"))
            continue
        out.extend(diff_yaml_files(sample_files[rel], ours_files[rel]))
    return out


def render_report(divergences: list[Divergence]) -> str:
    """Render a Markdown divergence report grouped by status, then by file."""
    expected = [d for d in divergences if d.status == "expected"]
    unexpected = [d for d in divergences if d.status == "unexpected"]
    lines: list[str] = []
    lines.append("# Divergence from sample\n")
    lines.append(
        f"Generated comparing importer output against the hand-crafted sample. "
        f"`expected` rows are known sample artifacts; `unexpected` rows are "
        f"differences that warrant review.\n"
    )
    lines.append(f"- expected: {len(expected)}")
    lines.append(f"- unexpected: {len(unexpected)}\n")

    def fmt_value(v) -> str:
        s = repr(v)
        return s if len(s) <= 80 else s[:77] + "..."

    for status, rows in (("Unexpected", unexpected), ("Expected", expected)):
        lines.append(f"## {status} ({len(rows)})\n")
        if not rows:
            lines.append("_none_\n")
            continue
        by_file: dict[str, list[Divergence]] = {}
        for d in rows:
            by_file.setdefault(d.file, []).append(d)
        for file in sorted(by_file):
            lines.append(f"### {file}\n")
            for d in by_file[file][:200]:  # cap output per file
                lines.append(
                    f"- `{d.path or '(root)'}` — {d.kind}: "
                    f"sample={fmt_value(d.sample)} ours={fmt_value(d.ours)}"
                    + (f"  _({d.note})_" if d.note else "")
                )
            if len(by_file[file]) > 200:
                lines.append(f"- _… and {len(by_file[file]) - 200} more rows_")
            lines.append("")
    return "\n".join(lines)
