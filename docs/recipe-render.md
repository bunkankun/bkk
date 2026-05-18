# Recipe rendering

`bkk recipe render` resolves a recipe, extracts named datasets from the
resolved content, and renders the result through a sandboxed Jinja template.
The first supported output target is Markdown, and the first dataset extractor
collects markers from a named pin.

This is separate from `bkk export`: export turns bundles into external text
formats such as KRP or TEI, while recipe rendering is for inspection reports,
reading lists, formatted excerpts, and other derived documents.

## Command

```bash
bkk recipe render module/recipes/voice-inspection.yaml \
  --corpus /path/to/bkk/corpus \
  --out /tmp/voices.md
```

If `--out` is omitted, the rendered document is printed to stdout. If
`--corpus` is omitted, the command reads `recipe.corpus` or `global.corpus`
from `.bkkrc`.

## Render recipe shape

```yaml
kind: bkk.recipe/v1
pins:
  - name: text
    role: base
    textid: KR3a0001
    selection:
      juan: 1

datasets:
  voices:
    from: text
    collect: markers
    where:
      type: voice
    include_text: true
    context: 12

render:
  format: markdown
  template: |
    # Voices in {{ pins.text.label }}

    {% for v in datasets.voices %}
    ## {{ v.name }} {{ v.id }}

    - location: {{ v.textid }} {{ v.juan_seq }}/{{ v.bucket }} @{{ v.offset }}+{{ v.length }}
    - responds to: {{ v.responds_to or "none" }}

    `{{ v.left }}[{{ v.text }}]{{ v.right }}`

    {% endfor %}
```

Composition-only recipes remain valid for fulfilment. Render recipes add three
conventions:

- Pins that are referenced by datasets or templates need a local `name`.
- `datasets` declares extracted structured data, keyed by dataset name.
- `render.template` is a Jinja-style template that receives only the controlled
  render context.

## Pin selection

Each pin's `selection:` block narrows the bundle to a slice. The same forms are
used by composition-only recipes for `bkk recipes:fulfil` — pin selection is
not render-specific. When `selection:` is omitted, the whole bundle is included
as one slice per juan.

| Form         | Keys                                | Result                                       |
| ------------ | ----------------------------------- | -------------------------------------------- |
| Whole bundle | *(omit `selection:`)*               | One slice per juan                           |
| Whole juan   | `juan`                              | Body bucket of that juan                     |
| Bucket       | `juan`, `bucket: front\|body\|back` | Whole named bucket                           |
| Marker range | `juan`, `from`, `to`                | Text between two marker IDs (auto-swapped)   |
| Offset slice | `juan`, `offset`, `length`          | Codepoint slice, 0-based                     |
| TOC entry    | `toc`                               | Span declared in the manifest's `table_of_contents` |

The forms are mutually exclusive: `offset/length` does not combine with
`from/to`, and `toc:` stands alone (no `juan:`). The default bucket is `body`.
Selection errors surface as `missing_juan`, `bad_slice_range`,
`marker_not_found`, `bad_bucket`, or `bad_toc_id`.

A pin's `role:` is an open vocabulary — common values are `base`,
`translation`, `commentary`, `overlay`, and `glossary` — and is interpreted by
the consumer.

## Multiple pins

A recipe can declare any number of pins, each with its own selection, and bind
a dataset to each. The example below pins a base text and a commentary slice
of the same textid, collects voice markers from each, and renders both into a
single Markdown report.

```yaml
kind: bkk.recipe/v1
pins:
  - name: root
    role: base
    textid: KR3a0001
    selection:
      juan: 1
  - name: comm
    role: commentary
    textid: KR3a0001
    selection:
      juan: 1
      from: "002-3a"
      to: "002-7b"

datasets:
  root_voices:
    from: root
    collect: markers
    where:
      type: voice
    include_text: true
    context: 8
  comm_voices:
    from: comm
    collect: markers
    where:
      type: voice
    include_text: true
    context: 8

render:
  format: markdown
  template: |
    # {{ pins.root.label }} — voices report

    ## Root ({{ pins.root.textid }})
    {% for v in datasets.root_voices %}
    - {{ v.id }} @{{ v.offset }}+{{ v.length }} `{{ v.left }}[{{ v.text }}]{{ v.right }}`
    {% endfor %}

    ## Commentary ({{ pins.comm.textid }}, {{ pins.comm.selection.from }}–{{ pins.comm.selection.to }})
    {% for v in datasets.comm_voices %}
    - {{ v.id }} @{{ v.offset }}+{{ v.length }} `{{ v.left }}[{{ v.text }}]{{ v.right }}`
    {% endfor %}
```

Rendered output (shape — values depend on the corpus):

```markdown
# 周易 — voices report

## Root (KR3a0001)
- v_001 @42+6 `…前文[彖曰乾元亨利]後文…`
- v_002 @93+4 `…前文[文言曰]後文…`

## Commentary (KR3a0001, 002-3a–002-7b)
- v_017 @588+5 `…前文[孔氏曰也]後文…`
```

Notes:

- Datasets bind 1:1 to named pins through `from:`. A pin not named in any
  dataset still appears in `pins.<name>` for template use.
- Pin `name:` values are local to the recipe; only named pins are addressable
  from datasets and templates.
- Multiple pins can share a `textid` — selection narrows them to different
  slices, as in the example above.

## Marker datasets

The v1 extractor supports:

```yaml
datasets:
  voices:
    from: text
    collect: markers
    where:
      type: voice
    include_text: true
    context: 12
```

`from` names a pin. The pin is resolved, verified, and sliced according to its
selection before markers are collected.

Each marker item includes the marker fields plus:

- `textid`
- `juan_seq`
- `bucket`
- `offset`, rebased to the original bucket offset
- `relative_offset`, within the selected slice
- `length`
- `end`
- `responds_to`, normalized from `responds-to`
- `text`, `left`, `right` when `include_text: true`

For `voice` markers this gives enough information to inspect root/commentary
boundaries directly in Markdown.

## Template context

Templates receive:

- `pins`: named pin metadata. Each entry exposes:
  - `name` — the local pin name from the recipe
  - `role` — the declared role (`base`, `commentary`, …)
  - `label` — manifest title if available, otherwise `textid`, otherwise `name`
  - `textid`
  - `canonical_identifier`
  - `selection` — the resolved selection dict (e.g. `pins.comm.selection.from`)
  - `verified` — whether the manifest hash matched
  - `manifest_hash`
  - `error` — non-null when the pin failed to resolve
- `datasets`: extracted datasets keyed by dataset name.
- `errors`: non-fatal fulfilment errors.
- `resolved_recipe`: the recipe after hashes and identifiers have been filled
  in by fulfilment.

Templates run in Jinja's sandboxed environment with strict undefined values.
They cannot access the filesystem, shell, Python imports, or host objects.

## Example

See [module/recipes/voice-inspection.yaml](../module/recipes/voice-inspection.yaml)
for a ready-to-edit voice inspection recipe.
