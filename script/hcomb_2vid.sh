#!/bin/sh
# hcomb_2vid
# combine 2 videos horizontally using ffmpeg
##

# treat input
CMDNAME='basename $0'
USAGE="$CMDNAME left_video right_video [output_video]"
LVID=
RVID=
OUTVID=output.mp4

if [ $# -lt 2 ]; then
    echo $USAGE 1>&2
    exit 1
else
    LVID=$1
    RVID=$2
fi
if [ $# -gt 3 ]; then
    OUTVID=$3
fi


# main process
ffmpeg -i $LVID -i $RVID -filter_complex "[0][1]scale2ref='oh*mdar':'if(lt(main_h,ih),ih,main_h)'[0s][1s]; [1s][0s]scale2ref='oh*mdar':'if(lt(main_h,ih),ih,main_h)'[1s][0s];[0s][1s]hstack,setsar=1" $OUTVID
