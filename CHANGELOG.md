# Changelog

## 3.5

- Added migration from old v3.0/v3.1 configuration layout.
- Creates upgrade backup in `/root/podkop-subscriptions-upgrade-backup-*.tar.gz`.
- Migrates legacy `subscription_group` and `subscription_schedule` from `/etc/config/podkop` when `/etc/config/podkop_subscriptions` is missing.
- Removes old service sections from `/etc/config/podkop`.
- Preserves `/etc/config/podkop-local-links`.
- Preserves `/etc/podkop-subscriptions/state.json`.
- Recreates Podkop Subscriptions cron lines.
- Removes old embedded web UI.
- Uses standalone LuCI page: `Services → Подписки Podkop`.
- Keeps normal status short.
- Shows detailed source diagnostics only on emergency.
- Fixes false emergency when update succeeded but observe/URLTest has not collected statistics yet.

## 3.4

- Added catch-up after long router downtime.
- Added `@reboot sleep 300` catch-up check.
- Added 30-minute retry mode after failed catch-up.
- Added top status block in LuCI.
- Added unsupported subscription format detection.
- Added first-run hint.

## 3.3

- Moved LuCI interface out of native Podkop page.
- Added standalone LuCI app.
- Stopped patching/replacing native Podkop `podkop.js` and `main.js`.

## 3.1

- Moved configuration to `/etc/config/podkop_subscriptions`.
- Added local links toggle.
- Added SNI rotation deduplication.
- Added key limit and latency-based filtering.
- Added live updater log in LuCI.
