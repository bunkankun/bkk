"""Recipe loading.

A recipe is a small YAML file that pins per-text knobs the importer can't
infer. Today two formats consume it: ``tls`` (existing recipes carry just
``format`` / ``bundle`` / ``output_dir``) and ``krp`` (branch→edition
mapping, master witnesses, imglist source).

The KRP shape:

    format: krp
    text_id: KR3a0013
    source:
      repo: ../input/krp/KR3a0013
      editions:
        - {branch: WYG, short: WYG}
      master:
        branch: master
        witnesses: [WYG]
      imglist:
        branch: _data
        path: imglist/{text_id}_{NNN}.txt
    metadata:
      title: 傅子
      date: '2015-08-24'
    output:
      bundle: ../output/KR3a0013

Recipes are optional — the CLI also supports a recipe-less convention based
fallback. See ``cli.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class EditionSpec:
    branch: str
    short: str


@dataclass
class MasterSpec:
    branch: str
    witnesses: list[str] = field(default_factory=list)


@dataclass
class ImglistSpec:
    branch: str
    path: str = "imglist/{text_id}_{NNN}.txt"


@dataclass
class KrpSource:
    repo: Path
    editions: list[EditionSpec]
    master: MasterSpec | None
    imglist: ImglistSpec | None


@dataclass
class Recipe:
    format: str
    text_id: str | None = None
    source: KrpSource | None = None
    metadata: dict = field(default_factory=dict)
    output_bundle: Path | None = None


def load_recipe(path: Path) -> Recipe:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    fmt = data.get("format")
    if fmt is None:
        raise ValueError(f"recipe {path} has no `format` field")

    src = None
    if "source" in data:
        s = data["source"]
        repo_raw = s.get("repo")
        repo = (path.parent / repo_raw).resolve() if repo_raw else None
        editions = [
            EditionSpec(branch=e["branch"], short=e["short"])
            for e in s.get("editions", [])
        ]
        master = None
        if "master" in s:
            m = s["master"]
            master = MasterSpec(
                branch=m["branch"],
                witnesses=list(m.get("witnesses", [])),
            )
        imglist = None
        if "imglist" in s:
            il = s["imglist"]
            imglist = ImglistSpec(
                branch=il["branch"],
                path=il.get("path", "imglist/{text_id}_{NNN}.txt"),
            )
        src = KrpSource(repo=repo, editions=editions,
                        master=master, imglist=imglist)

    out_bundle = None
    out = data.get("output", {})
    if "bundle" in out:
        out_bundle = (path.parent / out["bundle"]).resolve()

    return Recipe(
        format=fmt,
        text_id=data.get("text_id"),
        source=src,
        metadata=data.get("metadata", {}) or {},
        output_bundle=out_bundle,
    )
