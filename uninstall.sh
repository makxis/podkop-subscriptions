#!/bin/sh
set -eu

PURGE_CONFIG="0"
for arg in "$@"; do
  case "$arg" in
    --purge-config) PURGE_CONFIG="1" ;;
    -h|--help)
      echo "Usage: sh uninstall.sh [--purge-config]"
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

say() { printf '%s\n' "$*"; }

if [ -x /etc/init.d/podkop_subscriptions ]; then
  /etc/init.d/podkop_subscriptions stop >/dev/null 2>&1 || true
  /etc/init.d/podkop_subscriptions disable >/dev/null 2>&1 || true
fi
rm -f /etc/init.d/podkop_subscriptions

CRON_FILE=/etc/crontabs/root
TAG="# podkop-sub-updater"
UPDATER=/usr/bin/podkop-sub-updater.py
if [ -f "$CRON_FILE" ]; then
  tmp="${CRON_FILE}.tmp.$$"
  grep -v "$TAG" "$CRON_FILE" | grep -v "$UPDATER" > "$tmp" || true
  mv "$tmp" "$CRON_FILE"
  /etc/init.d/cron restart >/dev/null 2>&1 || true
fi

rm -f /usr/bin/podkop-sub-updater.py /usr/bin/podkop-sub-cron-sync /usr/bin/podkop-sub-run-now \
  /usr/bin/podkop-sub-clean-temp
# Копии *.bak.<дата>, которые прежние версии установщика клали рядом с
# каждым нашим файлом при обновлении.
for f in /usr/bin/podkop-sub-updater.py /usr/bin/podkop-sub-cron-sync /usr/bin/podkop-sub-run-now \
  /etc/init.d/podkop_subscriptions /www/luci-static/resources/view/podkop/subscriptions.js \
  /usr/share/luci/menu.d/luci-app-podkop-subscriptions.json \
  /usr/share/rpcd/acl.d/luci-app-podkop-subscriptions.json; do
  rm -f "$f".bak.* 2>/dev/null || true
done
rm -rf /usr/share/podkop-subscriptions
rm -f /www/luci-static/resources/view/podkop/subscriptions.js
rm -rf /www/luci-static/resources/view/podkop_subscriptions
rm -f /usr/share/luci/menu.d/luci-app-podkop-subscriptions.json
rm -f /usr/share/rpcd/acl.d/luci-app-podkop-subscriptions.json
rm -rf /tmp/podkop-sub-updater.lock /tmp/podkop-sub-cron-sync.lock
rm -f /tmp/podkop-sub-updater.flock /tmp/podkop-sub-updater.log /tmp/podkop-sub-updater.status

if [ "$PURGE_CONFIG" = "1" ]; then
  rm -f /etc/config/podkop-local-links /etc/config/podkop-subs /etc/config/podkop.podkop-subscriptions.bak
  rm -f /etc/config/podkop_subscriptions
  rm -rf /etc/podkop-subscriptions
fi

rm -f /tmp/luci-indexcache 2>/dev/null || true
rm -rf /tmp/luci-modulecache 2>/dev/null || true
/etc/init.d/rpcd restart >/dev/null 2>&1 || true
/etc/init.d/uhttpd restart >/dev/null 2>&1 || true

say "Removed podkop subscriptions updater/webui overlay."
