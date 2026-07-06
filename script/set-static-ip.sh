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
# HOW:  You only need to supply NEWIP (the static address to move to).
#       CONN, IFACE, GW, DNS and PREFIX are auto-detected from the currently
#       active connection; override any of them via an env var if needed.
#
#       Run it AS ROOT, ON THE TARGET MACHINE, FROM ITS OWN TERMINAL (not over
#       SSH) -- when the IP changes, any SSH session on the old address drops:
#
#           sudo NEWIP="172.16.31.30" bash set-static-ip.sh
#
set -euo pipefail

usage() {
  cat <<'EOF'
set-static-ip.sh -- move a NetworkManager connection to a fixed static IPv4.

USAGE
  sudo NEWIP="10.0.0.42" bash set-static-ip.sh   # only NEWIP is required
  bash set-static-ip.sh -h | --help              # show this help

Run it AS ROOT, ON THE TARGET MACHINE, FROM ITS OWN TERMINAL (not over SSH) --
when the IP changes, any SSH session on the old address will drop.

  NEWIP   REQUIRED. The free static IPv4 address to move to.
          Pick an unused address in the SAME subnet as your current IP.
          Verify it is free:  ping -c2 <candidate>   (no reply == free)
          (the script also ARP-probes it before applying.)

Everything below is auto-detected from the active connection. Override any of
them by passing an environment variable of the same name:

  CONN    NetworkManager connection name.   auto: the device's active connection
  IFACE   Network interface (e.g. eth0).    auto: interface of the default route
  GW      Default gateway / router.         auto: gateway of the default route
  DNS     DNS server(s).                    auto: connection's DNS (falls back to GW)
  PREFIX  Subnet prefix length.             auto: current prefix (falls back to 24)
EOF
}

case "${1:-}" in
  -h | --help) usage; exit 0 ;;
esac

die() { echo "!! $*" >&2; exit 1; }

# ================================ CONFIG =================================
# NEWIP is the only value you must supply. Everything else is auto-detected,
# but you can still override any of them via an env var of the same name.
NEWIP="${NEWIP:-}"          # REQUIRED: desired static IPv4, e.g. 172.16.31.30
CONN="${CONN:-}"            # auto: NetworkManager connection name
IFACE="${IFACE:-}"          # auto: network interface
GW="${GW:-}"                # auto: default gateway
DNS="${DNS:-}"              # auto: DNS server(s) (falls back to GW)
PREFIX="${PREFIX:-}"        # auto: subnet prefix length (falls back to 24)
# =========================================================================

if [ "$(id -u)" -ne 0 ]; then
  die "Please run with sudo:  sudo NEWIP=\"...\" bash $0"
fi

if [ -z "$NEWIP" ]; then
  usage >&2
  echo >&2
  die "Missing required value: NEWIP"
fi

# --- auto-detect anything not explicitly provided ----------------------------
if [ -z "$IFACE" ]; then
  IFACE="$(ip -o -4 route show to default 2>/dev/null | awk '{print $5; exit}')"
fi
[ -n "$IFACE" ] || die "Could not auto-detect IFACE; pass IFACE=... (see -h)."

if [ -z "$CONN" ]; then
  CONN="$(nmcli -t -f DEVICE,CONNECTION device status 2>/dev/null \
            | awk -F: -v d="$IFACE" '$1==d {print $2; exit}')"
fi
[ -n "$CONN" ] || die "Could not auto-detect CONN for $IFACE; pass CONN=... (see -h)."

if [ -z "$GW" ]; then
  GW="$(ip -o -4 route show to default 2>/dev/null | awk '{print $3; exit}')"
fi
[ -n "$GW" ] || die "Could not auto-detect GW; pass GW=... (see -h)."

if [ -z "$PREFIX" ]; then
  PREFIX="$(ip -o -4 addr show dev "$IFACE" 2>/dev/null | awk '{print $4; exit}' | cut -d/ -f2)"
  PREFIX="${PREFIX:-24}"
fi

if [ -z "$DNS" ]; then
  DNS="$(nmcli -g IP4.DNS device show "$IFACE" 2>/dev/null | paste -sd, -)"
  DNS="${DNS:-$GW}"
fi

# --- sanity-check the connection exists ---------------------------------------
nmcli con show "$CONN" >/dev/null 2>&1 \
  || die "NetworkManager connection '$CONN' not found. List them with: nmcli con show"

echo "=== using configuration (auto-detected unless overridden) ==="
printf '   %-7s %s\n' CONN "$CONN" IFACE "$IFACE" NEWIP "$NEWIP/$PREFIX" GW "$GW" DNS "$DNS"

echo
echo "=== current config for '$CONN' ==="
nmcli -f connection.id,ipv4.method,ipv4.addresses,ipv4.gateway,ipv4.dns con show "$CONN" || true

echo
echo "=== checking $NEWIP is free (ARP probe) ==="
ip neigh del "$NEWIP" dev "$IFACE" 2>/dev/null || true
ping -c 2 -W 1 "$NEWIP" >/dev/null 2>&1 || true
if ip neigh show "$NEWIP" 2>/dev/null | grep -qiE '([0-9a-f]{1,2}:){5}[0-9a-f]{1,2}'; then
  die "$NEWIP is IN USE now -- aborting. Choose a different NEWIP and re-run."
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
