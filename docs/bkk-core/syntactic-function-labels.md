# Syntactic-Function Label Parser

This document describes the parser and linter for bkk-core
`syntactic-function` labels. The implementation lives in
`module/bkk/core/syntactic_functions.py` and is exposed through:

```bash
PYTHONPATH=module python3 -m bkk core lint-syntactic-functions /path/to/bkk-core
```

The command accepts either a v2 bkk-core root or the
`records/syntactic-functions` directory directly.

## Purpose

Syntactic-function labels are compact TLS-style codes such as:

```text
vt{NEG}+Vtt[0](oN1.)+N2
npropostNab.adV>Nab
NP[post-npro2.][post=npro1:]adN
```

They are systematic enough to parse, but the corpus also contains historical
irregularities, typos, fullwidth punctuation, mixed role spellings, and a few
malformed brackets. The parser is therefore intentionally tolerant. It does
not try to declare the complete linguistic system final; it turns each label
into tokens and a syntax tree, then reports diagnostics that can gradually
become stricter validation rules.

## Pipeline

The linter runs each label through four stages.

### 1. Normalize

`normalize_label()` performs Unicode NFC normalization and maps common
fullwidth punctuation to ASCII:

```text
＋ -> +
－ -> -
（ -> (
） -> )
```

Normalization emits warnings instead of silently changing the record. It also
warns about:

- whitespace
- non-ASCII characters
- likely confusables, for example Cyrillic letters that look Latin

### 2. Lex

`lex_label()` splits the normalized string into tokens.

Token kinds:

| Kind | Examples |
|---|---|
| `atom` | `vt`, `vtt`, `npro`, `postN`, `adV`, `NP`, `VP`, `prep` |
| `number` | `0`, `1`, `2` |
| `connector` | `.`, `+`, `-`, `:`, `=`, `>`, `|`, `/`, `&`, `!` |
| `open` / `close` | `(`, `)`, `[`, `]`, `{`, `}` |
| `ellipsis` | `...` |
| `unknown` | any component not yet in the morpheme list |

The lexer uses longest-match recognition against `KNOWN_MORPHEMES`. This is
what allows a compact label like:

```text
npropostNab.adV>Nab
```

to split into meaningful components rather than becoming one opaque string.

Unknown components are warnings, not errors. Some are true typos; others are
rare morphemes that should be added to the lexicon after review.

### 3. Parse

`_Parser` builds a loose syntax tree. It understands:

| Syntax | Meaning in the AST |
|---|---|
| `{...}` | role annotation |
| `(...)` | optional material |
| `[...]` | elided or implicit material |
| connectors | relation markers between parts |
| numeric suffixes | index annotations such as `N1`, `V2` |

The parser allows connector clusters that occur in the data, such as `.+` or
`(+N.)`. It reserves errors for structural problems that are very likely to be
wrong:

- unexpected closing bracket
- mismatched bracket, for example `{PIVOT]`
- missing closing bracket
- dangling final connector, for example `...+Vt.`

### 4. Lint Records

`lint_syntactic_function_records()` walks all YAML records, parses each
`code`, and also checks record-level issues:

- missing `code`
- `code` differing from `labels.display`
- duplicate `code` values

The report keeps errors and warnings separate. The CLI exits with status `1`
when errors are present. With `--strict`, warnings also cause a failing exit.

## CLI Usage

Show the first 80 diagnostics:

```bash
PYTHONPATH=module python3 -m bkk core lint-syntactic-functions /home/chris/00scratch/codex/bkk-core
```

Show all diagnostics:

```bash
PYTHONPATH=module python3 -m bkk core lint-syntactic-functions /home/chris/00scratch/codex/bkk-core --limit 0
```

Fail on warnings as well as errors:

```bash
PYTHONPATH=module python3 -m bkk core lint-syntactic-functions /home/chris/00scratch/codex/bkk-core --strict
```

Typical summary:

```text
checked 2091 syntactic-function record(s), 2063 distinct label(s): 8 error(s), 316 warning(s)
```

## Diagnostic Classes

Current structural errors:

| Code | Meaning |
|---|---|
| `unexpected-close` | closing bracket appears with no matching opener |
| `mismatched-bracket` | opener and closer do not match |
| `unclosed-bracket` | input ended before the expected closer |
| `dangling-connector` | label ends with a connector |
| `missing-code` | record has no usable `code` |

Current warnings:

| Code | Meaning |
|---|---|
| `fullwidth-punctuation` | label uses fullwidth punctuation that normalizes to ASCII |
| `whitespace` | label contains whitespace |
| `non-ascii` | label contains non-ASCII characters |
| `unicode-confusable` | label contains a likely confusable Unicode character |
| `underscore` | label uses `_`, usually better written as an attached index |
| `unknown-token` | component is not in `KNOWN_MORPHEMES` |
| `role-whitespace` | role annotation contains spaces |
| `role-alias` | role has a known canonical spelling |
| `display-mismatch` | `code` and `labels.display` differ |
| `duplicate-code` | another syntactic-function record has the same code |

## Examples

Malformed bracket:

```text
vttoN1{PIVOT].+N2{PRED}
```

Diagnostic:

```text
error: mismatched-bracket: expected closing bracket '}', got ']'
```

Fullwidth punctuation:

```text
vt＋V(0)
```

Normalized form:

```text
vt+V(0)
```

Diagnostic:

```text
warning: fullwidth-punctuation: replace fullwidth punctuation '＋' with ASCII
```

Role alias:

```text
npro.post-V{PASSIVE}
```

Diagnostic:

```text
warning: role-alias: role 'PASSIVE' is usually written as 'PASS'
```

## Extending The Validator

The parser is designed to grow in stages.

Good next steps:

- Add confirmed rare morphemes to `KNOWN_MORPHEMES`.
- Add role spellings to `ROLE_ALIASES` when a canonical form is clear.
- Promote selected warnings to errors once the corpus has been cleaned.
- Add semantic checks after parsing, for example consistency of `N1`/`N2`
  numbering or plausibility of `post`, `adV`, `adN`, and `adS` attachments.

When adding rules, prefer a warning first. The labels are historical data, and
the linter is most useful when it can report many records without stopping at
the first unfamiliar pattern.
