#!/bin/bash

USAGE="Usage: $(basename "$0") [-h] [-q quality(0-100)] input_heic_existing_dir

Convert .HEIC files in a directory to .png.
Files that are actually JPEG (misnamed as .HEIC) are renamed to .jpg instead.

Options:
  -h    Show this help message and exit
  -q    Conversion quality (0-100)"

while getopts hq: OPT
do
    case $OPT in
	h)
	    echo "$USAGE"
	    exit 0;;
	q)
	    QUALITY=${OPTARG};;
	\?)
	    echo "$USAGE" 1>&2
	    exit 1
    esac
done

shift "$((OPTIND-1))"

INDIR="$*"
if [ -z "${INDIR}" ]; then
    echo "$USAGE" 1>&2
    exit 1
fi

for file in "${INDIR}"/*.HEIC "${INDIR}"/*.heic
do
    [ -e "$file" ] || continue

    # Detect actual file type
    filetype=$(file -b --mime-type "$file")

    if [[ "$filetype" == image/jpeg ]]; then
	outfile="${file/%.HEIC/.jpg}"
	outfile="${outfile/%.heic/.jpg}"
	echo "Renaming ${file} -> ${outfile} (actually JPEG)"
	mv "$file" "$outfile"
	continue
    fi

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
