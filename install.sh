#!/bin/sh
set -eu

APP_VERSION="2.6"
REPO="${REPO:-makxis/podkop-subscriptions}"
BRANCH="${BRANCH:-main}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/${REPO}/${BRANCH}}"
PANEL_MODE="ask"
SOURCE_MODE="auto"

SCRIPT_PATH="$0"
case "$SCRIPT_PATH" in
  */*) SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)" ;;
  *) SCRIPT_DIR="$(pwd)" ;;
esac

for arg in "$@"; do
  case "$arg" in
    --with-panel) PANEL_MODE="yes" ;;
    --no-panel|--core-only) PANEL_MODE="no" ;;
    --local) SOURCE_MODE="local" ;;
    --remote) SOURCE_MODE="remote" ;;
    --repo=*) REPO="${arg#--repo=}"; RAW_BASE="https://raw.githubusercontent.com/${REPO}/${BRANCH}" ;;
    --branch=*) BRANCH="${arg#--branch=}"; RAW_BASE="https://raw.githubusercontent.com/${REPO}/${BRANCH}" ;;
    --raw-base=*) RAW_BASE="${arg#--raw-base=}" ;;
    -h|--help)
      echo "Usage: sh install.sh [--with-panel|--no-panel] [--local|--remote] [--repo=owner/repo] [--branch=main]"
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

say() { printf '%s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

need_root() {
  [ "$(id -u)" = "0" ] || fail "run as root"
}

backup_file() {
  file="$1"
  [ -e "$file" ] || return 0
  ts="$(date +%Y%m%d-%H%M%S 2>/dev/null || echo now)"
  cp -fp "$file" "${file}.bak.${ts}"
}

copy_local() {
  src="$1"
  dest="$2"
  [ -f "$src" ] || fail "local file not found: $src"
  mkdir -p "$(dirname "$dest")"
  cp -f "$src" "$dest"
}

fetch_remote() {
  url="$1"
  dest="$2"
  tmp="${dest}.tmp.$$"

  mkdir -p "$(dirname "$dest")"
  if command -v wget >/dev/null 2>&1; then
    wget -qO "$tmp" "$url" || { rm -f "$tmp"; fail "download failed: $url"; }
  elif command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$tmp" || { rm -f "$tmp"; fail "download failed: $url"; }
  else
    fail "wget or curl is required"
  fi

  mv "$tmp" "$dest"
}

install_file() {
  rel="$1"
  dest="$2"
  local_src="$SCRIPT_DIR/$rel"

  if [ "$SOURCE_MODE" = "local" ] || { [ "$SOURCE_MODE" = "auto" ] && [ -f "$local_src" ]; }; then
    copy_local "$local_src" "$dest"
  else
    fetch_remote "$RAW_BASE/$rel" "$dest"
  fi
}

ensure_python() {
  if command -v python3 >/dev/null 2>&1; then
    return 0
  fi

  say "python3 not found, trying to install python3-light..."
  if command -v opkg >/dev/null 2>&1; then
    opkg update
    opkg install python3-light ca-bundle || opkg install python3 ca-bundle || fail "cannot install python3"
  elif command -v apk >/dev/null 2>&1; then
    apk update
    apk add python3-light ca-bundle || apk add python3 ca-bundle || fail "cannot install python3"
  else
    fail "python3 is required, but neither opkg nor apk was found"
  fi
}

install_core() {
  if [ "$SOURCE_MODE" = "remote" ]; then
    say "Installing Podkop subscription updater from: $RAW_BASE"
  else
    say "Installing Podkop subscription updater from local package: $SCRIPT_DIR"
  fi

  ensure_python

  backup_file /usr/bin/podkop-sub-updater.py
  backup_file /usr/bin/podkop-sub-cron-sync
  backup_file /usr/bin/podkop-sub-run-now

  install_file "podkop-sub-updater.py" /usr/bin/podkop-sub-updater.py
  install_file "podkop-sub-cron-sync" /usr/bin/podkop-sub-cron-sync
  install_file "podkop-sub-run-now" /usr/bin/podkop-sub-run-now
  mkdir -p /usr/share/podkop-subscriptions
  if [ -f "$SCRIPT_DIR/VERSION" ]; then
    install_file "VERSION" /usr/share/podkop-subscriptions/VERSION
  else
    printf '%s\n' "$APP_VERSION" > /usr/share/podkop-subscriptions/VERSION
  fi
  chmod 0755 /usr/bin/podkop-sub-updater.py /usr/bin/podkop-sub-cron-sync /usr/bin/podkop-sub-run-now

  touch /etc/config/podkop-local-links
  chmod 0600 /etc/config/podkop-local-links || true

  /usr/bin/podkop-sub-cron-sync || true
  say "Core installed: /usr/bin/podkop-sub-updater.py, /usr/bin/podkop-sub-cron-sync and /usr/bin/podkop-sub-run-now"
  say "Podkop Subscriptions version: $APP_VERSION"
}

install_panel() {
  if [ ! -f /www/luci-static/resources/view/podkop/podkop.js ]; then
    warn "base LuCI app podkop was not found at /www/luci-static/resources/view/podkop/podkop.js"
    warn "install podkop/luci-app-podkop separately first, then rerun with --with-panel"
    return 0
  fi

  say "Installing optional LuCI subscriptions panel..."

  backup_file /www/luci-static/resources/view/podkop/podkop.js
  backup_file /www/luci-static/resources/view/podkop/main.js
  backup_file /www/luci-static/resources/view/podkop/subscriptions.js
  backup_file /usr/share/rpcd/acl.d/luci-app-podkop.json

  install_file "luci/www/luci-static/resources/view/podkop/podkop.js" /www/luci-static/resources/view/podkop/podkop.js
  install_file "luci/www/luci-static/resources/view/podkop/main.js" /www/luci-static/resources/view/podkop/main.js
  install_file "luci/www/luci-static/resources/view/podkop/subscriptions.js" /www/luci-static/resources/view/podkop/subscriptions.js
  install_file "luci/usr/share/rpcd/acl.d/luci-app-podkop.json" /usr/share/rpcd/acl.d/luci-app-podkop.json

  rm -f /tmp/luci-indexcache 2>/dev/null || true
  rm -rf /tmp/luci-modulecache 2>/dev/null || true

  /etc/init.d/rpcd restart >/dev/null 2>&1 || true
  /etc/init.d/uhttpd restart >/dev/null 2>&1 || true

  say "LuCI panel installed. Reopen the Podkop page in LuCI."
}

ask_panel() {
  if [ "$PANEL_MODE" = "yes" ]; then
    return 0
  fi
  if [ "$PANEL_MODE" = "no" ]; then
    return 1
  fi

  printf 'Install optional LuCI panel for subscriptions? [Y/n]: '
  read ans || ans=""
  case "$ans" in
    n|N|no|NO|No) return 1 ;;
    *) return 0 ;;
  esac
}

need_root
install_core
if ask_panel; then
  install_panel
else
  say "Panel installation skipped. You can install it later: sh install.sh --with-panel"
fi

say ""
say "Done."
say ""
say "Version: $APP_VERSION"
say ""
say "New logic:"
say "  - subscription updater adds only new keys; it does not wipe missing keys;"
say "  - hourly observe-only reads Podkop URLTest state and updates fail_count;"
say "  - keys with fail_count >= 72 are removed only during scheduled updater runs;"
say "  - Podkop restarts only when keys were added/removed or duplicates were cleaned."
say ""
say "Manual tests:"
say "  /usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop"
say "  /usr/bin/podkop-sub-run-now"
say "  tail -n 80 /tmp/podkop-sub-updater.log"
say "  /usr/bin/podkop-sub-updater.py --subs /etc/config/podkop --config /etc/config/podkop --force"
