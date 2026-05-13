#!/usr/bin/env bash
# Walk a bkk corpus root and run `bkk voice add` on every text-id bundle.
#
# By default reruns are safe: `bkk voice add` refuses any bundle that
# already carries voice markers, so a second pass cleanly skips them.
# Pass --force to strip and rederive instead.

set -euo pipefail

source="parens"          # parens | indent | all
force=0
dry_run=0
list_only=0
root="${BKK_CORPUS:-/home/Shared/bkk/bkkbooks}"
declare -a only=()

usage() {
    cat <<EOF
usage: $(basename "$0") [--source SRC] [--force] [--dry-run] [--list]
                       [--only TEXT_ID]... [ROOT]

Iterate every <ROOT>/<text-id>/ bundle and run \`bkk voice add\` on it.

Options:
  --source SRC    parens (default), indent, or all
  --force         replace any voice markers already present
  --dry-run       pass --dry-run through to \`bkk voice add\`
  --list          list matching bundles and exit (no work done)
  --only ID       restrict to one text-id (repeatable). Without --only
                  all KR-shaped bundles under ROOT are processed.

Defaults:
  ROOT            $root      (or \$BKK_CORPUS if set)
  SRC             $source
EOF
}

while (( $# )); do
    case "$1" in
        --source) source="$2"; shift ;;
        --source=*) source="${1#*=}" ;;
        --force) force=1 ;;
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

case "$source" in
    parens|indent|all) ;;
    *) echo "invalid --source: $source (want parens|indent|all)" >&2; exit 2 ;;
esac

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

total=${#bundles[@]}
if (( total == 0 )); then
    echo "no bundles found under $root" >&2
    exit 0
fi

echo "root:    $root"
echo "source:  $source"
echo "force:   $force"
echo "dry-run: $dry_run"
echo "bundles: $total"

if (( list_only )); then
    printf '  %s\n' "${bundles[@]}"
    exit 0
fi

# Compose the flag set once.
flags=(--source "$source")
(( force )) && flags+=(--force)
(( dry_run )) && flags+=(--dry-run)

ok=0
failed=0
declare -a failed_ids=()
i=0
for bundle in "${bundles[@]}"; do
    i=$((i + 1))
    id=$(basename "$bundle")
    printf '[%d/%d] %s\n' "$i" "$total" "$id"
    if bkk voice add "$bundle" "${flags[@]}"; then
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
