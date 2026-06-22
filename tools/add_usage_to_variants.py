"""Augment a bkk-variant-pairs TSV with corpus usage counts.

Adds columns:
  var_count       — corpus count for var_char
  reg_count       — sum of corpus counts for all reg_chars on this row
  reg_breakdown   — per-reg counts when multi-target (e.g. "乾:1234|幹:567"); blank otherwise
  reg_share       — reg_count / (var_count + reg_count), 4 decimals; blank if both zero
"""
import argparse
import re

ap = argparse.ArgumentParser()
ap.add_argument('--in', dest='infile', required=True)
ap.add_argument('--out', dest='outfile', required=True)
ap.add_argument('--counts', default='tools/survey-out/codepoints.tsv')
ap.add_argument('--cutoff', default=None,
                help='Filter rows by reg_share, e.g. "gt0.7" or "lt0.7". '
                     'Rows with no usage data (both counts zero) are excluded when set, '
                     'unless --keep-zero is given.')
ap.add_argument('--keep-zero', action='store_true',
                help='When --cutoff is set, also keep rows with no corpus usage (blank reg_share).')
args = ap.parse_args()

cutoff_op = cutoff_val = None
if args.cutoff:
    m = re.match(r'^(gt|lt|ge|le)([0-9.]+)$', args.cutoff)
    if not m:
        ap.error('--cutoff must look like gt0.7 / lt0.7 / ge0.5 / le0.9')
    cutoff_op = m.group(1)
    cutoff_val = float(m.group(2))

def keep(share_str):
    if cutoff_op is None:
        return True
    if share_str == '':
        return args.keep_zero
    s = float(share_str)
    return {'gt': s > cutoff_val, 'lt': s < cutoff_val,
            'ge': s >= cutoff_val, 'le': s <= cutoff_val}[cutoff_op]

counts = {}
with open(args.counts, encoding='utf-8') as f:
    next(f)
    for line in f:
        parts = line.rstrip('\n').split('\t')
        if len(parts) < 3:
            continue
        ch, n = parts[1], parts[2]
        try:
            counts[ch] = int(n)
        except ValueError:
            pass

total_var = total_reg = 0
n_rows = n_kept = n_filtered = 0
n_reg_wins = n_var_wins = n_tie = 0

with open(args.infile, encoding='utf-8') as f, open(args.outfile, 'w', encoding='utf-8') as out:
    header = f.readline().rstrip('\n').split('\t')
    out.write('\t'.join(header + ['var_count', 'reg_count', 'reg_breakdown', 'reg_share']) + '\n')
    for line in f:
        parts = line.rstrip('\n').split('\t')
        var_char = parts[1]
        reg_chars = parts[3]
        var_c = counts.get(var_char, 0)
        reg_per = [(c, counts.get(c, 0)) for c in reg_chars]
        reg_c = sum(n for _, n in reg_per)
        breakdown = ''
        if len(reg_per) > 1:
            breakdown = '|'.join(f'{c}:{n}' for c, n in reg_per)
        denom = var_c + reg_c
        share = f'{reg_c / denom:.4f}' if denom else ''
        n_rows += 1
        if not keep(share):
            n_filtered += 1
            continue
        out.write('\t'.join(parts + [str(var_c), str(reg_c), breakdown, share]) + '\n')
        n_kept += 1
        total_var += var_c
        total_reg += reg_c
        if reg_c > var_c:
            n_reg_wins += 1
        elif var_c > reg_c:
            n_var_wins += 1
        else:
            n_tie += 1

denom = total_var + total_reg
print(f'rows: {n_rows} (kept {n_kept}, filtered {n_filtered})')
print(f'  total var occurrences in corpus: {total_var:,}')
print(f'  total reg occurrences in corpus: {total_reg:,}')
print(f'  overall reg share: {total_reg / denom:.4f}' if denom else '  no data')
print(f'  rows where reg > var: {n_reg_wins}')
print(f'  rows where var > reg: {n_var_wins}')
print(f'  rows tied (incl. both-zero): {n_tie}')
