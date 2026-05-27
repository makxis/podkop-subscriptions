# Podkop Subscriptions

[README_RU](README_RU.md)

Add-on for Podkop that updates proxy links from subscriptions and optionally adds a `Подписки` tab to the existing Podkop LuCI interface.

This project is installed separately from Podkop. Podkop itself must be installed first.

## Tested configuration

This version was tested on:

- OpenWrt: `24.10.3`-`24.10.5`
- Podkop: `v0.7.17`
- LuCI App Podkop: `v0.7.17`
- Sing-box: `1.12.22`

Older or newer versions may work, but are not guaranteed. The LuCI overlay depends on the existing Podkop LuCI file structure and RPC/ACL behavior.

## What it installs

- `/usr/bin/podkop-sub-updater.py` — subscription updater and key maintenance script.
- `/usr/bin/podkop-sub-cron-sync` — cron schedule synchronizer.
- `/usr/bin/podkop-sub-run-now` — background manual updater launcher.
- `/usr/share/podkop-subscriptions/VERSION` — installed add-on version.
- `/etc/podkop-subscriptions/state.json` — internal state file with key status counters.
- `/etc/config/podkop-local-links` — local user-managed proxy links protected from automatic cleanup.
- Optional LuCI overlay with the `Подписки` tab.

## Installation

Interactive install:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh
```

Core updater only, without LuCI panel:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --no-panel
```

Core updater and LuCI panel:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --with-panel
```

## Initial setup

Before configuring subscriptions, open the existing Podkop page in LuCI and create at least one Podkop section with at least one valid proxy key.

Then press **Save & Apply** and wait until Podkop applies the configuration.

After that, open the `Подписки` tab and configure:

- target Podkop section;
- subscription source URL or local source;
- regex filter, if needed;
- filter mode;
- proxy group type: `urltest` or `selector`;
- update schedule.

Press **Save & Apply** again, wait until Podkop applies the settings, then run the updater from LuCI or SSH.

## Version check

```sh
/usr/bin/podkop-sub-updater.py --version
/usr/bin/podkop-sub-run-now --version
cat /usr/share/podkop-subscriptions/VERSION
```

The LuCI `Подписки` tab also shows the add-on version at the bottom.

## Manual commands

Passive key status observation, without changing Podkop config:

```sh
/usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop
```

Manual maintenance run:

```sh
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop --config /etc/config/podkop --force
```

Manual background run:

```sh
/usr/bin/podkop-sub-run-now
tail -n 80 /tmp/podkop-sub-updater.log
```

Cron synchronization:

```sh
/usr/bin/podkop-sub-cron-sync
cat /etc/crontabs/root
```

## Update logic

Subscriptions are used only as a source of new proxy links. They do not fully replace the Podkop section.

Behavior:

- new links from subscriptions are added to the existing section;
- existing links are not removed just because they disappeared from a subscription;
- if a subscription cannot be loaded or returns zero valid links, the current Podkop section is kept unchanged;
- every hour, `--observe-only` reads Podkop URLTest state and updates `fail_count` in `/etc/podkop-subscriptions/state.json`;
- working links reset `fail_count` to `0`;
- links shown as `N/A` or without delay/history increment `fail_count` by `1`;
- links are removed only during scheduled maintenance runs and only when `fail_count >= 72`;
- links from `/etc/config/podkop-local-links` are protected from automatic deletion;
- `--observe-only` does not change `/etc/config/podkop` and does not restart Podkop;
- Podkop is restarted only when keys were added, removed, or duplicates were cleaned.

If the router is powered off, counters do not grow. After the router is powered on again, counting continues from the previous state.

## Subscription source retries

HTTP/HTTPS subscription sources are loaded with fixed retries:

- 3 attempts;
- 45 seconds timeout per attempt.

If all attempts fail, the source is considered unavailable, the error is logged, and current keys are kept.

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

Remove local links file too:

```sh
sh /tmp/podkop-sub-uninstall.sh --purge-config
```

## Security

Do not commit real router configs or backups to GitHub:

- `/etc/config/podkop`;
- `/etc/config/podkop-local-links` with real links;
- `/etc/crontabs/root`;
- OpenWrt backups;
- router archives;
- real `vless://`, `ss://`, `trojan://`, `hy2://`, `hysteria2://`, or `socks://` links;
- UUIDs, keys, SNI values, `pbk`, `sid`, subscription tokens, or other private parameters.

The repository should contain only scripts, LuCI overlay files, and sanitized examples.
