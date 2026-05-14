#!/usr/bin/env bash
# Walk a bkk corpus root and run `bkk voice remove` on every text-id bundle
# that actually carries voice markers.
#
# Voice markers serialize as flow-style mappings (`{type: voice, ...}`) in
# juan YAML files; bundles with none are skipped without invoking the CLI.

set -euo pipefail

dry_run=0
list_only=0
root="${BKK_CORPUS:-/home/Shared/bkk/bkkbooks}"
declare -a only=()

usage() {
    cat <<EOF
usage: $(basename "$0") [--dry-run] [--list] [--only TEXT_ID]... [ROOT]

Iterate every <ROOT>/<text-id>/ bundle and run \`bkk voice remove\` on it.

Options:
  --dry-run       pass --dry-run through to \`bkk voice remove\`
  --list          list matching bundles and exit (no work done)
  --only ID       restrict to one text-id (repeatable). Without --only
                  all KR-shaped bundles under ROOT are processed.

Defaults:
  ROOT            $root      (or \$BKK_CORPUS if set)
EOF
}

while (( $# )); do
    case "$1" in
        --dry-run) dry_run=1 ;;
        --list) list_only=1 ;;
        --only) only+=("$2"); shift ;;
        --only=*) only+=("${1#*=}") ;;
        -h|--help) usage; exit 0 ;;
        --*) echo "unknown option: $1" >&2; exit 2 ;;
        *) root="$1" ;;
    esac
    shift
done

if [[ ! -d "$root" ]]; then
    echo "not a directory: $root" >&2
    exit 1
fi

if ! command -v bkk >/dev/null 2>&1; then
    echo "bkk not on PATH" >&2
    exit 1
fi

# Discover bundles: any directory matching KR[0-9][a-z][0-9a-z]{4} that
# carries its own <basename>.manifest.yaml. We look two levels deep to
# cover both flat (ROOT/<id>/) and sectioned (ROOT/KRx/KRxa/<id>/) layouts.
mapfile -t bundles < <(
    find "$root" -mindepth 1 -maxdepth 4 -type d \
        -regextype posix-extended \
        -regex '.*/KR[0-9][a-z][0-9a-z]{4}$' \
        -printf '%p\n' \
    | while IFS= read -r d; do
        name=$(basename "$d")
        [[ -f "$d/$name.manifest.yaml" ]] && printf '%s\n' "$d"
      done \
    | sort -u
)

# Optional --only filter.
if (( ${#only[@]} )); then
    declare -A want=()
    for id in "${only[@]}"; do want[$id]=1; done
    filtered=()
    for d in "${bundles[@]}"; do
        if [[ -n "${want[$(basename "$d")]+x}" ]]; then
            filtered+=("$d")
        fi
    done
    bundles=("${filtered[@]}")
fi

discovered=${#bundles[@]}
if (( discovered == 0 )); then
    echo "no bundles found under $root" >&2
    exit 0
fi

# Pre-filter: only keep bundles whose juan YAMLs contain a voice marker.
# Markers are written in flow style as `{type: voice, ...}`, so a single
# recursive grep per bundle is enough; -q exits on the first hit.
to_process=()
skipped=0
for bundle in "${bundles[@]}"; do
    if grep -rqE --include='*.yaml' \
            --exclude='*.manifest.yaml' \
            --exclude='*.ann.yaml' \
            --exclude='*.source.yaml' \
            'type:[[:space:]]*voice' "$bundle"; then
        to_process+=("$bundle")
    else
        skipped=$((skipped + 1))
    fi
done

bundles=("${to_process[@]}")
total=${#bundles[@]}

echo "root:      $root"
echo "dry-run:   $dry_run"
echo "discovered: $discovered"
echo "skipped:   $skipped (no voice markers)"
echo "to process: $total"

if (( list_only )); then
    printf '  %s\n' "${bundles[@]}"
    exit 0
fi

if (( total == 0 )); then
    exit 0
fi

flags=()
(( dry_run )) && flags+=(--dry-run)

ok=0
failed=0
declare -a failed_ids=()
i=0
for bundle in "${bundles[@]}"; do
    i=$((i + 1))
    id=$(basename "$bundle")
    printf '[%d/%d] %s\n' "$i" "$total" "$id"
    if bkk voice remove "$bundle" "${flags[@]}"; then
        ok=$((ok + 1))
    else
        rc=$?
        failed=$((failed + 1))
        failed_ids+=("$id (rc=$rc)")
    fi
done

echo
echo "done: $ok ok, $failed failed of $total"
if (( failed )); then
    printf '  failed: %s\n' "${failed_ids[@]}" >&2
    exit 1
fi
