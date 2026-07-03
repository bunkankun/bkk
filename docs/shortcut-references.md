# Short cut notation

Currently, a reference in recipes is quite elaborate:

```yaml
- role: base
  textid: KR1h0004
  selection:
    juan: 1
    bucket: body
    offset: 0
    length: 86
```
I would like to shorten this in some cases for easier usage. 

```yaml
- role: base
  ref: 1h4/1/@0+86
```

This references the same span under these conditions:

- `KR` is the default prefix and may be omitted.
- In the serial number of texts within a section (`KR1h` in this case), leading zeroes may be omitted.
- `body` is the default bucket and may be omitted.
- `+86` means length 86, matching the explicit `length: 86` field.
- Omitting `@0+86` selects the whole default bucket, so `1h4/1` selects juan 1 body.
- The slash before `@` is required when the bucket is omitted.

## some questions and observations:
- This makes sense as recipe authoring shorthand if the recipe loader normalizes it into explicit `textid` plus `selection`.
- A reference to the `front` or `back` bucket always has to be explicit, so `1h4/1/front@0+86` is clearly distinct from `1h4/1/@0+86`.
- Use `+length`, not `-end`. `@10-86` is ambiguous because it can mean either length 86 or end offset 86.

## Parallel scan selectors

`bkk index parallel --text-id` accepts the same compact text/juan prefix:

```console
bkk index parallel --text-id 1h4/1
```

This is equivalent to `--text-id KR1h0004/1` and scans juan 1 against the
complete selected index. A bare canonical ID such as `KR1h0004` continues to
scan the whole bundle. Bucket and range suffixes are not accepted by this
command because its interactive scope is a complete text or complete juan.
