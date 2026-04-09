#!/bin/bash
# Explanation: Fix NVIDIA GPU "Xid 79: GPU has fallen off the bus" crash triggered by screen lock.
# Author: Masahiro Ogawa
#
# Problem:
#   When locking the screen on a Linux PC with an NVIDIA GPU and an external monitor,
#   GNOME blanks the display via DPMS. The NVIDIA driver then tries to reconfigure
#   display outputs, triggering rapid re-detection of the external monitor.
#   If PCIe runtime power management is set to "auto" and D3cold (full power-off)
#   is allowed, the GPU enters a deep power state during this reconfiguration and
#   fails to wake back up. The kernel reports:
#     NVRM: Xid (PCI:0000:01:00): 79, GPU has fallen off the bus.
#   The X server crashes, GNOME Shell dies, and the system is stuck in an
#   unrecoverable loop of "Error while waiting for GPU progress" every 5 seconds
#   until hard reboot.
#
# Root cause:
#   power-profiles-daemon (especially in power-saver mode) sets PCI device
#   power/control to "auto", enabling runtime PM. Combined with d3cold_allowed=1,
#   the GPU can be fully powered off and fail to recover during display mode changes.
#   A udev rule alone is not sufficient because power-profiles-daemon overrides it
#   after boot.
#
# Fix:
#   1. udev rule: Sets power/control=on and d3cold_allowed=0 for NVIDIA GPU and
#      its audio device on add/change events.
#   2. systemd service: Runs after power-profiles-daemon to enforce the settings,
#      ensuring they stick even after power-profiles-daemon overrides the udev rule.
#
# Verified on: Ubuntu 24.04, NVIDIA driver 580.x (open kernel module),
#              RTX 4080 Laptop GPU, Acer Predator laptop with external monitor.
# Should work on: Any Linux system with NVIDIA GPU experiencing Xid 79 on screen lock.
#
# How to verify the issue before applying:
#   journalctl -b -1 -k | grep "Xid.*79.*fallen"
#   cat /sys/bus/pci/devices/0000:01:00.0/power/control  # "auto" = vulnerable
#   cat /sys/bus/pci/devices/0000:01:00.0/d3cold_allowed  # "1" = vulnerable
#
# How to verify the fix after applying:
#   cat /sys/bus/pci/devices/0000:01:00.0/power/control  # should be "on"
#   cat /sys/bus/pci/devices/0000:01:00.0/d3cold_allowed  # should be "0"
###

set -e

UDEV_RULE=/etc/udev/rules.d/80-nvidia-no-runtime-pm.rules
SYSTEMD_SERVICE=/etc/systemd/system/nvidia-no-runtime-pm.service

echo "[INFO] Installing udev rule: ${UDEV_RULE}"
sudo tee ${UDEV_RULE} > /dev/null << 'UDEV_EOF'
# Prevent NVIDIA GPU from entering runtime PM / D3cold to avoid Xid 79 crash on screen lock
# GPU (VGA controller class 0x030000)
ACTION=="add|change", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x030000", ATTR{power/control}="on", ATTR{d3cold_allowed}="0"
# Audio device on same GPU (audio class 0x040300)
ACTION=="add|change", SUBSYSTEM=="pci", ATTR{vendor}=="0x10de", ATTR{class}=="0x040300", ATTR{power/control}="on", ATTR{d3cold_allowed}="0"
UDEV_EOF

echo "[INFO] Installing systemd service: ${SYSTEMD_SERVICE}"
sudo tee ${SYSTEMD_SERVICE} > /dev/null << 'SERVICE_EOF'
[Unit]
Description=Disable NVIDIA GPU runtime PM to prevent Xid 79 crash
After=power-profiles-daemon.service
After=multi-user.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c '\
  for dev in /sys/bus/pci/devices/*/; do \
    vendor=$(cat "$dev/vendor" 2>/dev/null); \
    if [ "$vendor" = "0x10de" ]; then \
      echo on > "$dev/power/control" 2>/dev/null || true; \
      echo 0 > "$dev/d3cold_allowed" 2>/dev/null || true; \
    fi; \
  done'

[Install]
WantedBy=multi-user.target
SERVICE_EOF

echo "[INFO] Reloading systemd and enabling service..."
sudo systemctl daemon-reload
sudo systemctl enable --now nvidia-no-runtime-pm.service

echo "[INFO] Reloading udev rules..."
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=pci --attr-match=vendor=0x10de

echo ""
echo "[INFO] Verifying..."
for dev in /sys/bus/pci/devices/*/; do
    vendor=$(cat "$dev/vendor" 2>/dev/null)
    if [ "$vendor" = "0x10de" ]; then
        devname=$(basename "$dev")
        power=$(cat "$dev/power/control" 2>/dev/null)
        d3cold=$(cat "$dev/d3cold_allowed" 2>/dev/null)
        echo "  ${devname}: power/control=${power}, d3cold_allowed=${d3cold}"
    fi
done

echo ""
echo "[INFO] Done. Lock your screen to test. If the issue is resolved, the fix persists across reboots."
