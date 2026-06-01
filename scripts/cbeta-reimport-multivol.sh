#!/usr/bin/env bash
# Re-import all multivolume CBETA texts so they get the correct primary
# identifier in the rebuilt master manifest.
#
# Usage: cbeta-reimport-multivol.sh <cbeta-xml-root> [<out-root>]
#
#   cbeta-xml-root  Path to the CBETA XML tree (e.g. /home/chris/src/xml-p5)
#   out-root        Output corpus root; defaults to the value in .bkkrc
#                   (import.out).  Pass explicitly to override.
#
# The script deletes and re-imports each bundle so the fixed manifest logic
# produces the correct cbeta identifier.  Run from the project root.

set -euo pipefail

IN_ROOT="${1:-}"
if [[ -z "$IN_ROOT" ]]; then
    echo "usage: $0 <cbeta-xml-root> [<out-root>]" >&2
    exit 1
fi
OUT_ARGS=()
if [[ -n "${2:-}" ]]; then
    OUT_ARGS=(--out "$2")
fi

# All kr_ids whose CBETA source spans more than one XML file.
MULTIVOL=(
    KR6b0011
    KR6c0001
    KR6d0008
    KR6d0068
    KR6e0017
    KR6e0021
    KR6e0124
    KR6k0146
    KR6n0045
    KR6n0147
    KR6q0019
    KR6q0022
    KR6q0602
    KR6r0099
    KR6s0007
    KR6s0010
    KR6s0015
    KR6s0064
    KR6s0065
    KR6s0138
    KR6s0140
    KR6s0143
    KR6v0300
    KR6v0301
    KR6v0386
    KR6v0506
    KR6v0508
    KR6v0525
    KR6v0527
    KR6v0547
    KR6v0549
    KR6v0557
    KR6v0558
    KR6v0561
    KR6v0562
    KR6v0563
    KR6v0564
    KR6v0568
    KR6v0570
    KR6v0571
    KR6v0574
    KR6v0575
    KR6v0576
    KR6v0607
    KR6x0001
    KR6x0002
    KR6x0004
    KR6x0005
    KR6x0006
    KR6x0007
    KR6x0017
    KR6x0018
    KR6x0019
    KR6x0022
    KR6x0025
    KR6x0028
    KR6x0029
    KR6x0030
    KR6x0031
    KR6x0035
)

total=${#MULTIVOL[@]}
n=0
errors=0

for kr_id in "${MULTIVOL[@]}"; do
    n=$(( n + 1 ))
    echo "[$n/$total] $kr_id"
    if ! bkk import --format cbeta \
            --mapping /home/chris/projects/bkk/catalog/KRtoCBETA-mapping.csv \
            --in "$IN_ROOT" \
            "${OUT_ARGS[@]}" \
            --text-id "$kr_id" \
            --yes; then
        echo "  ERROR: $kr_id failed" >&2
        errors=$(( errors + 1 ))
    fi
done

echo ""
echo "Done: $total texts, $errors error(s)."
