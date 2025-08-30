#!/bin/bash
###

# param
TRASH_DIR=$HOME/trash

# run
mkdir -p $TRASH_DIR

# Check if we can move the files without sudo first
if mv --backup=numbered --suffix=.$(date +%Y%m%d_%H%M%S) -f "$@" $TRASH_DIR/ 2>/dev/null; then
    exit 0
else
    # If permission denied, use sudo
    sudo mv --backup=numbered --suffix=.$(date +%Y%m%d_%H%M%S) -f "$@" $TRASH_DIR/
fi