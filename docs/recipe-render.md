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

- `pins`: named pin metadata, including `label`, `textid`, verification state,
  manifest hash, canonical identifier, and selection.
- `datasets`: extracted datasets keyed by dataset name.
- `errors`: non-fatal fulfilment errors.
- `resolved_recipe`: the recipe after hashes and identifiers have been filled
  in by fulfilment.

Templates run in Jinja's sandboxed environment with strict undefined values.
They cannot access the filesystem, shell, Python imports, or host objects.

## Example

See [module/recipes/voice-inspection.yaml](../module/recipes/voice-inspection.yaml)
for a ready-to-edit voice inspection recipe.
