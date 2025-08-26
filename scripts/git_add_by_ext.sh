#!/usr/bin/env bash
# Add files to git grouped by extension when possible, otherwise add by filename.
# Debian-compatible (bash). Usage: ./scripts/git_add_by_ext.sh

set -euo pipefail
shopt -s globstar nullglob

# Find repo root
repo_root=$(git rev-parse --show-toplevel 2>/dev/null || echo "$(pwd)")
cd "$repo_root" || exit 1

# Create a temporary workspace to bucket files by (sanitized) extension.
tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/git_add_by_ext.XXXX")
trap 'rm -rf -- "$tmpdir"' EXIT

# Map sanitized bucket name -> original extension in a small map file
mapfile="$tmpdir/._map"

added=0

# Collect files into bucket files named by a sanitized extension
while IFS= read -r -d '' f; do
    # skip .git
    [[ $f == ./.git/* ]] && continue
    rel=${f#./}
    base=$(basename "$rel")
    if [[ "$base" == .* && "$base" != *.* ]]; then
        ext=""
    elif [[ "$base" == *.* ]]; then
        ext="${base##*.}"
    else
        ext=""
    fi

    # sanitize extension for filename safety (fallback marker for empty)
    if [[ -z "$ext" ]]; then
        sname="__noext"
    else
        # replace any non-safe chars with underscore
        sname=${ext//[^A-Za-z0-9._-]/_}
    fi

    # remember mapping (only once)
    if ! grep -q "^$sname:" "$mapfile" 2>/dev/null; then
        printf '%s:%s\n' "$sname" "$ext" >> "$mapfile"
    fi

    # append null-separated entries into bucket
    printf '%s\0' "$rel" >> "$tmpdir/$sname"
done < <(find . -type f \
    -not -path './.git/*' \
    -not -path './.venv/*' -not -path './venv/*' -not -path './env/*' \
    -not -path '*/__pycache__/*' -not -path '*/.pytest_cache/*' -not -path '*/node_modules/*' \
    -not -path './build/*' -not -path './dist/*' -not -path '*/.egg-info/*' -not -path '*/.dist-info/*' \
    -not -name '*.pyc' -not -name '*.pyo' -not -name '*.so' -not -name '*.o' -not -name '*.class' -not -name '*.dll' -not -name '*.exe' \
    -print0)

# Iterate over buckets
for bucket in "$tmpdir"/*; do
    # skip the map file if present
    [[ "$(basename "$bucket")" == '._map' ]] && continue
    [[ ! -f "$bucket" ]] && continue

    sname=$(basename "$bucket")
    ext=$(awk -F: -v key="$sname" '$1==key{print substr($0,index($0,":")+1); exit}' "$mapfile" 2>/dev/null || true)

    # read null-separated entries into array
    mapfile -d $'\0' -t files < "$bucket" || true

    # Helper: detect junk files/paths we should never add
    is_junk() {
        local p=$1
        [[ -z "$p" ]] && return 1
        case "$p" in
            */__pycache__/*|*/.pytest_cache/*|*/node_modules/*|*/.venv/*|*/venv/*|*/env/*) return 0 ;;
        esac
        case "${p##*/}" in
            *.pyc|*.pyo|*.so|*.o|*.class|*.dll|*.exe) return 0 ;;
        esac
        return 1
    }

    # If we have an extension, try adding by glob first (fast)
    if [[ -n "$ext" ]]; then
        matches=(**/*."$ext")
        # filter matches to remove junk entries
        if ((${#matches[@]})); then
            good_matches=()
            for m in "${matches[@]}"; do
                is_junk "$m" && continue
                good_matches+=("$m")
            done
            if ((${#good_matches[@]})); then
                echo "Adding by extension '*.$ext' (${#good_matches[@]} files)..."
                git add -- "${good_matches[@]}" || true
                added=$((added + ${#good_matches[@]}))
                continue
            fi
        fi
    fi

    # Fallback: add files individually
    if ((${#files[@]})); then
        # filter files list
        good_files=()
        for f in "${files[@]}"; do
            [[ -z "$f" ]] && continue
            is_junk "$f" && continue
            good_files+=("$f")
        done
        if ((${#good_files[@]})); then
            echo "Adding ${#good_files[@]} file(s)${ext:+ with extension '.$ext'} by filename..."
            for f in "${good_files[@]}"; do
                git add -- "$f" || true
                added=$((added + 1))
            done
        fi
    fi
done

echo "Done. Attempted to add $added file(s)."
# show short status
# Unstage any already-staged junk files (safety cleanup for prior runs)
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    # use null-separated to handle special filenames
    git diff --name-only --cached -z | while IFS= read -r -d $'\0' staged; do
        if is_junk "$staged"; then
            echo "Unstaging junk: $staged"
            git reset -- "$staged" || true
        fi
    done
fi

git status --porcelain
