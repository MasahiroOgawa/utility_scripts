#!/bin/bash
# Pull 3DScanner app's Documents folder from iPhone over USB into this share dir.
# Requires: ifuse + libimobiledevice-utils + ideviceinstaller, and iPhone trusted.
set -e

BUNDLE_ID="com.dopymas.dopescan.3SM82K4JRQ"
MOUNT_POINT="/tmp/iphone-app-docs"
DEST_DIR="/home/mas/iphone-share"

cleanup() {
    if mountpoint -q "${MOUNT_POINT}"; then
        fusermount -u "${MOUNT_POINT}"
    fi
    rmdir "${MOUNT_POINT}" 2>/dev/null || true
}
trap cleanup EXIT

mkdir -p "${MOUNT_POINT}"
ifuse --documents "${BUNDLE_ID}" "${MOUNT_POINT}"

echo "--- Contents of ${BUNDLE_ID} Documents ---"
ls -lah "${MOUNT_POINT}/"
echo

echo "--- Copying to ${DEST_DIR} ---"
cp -rv "${MOUNT_POINT}/." "${DEST_DIR}/"
echo
echo "Done. Files are in ${DEST_DIR}"
