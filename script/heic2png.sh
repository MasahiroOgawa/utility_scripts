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
arg=$@

if [ -z ${arg} ]; then
    echo "$USAGE" 1>&2
    exit 1
else
    INDIR="${arg}"
fi



for file in ${INDIR}/*.HEIC
do
    echo "converting ${file} ..."
    
    # convert ${file} ${file/%.HEIC/.png}
    
    if [ -z "${QUALITY}" ]
    then 
	heif-convert ${file} ${file/%.HEIC/.png}
    else
	echo "quality=${QUALITY}"
	heif-convert -q ${QUALITY} ${file} ${file/%.HEIC/.png}
    fi	
done
