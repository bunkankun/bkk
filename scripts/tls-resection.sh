#!/usr/bin/env bash
# Reorganize a flat tls-out tree into a sectioned tree:
#   KR3a0001  -> KR3a/KR3a0001/
#   KR3e0001  -> KR3e/KR3e0001/
#   KR3ea001  -> KR3e/KR3ea/KR3ea001/
#
# Dry-run by default. Pass --apply to actually move things.

set -euo pipefail

apply=0
verbose=0
root="$HOME/00scratch/bkk-work/tls-out"

for arg in "$@"; do
    case "$arg" in
        --apply) apply=1 ;;
        -v|--verbose) verbose=1 ;;
        -h|--help)
            cat <<EOF
usage: $(basename "$0") [--apply] [-v] [ROOT]

Reorganize tls-out so each text-id sits under its 4-char section, with an
extra subdivision level when the 5th char is a letter (KR3ea001 ->
KR3e/KR3ea/KR3ea001/).

Without --apply, prints planned moves and exits without touching anything.
EOF
            exit 0
            ;;
        --*)
            echo "unknown option: $arg" >&2
            exit 2
            ;;
        *)
            root="$arg"
            ;;
    esac
done

if [[ ! -d "$root" ]]; then
    echo "not a directory: $root" >&2
    exit 1
fi

moved=0
already=0
conflicts=0
unknown=0

# -mindepth 1 -maxdepth 3 so we re-detect already-relocated text-ids
# (root/section/text-id and root/section/subdiv/text-id) and treat them
# as in-place. Sort for deterministic output.
while IFS= read -r path; do
    name=$(basename "$path")

    # Containers: 4-char section or 5-char subdivision -- skip.
    if [[ "$name" =~ ^KR[0-9][a-z]$ ]] || [[ "$name" =~ ^KR[0-9][a-z]{2}$ ]]; then
        continue
    fi

    # 8-char text-id: KR + digit + letter + 4 of [0-9a-z].
    if [[ ! "$name" =~ ^KR[0-9][a-z][0-9a-z]{4}$ ]]; then
        unknown=$((unknown + 1))
        echo "skip (unknown name): $path" >&2
        continue
    fi

    section="${name:0:4}"
    fifth="${name:4:1}"
    if [[ "$fifth" =~ [a-z] ]]; then
        subdiv="${name:0:5}"
        dest="$root/$section/$subdiv/$name"
    else
        dest="$root/$section/$name"
    fi

    if [[ "$path" == "$dest" ]]; then
        already=$((already + 1))
        if (( verbose )); then
            echo "in place: $path"
        fi
        continue
    fi

    if [[ -e "$dest" ]]; then
        conflicts=$((conflicts + 1))
        echo "conflict (dest exists): $path -> $dest" >&2
        continue
    fi

    if (( apply )); then
        mkdir -p "$(dirname "$dest")"
        mv "$path" "$dest"
    else
        echo "mv $path -> $dest"
    fi
    moved=$((moved + 1))
done < <(find "$root" -mindepth 1 -maxdepth 3 -type d | sort)

mode_label="dry-run"
(( apply )) && mode_label="applied"
echo "[$mode_label] moved $moved, already-in-place $already, conflicts $conflicts, skipped-unknown $unknown"
