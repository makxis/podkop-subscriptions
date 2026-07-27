# Changelog

## 3.6.4

- `install-dnsproxy.sh` now supports apk, so it works on OpenWrt 25.12 and
  newer. It previously hardcoded opkg and aborted immediately on apk-based
  releases. The package manager is detected at runtime; the architecture comes
  from `DISTRIB_ARCH` first (identical on both branches) and falls back to
  `apk --print-arch` or `opkg print-architecture`. Fantastic Packages serves
  `index.json` on both branches, so it is used to probe the architecture; the
  LuCI package filename comes from `Packages.gz` for opkg and from that
  `index.json` version for apk, since apk's own index is binary. Local `.apk`
  files are installed with `--allow-untrusted --force-non-repository`, both of
  which apk requires for an unsigned package installed from a file.
  The installer carries its own version, bumped to 1.1.0, and both READMEs now
  state it.
- Verified the 3.6.3 fixes survive a real reboot on OpenWrt 24.10.3: cron is
  rebuilt without `@reboot` and crond logs no parse errors, `/etc/config` stays
  free of non-uci files, `reload_config` succeeds, and the procd `catchup`
  instance fires once five minutes after boot and exits without respawning.

## 3.6.3

- Moved the project's non-uci files out of `/etc/config`. Everything in that
  directory is parsed by uci, so a plain list of proxy links and a copy of
  podkop's config made every `uci` call log `Parse error`, and `reload_config`
  fail with `uci: Invalid argument` — which meant LuCI's Apply could stop short
  of resynchronizing cron. New locations:
  `/etc/config/podkop-local-links` -> `/etc/podkop-subscriptions/local-links`,
  `/etc/config/podkop.podkop-subscriptions.bak` ->
  `/etc/podkop-subscriptions/podkop.bak`. The installer migrates existing files
  and also relocates the stale `/etc/config/podkop-subs` from old versions; the
  updater still reads the legacy local-links path when the new one is absent.

- Moved the post-boot catch-up out of cron. BusyBox crond does not support
  `@reboot` and rejected the whole entry with `parse error at @reboot`, logging
  it on every crontab reload, so the boot catch-up never ran on OpenWrt. It now
  runs as a procd instance from `/etc/init.d/podkop_subscriptions`, with the
  delay configurable via `BOOT_CATCHUP_DELAY`.
- Fixed `install-dnsproxy.sh` writing an unparseable `/etc/config/dnsproxy`:
  a heredoc used literal `\t` sequences for indentation, so every option line
  began with a backslash and uci refused the file, leaving dnsproxy unable to
  start. Also moved the dnsproxy config backup to after package installation,
  so the rollback paths have something to restore on a fresh router.
- Documentation: corrected the claim that any `uci commit podkop_subscriptions`
  resynchronizes cron. The procd trigger fires on the `config.change` event,
  which `reload_config` emits (as LuCI's Apply does); a bare `uci commit` from
  the console does not.

## 3.6.2

- Fixed LuCI schedule changes not reaching `/etc/crontabs/root`: the JS form now runs `podkop-sub-cron-sync` through the real `Map.save()` path.
- Added `/etc/init.d/podkop_subscriptions` with a procd `config.change` trigger, so every `uci commit podkop_subscriptions` synchronizes cron regardless of whether the change came from LuCI, SSH, or another script.
- Made `podkop-sub-cron-sync` concurrency-safe and idempotent for duplicate LuCI/procd invocations.
- Cron synchronization now uses an atomic kernel `fcntl.flock`, eliminating the previous `mkdir` → PID-file race and stale lock directories.
- Added one common `fcntl.flock` inside `podkop-sub-updater.py` for scheduled updates, observer, catch-up, retry, and manual runs.
- `--observe-only` now quietly skips when another updater is active; normal and catch-up runs wait up to 300 seconds and return code 75 if the lock remains busy.
- Installer now installs, enables, backs up, and starts the procd trigger; uninstaller stops, disables, and removes it.
- Upgrade behavior still preserves `/etc/config/podkop_subscriptions`, local links, `state.json`, cron, and backups.

## 3.6.1

- Status summary now shows whether auto-update is actually applied in cron.
- Version bumped from 3.6 to 3.6.1.

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
- Updater: individual source download/format failures are now WARN; ERROR is emitted only when no section gets valid fresh keys.
- Updater/LuCI: source status ignores the local list; red ERROR is emitted only when external subscriptions produce no valid keys. Endpoint dedupe help now correctly says IP/domain + port.
- LuCI/status: status summary now reports whether configured schedules are actually applied in cron.
