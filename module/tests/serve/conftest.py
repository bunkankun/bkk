"""Shared fixtures: a tmp corpus + TestClient bound to a freshly built app."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from bkk.serve import create_app
from bkk.serve.config import ServeConfig


def write_bundle(
    corpus_root: Path,
    textid: str,
    body_text: str,
    *,
    title: str = "Test Bundle",
    editions: list[dict] | None = None,
    identifiers: dict | None = None,
    variants: list[dict] | None = None,
    base_edition: str | None = None,
    canonical_identifier: str | None = None,
    extra_metadata: dict | None = None,
    references: list[dict] | None = None,
    extra_files: dict[str, str] | None = None,
    manifest_hash: str | None = None,
) -> Path:
    """Write a single-juan synthetic bundle and return its directory."""
    bundle_dir = corpus_root / textid
    bundle_dir.mkdir(parents=True)

    markers = [{"type": "variant", **v} for v in (variants or [])]
    (bundle_dir / f"{textid}_001.yaml").write_text(
        yaml.safe_dump(
            {
                "canonical_identifier": f"bkk:test/{textid}/v1/juan/1",
                "seq": 1,
                "body": {
                    "text": body_text,
                    "hash": "sha256:0",
                    "markers": markers,
                },
                "hash": "sha256:0",
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    metadata: dict = {"title": title, "edition": {"short": "bkk"}}
    if identifiers is not None:
        metadata["identifiers"] = identifiers
    if base_edition is not None:
        metadata["base_edition"] = base_edition
    if extra_metadata:
        metadata.update(extra_metadata)

    assets: dict = {
        "parts": [
            {"seq": 1, "filename": f"{textid}_001.yaml", "hash": "sha256:0"}
        ],
    }
    if references is not None:
        assets["references"] = references

    manifest_doc: dict = {
        "canonical_identifier": (
            canonical_identifier
            if canonical_identifier is not None
            else f"bkk:test/{textid}/v1"
        ),
        "editions": editions or [{"short": "X", "label": "x"}],
        "assets": assets,
    }
    if manifest_hash is not None:
        manifest_doc["hash"] = manifest_hash
    (bundle_dir / f"{textid}.manifest.yaml").write_text(
        yaml.safe_dump(
            {
                **manifest_doc,
                "table_of_contents": [
                    {
                        "ref": {
                            "seq": 1,
                            "marker_id": f"{textid}_001-1a",
                            "span": ["body", 0, len(body_text)],
                        },
                        "label": title,
                    }
                ],
                "metadata": metadata,
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    for filename, content in (extra_files or {}).items():
        (bundle_dir / filename).write_text(content, encoding="utf-8")

    return bundle_dir


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    """Two-bundle synthetic corpus, ready for a server to point at."""
    write_bundle(
        tmp_path,
        "TEST0001",
        "甲乙丙丁戊己庚辛壬癸",
        title="天干",
        identifiers={"krp": "TEST0001", "slug": ["tiangan"]},
    )
    write_bundle(
        tmp_path,
        "TEST0002",
        "ABCDEFGHIJKLMNOP",
        title="Latin Sample",
    )
    return tmp_path


@pytest.fixture
def client(corpus: Path) -> TestClient:
    config = ServeConfig(
        corpus_root=corpus,
        index_path=corpus / "_corpus.bkkx",
    )
    app = create_app(config)
    return TestClient(app)
