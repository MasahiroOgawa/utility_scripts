#!/bin/bash
##
# Small-file upload server — iPhone Safari → Ubuntu over LAN.
#
# No iPhone app install. Open the URL below in Safari on the iPhone,
# tap the file picker in the web form, upload.
#
# NOTE: Python's uploadserver uses cgi.FieldStorage which buffers the whole
# body in memory — reliable for small files (< ~500 MB), flaky for multi-GB.
# For large transfers (e.g. 3D scans), use cp_3DScanner_data.sh over USB.

SHARE_DIR="/home/mas/iphone-share"
PORT=8000

mkdir -p "${SHARE_DIR}"

PC_IP=$(ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)

echo "Upload server is about to start on port ${PORT}."
echo
echo "On iPhone Safari, open:"
echo "  http://${PC_IP}:${PORT}/upload"
echo
echo "Pick a file in the form, tap Submit. File lands in ${SHARE_DIR}"
echo
echo "Ctrl-C to stop the server."
echo

cd "${SHARE_DIR}"
exec uv tool run uploadserver "${PORT}"
