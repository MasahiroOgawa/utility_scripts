#!/bin/bash
###

# param
TRASH_DIR=$HOME/trash

# run
mkdir -p "$TRASH_DIR"

# Move each argument into the trash, picking a unique destination name on
# collision so the move always succeeds (and never overwrites earlier trash).
trash_real=$(realpath -- "$TRASH_DIR")

if [[ "$(realpath -- "$PWD")" == "$trash_real" ]]; then
    echo "trash.sh: already in $TRASH_DIR; nothing to clean up." >&2
    exit 0
fi

for arg in "$@"; do
    abs=$(realpath -- "$arg") || exit 1
    # Only skip when the file's *immediate parent* is the trash root — a file
    # in a subdirectory of trash (e.g. ~/trash/Downloads/foo) is exactly what
    # "clean up" should flatten into ~/trash/foo.
    if [[ "$(dirname "$abs")" == "$trash_real" ]]; then
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
