#!/bin/bash
###

# param
TRASH_DIR=$HOME/trash

# run
mkdir -p "$TRASH_DIR"

# Move each argument into the trash, picking a unique destination name on
# collision so the move always succeeds (and never overwrites earlier trash).
for arg in "$@"; do
    abs=$(realpath -- "$arg") || exit 1
    if [[ "$abs" == "$TRASH_DIR" || "$abs" == "$TRASH_DIR"/* ]]; then
        echo "trash.sh: skipping '$arg' (already in $TRASH_DIR)" >&2
        continue
    fi

    base=$(basename "$abs")
    dest="$TRASH_DIR/$base"
    if [[ -e "$dest" || -L "$dest" ]]; then
        ts=$(date +%Y%m%d_%H%M%S)
        dest="$TRASH_DIR/${base}.${ts}"
        i=1
        while [[ -e "$dest" || -L "$dest" ]]; do
            dest="$TRASH_DIR/${base}.${ts}_${i}"
            ((i++))
        done
    fi

    if ! mv -f "$abs" "$dest" 2>/dev/null; then
        sudo mv -f "$abs" "$dest"
    fi
done
