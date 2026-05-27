# Podkop Subscriptions + optional LuCI panel

[README_RU](README_RU.md)

This repository contains an add-on for Podkop that adds subscription-based proxy link updates and an optional LuCI tab inside the existing Podkop LuCI page.

The add-on is installed separately from Podkop. It does **not** install Podkop itself.

## Tested configuration

This version was tested on:

- OpenWrt: `24.10.5`
- Podkop: `v0.7.17`
- LuCI App Podkop: `v0.7.17`
- Sing-box: `1.12.22`

Older or newer versions may work, but are not guaranteed. The LuCI overlay depends on the existing Podkop LuCI file structure and RPC/ACL behavior.

## What it does

- Installs `podkop-sub-updater.py` to `/usr/bin/podkop-sub-updater.py`.
- Installs `podkop-sub-cron-sync` to `/usr/bin/podkop-sub-cron-sync`.
- Installs `podkop-sub-run-now` to `/usr/bin/podkop-sub-run-now` for background manual launches from LuCI.
- Uses `/etc/config/podkop` as the default UCI config for subscription groups and target Podkop sections.
- Optionally overlays the existing Podkop LuCI app with a new `Подписки` tab.
- Stores internal state in `/etc/podkop-subscriptions/state.json`.
- Keeps private proxy links out of GitHub. Manual/local proxy links should be stored on the router in `/etc/config/podkop-local-links`.

## One-line install

Interactive install:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh
```

Core updater only, without LuCI panel:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --no-panel
```

Core updater + LuCI panel:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --with-panel
```

## After installation

Before configuring subscriptions, open the existing Podkop page in LuCI and create at least one Podkop section with at least one valid proxy key. Then press **Save & Apply** and wait until Podkop restarts.

After that, open the `Подписки` tab and configure:

- target Podkop section;
- subscription source URL or local source;
- regex filter, if needed;
- filter mode;
- proxy group type: `urltest` or `selector`;
- schedule.

Press **Save & Apply** again, wait until Podkop applies the settings, then run the updater from LuCI or from SSH.

## Manual commands

Show installed version:

```sh
/usr/bin/podkop-sub-updater.py --version
/usr/bin/podkop-sub-run-now --version
cat /usr/share/podkop-subscriptions/VERSION
```

Passive health observation, without changing Podkop config:

```sh
/usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop
```

Manual maintenance run:

```sh
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop --config /etc/config/podkop --force
```

Manual background run, same as LuCI button:

```sh
/usr/bin/podkop-sub-run-now
tail -n 80 /tmp/podkop-sub-updater.log
```

Sync cron entries:

```sh
/usr/bin/podkop-sub-cron-sync
cat /etc/crontabs/root
```

## Stateful update logic

Starting from the stateful version, subscriptions are not used as a full source of truth for overwriting the Podkop section.

The logic is:

- subscriptions only add new proxy links;
- existing proxy links are not removed just because they disappeared from a subscription;
- if a subscription cannot be loaded or returns zero valid links, the current Podkop section is not cleared;
- each hour, `--observe-only` reads Podkop URLTest state and updates `fail_count` in `/etc/podkop-subscriptions/state.json`;
- a working key resets `fail_count` to `0`;
- a key shown as `N/A` or without delay/history increments `fail_count` by `1`;
- keys are removed only during scheduled maintenance runs and only when `fail_count >= 72`;
- links from `/etc/config/podkop-local-links` are treated as user-managed local links and are protected from automatic deletion;
- during the day, `--observe-only` does not change `/etc/config/podkop` and does not restart Podkop;
- Podkop restarts only when keys were added, removed, or duplicates were cleaned.

This avoids accidental deletion during temporary DNS, mobile coverage, whitelist, or connectivity problems.

## Slow or unavailable subscription source

HTTP/HTTPS subscription sources are loaded with fixed retries:

- 3 attempts;
- 45 seconds timeout per attempt.

These values are intentionally not exposed as runtime options. If all attempts fail, the source is considered unavailable, the error is logged, and current working keys are kept.

## LuCI manual updater button

The `Запустить updater` button runs `/usr/bin/podkop-sub-run-now`, which starts the updater in the background and immediately returns a response to LuCI. This prevents LuCI XHR timeout when a subscription source is slow.

Manual run log:

```sh
/tmp/podkop-sub-updater.log
```

View from SSH:

```sh
tail -n 80 /tmp/podkop-sub-updater.log
```

If an updater run is already active, another LuCI click is skipped using `/tmp/podkop-sub-updater.lock`.

## Local archive install

If the archive is already unpacked on OpenWrt:

```sh
cd /tmp/podkop-subscriptions-clean-v2.6
sh install.sh --local --no-panel
sh install.sh --local --with-panel
```

When run from an unpacked archive, `install.sh` uses local files by default. Use `--remote` to force downloading from GitHub.

## Uninstall

```sh
wget -O /tmp/podkop-sub-uninstall.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/uninstall.sh && sh /tmp/podkop-sub-uninstall.sh
```

To also remove `/etc/config/podkop-local-links`:

```sh
sh /tmp/podkop-sub-uninstall.sh --purge-config
```

## Security note

Do not commit real router configs or backups to GitHub:

- `/etc/config/podkop`
- `/etc/config/podkop-local-links` with real links
- `/etc/crontabs/root`
- OpenWrt backups
- router archives
- real `vless://`, `ss://`, `trojan://`, `hy2://`, `hysteria2://`, or `socks://` links
- UUIDs, private/public keys, SNI values, `pbk`, `sid`, or subscription tokens

The repository should contain only scripts, LuCI overlay files, and sanitized examples.
