"""SQLite schema for the ``.bkkx`` index artifact.

One file per bundle, or one merged file across many bundles. Tables:

- ``meta``       — schema version, textid (per-bundle only), edition list
- ``bundle``     — one row per bundle in the file (merged corpus indices)
- ``juan``       — one row per juan
- ``bucket``     — front/body/back text per juan (master/established reading)
- ``witness``    — derived per-witness text plus segment map back to master
- ``variant``    — flattened variant entries for KWIC overlay rendering
- ``toc``        — manifest TOC entries (for KWIC chapter labels)
- ``trigram``    — character-trigram inverted index over master + witness text

The schema is shaped so the same file can be queried by ``sql.js``/``wa-sqlite``
in a browser without translation.
"""

from __future__ import annotations

SCHEMA_VERSION = 2

TABLES_DDL = """
CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE bundle (
  textid      TEXT PRIMARY KEY,
  editions    TEXT NOT NULL,        -- JSON list of edition shorts
  source_path TEXT NOT NULL,
  source_hash TEXT NOT NULL          -- sha256 of the source .bkkx
);

CREATE TABLE juan (
  juan_id INTEGER PRIMARY KEY,
  textid  TEXT    NOT NULL,
  seq     INTEGER NOT NULL,
  hash    TEXT
);

CREATE TABLE bucket (
  bucket_id INTEGER PRIMARY KEY,
  juan_id   INTEGER NOT NULL REFERENCES juan(juan_id),
  kind      TEXT    NOT NULL,        -- 'front' | 'body' | 'back'
  text      TEXT    NOT NULL
);

CREATE TABLE witness (
  witness_id INTEGER PRIMARY KEY,
  bucket_id  INTEGER NOT NULL REFERENCES bucket(bucket_id),
  label      TEXT    NOT NULL,
  text       TEXT    NOT NULL,
  segments   BLOB    NOT NULL        -- JSON: [[w_start, w_end, m_start, m_end, is_variant], ...]
);

CREATE TABLE variant (
  variant_id    INTEGER PRIMARY KEY,
  bucket_id     INTEGER NOT NULL REFERENCES bucket(bucket_id),
  master_offset INTEGER NOT NULL,
  length        INTEGER NOT NULL,
  content       TEXT    NOT NULL,
  witness       TEXT    NOT NULL,
  witness_form  TEXT    NOT NULL
);

CREATE TABLE toc (
  textid     TEXT    NOT NULL,
  juan_seq   INTEGER NOT NULL,
  bucket     TEXT    NOT NULL,
  span_start INTEGER NOT NULL,
  span_end   INTEGER NOT NULL,
  label      TEXT    NOT NULL,
  marker_id  TEXT    NOT NULL
);

CREATE TABLE trigram (
  gram        TEXT    NOT NULL,
  source_kind TEXT    NOT NULL,      -- 'bucket' | 'witness'
  source_id   INTEGER NOT NULL,
  position    INTEGER NOT NULL
);
"""

# Indices are split out so a bulk-insert path (the merger) can drop them
# before the loop and recreate them once at the end — orders of magnitude
# faster than maintaining a trigram b-tree row by row.
INDICES_DDL = """
CREATE INDEX idx_variant_overlay ON variant(bucket_id, master_offset);
CREATE INDEX idx_toc_lookup ON toc(textid, juan_seq, bucket, span_start);
CREATE INDEX idx_trigram_gram ON trigram(gram);
"""

DDL = TABLES_DDL + INDICES_DDL


def drop_heavy_indices(conn) -> None:
    """Drop the indices that get rebuilt at the end of a bulk-merge run."""
    for name in ("idx_trigram_gram", "idx_toc_lookup", "idx_variant_overlay"):
        conn.execute(f"DROP INDEX IF EXISTS {name}")


def create_heavy_indices(conn) -> None:
    """Recreate the indices dropped by :func:`drop_heavy_indices`."""
    conn.executescript(INDICES_DDL)
