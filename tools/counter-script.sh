cd /home/chris/00scratch/bkk-work/devcorpus && python3 -c "
import re, sys
from collections import Counter
from pathlib import Path

pb_re = re.compile(r\"type: page-break.*?id: (\S+?)[,}]\")
edition_re = re.compile(r\"^([A-Za-z0-9]+)_([^_]+)_\")

counts = Counter()
files_with_pb = 0
files_total = 0
no_image = 0
has_image = 0
img_re = re.compile(r\"image: (\S+?)[,}]\")

for p in Path('.').rglob('*.yaml'):
    if p.name.endswith('.manifest.yaml'):
        continue
    files_total += 1
    text = p.read_text(encoding='utf-8', errors='replace')
    found = False
    for line in text.splitlines():
        if 'type: page-break' not in line:
            continue
        found = True
        m_id = pb_re.search(line)
        if not m_id:
            continue
        m_ed = edition_re.match(m_id.group(1))
        if m_ed:
            counts[m_ed.group(2)] += 1
        else:
            counts['<unparsed>'] += 1
        if 'image:' in line:
            has_image += 1
        else:
            no_image += 1
    if found:
        files_with_pb += 1

print(f'juan files: {files_total}, with page-breaks: {files_with_pb}')
print(f'page-break markers total: {sum(counts.values())}')
print(f'  with image:    {has_image}')
print(f'  without image: {no_image}')
print()
print('Edition shortkey: count')
for ed, n in counts.most_common():
    print(f'  {ed!r}: {n}')
"
