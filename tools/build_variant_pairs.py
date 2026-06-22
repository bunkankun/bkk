"""Merge appendix1 (通用规范汉字表) variants with Chinese Var-to-Rep into bkk-variant-pairs.tsv.

Rules:
  - appendix1 (GB2013): traditional col is canonical; regulated→traditional (when distinct),
    and each char in variant col [...] → traditional.
  - Chinese Var-to-Rep: direct variant→representative mappings.
  - On reverse-direction conflict ((A,B) vs (B,A)), the preferred source wins; note in remarks.
  - When the same var maps to multiple regs, fold into ONE row listing all targets.
  - Strip variation selectors entirely.
  - Output: var_cp, var_char, reg_cp, reg_char, remarks.

CLI: --prefer GB2013 (default) | Var-to-Rep
"""
import argparse
import unicodedata
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument('--prefer', choices=['GB2013', 'Var-to-Rep'], default='GB2013')
ap.add_argument('--out', default=None)
args = ap.parse_args()
PREFER = args.prefer
OTHER = 'Var-to-Rep' if PREFER == 'GB2013' else 'GB2013'

SURVEY = Path(__file__).parent / 'survey-out'
APPENDIX = SURVEY / 'appendix1_variants.tsv'
VAR2REP = SURVEY / 'Chinese Var-to-Rep_v1_0.tsv'
default_name = 'bkk-variant-pairs.tsv' if PREFER == 'GB2013' else 'bkk-variant-pairs-v2r.tsv'
OUT = SURVEY / (args.out or default_name)


def strip_vs(s: str) -> str:
    return ''.join(c for c in s if unicodedata.category(c) != 'Mn')


def cp(ch: str) -> str:
    return ','.join(f'U+{ord(c):04X}' for c in ch)


def split_variant_field(field: str) -> list[str]:
    """Split bracketed variant list; VS removal happens later so no need to keep them attached."""
    inner = field.strip('[]')
    return [c for c in inner if unicodedata.category(c) != 'Mn']


# --- Collect (var, reg, source) assertions ---
assertions = []

with APPENDIX.open(encoding='utf-8') as f:
    next(f)
    for line in f:
        parts = line.rstrip('\n').split('\t')
        if len(parts) < 4:
            parts += [''] * (4 - len(parts))
        _id, reg, trad, var = parts
        reg, trad = strip_vs(reg), strip_vs(trad)
        # Keep self-maps (reg==trad): they're meaningful when the same regulated char
        # also appears in another row with a different traditional, producing a
        # multi-target including the char itself. Pruned later if standalone.
        if reg and trad:
            assertions.append((reg, trad, 'GB2013'))
        for v in split_variant_field(var):
            if v and v != trad:
                assertions.append((v, trad, 'GB2013'))

with VAR2REP.open(encoding='utf-8') as f:
    next(f)
    for line in f:
        parts = line.rstrip('\n').split('\t')
        if len(parts) < 4:
            continue
        vch, rch = strip_vs(parts[1]), strip_vs(parts[3])
        if vch and rch and vch != rch:
            assertions.append((vch, rch, 'Var-to-Rep'))

# --- Deduplicate (var,reg) pairs, merging sources ---
pair_sources = {}  # (var,reg) -> set of sources
for v, r, src in assertions:
    pair_sources.setdefault((v, r), set()).add(src)

# --- Resolve reverse-direction conflicts: PREFER source wins ---
dropped = {}  # dropped pair -> kept pair (for remark on the kept side)
for (a, b) in list(pair_sources):
    if a == b:
        continue
    if (b, a) not in pair_sources or (a, b) in dropped or (b, a) in dropped:
        continue
    src_ab = pair_sources[(a, b)]
    src_ba = pair_sources[(b, a)]
    if PREFER in src_ab and PREFER not in src_ba:
        kept, drop = (a, b), (b, a)
    elif PREFER in src_ba and PREFER not in src_ab:
        kept, drop = (b, a), (a, b)
    else:
        kept, drop = (a, b), (b, a)
    dropped[drop] = kept

for d in dropped:
    pair_sources.pop(d, None)

# --- Group by var: fold multi-targets into one row ---
by_var = {}  # var -> list of regs (insertion-ordered, GB2013 first)
order = []
# Iterate insertion-preserving by first encounter in assertions list.
for v, r, _ in assertions:
    if (v, r) not in pair_sources:
        continue
    if v not in by_var:
        by_var[v] = []
        order.append(v)
    if r not in by_var[v]:
        by_var[v].append(r)

# --- Prune standalone self-maps: keep only when paired with other targets ---
order = [v for v in order if by_var[v] != [v]]
by_var = {v: by_var[v] for v in order}

# --- Cross-column overlap (chain cases like A→B, B→C) ---
all_vars = set(by_var)
all_regs = {r for regs in by_var.values() for r in regs}
overlap = all_vars & all_regs

# --- Emit ---
with OUT.open('w', encoding='utf-8') as out:
    out.write('var_cp\tvar_char\treg_cp\treg_char\tremarks\n')
    for v in order:
        regs = by_var[v]
        reg_chars_str = ''.join(regs)
        reg_cp_str = ','.join(cp(r) for r in regs)
        remarks = []
        srcs = sorted({s for r in regs for s in pair_sources[(v, r)]})
        for r in regs:
            if (r, v) in dropped:
                remarks.append(f'reverse-dropped:{r}→{v} ({PREFER} preferred over {OTHER})')
        if v in overlap:
            remarks.append('var-also-canonical-elsewhere')
        chained_regs = [r for r in regs if r in all_vars]
        if chained_regs:
            remarks.append('reg-also-variant-elsewhere:' + ''.join(chained_regs))
        if len(regs) > 1:
            remarks.append(f'multi-target ({len(regs)})')
        remarks.append('src=' + ','.join(srcs))
        out.write(f'{cp(v)}\t{v}\t{reg_cp_str}\t{reg_chars_str}\t{"; ".join(remarks)}\n')

# --- Stats ---
multi = sum(1 for regs in by_var.values() if len(regs) > 1)
print(f'wrote {len(order)} rows -> {OUT}')
print(f'  reverse-conflict pairs dropped: {len(dropped)}')
print(f'  multi-target rows: {multi}')
print(f'  chars still in both var & reg: {len(overlap)}')
