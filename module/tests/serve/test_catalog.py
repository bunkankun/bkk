"""/catalog: curated whitelist filtering + recipe-shaped response."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bkk.index import build_catalog_index
from bkk.serve import create_app
from bkk.serve.config import ServeConfig
from bkk.serve.routers.catalog import _browse_catalog_index, categories
from bkk.serve.state import AppState

from .conftest import write_bundle


def _write_frontmatter(path: Path, rows: list[dict[str, str]]) -> Path:
    fields = [
        "id", "title", "titlePinyin", "titleEnglish",
        "notBefore", "notAfter", "dzt_date",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        import csv
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})
    return path


@pytest.fixture
def rich_corpus(tmp_path: Path) -> Path:
    write_bundle(
        tmp_path,
        "TEXT_A",
        "甲",
        title="孟子",
        identifiers={"krp": "KR1h0001", "slug": ["mengzi"]},
        extra_metadata={
            "tags": {"kr-categories": ["KR1h"]},
            "authors": [{"name": "孟子"}],
            "composition_period": "戰國",
            "alt_titles": ["孟子注疏"],
            "source": {"name": "Kanripo"},
        },
    )
    write_bundle(
        tmp_path,
        "TEXT_B",
        "乙",
        title="論語",
        identifiers={"krp": "KR1h0002"},
        extra_metadata={
            "tags": {"kr-categories": ["KR1h"]},
            "authors": [{"name": "孔子"}],
            "composition_period": "春秋",
        },
    )
    write_bundle(
        tmp_path,
        "TEXT_C",
        "丙",
        title="說文解字",
        identifiers={"krp": "KR3a0001"},
        extra_metadata={
            "tags": {"kr-categories": ["KR3a"]},
            "authors": [{"name": "許慎"}],
        },
    )
    return tmp_path


@pytest.fixture
def cat_client(rich_corpus: Path) -> TestClient:
    config = ServeConfig(corpus_root=rich_corpus, index_path=rich_corpus / "_corpus.bkkx")
    return TestClient(create_app(config))


def test_catalog_no_filters_returns_all(cat_client: TestClient):
    r = cat_client.get("/catalog")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert {m["textid"] for m in body["matches"]} == {"TEXT_A", "TEXT_B", "TEXT_C"}
    assert "recipe" in body
    assert len(body["recipe"]["pins"]) == 3
    for pin in body["recipe"]["pins"]:
        assert pin["role"] == "match"


def test_catalog_filter_by_kr_category(cat_client: TestClient):
    r = cat_client.get("/catalog", params={"tags.kr-categories": "KR1h"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert {m["textid"] for m in body["matches"]} == {"TEXT_A", "TEXT_B"}


def test_catalog_filter_by_author(cat_client: TestClient):
    r = cat_client.get("/catalog", params={"authors.name": "孔子"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["matches"][0]["textid"] == "TEXT_B"


def test_catalog_filter_by_identifier(cat_client: TestClient):
    r = cat_client.get("/catalog", params={"metadata.identifiers.slug": "mengzi"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["matches"][0]["textid"] == "TEXT_A"


def test_catalog_and_across_keys(cat_client: TestClient):
    r = cat_client.get(
        "/catalog",
        params=[("tags.kr-categories", "KR1h"), ("authors.name", "孟子")],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["matches"][0]["textid"] == "TEXT_A"


def test_catalog_or_within_key(cat_client: TestClient):
    r = cat_client.get(
        "/catalog",
        params=[("authors.name", "孟子"), ("authors.name", "孔子")],
    )
    assert r.status_code == 200
    body = r.json()
    assert {m["textid"] for m in body["matches"]} == {"TEXT_A", "TEXT_B"}


def test_catalog_unknown_filter_400(cat_client: TestClient):
    r = cat_client.get("/catalog", params={"not_a_real_key": "x"})
    assert r.status_code == 400
    body = r.json()
    assert body["error"] == "unknown_filter_keys"
    assert "not_a_real_key" in body["unknown"]
    assert "title" in body["allowed"]


def test_catalog_pagination(cat_client: TestClient):
    r = cat_client.get("/catalog", params={"limit": 2, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert body["next_offset"] == 2
    assert len(body["matches"]) == 2

    r2 = cat_client.get("/catalog", params={"limit": 2, "offset": 2})
    body2 = r2.json()
    assert body2["next_offset"] is None
    assert len(body2["matches"]) == 1


def test_catalog_categories_use_bkkc_counts(tmp_path: Path):
    write_bundle(tmp_path, "KR1h0001", "甲", title="Late", identifiers={"krp": "KR1h0001"})
    write_bundle(tmp_path, "KR1h0002", "乙", title="Early", identifiers={"krp": "KR1h0002"})
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {"id": "KR1", "title": "經部"},
            {"id": "KR1h", "title": "四書類"},
            {"id": "KR1h0001", "title": "晚書", "notBefore": "100", "notAfter": "100"},
            {"id": "KR1h0002", "title": "早書", "notBefore": "1", "notAfter": "1"},
        ],
    )
    catalog_path = build_catalog_index(tmp_path, csv_path, tmp_path / "_catalog.bkkc")
    client = TestClient(create_app(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        catalog_path=catalog_path,
    )))

    body = client.get("/catalog/categories").json()
    kr1 = next(cat for cat in body["categories"] if cat["code"] == "KR1")
    kr1h = next(sub for sub in kr1["subcategories"] if sub["code"] == "KR1h")
    assert kr1["bundle_count"] == 2
    assert kr1h["bundle_count"] == 2


def test_catalog_categories_include_index_only_nested_sections(tmp_path: Path):
    write_bundle(tmp_path, "KR3ea001", "甲", title="Needles")
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {"id": "KR3", "title": "子部", "titleEnglish": "Masters"},
            {"id": "KR3e", "title": "醫家類", "titleEnglish": "Medicine"},
            {"id": "KR3ea", "title": "醫經", "titleEnglish": "Medical Classics"},
            {
                "id": "KR3ea001", "title": "針書",
                "titlePinyin": "Zhenshu", "titleEnglish": "Needle Book",
                "notBefore": "1", "notAfter": "1",
            },
        ],
    )
    catalog_path = build_catalog_index(tmp_path, csv_path, tmp_path / "_catalog.bkkc")
    state = AppState(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        catalog_path=catalog_path,
    ))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bkk=state)))

    body = categories(request)

    kr3 = next(cat for cat in body.categories if cat.code == "KR3")
    kr3e = next(sub for sub in kr3.subcategories if sub.code == "KR3e")
    kr3ea = next(sub for sub in kr3e.subcategories if sub.code == "KR3ea")
    assert kr3e.bundle_count == 1
    assert kr3ea.zh == "醫經"
    assert kr3ea.bundle_count == 1


def test_catalog_browse_uses_bkkc_for_category_sort_and_metadata(tmp_path: Path):
    write_bundle(tmp_path, "KR1h0001", "甲", title="Late", identifiers={"krp": "KR1h0001"})
    write_bundle(tmp_path, "KR1h0002", "乙", title="Early", identifiers={"krp": "KR1h0002"})
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {"id": "KR1", "title": "經部"},
            {"id": "KR1h", "title": "四書類"},
            {
                "id": "KR1h0001", "title": "晚書", "titlePinyin": "Wanshu",
                "titleEnglish": "Late Book", "notBefore": "100", "notAfter": "100",
            },
            {
                "id": "KR1h0002", "title": "早書", "titlePinyin": "Zaoshu",
                "titleEnglish": "Early Book", "notBefore": "1", "notAfter": "1",
            },
        ],
    )
    catalog_path = build_catalog_index(tmp_path, csv_path, tmp_path / "_catalog.bkkc")
    client = TestClient(create_app(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        catalog_path=catalog_path,
    )))

    body = client.get("/catalog", params={"tags.kr-categories": "KR1h"}).json()

    assert body["total"] == 2
    assert [m["textid"] for m in body["matches"]] == ["KR1h0002", "KR1h0001"]
    assert body["matches"][0]["title"] == "早書"
    assert body["matches"][0]["metadata"]["index_date"] == 1
    assert body["matches"][0]["metadata"]["title_pinyin"] == "Zaoshu"


def test_catalog_browse_uses_bkkc_without_manifest_cache(tmp_path: Path):
    write_bundle(tmp_path, "KR1h0001", "甲", title="Manifest title")
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {"id": "KR1", "title": "經部"},
            {"id": "KR1h", "title": "四書類"},
            {
                "id": "KR1h0001", "title": "Catalog title",
                "titlePinyin": "Catalog", "titleEnglish": "Catalog Book",
                "notBefore": "1", "notAfter": "1",
            },
        ],
    )
    catalog_path = build_catalog_index(tmp_path, csv_path, tmp_path / "_catalog.bkkc")
    state = AppState(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        catalog_path=catalog_path,
    ))

    class ExplodingCache:
        def get(self):
            raise AssertionError("catalog index path should not read manifest cache")

    state._cache = ExplodingCache()

    body = _browse_catalog_index(
        state,
        {"tags.kr-categories": ["KR1h"]},
        limit=50,
        offset=0,
    )

    assert body is not None
    assert body.total == 1
    assert body.matches[0].textid == "KR1h0001"
    assert body.matches[0].title == "Catalog title"


def test_catalog_browse_parent_category_excludes_descendants(tmp_path: Path):
    write_bundle(tmp_path, "KR3f0001", "乙", title="Parent")
    write_bundle(tmp_path, "KR3fa001", "甲", title="Stars")
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {"id": "KR3", "title": "子部"},
            {"id": "KR3f", "title": "天文算法類"},
            {
                "id": "KR3f0001", "title": "父類書",
                "notBefore": "1", "notAfter": "1",
            },
            {"id": "KR3fa", "title": "天文"},
            {
                "id": "KR3fa001", "title": "星書",
                "notBefore": "1", "notAfter": "1",
            },
        ],
    )
    catalog_path = build_catalog_index(tmp_path, csv_path, tmp_path / "_catalog.bkkc")
    state = AppState(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        catalog_path=catalog_path,
    ))

    body = _browse_catalog_index(
        state,
        {"tags.kr-categories": ["KR3f"]},
        limit=50,
        offset=0,
    )

    assert body is not None
    assert body.total == 1
    assert body.matches[0].textid == "KR3f0001"


def test_catalog_q_searches_titles_pinyin_english_and_identifiers(tmp_path: Path):
    write_bundle(
        tmp_path,
        "KR1a0001",
        "甲",
        title="Manifest Zhouyi",
        identifiers={"krp": "KR1a0001", "slug": ["book-of-changes"]},
        canonical_identifier="bkk:test/KR1a0001/v1",
    )
    write_bundle(
        tmp_path,
        "KR1a0002",
        "乙",
        title="Manifest Other",
        identifiers={"krp": "KR1a0002"},
    )
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {"id": "KR1", "title": "經部"},
            {"id": "KR1a", "title": "易類"},
            {
                "id": "KR1a0001", "title": "周易", "titlePinyin": "Zhōuyì",
                "titleEnglish": "The Changes", "notBefore": "-900",
                "notAfter": "-100",
            },
            {
                "id": "KR1a0002", "title": "周易旁通",
                "titlePinyin": "Zhouyi pangtong",
                "titleEnglish": "Changes Companion", "notBefore": "10",
                "notAfter": "10",
            },
        ],
    )
    catalog_path = build_catalog_index(tmp_path, csv_path, tmp_path / "_catalog.bkkc")
    client = TestClient(create_app(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        catalog_path=catalog_path,
    )))

    by_pinyin = client.get("/catalog", params={"q": "zhouyi"}).json()
    assert by_pinyin["total"] == 2
    assert {m["textid"] for m in by_pinyin["matches"]} == {"KR1a0001", "KR1a0002"}

    by_english = client.get("/catalog", params={"q": "changes"}).json()
    assert by_english["total"] == 2

    by_identifier = client.get("/catalog", params={"q": "book-of-changes"}).json()
    assert by_identifier["total"] == 1
    assert by_identifier["matches"][0]["textid"] == "KR1a0001"


def test_catalog_q_ranks_exact_id_before_substring(tmp_path: Path):
    write_bundle(tmp_path, "KR1a0001", "甲", identifiers={"krp": "KR1a0001"})
    write_bundle(tmp_path, "KR1a0002", "乙", identifiers={"slug": ["prefix-KR1a0001"]})
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {"id": "KR1", "title": "經部"},
            {"id": "KR1a", "title": "易類"},
            {"id": "KR1a0001", "title": "A", "notBefore": "100", "notAfter": "100"},
            {"id": "KR1a0002", "title": "B", "notBefore": "1", "notAfter": "1"},
        ],
    )
    catalog_path = build_catalog_index(tmp_path, csv_path, tmp_path / "_catalog.bkkc")
    client = TestClient(create_app(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        catalog_path=catalog_path,
    )))

    body = client.get("/catalog", params={"q": "KR1a0001"}).json()

    assert [m["textid"] for m in body["matches"]] == ["KR1a0001", "KR1a0002"]


def test_catalog_q_combines_with_category_filter(tmp_path: Path):
    write_bundle(tmp_path, "KR1a0001", "甲", identifiers={"krp": "KR1a0001"})
    write_bundle(tmp_path, "KR3a0001", "乙", identifiers={"krp": "KR3a0001"})
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {"id": "KR1", "title": "經部"},
            {"id": "KR1a", "title": "易類"},
            {"id": "KR3", "title": "子部"},
            {"id": "KR3a", "title": "儒家類"},
            {
                "id": "KR1a0001", "title": "同名", "titlePinyin": "Tongming",
                "notBefore": "1", "notAfter": "1",
            },
            {
                "id": "KR3a0001", "title": "同名", "titlePinyin": "Tongming",
                "notBefore": "1", "notAfter": "1",
            },
        ],
    )
    catalog_path = build_catalog_index(tmp_path, csv_path, tmp_path / "_catalog.bkkc")
    client = TestClient(create_app(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        catalog_path=catalog_path,
    )))

    body = client.get(
        "/catalog",
        params={"q": "tongming", "tags.kr-categories": "KR3a"},
    ).json()

    assert body["total"] == 1
    assert body["matches"][0]["textid"] == "KR3a0001"


def test_catalog_timeline_buckets_and_browsing(tmp_path: Path):
    write_bundle(tmp_path, "KR1a0001", "甲")
    write_bundle(tmp_path, "KR1a0002", "乙")
    write_bundle(tmp_path, "KR1a0003", "丙")
    csv_path = _write_frontmatter(
        tmp_path / "frontmatter.csv",
        [
            {"id": "KR1", "title": "經部"},
            {"id": "KR1a", "title": "易類"},
            {"id": "KR1a0001", "title": "BCE", "notBefore": "-390", "notAfter": "-390"},
            {"id": "KR1a0002", "title": "CE", "notBefore": "101", "notAfter": "101"},
            {"id": "KR1a0003", "title": "Missing"},
        ],
    )
    catalog_path = build_catalog_index(tmp_path, csv_path, tmp_path / "_catalog.bkkc")
    client = TestClient(create_app(ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        catalog_path=catalog_path,
    )))

    timeline = client.get("/catalog/timeline").json()

    assert timeline["buckets"] == [
        {
            "key": "bce-04",
            "label": "4th c. BCE",
            "start": -400,
            "end": -301,
            "bundle_count": 1,
        },
        {
            "key": "ce-02",
            "label": "2nd c. CE",
            "start": 101,
            "end": 200,
            "bundle_count": 1,
        },
    ]

    body = client.get("/catalog", params={"century": "bce-04"}).json()
    assert body["total"] == 1
    assert body["matches"][0]["textid"] == "KR1a0001"
