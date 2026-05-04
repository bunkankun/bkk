# CLI module for the BKK files


## Import

Here is where the import is handled. We will have input from different sources, with varying shapes.  

A `read` module will read them in to an abstract shape and a `write` module will produce the required files where they belong. 

### Invocation of the input procedure

`bkk import --format <format> ` 

The format will be `krp` for the Kanseki Repository format, and `tls` for the TLS / HXWD format. 

### Archival format

The archival format is mainly for text data of premodern Chinese. Texts used to be transmitted in scrolls, *juan* in Chinese; this remains a useful subdivision and is still in use today. In our format, each juan is a separate file. A **manifest** plus a **table of contents** and additional metadata pertain to the whole text and point into the juan files. All `text` fields, no matter their location or type, are accompanied by a `hash` field whose value audits the content.

A **juan** file has a `front`, a `body`, and a `back`; only the body must be non-empty, the others are optional and need not be present if empty. Additional metadata fields are available. The text elements of the body and back may be subdivided where appropriate. A typical front contains an opening line that locates the juan in a larger collection, the title of the text, the sequential number of the juan, and an attribution naming persons and roles with respect to the body. The back contains a closing line. The placement of prefaces, postfaces, colophons, and similar paratextual material is open: such material may go into the body or be separated out into front or back, at the discretion of the project applying the format.

The body has one text element that holds the canonical character content of the whole juan. Space characters, punctuation, line breaks, and similar content are not present in this stream — they are extracted into a **markers** object that follows the text element. A marker has at minimum a **type** and an **offset**; further fields are optional and typically include **id**, **content**, and additional structured information appropriate to the marker's type. The set of marker types is open; a small core vocabulary is defined separately.

One text can be represented in several editions, they can be made accessible through the 'master' edition of the text or a recipe can adress them directly. 

A sample to illustrate the format has been prepared in samples/KR6q0053

### Input sources


#### Kanseki Repository

Mandoku-view source format. The text lives in a git repository whose branches
are editions: each text-bearing branch (e.g. `WYG`) carries one
`KR<id>_NNN.txt` file per juan, the `master` branch carries a curated reading,
and a sibling `_data` branch carries `imglist/<text-id>_NNN.txt` mapping
`<juan>-<page>` ids to image filenames plus `imglist/imginfo.cfg` (base URLs).

Per-text knobs (branch → edition mapping, master witnesses, imglist source)
are pinned in a recipe file:

```
python -m bkk.importer --format krp --recipe recipes/<text-id>.yaml
```

Each documentary edition is written under `<out>/<text-id>/editions/<short>/`;
the master edition is written at the bundle root with a `PUA-map.yaml`
summarising every Kanripo `&KRnnnn;` private-use-area character that appears
in any edition (codepoint = `0x105000 + nnnn`). Where the master differs from
a witness, a `variant` marker is emitted on the master juan.

See [recipes/KR3a0013.yaml](recipes/KR3a0013.yaml) for the schema.

#### TLS / HXWD

XML format used in the TLS application.  In the application, texts are in subdirectories of `tls-texts/data/`, sources for the annotations in `tls-data/notes/swl` and `tls-data/notes/doc` 

See `input/tls` for the text files that will be used to produce the output, to be written to `output/`

#### CBETA

TBD

#### Plain text files

TBD

## Index

The indexer turns a bundle (or a whole corpus root) into a portable SQLite
artifact (`.bkkx`) that supports variant-aware substring search with KWIC
output. Queries match against the master text *and* against each per-edition
witness reading, so a character that appears only in a witness still finds
the master position.

The artifact is plain SQLite — the same file can be loaded by `sql.js` in
the browser to drive static-site search.

### Per-bundle build

```
python -m bkk.index build <bundle_dir> [--out PATH]
```

Walks `<bundle_dir>/<textid>_NNN.yaml` plus the manifest and writes
`<bundle_dir>/<textid>.bkkx` (or `--out PATH`).

### Corpus-level merge

```
python -m bkk.index merge <corpus_root> --out corpus.bkkx
                          [--prefix KR3a]   # restrict to one subgroup
                          [--rebuild]       # rebuild every per-bundle .bkkx
                          [--no-build]      # error if any per-bundle .bkkx is missing/stale
```

Walks `<corpus_root>/<textid>/<textid>.manifest.yaml`, builds any missing or
stale per-bundle `.bkkx` (mtime check against the manifest and juan files),
then unions every per-bundle index into one merged `.bkkx`. Primary keys are
shifted per source so they remain unique in the merged file. The merged
artifact carries a `bundle` provenance table (textid, editions, source path,
sha256 of the source `.bkkx`).

### Search

```
python -m bkk.index search <bkkx_path> <query> [--context N]
                                               [--witness LABEL]...
                                               [--textid ID]
```

Plain CJK substring; matching is variant-aware. `--witness` (repeatable)
restricts witness-side matches; master matches are always returned.
`--textid` scopes to one bundle inside a merged corpus index. Output is one
KWIC line per hit, with any variant readings overlaid; the variant that
contains the matched span is flagged with `*`.

Example (variant query against a merged corpus):

```
$ python -m bkk.index search corpus.bkkx 甞不盡 --context 8
KR1a0024:001/body@24307  [卷一]  via SBCK='甞不盡'
  …以決勢足以專然未「嘗不盡」天下之議上九視履…
  * variant @24307 len=1 '嘗' → SBCK='甞'
```

### Python API

```python
from bkk.index import Index, build_index, merge_bundles

build_index("path/to/KR1a0024")                       # writes KR1a0024.bkkx
merge_bundles("/path/to/corpus", "corpus.bkkx")       # builds + merges

with Index("corpus.bkkx") as ix:
    for hit in ix.search("甞不盡"):
        print(hit.textid, hit.master_offset, hit.matched_via)
```
