#!/usr/bin/env bash
# Sync sectioned tls-out to a remote krp tree, skipping any text-id that
# already exists on the remote (by directory basename).
#
# Dry-run by default. Pass --apply to actually transfer.

set -euo pipefail

apply=0
verbose=0
host="oryx2"
remote_root="/home/Shared/krp"
root="$HOME/00scratch/bkk-work/tls-out"

usage() {
    cat <<EOF
usage: $(basename "$0") [--apply] [-v] [--host HOST] [--remote-root PATH] [ROOT]

Sync text-id directories from a sectioned local tls-out tree to a remote
krp tree, copying only those text-ids whose directory does not yet exist
on the remote. Existence is checked by directory basename anywhere on the
remote (-maxdepth 3).

Defaults:
  ROOT         $root
  --host       $host
  --remote-root $remote_root
EOF
}

while (( $# )); do
    case "$1" in
        --apply) apply=1 ;;
        -v|--verbose) verbose=1 ;;
        --host) host="$2"; shift ;;
        --host=*) host="${1#*=}" ;;
        --remote-root) remote_root="$2"; shift ;;
        --remote-root=*) remote_root="${1#*=}" ;;
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

# Single connectivity probe so auth failures surface cleanly.
if ! ssh -o BatchMode=yes "$host" true 2>/dev/null; then
    echo "ssh $host failed (BatchMode). Check auth/agent." >&2
    exit 1
fi

# Enumerate remote text-id basenames.
remote_listing=$(ssh "$host" "find '$remote_root' -mindepth 1 -maxdepth 3 -type d -regextype posix-extended -regex '.*/KR[0-9][a-z][0-9a-z]{4}\$' \\! -empty -printf '%f\n' 2>/dev/null | sort -u" || true)

declare -A on_remote=()
remote_count=0
while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    on_remote[$id]=1
    remote_count=$((remote_count + 1))
done <<<"$remote_listing"

# Walk local, classify.
list_file=$(mktemp)
trap 'rm -f "$list_file"' EXIT

local_count=0
to_copy=0
skipped_existing=0
skipped_other=0
declare -a sample_existing=()
declare -a sample_other=()

while IFS= read -r path; do
    name=$(basename "$path")
    if [[ ! "$name" =~ ^KR[0-9][a-z][0-9a-z]{4}$ ]]; then
        skipped_other=$((skipped_other + 1))
        (( ${#sample_other[@]} < 5 )) && sample_other+=("$path")
        continue
    fi
    local_count=$((local_count + 1))
    if [[ -n "${on_remote[$name]+x}" ]]; then
        skipped_existing=$((skipped_existing + 1))
        (( ${#sample_existing[@]} < 5 )) && sample_existing+=("$name")
        continue
    fi
    rel="${path#$root/}"
    printf '%s\n' "$rel" >>"$list_file"
    to_copy=$((to_copy + 1))
done < <(find "$root" -mindepth 2 -maxdepth 3 -type d | sort)

echo "local text-ids: $local_count"
echo "remote text-ids: $remote_count"
echo "to copy: $to_copy"
echo "skipped (already on remote): $skipped_existing"
echo "skipped (name not a text-id): $skipped_other"

if (( verbose )); then
    if (( ${#sample_existing[@]} )); then
        echo "  sample existing-on-remote: ${sample_existing[*]}"
    fi
    if (( ${#sample_other[@]} )); then
        echo "  sample non-text-id names:"
        printf '    %s\n' "${sample_other[@]}"
    fi
fi

if (( to_copy == 0 )); then
    echo "nothing to do."
    exit 0
fi

if (( ! apply )); then
    echo
    echo "preview (first 20 of $to_copy):"
    head -20 "$list_file" | sed 's/^/  /'
    echo
    echo "dry-run; pass --apply to transfer."
    exit 0
fi

echo
echo "rsync -> $host:$remote_root/"
rsync -ar --info=stats2,progress2 \
    --files-from="$list_file" \
    "$root/" "$host:$remote_root/"
