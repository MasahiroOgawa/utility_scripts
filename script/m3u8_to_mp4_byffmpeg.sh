#!/bin/bash
USAGE="Usage: $0 <m3u8 file>"
if [ $# -ne 1 ]; then
    echo $USAGE
    exit 1
fi

ffmpeg -i $1 -c copy -bsf:a aac_adtstoasc ${1%.*}.mp4

