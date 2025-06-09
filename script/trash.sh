#!/bin/bash
###

# param
TRASH_DIR=$HOME/trash

# define function to use trash command
function trash(){
    mv --backup=numbered --suffix=.$(date +%Y%m%d_%H%M%S) -f "$@" $TRASH_DIR/ ;
}

# run
mkdir -p $TRASH_DIR
trash "$@"