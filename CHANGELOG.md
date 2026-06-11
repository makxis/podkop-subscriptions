# Changelog

## 3.6

- Added safe subscription validation before writing links to Podkop:
  - Python format checks;
  - temporary sing-box config generation;
  - batch `sing-box check`;
  - protection from overwriting a working section when no valid links remain.
- Added `dedupe_endpoint_host` to collapse IP/domain rotations when needed.
- Added normalization of missing `type` for `vless://` and `trojan://` links to `type=tcp`.
- Added fixed subscription request profile:
  - `User-Agent: v2raytun/android`;
  - fixed Android device headers;
  - fixed `X-HWID` for subscription requests.
- Added `--fail-count` command for viewing fail counters without printing proxy links.
- Added cleaner Russian one-line summary logs.
- Added terminal color accents for direct CLI runs; syslog and LuCI logs remain plain text.
- Added `/usr/bin/podkop-sub-clean-temp` to remove temporary installation files safely.
- Changed LuCI Regex field to a two-line resizable textarea.
- Added clear Regex help text in LuCI, including the `xhttp|#.*(...)` pattern.
- Made the LuCI panel visible by default again; hidden installation remains an internal install mode.
- Reworked README files for clearer installation, filtering, validation, and troubleshooting guidance.
- Preserves config, local links, state, cron, and backups during upgrade.

## 3.5

- Added safer migration and upgrade behavior.
- Creates upgrade backup in `/root/podkop-subscriptions-upgrade-backup-*.tar.gz`.
- Preserves `/etc/config/podkop_subscriptions`, `/etc/config/podkop-local-links`, and `/etc/podkop-subscriptions/state.json`.
- Recreates Podkop Subscriptions cron lines.
- Removes old embedded web UI leftovers.

## 3.4

- Added catch-up after long router downtime.
- Added `@reboot sleep 300` catch-up check.
- Added 30-minute retry mode after failed catch-up.
- Added top status block in LuCI.
- Added first-run hint and clearer emergency status.

## 3.3

- Moved LuCI interface out of the native Podkop page.
- Added standalone LuCI app.
- Stopped patching or replacing native Podkop LuCI files.

## 3.1

- Moved configuration to `/etc/config/podkop_subscriptions`.
- Added local links support.
- Added SNI rotation deduplication.
- Added key count and latency-based filtering.
- Added live updater log in LuCI.
- Updater: endpoint dedupe now uses IP/domain + port, so the same host on different ports is preserved.
