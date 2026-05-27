"""Direct CBETA P5 reader.

CBETA's native XML is not the same shape as the TLS-CBETA derivative: juan
and mulu elements live in the CBETA namespace and body text is often bare
paragraph/verse content rather than TLS ``<seg>`` wrappers. This reader keeps
the import deliberately documentary: body text becomes BKK text, layout and
source punctuation become markers, and the bundle id is supplied by the
KR-to-CBETA mapping row.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

from lxml import etree

from ..charset import is_allowed_body_char
from ..ir import Bundle, Juan, Marker, Section
from .tls import XML_NS, _attrs_to_dict, _qname_to_str


TEI_NS = "http://www.tei-c.org/ns/1.0"
CB_NS = "http://www.cbeta.org/ns/1.0"


def _q(local: str, ns: str = TEI_NS) -> str:
    return f"{{{ns}}}{local}"


def _cb(local: str) -> str:
    return f"{{{CB_NS}}}{local}"


def _xmlid(el) -> str:
    return el.get(_q("id", XML_NS), "")


def _local(el) -> str:
    return etree.QName(el).localname


def _strip_ref(raw: str) -> str:
    return raw[1:] if raw.startswith("#") else raw


def _reading_text(el) -> str:
    return unicodedata.normalize("NFC", "".join(el.itertext()).strip())


def _normalize_juan_n(raw: str) -> str:
    if not raw:
        return "001"
    if raw.isdigit() and len(raw) < 3:
        return raw.zfill(3)
    return raw


def _marker_id(text_id: str, edition: str, label: str, tail: str) -> str:
    return f"{text_id}_{edition}_{label}-{tail}"


def _append_text(text: str, text_buf: list[str], markers: list[Marker]) -> None:
    if not text:
        return

    def offset() -> int:
        return sum(len(p) for p in text_buf)

    for ch in unicodedata.normalize("NFC", text):
        if is_allowed_body_char(ch):
            text_buf.append(ch)
        elif ch == "\u3000":
            markers.append(Marker(
                type="indent", offset=offset(), content=ch, id="",
            ))
        elif ch.isspace():
            continue
        else:
            markers.append(Marker(
                type="punctuation", offset=offset(), content=ch, id="",
            ))


class _DirectReader:
    def __init__(self, kr_id: str, old_id: str, edition: str):
        self.kr_id = kr_id
        self.old_id = old_id
        self.edition = edition
        self.current_label = "000"
        self.current_jhead = ""
        self.text_buf: list[str] = []
        self.markers: list[Marker] = []
        self.juans: list[Juan] = []
        self.mulu_indexes: dict[str, int] = {}
        self.seen_ids: dict[str, int] = {}
        self.anchor_offsets: dict[str, tuple[str, int]] = {}

    def offset(self) -> int:
        return sum(len(p) for p in self.text_buf)

    def unique_id(self, raw_id: str) -> str:
        if not raw_id:
            return ""
        count = self.seen_ids.get(raw_id, 0) + 1
        self.seen_ids[raw_id] = count
        if count == 1:
            return raw_id
        return f"{raw_id}_dup{count}"

    def finish_juan(self) -> None:
        if not self.text_buf and not self.markers:
            return
        try:
            seq = int(self.current_label)
        except ValueError:
            seq = len(self.juans) + 1
        section = Section(
            head_text=self.current_jhead,
            head_marker_id=(
                _marker_id(
                    self.kr_id, self.edition,
                    self.current_label, "juan-start",
                )
                if self.current_jhead else ""
            ),
            text="".join(self.text_buf),
            markers=list(self.markers),
            bucket="front" if self.current_label == "000" else "body",
        )
        metadata: dict = {"flavor": "cbeta"}
        if self.current_jhead:
            metadata["juan_label"] = self.current_jhead
        self.juans.append(Juan(seq=seq, sections=[section], metadata=metadata))
        self.text_buf = []
        self.markers = []

    def append_text(self, text: str) -> None:
        _append_text(text, self.text_buf, self.markers)

    def emit_pb(self, el) -> None:
        original_id = _xmlid(el)
        tail = (el.get("n") or "").strip()
        if not tail:
            tail = original_id.rsplit(".", 1)[-1] if original_id else "pb"
        mid = self.unique_id(
            _marker_id(self.kr_id, self.edition, self.current_label, tail)
        )
        extras = {
            _qname_to_str(k): v
            for k, v in el.attrib.items()
            if _qname_to_str(k) != "xml:id"
        }
        if original_id:
            extras["source_xml_id"] = original_id
        self.markers.append(Marker(
            type="page-break", offset=self.offset(), id=mid, extras=extras,
        ))

    def emit_lb(self, el) -> None:
        ed = el.get("ed", "")
        n = el.get("n", "")
        tail = n or "lb"
        self.markers.append(Marker(
            type="line-break", offset=self.offset(),
            id=self.unique_id(
                _marker_id(self.kr_id, self.edition, self.current_label, tail)
            ),
            extras={k: v for k, v in {"ed": ed, "n": n}.items() if v},
        ))

    def emit_mulu(self, el) -> None:
        text = "".join(el.itertext()).strip()
        if not text:
            return
        label = self.current_label
        self.mulu_indexes[label] = self.mulu_indexes.get(label, 0) + 1
        mid = _marker_id(
            self.kr_id, self.edition,
            label, f"mulu-{self.mulu_indexes[label]}",
        )
        extras = _attrs_to_dict(el.attrib)
        self.markers.append(Marker(
            type="cbeta:mulu", offset=self.offset(),
            content=unicodedata.normalize("NFC", text),
            id=mid, extras=extras,
        ))

    def emit_juan(self, el) -> None:
        fun = el.get("fun", "")
        label = _normalize_juan_n(el.get("n", ""))
        jhead_el = el.find(_cb("jhead"))
        jhead = ""
        if jhead_el is not None:
            jhead = unicodedata.normalize(
                "NFC", "".join(jhead_el.itertext()).strip(),
            )

        if fun == "open":
            self.finish_juan()
            self.current_label = label
            self.current_jhead = jhead
            extras = {"juan_n": label}
            if jhead:
                extras["jhead"] = jhead
            self.markers.append(Marker(
                type="cbeta:juan-start", offset=self.offset(),
                content=jhead,
                id=_marker_id(self.kr_id, self.edition, label, "juan-start"),
                extras=extras,
            ))
        elif fun == "close":
            extras = {"juan_n": label}
            if jhead:
                extras["jhead"] = jhead
            self.markers.append(Marker(
                type="cbeta:juan-end", offset=self.offset(),
                content=jhead,
                id=_marker_id(self.kr_id, self.edition, label, "juan-end"),
                extras=extras,
            ))

    def record_anchor(self, el) -> None:
        xml_id = _xmlid(el)
        if xml_id.startswith(("beg", "end")):
            self.anchor_offsets[xml_id] = (self.current_label, self.offset())

    def attach_variant(
        self,
        *,
        label: str,
        offset: int,
        length: int,
        lemma: str,
        readings: dict[str, str],
    ) -> None:
        for juan in self.juans:
            if juan.seq == _juan_seq(label):
                break
        else:
            return
        if not juan.sections:
            return
        juan.sections[0].markers.append(Marker(
            type="variant",
            offset=offset,
            content=lemma,
            id="",
            extras={"length": length, **readings},
        ))

    def walk(self, el) -> None:
        for child in el.iterchildren():
            if not isinstance(child.tag, str):
                continue
            tag = _local(child)
            ns = etree.QName(child).namespace
            if tag == "pb" and ns == TEI_NS:
                self.emit_pb(child)
            elif tag == "lb" and ns == TEI_NS:
                self.emit_lb(child)
            elif tag == "mulu" and ns == CB_NS:
                self.emit_mulu(child)
            elif tag == "juan" and ns == CB_NS:
                self.emit_juan(child)
            elif tag == "anchor" and ns == TEI_NS:
                self.record_anchor(child)
            elif tag == "caesura":
                self.markers.append(Marker(
                    type="punctuation", offset=self.offset(),
                    content="。", id="",
                ))
            else:
                self.append_text(child.text or "")
                self.walk(child)
            self.append_text(child.tail or "")


def _preferred_title(tree) -> str:
    titles = tree.findall(f".//{_q('titleStmt')}/{_q('title')}")
    for title in titles:
        if title.get("level") == "m" and title.get(_q("lang", XML_NS)) == "zh-Hant":
            text = "".join(title.itertext()).strip()
            if text:
                return text
    for title in titles:
        if title.get(_q("lang", XML_NS)) == "zh-Hant":
            text = "".join(title.itertext()).strip()
            if text:
                return text
    for title in titles:
        text = "".join(title.itertext()).strip()
        if text:
            return text
    return ""


def _source_info_header(tree) -> dict:
    root = tree.getroot()
    out: dict = {}
    if etree.QName(root).localname == "TEI":
        attrs = _attrs_to_dict(root.attrib)
        if attrs:
            out["root_attrs"] = attrs
    header = tree.find(f".//{_q('teiHeader')}")
    if header is not None:
        out["header"] = {
            "tag": "teiHeader",
            "attrs": _attrs_to_dict(header.attrib),
        }
    return out


def _juan_seq(label: str) -> int:
    try:
        return int(label)
    except ValueError:
        return 0


def _wit_label(witness_el) -> str:
    raw = _reading_text(witness_el)
    return raw.strip("【】[]()（）").strip() or _strip_ref(_xmlid(witness_el))


def _witness_map(tree) -> dict[str, str]:
    out: dict[str, str] = {}
    for wit in tree.findall(f".//{_q('witness')}"):
        xml_id = _xmlid(wit)
        if xml_id:
            out[xml_id] = _wit_label(wit)
    return out


def _wit_keys(raw: str, wit_map: dict[str, str]) -> list[str]:
    out: list[str] = []
    for token in raw.split():
        ref = _strip_ref(token)
        out.append(wit_map.get(ref, ref))
    return out


def _attach_apparatus_variants(
    tree,
    reader: _DirectReader,
    wit_map: dict[str, str],
) -> list[dict]:
    used_witnesses: dict[str, dict] = {}
    for app in tree.findall(f".//{_q('back')}//{_q('app')}"):
        from_id = _strip_ref(app.get("from", ""))
        to_id = _strip_ref(app.get("to", ""))
        if not from_id or not to_id:
            continue
        start = reader.anchor_offsets.get(from_id)
        end = reader.anchor_offsets.get(to_id)
        if start is None or end is None or start[0] != end[0]:
            continue

        lem = app.find(_q("lem"))
        if lem is None:
            continue
        lemma = _reading_text(lem)
        readings: dict[str, str] = {}
        for rdg in app.findall(_q("rdg")):
            reading = _reading_text(rdg)
            for key in _wit_keys(rdg.get("wit", ""), wit_map):
                if not key:
                    continue
                readings[key] = reading
                used_witnesses.setdefault(key, {"short": key})

        if not readings:
            continue
        reader.attach_variant(
            label=start[0],
            offset=start[1],
            length=max(0, end[1] - start[1]),
            lemma=lemma,
            readings=readings,
        )

    for xml_id, label in wit_map.items():
        if label in used_witnesses:
            used_witnesses[label]["label"] = label
            used_witnesses[label]["source_xml_id"] = xml_id
    return list(used_witnesses.values())


def _derive_edition(tree, old_id: str) -> str:
    pb = tree.find(f".//{_q('body')}//{_q('pb')}")
    if pb is not None and pb.get("ed"):
        return pb.get("ed", "").strip()
    for ch in old_id:
        if ch.isalpha():
            return ch
    return "CBETA"


def read_cbeta(text_xml: Path, row: dict[str, str]) -> Bundle:
    kr_id = row["kr_id"]
    old_id = row["old_id"]
    tree = etree.parse(str(text_xml), etree.XMLParser(recover=True))
    body = tree.find(f".//{_q('body')}")
    if body is None:
        raise ValueError(f"no <body> element in {text_xml}")

    edition = _derive_edition(tree, old_id)
    reader = _DirectReader(kr_id, old_id, edition)
    reader.walk(body)
    reader.finish_juan()
    wit_map = _witness_map(tree)
    variant_editions = _attach_apparatus_variants(tree, reader, wit_map)

    identifiers = {
        "krp": kr_id,
        "cbeta": old_id,
        "cbeta_old_id": old_id,
    }
    for key in ("authorityID", "json_key", "alt"):
        if row.get(key):
            identifiers[key.lower()] = row[key]

    title = row.get("title") or _preferred_title(tree)
    source = {"repository": "cbeta", "path": str(text_xml), "old_id": old_id}
    metadata = {
        "title": title,
        "identifiers": identifiers,
        "source": source,
    }
    if variant_editions:
        metadata["editions"] = variant_editions

    source_info = {
        "text_id": kr_id,
        "format": "cbeta-direct",
        "format_version": 1,
        "source_files": {"text": str(text_xml)},
        "mapping": {
            k: v for k, v in row.items()
            if k in {"kr_id", "kr_subsection", "old_id", "authorityID", "json_key"}
            and v
        },
        "tei": _source_info_header(tree),
    }

    return Bundle(
        text_id=kr_id,
        juans=reader.juans,
        metadata=metadata,
        edition_short=edition,
        source=source,
        source_info=source_info,
        witnesses=[e["short"] for e in variant_editions],
    )
