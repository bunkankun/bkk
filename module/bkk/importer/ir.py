"""In-memory abstract shape produced by readers and consumed by writers.

Readers (e.g. read/tls.py) produce a :class:`Bundle` from source files.
Writers (write/bundle.py) consume a :class:`Bundle` and emit YAML files
in the BKK archival format.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Marker:
    type: str
    offset: int
    content: str = ""
    id: str = ""
    extras: dict = field(default_factory=dict)


@dataclass
class Section:
    """One top-level <div> from the source. Not yet bucketed into front/body."""
    head_text: str
    head_marker_id: str
    text: str
    markers: list[Marker]
    bucket: str | None = None  # explicit "front"/"body"/"back" override


@dataclass
class Annotation:
    """One annotation entry, with seg_id+pos still un-resolved into an offset."""
    seg_id: str
    pos: int | None
    payload: dict
    source_role: str = "tls:ann"
    provenance: str | None = None


@dataclass
class Juan:
    seq: int
    sections: list[Section]
    annotations: list[Annotation] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class Bundle:
    text_id: str
    juans: list[Juan]
    metadata: dict = field(default_factory=dict)
    edition_short: str = "T"
    source: dict = field(default_factory=dict)
    source_info: dict | None = None
    pua_map: dict | None = None  # PUA-map.yaml payload (master only); None to skip
    witnesses: list[str] = field(default_factory=list)  # short ids of compared editions
