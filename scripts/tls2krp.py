#!/usr/bin/env python3
"""Quick TLS-flavored CBETA → KRP-shape text converter.

One TEI XML file → one plain-text file. The body is rendered as text
content with two special-cased structural markers preserved:

  <lb/>                 → newline
  <pb xml:id="X" .../>  → literal "<pb id=\"X\"/>" inline tag

A third element is also handled because dropping it silently loses
punctuation: <c n="."/> emits its `n` attribute value (TEI's glyph
fallback for characters that don't round-trip cleanly).

Every other tag is stripped; only its text content survives. The
script does NOT go through the BKK bundle pipeline — it's a direct
file-to-file transform for one-off use.

Usage (single file):
    python tls2krp.py INPUT.xml [OUTPUT.txt]      # OUTPUT optional → stdout

Usage (batch):
    python tls2krp.py INPUT_DIR OUTPUT_DIR

In batch mode, every ``*.xml`` under INPUT_DIR is converted; each
result lands at ``OUTPUT_DIR/<text-id>/<text-id>.txt``. The text-id
comes from ``<idno type="kanripo">`` when present, otherwise from the
input filename stem (with a stderr warning).
"""
from __future__ import annotations

import sys
from pathlib import Path

from lxml import etree


TEI = "http://www.tei-c.org/ns/1.0"
XML_NS = "http://www.w3.org/XML/1998/namespace"


def _local(tag) -> str | None:
    """Return the local name of an lxml tag, or None for comments/PIs."""
    if not isinstance(tag, str):
        return None
    return etree.QName(tag).localname


def render(el) -> str:
    """Recursively render an element to KRP-shape text."""
    name = _local(el.tag)
    tail = el.tail or ""

    if name == "lb":
        return "\n" + tail
    if name == "pb":
        ref = el.get(f"{{{XML_NS}}}id") or el.get("n") or ""
        return f'<pb id="{ref}"/>' + tail
    if name == "c":
        return (el.get("n") or "") + tail

    parts = [el.text or ""]
    for child in el:
        parts.append(render(child))
    parts.append(tail)
    return "".join(parts)


def convert(xml_path: Path) -> str:
    parser = etree.XMLParser(recover=True)
    tree = etree.parse(str(xml_path), parser)
    body = tree.find(f".//{{{TEI}}}body")
    if body is None:
        raise SystemExit(f"error: no <body> element in {xml_path}")
    text = (body.text or "") + "".join(render(c) for c in body)
    text = "".join(line.lstrip(" \t") for line in text.splitlines(keepends=True))
    if text and not text.endswith("\n"):
        text += "\n"
    return text


def _read_kanripo_idno(xml_path: Path) -> str | None:
    """Return the first ``<idno type="kanripo">`` value in ``xml_path``, or None."""
    parser = etree.XMLParser(recover=True)
    try:
        tree = etree.parse(str(xml_path), parser)
    except (etree.XMLSyntaxError, OSError):
        return None
    for idno in tree.iter(f"{{{TEI}}}idno"):
        if (idno.get("type") or "").strip().lower() == "kanripo":
            val = (idno.text or "").strip()
            if val:
                return val
    return None


def convert_dir(in_dir: Path, out_dir: Path) -> int:
    """Batch-convert every ``*.xml`` under ``in_dir`` into ``out_dir``.

    Each text lands at ``out_dir/<text-id>/<text-id>.txt``. Multiple
    input files declaring the same kanripo idno (TLS letter-suffix
    splits) are concatenated in sorted-filename order.
    """
    written: dict[str, Path] = {}
    for xml_path in sorted(in_dir.rglob("*.xml")):
        text_id = _read_kanripo_idno(xml_path)
        if text_id is None:
            text_id = xml_path.stem
            print(f"warn: no <idno type=\"kanripo\"> in {xml_path}; "
                  f"using filename stem {text_id!r}", file=sys.stderr)
        body = convert(xml_path)
        out_path = out_dir / text_id / f"{text_id}.txt"
        if text_id in written:
            print(f"note: appending {xml_path.name} to existing {text_id} bundle",
                  file=sys.stderr)
            with out_path.open("a", encoding="utf-8") as fh:
                fh.write(body if body.startswith("\n") else "\n" + body)
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(body, encoding="utf-8")
            written[text_id] = out_path
    return len(written)


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] in {"-h", "--help"}:
        print(__doc__, file=sys.stderr)
        return 2
    src = Path(argv[1])
    if src.is_dir():
        if len(argv) < 3:
            print("error: batch mode requires OUTPUT_DIR", file=sys.stderr)
            return 2
        out_dir = Path(argv[2])
        n = convert_dir(src, out_dir)
        print(f"wrote {n} text(s) to {out_dir}", file=sys.stderr)
        return 0
    text = convert(src)
    if len(argv) >= 3:
        Path(argv[2]).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
