#!/bin/bash

USAGE="Usage: $CMDNAME [-q quality(0-100)] input_heic_existing_dir"

while getopts q: OPT
do
    case $OPT in
	q)
	    QUALITY=${OPTARG};;
	\?)
	    echo "unexpected option." 1>&2
	    exit 1
    esac
done

# Shift the option arguments to access the non-option arguments
shift "$((OPTIND-1))"
arg="$*"

if [ -z "${arg}" ]; then
    echo "$USAGE" 1>&2
    exit 1
else
    INDIR="${arg}"
fi

for file in "${INDIR}"/*.HEIC "${INDIR}"/*.heic
do
    [ -e "$file" ] || continue
    echo "converting ${file} ..."

    outfile="${file/%.HEIC/.png}"
    outfile="${outfile/%.heic/.png}"

    if [ -z "${QUALITY}" ]; then
	heif-convert "$file" "$outfile"
    else
	echo "quality=${QUALITY}"
	heif-convert -q "${QUALITY}" "$file" "$outfile"
    fi
done
