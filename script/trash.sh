#!/bin/bash
###

# param
TRASH_DIR=$HOME/trash
SUFFIX=.$(date +%Y%m%d_%H%M%S)

# run
mkdir -p "$TRASH_DIR"

# Resolve each argument to an absolute path so '.' / '..' / trailing-slash
# forms work (mv can't move '.' directly — "Device or resource busy").
paths=()
for arg in "$@"; do
    abs=$(realpath -- "$arg") || exit 1
    if [[ "$abs" == "$TRASH_DIR" || "$abs" == "$TRASH_DIR"/* ]]; then
        echo "trash.sh: skipping '$arg' (already in $TRASH_DIR)" >&2
        continue
    fi
    paths+=("$abs")
done

[[ ${#paths[@]} -eq 0 ]] && exit 0

if mv --backup=numbered --suffix="$SUFFIX" -f "${paths[@]}" "$TRASH_DIR/" 2>/dev/null; then
    exit 0
else
    sudo mv --backup=numbered --suffix="$SUFFIX" -f "${paths[@]}" "$TRASH_DIR/"
fi
