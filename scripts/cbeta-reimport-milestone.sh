#!/usr/bin/env bash
# Re-import all CBETA texts that use <milestone unit="juan"> for juan
# boundaries instead of <cb:juan>.  These were previously collapsed into a
# single KRid_000.yaml file.
#
# Usage: cbeta-reimport-milestone.sh <cbeta-xml-root> [<out-root>]

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

MILESTONE_TEXTS=(
    KR6c0229
    KR6d0220
    KR6e0140
    KR6i0340
    KR6j0696
    KR6j0726
    KR6n0143
    KR6q0163
    KR6q0164
    KR6q0190
    KR6q0533
    KR6s0009
    KR6v0001
    KR6v0017
    KR6v0025
    KR6v0046
    KR6v0047
    KR6v0048
    KR6v0050
    KR6v0051
    KR6v0052
    KR6v0066
    KR6v0067
    KR6v0068
    KR6v0070
    KR6v0077
    KR6v0078
    KR6v0079
    KR6v0080
    KR6v0081
    KR6v0082
    KR6v0083
    KR6v0084
    KR6v0089
    KR6v0094
    KR6v0095
    KR6v0096
    KR6v0097
    KR6v0098
    KR6v0099
    KR6v0100
    KR6v0101
    KR6v0117
    KR6v0121
    KR6v0122
    KR6v0123
    KR6v0127
    KR6v0128
    KR6v0129
    KR6v0130
    KR6v0302
    KR6v0304
    KR6v0323
    KR6v0363
    KR6v0396
    KR6v0400
    KR6v0401
    KR6v0403
    KR6v0413
    KR6v0414
    KR6v0416
    KR6v0426
    KR6v0455
    KR6v0458
    KR6v0459
    KR6v0539
    KR6v0542
    KR6v0553
    KR6v0554
    KR6v0555
    KR6v0556
    KR6v0557
    KR6v0558
    KR6v0559
    KR6v0560
    KR6v0561
    KR6v0562
    KR6v0563
    KR6v0564
    KR6v0565
    KR6v0566
    KR6v0567
    KR6v0568
    KR6v0569
    KR6v0570
    KR6v0571
    KR6v0572
    KR6v0573
    KR6v0574
    KR6v0575
    KR6v0576
    KR6v0577
    KR6v0578
    KR6v0591
    KR6v0592
    KR6v0593
    KR6v0594
    KR6v0595
    KR6v0596
    KR6v0597
    KR6v0598
    KR6v0599
    KR6v0600
    KR6v0601
    KR6v0602
    KR6v0603
    KR6v0604
    KR6v0605
    KR6v0606
    KR6v0609
    KR6v0610
    KR6v0611
    KR6v0612
    KR6v0613
    KR6v0614
    KR6v0615
    KR6v0618
    KR6w0003
    KR6w0005
    KR6w0006
    KR6w0009
    KR6w0045
    KR6w0051
    KR6w0053
    KR6w0059
    KR6w0063
    KR6x0001
    KR6x0002
    KR6x0003
    KR6x0004
    KR6x0005
    KR6x0006
    KR6x0007
    KR6x0008
    KR6x0009
    KR6x0010
    KR6x0011
    KR6x0012
    KR6x0013
    KR6x0014
    KR6x0015
    KR6x0016
    KR6x0017
    KR6x0018
    KR6x0019
    KR6x0020
    KR6x0021
    KR6x0022
    KR6x0023
    KR6x0024
    KR6x0025
    KR6x0026
    KR6x0027
    KR6x0028
    KR6x0029
    KR6x0030
    KR6x0031
    KR6x0032
    KR6x0033
    KR6x0034
    KR6x0035
    KR6x0036
    KR6x0037
    KR6x0038
)

total=${#MILESTONE_TEXTS[@]}
n=0
errors=0

for kr_id in "${MILESTONE_TEXTS[@]}"; do
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
