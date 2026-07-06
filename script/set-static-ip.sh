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

usage() {
  cat <<'EOF'
set-static-ip.sh -- move a NetworkManager connection to a fixed static IPv4.

USAGE
  sudo bash set-static-ip.sh                     # uses the CONFIG block / env vars
  sudo CONN=... IFACE=... NEWIP=... GW=... bash set-static-ip.sh
  bash set-static-ip.sh -h | --help              # show this help

Run it AS ROOT, ON THE TARGET MACHINE, FROM ITS OWN TERMINAL (not over SSH) --
when the IP changes, any SSH session on the old address will drop.

Set these variables (edit the CONFIG block at the top, or pass as env vars):

  CONN    NetworkManager connection name (required).
          Find it:  nmcli con show
                    nmcli -t -f NAME,DEVICE,TYPE con show --active

  IFACE   Network interface, e.g. wlp0s20f3 / eth0 / wlan0 (required).
          Find it:  nmcli -g GENERAL.DEVICES con show "$CONN"
                    ip -br addr           (the UP line with your current IP)
                    nmcli device status

  NEWIP   The free static IPv4 address to move to (required).
          Pick an unused address in the SAME subnet as your current IP.
          Verify it is free:  ping -c2 <candidate>   (no reply == free)
          (the script also ARP-probes it before applying.)

  GW      Default gateway / router address (required).
          Find it:  ip route | grep default
                    nmcli -g IP4.GATEWAY device show "$IFACE"

  DNS     DNS server(s), space/comma separated (optional; defaults to GW).
          Find it:  nmcli -g IP4.DNS device show "$IFACE"
                    resolvectl status "$IFACE" | grep 'DNS Server'

  PREFIX  Subnet prefix length (optional; default 24 == 255.255.255.0).
          It is the number after the "/" in `ip -br addr`, e.g. 10.0.0.5/24 -> 24.
EOF
}

case "${1:-}" in
  -h | --help) usage; exit 0 ;;
esac

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
  usage >&2
  echo >&2
  die "Missing required config: ${missing[*]}"
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
