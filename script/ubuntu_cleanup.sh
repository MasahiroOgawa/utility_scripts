#!/bin/bash

# Ubuntu Cleanup Script
# Cleans trash, cache, logs, and unnecessary content

set -uo pipefail

CMDNAME=$(basename "$0")
USAGE="Usage: $CMDNAME [-n dry-run] [-a all] [-v verbose]
Options:
    -n  Dry run (show what would be deleted without deleting)
    -a  Clean all (includes system cache, requires sudo)
    -v  Verbose output
    -h  Show this help"

DRY_RUN=false
CLEAN_ALL=false
VERBOSE=false

while getopts navh OPT; do
    case $OPT in
        n) DRY_RUN=true ;;
        a) CLEAN_ALL=true ;;
        v) VERBOSE=true ;;
        h) echo "$USAGE"; exit 0 ;;
        \?) echo "$USAGE" 1>&2; exit 1 ;;
    esac
done

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

log_verbose() {
    if $VERBOSE; then
        log "$1"
    fi
}

get_size() {
    local path="$1"
    if [ -e "$path" ]; then
        du -sh "$path" 2>/dev/null | cut -f1 || echo "unknown"
    else
        echo "0"
    fi
}

clean_path() {
    local path="$1"
    local desc="$2"

    if [ -e "$path" ]; then
        local size
        size=$(get_size "$path")
        if $DRY_RUN; then
            log "[DRY-RUN] Would clean $desc: $path ($size)"
        else
            log "Cleaning $desc: $path ($size)"
            rm -rf "$path" 2>/dev/null || log "Warning: Could not fully clean $path"
        fi
    else
        log_verbose "Skipping $desc (not found): $path"
    fi
}

clean_dir_contents() {
    local path="$1"
    local desc="$2"

    if [ -d "$path" ]; then
        local size
        size=$(get_size "$path")
        if $DRY_RUN; then
            log "[DRY-RUN] Would clean $desc: $path ($size)"
        else
            log "Cleaning $desc: $path ($size)"
            rm -rf "${path:?}"/* 2>/dev/null || log "Warning: Could not fully clean $path"
        fi
    else
        log_verbose "Skipping $desc (not found): $path"
    fi
}

bytes_to_human() {
    local bytes=$1
    if [ "$bytes" -ge 1073741824 ]; then
        echo "$(awk "BEGIN {printf \"%.2f\", $bytes/1073741824}")G"
    elif [ "$bytes" -ge 1048576 ]; then
        echo "$(awk "BEGIN {printf \"%.2f\", $bytes/1048576}")M"
    elif [ "$bytes" -ge 1024 ]; then
        echo "$(awk "BEGIN {printf \"%.2f\", $bytes/1024}")K"
    else
        echo "${bytes}B"
    fi
}

get_free_space() {
    df -B1 / | awk 'NR==2 {print $4}'
}

# Record initial free space
INITIAL_FREE=$(get_free_space)
log "Starting Ubuntu cleanup..."
log "Initial free space: $(bytes_to_human "$INITIAL_FREE")"
echo ""

# ========== User Space Cleanup ==========
log "=== User Space Cleanup ==="

# 1. Empty Trash
log "Cleaning user trash..."
clean_dir_contents "$HOME/.local/share/Trash/files" "trash files"
clean_dir_contents "$HOME/.local/share/Trash/info" "trash info"

# 2. User Cache
log "Cleaning user cache..."
clean_dir_contents "$HOME/.cache" "user cache"

# 3. Thumbnail Cache
log "Cleaning thumbnail cache..."
clean_dir_contents "$HOME/.cache/thumbnails" "thumbnails"

# 4. Browser Caches (common browsers)
log "Cleaning browser caches..."
# Firefox
if [ -d "$HOME/.mozilla/firefox" ]; then
    for profile in "$HOME/.mozilla/firefox"/*.default*; do
        if [ -d "$profile/cache2" ]; then
            clean_dir_contents "$profile/cache2" "Firefox cache"
        fi
    done
fi

# Chrome/Chromium
clean_dir_contents "$HOME/.config/google-chrome/Default/Cache" "Chrome cache"
clean_dir_contents "$HOME/.config/chromium/Default/Cache" "Chromium cache"

# 5. Old config and cache files
log "Cleaning old temporary files..."
if $DRY_RUN; then
    log "[DRY-RUN] Would clean old files in /tmp"
else
    find /tmp -user "$USER" -type f -atime +7 -delete 2>/dev/null || true
fi

# 6. Pip cache
clean_dir_contents "$HOME/.cache/pip" "pip cache"

# 7. NPM cache
clean_dir_contents "$HOME/.npm/_cacache" "npm cache"

# 8. Yarn cache
clean_dir_contents "$HOME/.cache/yarn" "yarn cache"

# 9. Backup files and directories
log "Cleaning backup files and directories..."
BACKUP_PATTERNS=(
    "*_bk"
    "*_backup"
    "*.bak"
    "*.backup"
    "*.orig"
    "*~"
)

for pattern in "${BACKUP_PATTERNS[@]}"; do
    if $DRY_RUN; then
        found=$(find "$HOME" -name "$pattern" -not -path "$HOME/.local/share/Trash/*" 2>/dev/null | head -20)
        if [ -n "$found" ]; then
            log "[DRY-RUN] Would clean files matching '$pattern':"
            echo "$found" | while read -r f; do
                size=$(get_size "$f")
                echo "  $f ($size)"
            done
        fi
    else
        find "$HOME" -name "$pattern" -not -path "$HOME/.local/share/Trash/*" 2>/dev/null | while read -r f; do
            size=$(get_size "$f")
            log "Removing backup: $f ($size)"
            rm -rf "$f" 2>/dev/null || true
        done
    fi
done

# 10. VSCode extension backups
clean_path "$HOME/.vscode/extensions/extensions_bk" "VSCode extensions backup"
clean_path "$HOME/.vscode-server/extensions/extensions_bk" "VSCode server extensions backup"

echo ""

# ========== System Cleanup (requires sudo) ==========
if $CLEAN_ALL; then
    log "=== System Cleanup (requires sudo) ==="

    if [ "$EUID" -ne 0 ]; then
        log "Requesting sudo privileges for system cleanup..."
    fi

    # 1. APT cache
    log "Cleaning APT cache..."
    if $DRY_RUN; then
        log "[DRY-RUN] Would run: apt-get clean"
        log "[DRY-RUN] Would run: apt-get autoclean"
    else
        sudo apt-get clean -y
        sudo apt-get autoclean -y
    fi

    # 2. Remove orphaned packages
    log "Removing orphaned packages..."
    if $DRY_RUN; then
        log "[DRY-RUN] Would run: apt-get autoremove"
        sudo apt-get autoremove --dry-run 2>/dev/null | grep "^Remv" || log "No orphaned packages"
    else
        sudo apt-get autoremove -y
    fi

    # 3. Clean old kernels (keep current and one previous)
    log "Checking for old kernels..."
    CURRENT_KERNEL=$(uname -r)
    OLD_KERNELS=$(dpkg -l 'linux-image-*' 2>/dev/null | awk '/^ii/{print $2}' | grep -v "$CURRENT_KERNEL" | grep -v 'linux-image-generic' || true)
    if [ -n "$OLD_KERNELS" ]; then
        if $DRY_RUN; then
            log "[DRY-RUN] Would remove old kernels:"
            echo "$OLD_KERNELS"
        else
            log "Removing old kernels..."
            echo "$OLD_KERNELS" | xargs sudo apt-get purge -y
        fi
    else
        log "No old kernels to remove"
    fi

    # 4. Clean systemd journal logs (keep last 3 days)
    log "Cleaning systemd journal logs..."
    if $DRY_RUN; then
        log "[DRY-RUN] Would run: journalctl --vacuum-time=3d"
        sudo journalctl --disk-usage
    else
        sudo journalctl --vacuum-time=3d
    fi

    # 5. Clean old log files
    log "Cleaning old log files..."
    if $DRY_RUN; then
        log "[DRY-RUN] Would clean rotated logs in /var/log"
        find /var/log -name "*.gz" -o -name "*.old" -o -name "*.[0-9]" 2>/dev/null | head -20
    else
        sudo find /var/log -name "*.gz" -delete 2>/dev/null || true
        sudo find /var/log -name "*.old" -delete 2>/dev/null || true
        sudo find /var/log -name "*.[0-9]" -delete 2>/dev/null || true
    fi

    # 6. Clean /tmp (system-wide, older than 7 days)
    log "Cleaning old files in /tmp..."
    if $DRY_RUN; then
        log "[DRY-RUN] Would clean files older than 7 days in /tmp"
    else
        sudo find /tmp -type f -atime +7 -delete 2>/dev/null || true
    fi

    # 7. Clean snap cache
    if command -v snap &>/dev/null; then
        log "Cleaning snap cache..."
        if $DRY_RUN; then
            log "[DRY-RUN] Would remove old snap revisions"
            snap list --all | awk '/disabled/{print $1, $3}'
        else
            snap list --all | awk '/disabled/{print $1, $3}' | while read -r snapname revision; do
                sudo snap remove "$snapname" --revision="$revision" 2>/dev/null || true
            done
        fi
    fi

    echo ""
fi

# ========== Summary ==========
FINAL_FREE=$(get_free_space)
FREED=$((FINAL_FREE - INITIAL_FREE))

log "=== Cleanup Summary ==="
log "Final free space: $(bytes_to_human "$FINAL_FREE")"
if [ "$FREED" -gt 0 ]; then
    log "Space freed: $(bytes_to_human "$FREED")"
else
    log "Space freed: 0 (or space was used by other processes)"
fi

if $DRY_RUN; then
    echo ""
    log "This was a dry run. No files were deleted."
    log "Run without -n to actually clean up."
fi

log "Cleanup complete!"
