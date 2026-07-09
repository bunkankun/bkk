"""Endpoint /search wraps bkk.index.Index."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from bkk.index.catalog import build_catalog_index
from bkk.serve import create_app
from bkk.serve.config import ServeConfig
from bkk.serve.routers import search as search_router

from .conftest import write_bundle


def test_search_master_hit(client):
    r = client.get("/search", params={"q": "丙丁"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "丙丁"
    assert body["query_mode"] == "literal"
    assert body["total"] >= 1
    hit = body["hits"][0]
    assert hit["textid"] == "TEST0001"
    assert hit["bucket"] == "body"
    assert hit["match"] == "丙丁"
    assert hit["matched_via"] == "master"


def test_search_regex_master_hit(client):
    r = client.get("/search", params={"q": "/甲乙.丁/"})
    assert r.status_code == 200
    body = r.json()
    assert body["query_mode"] == "regex"
    assert body["total"] == 1
    hit = body["hits"][0]
    assert hit["match"] == "甲乙丙丁"
    assert hit["matched_text"] == "甲乙丙丁"
    assert hit["matched_via"] == "master"


def test_search_regex_witness_hit(tmp_path: Path):
    write_bundle(
        tmp_path,
        "TESTVAR",
        "abcXdef",
        editions=[{"short": "SBCK", "label": "SBCK"}],
        variants=[{"offset": 3, "length": 1, "content": "X", "SBCK": "Y"}],
    )
    config = ServeConfig(corpus_root=tmp_path, index_path=tmp_path / "_corpus.bkkx")
    client = TestClient(create_app(config))

    r = client.get("/search", params={"q": "/Yd./"})
    assert r.status_code == 200
    body = r.json()
    assert body["query_mode"] == "regex"
    assert body["total"] == 1
    hit = body["hits"][0]
    assert hit["matched_via"] == "SBCK"
    assert hit["matched_text"] == "Yde"
    assert hit["match"] == "Xde"


def test_search_regex_requires_anchor_or_scope(client):
    r = client.get("/search", params={"q": "/甲.丁/"})
    assert r.status_code == 400
    assert r.json()["error"] == "regex_requires_scope"

    scoped = client.get("/search", params={"q": "/甲..丁/", "textid": "TEST0001"})
    assert scoped.status_code == 200
    assert scoped.json()["query_mode"] == "regex"
    assert scoped.json()["total"] == 1


def test_search_regex_rejects_invalid_and_zero_length(client):
    bad = client.get("/search", params={"q": "/甲(/"})
    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_regex"

    zero = client.get("/search", params={"q": "/甲*/"})
    assert zero.status_code == 400
    assert zero.json()["error"] == "zero_length_regex"


def test_search_no_match(client):
    r = client.get("/search", params={"q": "未存在"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0
    assert body["hits"] == []


def test_search_textid_scope(client):
    # Substring exists in TEST0002 but we restrict to TEST0001.
    r = client.get("/search", params={"q": "DEF", "textid": "TEST0001"})
    assert r.status_code == 200
    assert r.json()["total"] == 0

    r = client.get("/search", params={"q": "DEF", "textid": "TEST0002"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert all(h["textid"] == "TEST0002" for h in body["hits"])


def test_search_repeated_textids_scope(client):
    request = SimpleNamespace(app=client.app)
    hits, *_ = search_router._search_hits(
        request,
        q="甲",
        textid=None,
        textids=["TEST0001", "TEST0002"],
        witness=None,
        voice=None,
        category=None,
        category_descendants=True,
        date_before=None,
        date_after=None,
        left_char=None,
        right_char=None,
        left_bigram=None,
        right_bigram=None,
        around_binom=None,
        sort="textid",
        context=20,
    )
    assert len(hits) >= 1
    assert {h.textid for h in hits} == {"TEST0001"}


def test_search_textids_endpoint_returns_full_unique_set(client):
    request = SimpleNamespace(app=client.app)
    body = search_router.search_textids(
        request,
        q="甲",
        textid=None,
        textids=None,
        textid_not=None,
        witness=None,
        witness_not=None,
        voice=None,
        voice_not=None,
        category=None,
        category_not=None,
        category_descendants=True,
        date_before=None,
        date_after=None,
        left_char=None,
        left_char_not=None,
        right_char=None,
        right_char_not=None,
        left_bigram=None,
        left_bigram_not=None,
        right_bigram=None,
        right_bigram_not=None,
        around_binom=None,
        around_binom_not=None,
        sort="textid",
        context=20,
    )
    assert body.query == "甲"
    assert body.hit_count >= body.text_count
    assert body.text_count == len(body.textids)
    assert body.textids == sorted(set(body.textids))


def test_search_hit_carries_recipe(client):
    r = client.get("/search", params={"q": "丙丁"})
    assert r.status_code == 200
    hit = r.json()["hits"][0]
    recipe = hit["recipe"]
    assert recipe["pins"][0]["textid"] == hit["textid"]
    sel = recipe["pins"][0]["selection"]
    assert sel["juan"] == hit["juan_seq"]
    assert sel["bucket"] == hit["bucket"]
    assert sel["offset"] == hit["master_offset"]
    assert sel["length"] == hit["master_length"]


def test_search_hit_recipe_round_trip(client):
    r = client.get("/search", params={"q": "丙丁"})
    hit = r.json()["hits"][0]
    fulfil = client.post("/recipes:fulfil", json=hit["recipe"])
    assert fulfil.status_code == 200
    data = fulfil.json()
    assert data["errors"] == []
    assert data["results"][0]["content"]["text"] == "丙丁"


def test_search_pagination(client):
    r = client.get("/search", params={"q": "甲", "limit": 1, "offset": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["limit"] == 1
    assert body["offset"] == 0
    assert len(body["hits"]) <= 1


def test_search_category_and_date_filters_use_catalog_index(tmp_path: Path):
    write_bundle(tmp_path, "KR1a0001", "甲乙丙", title="early")
    write_bundle(tmp_path, "KR3b0001", "丁甲戊", title="late")
    csv_path = tmp_path / "frontmatter.csv"
    csv_path.write_text(
        "\n".join(
            [
                "id,title,titlePinyin,titleEnglish,notBefore,notAfter,dzt_date",
                "KR1,經部,jing,Jing,,,",
                "KR1a,易類,yi,Yi,,,",
                "KR3,子部,zi,Zi,,,",
                "KR3b,儒家類,ru,Ru,,,",
                "KR1a0001,early,,,100,100,",
                "KR3b0001,late,,,900,900,",
            ]
        ),
        encoding="utf-8",
    )
    catalog_path = build_catalog_index(tmp_path, csv_path, tmp_path / "_catalog.bkkc")
    config = ServeConfig(
        corpus_root=tmp_path,
        index_path=tmp_path / "_corpus.bkkx",
        catalog_path=catalog_path,
    )
    client = TestClient(create_app(config))

    r = client.get(
        "/search",
        params={"q": "甲", "category": "KR1", "date_before": "500"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 1
    assert body["hits"][0]["textid"] == "KR1a0001"
    categories = {v["value"]: v for v in body["facets"]["category"]}
    assert categories["KR1"]["selected"] is True
    assert categories["KR1a"]["count"] == 1
    assert body["facets"]["date"]["max"] == 100


def test_search_context_facets_filter_and_count(client):
    r = client.get("/search", params={"q": "丙", "left_char": "乙"})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1
    assert all(h["left"].endswith("乙") for h in body["hits"])
    left_values = {v["value"]: v for v in body["facets"]["left_char"]}
    assert left_values["乙"]["selected"] is True


# ---------------------------------------------------------------- sort modes


@pytest.fixture
def sort_client(tmp_path: Path) -> TestClient:
    """Three single-juan bundles with controllable text + dates for sort tests.

    The shared query "甲" appears in all three. The contexts are crafted so:
    - SORT_A and SORT_B share many KWIC chars (closeness keeps them adjacent)
    - SORT_C has a disjoint KWIC (closeness pushes it to the end)
    - composition_period values give a unique date ordering
    """
    write_bundle(
        tmp_path,
        "SORTC",
        "禾稻黍稷甲麥麻菽豆",  # KWIC chars: 禾稻黍稷 麥麻菽豆 (no overlap with A/B)
        title="C-bundle",
        extra_metadata={"composition_period": "1900"},
    )
    write_bundle(
        tmp_path,
        "SORTA",
        "天日月星甲山川風雨",  # KWIC chars share 天日月星 / 山川風雨 with B
        title="A-bundle",
        extra_metadata={"composition_period": "前500"},
    )
    write_bundle(
        tmp_path,
        "SORTB",
        "日月星天甲川山雨風",  # same chars as A, different positions
        title="B-bundle",
        extra_metadata={"composition_period": "1000"},
    )
    config = ServeConfig(corpus_root=tmp_path, index_path=tmp_path / "_corpus.bkkx")
    app = create_app(config)
    return TestClient(app)


def test_search_default_sort_is_match(sort_client):
    r = sort_client.get("/search", params={"q": "甲"})
    assert r.status_code == 200
    assert r.json()["sort"] == "match"


def test_search_sort_textid(sort_client):
    r = sort_client.get("/search", params={"q": "甲", "sort": "textid"})
    body = r.json()
    assert body["sort"] == "textid"
    textids = [h["textid"] for h in body["hits"]]
    assert textids == sorted(textids)
    assert textids == ["SORTA", "SORTB", "SORTC"]


def test_search_sort_match(sort_client):
    """Sort by (match + right) — 甲 + first right-context char ascending."""
    r = sort_client.get("/search", params={"q": "甲", "sort": "match"})
    body = r.json()
    assert body["sort"] == "match"
    # match is identical for all three (甲); tiebreak on right context.
    # Right contexts start with: A=山, B=川, C=麥
    # NFC sort order of those leading chars determines ordering.
    rights = [h["right"][:1] for h in body["hits"]]
    assert rights == sorted(rights)


def test_search_sort_reverse_prematch(sort_client):
    """Sort by reversed left context — last char before match ascending."""
    r = sort_client.get(
        "/search", params={"q": "甲", "sort": "reverse_prematch"}
    )
    body = r.json()
    assert body["sort"] == "reverse_prematch"
    # Left contexts end with: A=星, B=天, C=稷 -> reversed first chars 星/天/稷
    last_left = [h["left"][-1:] for h in body["hits"]]
    assert last_left == sorted(last_left)


def test_search_sort_date(sort_client):
    """前500 (BCE -500) < 1000 < 1900."""
    r = sort_client.get("/search", params={"q": "甲", "sort": "date"})
    body = r.json()
    assert body["sort"] == "date"
    textids = [h["textid"] for h in body["hits"]]
    assert textids == ["SORTA", "SORTB", "SORTC"]


def test_search_sort_date_missing_period_falls_to_end(tmp_path: Path):
    write_bundle(tmp_path, "DATED", "甲乙", title="d", extra_metadata={"composition_period": "1500"})
    write_bundle(tmp_path, "UNDATED", "丙甲丁", title="u")  # no composition_period
    config = ServeConfig(corpus_root=tmp_path, index_path=tmp_path / "_corpus.bkkx")
    client = TestClient(create_app(config))
    r = client.get("/search", params={"q": "甲", "sort": "date"})
    body = r.json()
    assert [h["textid"] for h in body["hits"]] == ["DATED", "UNDATED"]


def test_search_sort_closeness(sort_client):
    """A and B share KWIC chars (天日月星 / 山川風雨); C shares none.

    Greedy chain should place A and B adjacent, then C at the end.
    """
    r = sort_client.get("/search", params={"q": "甲", "sort": "closeness"})
    body = r.json()
    assert body["sort"] == "closeness"
    textids = [h["textid"] for h in body["hits"]]
    # The first two slots are A and B (in either order); C is last.
    assert set(textids[:2]) == {"SORTA", "SORTB"}
    assert textids[2] == "SORTC"


def test_search_sort_invalid_returns_422(sort_client):
    r = sort_client.get("/search", params={"q": "甲", "sort": "lolnope"})
    assert r.status_code == 422
