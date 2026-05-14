# BKK Index module

We want to build a module to index the files of the BKK project.  

The index will be used to index files in `/home/Shared/bkk/bkbooks` I put some examples there.  Target for the index is only the top level <textid>_NNN.yaml files, files in the `./editions` subdirectory are ignored for the index. 

What we want to achieve:

- queries to the index should be able to provide a KWIC view with a configurable context window to both sides of a match
- the text format allows for variants, encoded as (example from `KR1a0024_001.yaml`)
```
  - {type: variant, offset: 24253, length: 1, content: 為, SBCK: 爲}
  - {type: variant, offset: 24307, length: 1, content: 嘗, SBCK: 甞}
```

  The queries should be able to find strings that contain both 
  `專然未嘗不盡天下` and `專然未甞不盡天下` for the second variant above, assuming this is the location at offset 24307 with a bit of context to each side. Or put it differently: A query for '甞不盡' should find this location, although '甞' is not a character used in the established source text, but only visible through the variant.  In the display of the KWIC results, the search for both  '甞不盡' and  '甞嘗盡' should produce identical lines, each emitting both the established text and the variants seen at this positions, the latter have to be marked as variants.

- we would like to have a search procedure that could also be used also on static sites driven with JS from the browser,  if possible. 

## Voice-aware search

Each bucket carries `type: voice` range markers (see `bunkankun.md` §"Voices") that name slices of text as `root`, `commentary`, etc. These are materialised in the index as a `voice_range` table; every hit is tagged with the **innermost** voice range that fully contains its span (`Hit.voice`) plus the outermost→innermost chain of containing names (`Hit.voice_stack`).

- A hit that does not lie entirely within any single range is tagged `mixed`; a hit in unmarked text (e.g. front matter) is `none`.
- The filter `--voice NAME` (CLI) / `?voice=NAME` (HTTP) keeps only hits that have **some** fully-containing range with that name — so a hit nested inside a sound-gloss inside a commentary qualifies under either `--voice commentary` or `--voice sound-gloss`. The flag is repeatable; omitting it returns all hits.
- The vocabulary is open: any name emitted by the importer (`root`, `commentary`, future `sound-gloss`, named commentators, …) shows up in `Index.available_voices()` and is a valid filter value without code changes.
- Witness hits inherit the master span's voice via the segment map, so variant-mediated hits classify the same way as the underlying master reading.

