# DTS API

`bkk serve` exposes a first DTS-compatible read API under `/api/dts`.
It is intended for collection discovery, CTF-backed table-of-contents
navigation, and text or fragment retrieval.

The implementation follows the DTS 1.0 endpoint split:

- `GET /api/dts`
- `GET /api/dts/collection`
- `GET /api/dts/navigation`
- `GET /api/dts/document`

This is a Level 0 implementation. It supports collection browsing,
single-resource navigation with `ref` and `down`, and document retrieval for
whole texts or one CTF ref. DTS `start`/`end` range queries and named
alternate `tree` values are not implemented yet.

## Configuration

The DTS collection endpoint requires the existing catalog index:

```bash
bkk index catalog
```

By default `bkk serve` reads the catalog index from `<corpus>/_catalog.bkkc`,
or from `--catalog`, `BKK_CATALOG_PATH`, or `serve.catalog`.

Navigation and fragment retrieval require CTF files. Configure the CTF root
with one of:

```bash
bkk serve --ctf-root /home/Shared/bkk/ctf
```

```bash
BKK_CTF_ROOT=/home/Shared/bkk/ctf bkk serve
```

or in `.bkkrc`:

```yaml
global:
  ctf_root: /home/Shared/bkk/ctf
```

`serve.ctf_root` is also accepted. CLI flags override environment variables,
which override `.bkkrc`.

## Collections

The collection hierarchy is:

```text
BKK
└── KR1..KR6 top-level collections
    └── catalog sections such as KR1h or KR6a
        └── text resources such as KR6a0001
```

Top-level `KR1` through `KR6` labels come from
`module/bkk/data/kr_categories.yaml`. Section and text metadata comes from
the catalog index built from `catalog/frontmatter.csv`.

Examples:

```bash
curl 'http://127.0.0.1:8000/api/dts/collection'
curl 'http://127.0.0.1:8000/api/dts/collection?id=KR6'
curl 'http://127.0.0.1:8000/api/dts/collection?id=KR6a'
curl 'http://127.0.0.1:8000/api/dts/collection?id=KR6a0001'
curl 'http://127.0.0.1:8000/api/dts/collection?id=KR6a0001&nav=parents'
```

Resource IDs are BKK text IDs, for example `KR6a0001`. Canonical BKK
identifiers are included as extension metadata when available.

Collection and resource responses are JSON-LD with media type
`application/ld+json`.

## Navigation

Navigation reads CTF files below:

```text
<ctf-root>/<section>/<text-id>.ctf.tsv
<ctf-root>/<section>/<text-id>_<seq:03d>.ctf.yaml
```

Whole-text TSV is preferred when present. If no TSV exists, the server merges
per-juan YAML CTF files in filename order.

Examples:

```bash
curl 'http://127.0.0.1:8000/api/dts/navigation?resource=KR6a0001'
curl 'http://127.0.0.1:8000/api/dts/navigation?resource=KR6a0001&down=-1'
curl 'http://127.0.0.1:8000/api/dts/navigation?resource=KR6a0001&ref=KR6a0001/5/1/@14+9'
```

The `member` array contains DTS `CitableUnit` objects:

- `identifier` is the CTF node ID.
- `parent` is the CTF `parent_id`, or `null` when the parent is the text ID.
- `citeType` is `juan`, `label`, or `fragment`.
- `extensions.bkk:start`, `extensions.bkk:end`, and `extensions.bkk:juan`
  are included when the CTF row has enough span data.

`down` defaults to `1`. Use `down=-1` to return all descendants. Pagination is
fixed at 100 members per page.

## Documents

The document endpoint returns a whole resource or one CTF fragment:

```bash
curl 'http://127.0.0.1:8000/api/dts/document?resource=KR6a0001'
curl 'http://127.0.0.1:8000/api/dts/document?resource=KR6a0001&ref=KR6a0001/5/1/@14+9'
curl 'http://127.0.0.1:8000/api/dts/document?resource=KR6a0001&ref=KR6a0001/5/1/@14+9&mediaType=text/plain'
```

Default output is minimal TEI XML:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <text>
    <body>
      <dts:wrapper xmlns:dts="https://w3id.org/api/dts#" ref="...">
        ...
      </dts:wrapper>
    </body>
  </text>
</TEI>
```

Use `mediaType=text/plain` for raw text. Supported media types are:

- `application/tei+xml`
- `text/plain`

Fragment lookup uses the CTF span and then slices the master body text from
the corresponding BKK juan YAML. Whole-resource retrieval concatenates body
text from all manifest parts in manifest order.

## Current Limitations

- `start`/`end` range navigation and document requests return `400`.
- Alternate citation trees via `tree=` return `400`.
- Private user texts are not exposed through DTS unless they are later given
  catalog rows and CTF files.
- CTF files are read directly from disk on request; there is no separate DTS
  index yet.
