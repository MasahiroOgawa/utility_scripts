#!/usr/bin/env bash
#
# set-static-ip.sh
#
# WHAT: Move a NetworkManager connection to a fixed static IPv4 address.
#
# WHY:  Sometimes a DHCP-assigned address gets hijacked or clashes with another
#       device, so a host becomes unreachable even though it is healthy. Moving
#       it to a known-free static address in the same subnet fixes this.
#
# HOW:  Edit the CONFIG block below (or override any value via an environment
#       variable of the same name), then run the script AS ROOT, ON THE TARGET
#       MACHINE, FROM ITS OWN TERMINAL (not over SSH) -- when the IP changes,
#       any SSH session connected via the old address will drop; that's
#       expected:
#
#           sudo bash set-static-ip.sh
#
#       Override examples (no need to edit the file):
#           sudo CONN="my-wifi" IFACE="wlan0" NEWIP="10.0.0.42" bash set-static-ip.sh
#
set -euo pipefail

# ============================ CONFIG (edit me) ============================
# Every value can also be supplied as an environment variable of the same name.
CONN="${CONN:-}"                        # NetworkManager connection name (nmcli con show)
IFACE="${IFACE:-}"                      # network interface, e.g. wlp0s20f3 / eth0 / wlan0
NEWIP="${NEWIP:-}"                      # desired static IPv4 address, e.g. 172.16.31.30
PREFIX="${PREFIX:-24}"                  # subnet prefix length (24 == 255.255.255.0)
GW="${GW:-}"                            # default gateway, e.g. 172.16.31.254
DNS="${DNS:-}"                          # DNS server(s), space/comma separated
# =========================================================================

die() { echo "!! $*" >&2; exit 1; }

if [ "$(id -u)" -ne 0 ]; then
  die "Please run with sudo:  sudo bash $0"
fi

# --- validate required config -------------------------------------------------
missing=()
for var in CONN IFACE NEWIP GW; do
  [ -n "${!var}" ] || missing+=("$var")
done
if [ "${#missing[@]}" -gt 0 ]; then
  echo "!! Missing required config: ${missing[*]}" >&2
  echo "   Edit the CONFIG block at the top of $0, or pass them as env vars, e.g.:" >&2
  echo "     sudo CONN=\"my-wifi\" IFACE=\"wlan0\" NEWIP=\"10.0.0.42\" GW=\"10.0.0.1\" bash $0" >&2
  exit 1
fi
: "${DNS:=$GW}"   # default DNS to the gateway if not set

# --- sanity-check the connection exists ---------------------------------------
nmcli con show "$CONN" >/dev/null 2>&1 \
  || die "NetworkManager connection '$CONN' not found. List them with: nmcli con show"

echo "=== current config for '$CONN' ==="
nmcli -f connection.id,ipv4.method,ipv4.addresses,ipv4.gateway,ipv4.dns con show "$CONN" || true

echo
echo "=== checking $NEWIP is free (ARP probe) ==="
ip neigh del "$NEWIP" dev "$IFACE" 2>/dev/null || true
ping -c 2 -W 1 "$NEWIP" >/dev/null 2>&1 || true
if ip neigh show "$NEWIP" 2>/dev/null | grep -qiE '([0-9a-f]{1,2}:){5}[0-9a-f]{1,2}'; then
  die "$NEWIP is IN USE now -- aborting. Edit NEWIP and re-run."
fi
echo "   $NEWIP looks free."

echo
echo "=== previous method (for reference) ==="
echo "   ipv4.method was: $(nmcli -g ipv4.method con show "$CONN" 2>/dev/null || echo unknown)"

echo
echo "=== applying static IP $NEWIP/$PREFIX (gw $GW, dns $DNS) ==="
nmcli con mod "$CONN" \
  ipv4.method manual \
  ipv4.addresses "$NEWIP/$PREFIX" \
  ipv4.gateway "$GW" \
  ipv4.dns "$DNS"

echo
echo "=== reactivating '$CONN' (network blips; SSH over the old IP drops here) ==="
nmcli con up "$CONN"

echo
echo "=== new state ==="
ip -br addr show "$IFACE"
echo
echo "DONE. Host should now be reachable at $NEWIP"
echo
echo "REVERT to DHCP if needed:"
echo "  sudo nmcli con mod \"$CONN\" ipv4.method auto ipv4.addresses '' ipv4.gateway '' ipv4.dns '' && sudo nmcli con up \"$CONN\""
