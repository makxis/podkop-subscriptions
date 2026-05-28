# Podkop Subscriptions

Add-on for [Podkop](https://github.com/itdoginfo/podkop). It loads proxy links from subscriptions, writes them into a selected Podkop section, and can automatically remove dead, slow, or outdated key variants.

This project is installed separately from Podkop. Podkop itself must be installed and configured first.

[Русская документация](README_RU.md)

## Tested configuration

Tested on:

- OpenWrt: `24.10.3`-`24.10.6`
- Podkop: `v0.7.17`
- LuCI App Podkop: `v0.7.17`
- Sing-box: `1.12.22`

Other versions may work, but compatibility is not guaranteed.

## Features

- Loads proxy links from HTTP/HTTPS subscriptions.
- Supports a local key list from `/etc/config/podkop-local-links`.
- Supports `vless://`, `ss://`, `trojan://`, `socks4://`, `socks4a://`, `socks5://`, `hy2://`, `hysteria2://`.
- Supports plain-text and base64 subscriptions.
- Filters links using regex.
- Adds only new links and does not clear the section when a subscription fails.
- Tracks dead keys using `fail_count`.
- Can limit the maximum number of keys in a section.
- Can remove high-latency keys.
- Can collapse SNI rotations when a provider changes only the `sni` parameter.
- Can run from SSH only, without the LuCI tab.
- Optionally adds a `Подписки` tab to the Podkop LuCI page.

## Installation

Interactive install:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh
```

Core script only, without LuCI tab:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --no-panel
```

Core script and LuCI tab:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --with-panel
```

During interactive installation, the setup wizard can create a working configuration: choose a Podkop section, add a subscription URL, enable local links, configure limits, and set a schedule.

## Files

### `/etc/config/podkop_subscriptions`

Main Podkop Subscriptions configuration file.

It contains subscription sources, target Podkop section, filters, limits, SNI deduplication, and schedule settings.

### `/etc/config/podkop`

Native Podkop configuration.

The updater reads existing Podkop sections from this file and writes the final proxy list into the selected section.

### `/etc/config/podkop-local-links`

Local user-managed proxy links.

These links are protected from automatic deletion. Enable them with:

```text
option use_local_links '1'
```

### `/etc/podkop-subscriptions/state.json`

Updater runtime state: `fail_count`, recently removed keys, and technical key identifiers.

Normally, this file should not be edited manually.

### `/tmp/podkop-sub-updater.log`

Log of the last manual run started through LuCI or `/usr/bin/podkop-sub-run-now`.

## Configuration without LuCI

Open the main configuration file:

```sh
vi /etc/config/podkop_subscriptions
```

Minimal example:

```text
config subscription_group 'main'
    option enabled '1'

    # Podkop section to write keys into
    option target_section 'main'

    # Subscription URL
    list source 'https://example.com/subscription'

    # Use /etc/config/podkop-local-links
    option use_local_links '1'

    # Empty regex means no filter
    option regex ''
    option match_mode 'ifnotmatch'
    option on_empty 'skip'

    # Podkop group type
    option proxy_type 'urltest'

    # Limits. 0 means disabled.
    option max_links '50'
    option max_latency_ms '500'

    # Aggressive cleanup
    option force_cleanup '0'

    # Collapse keys that differ only by sni
    option dedupe_sni_rotation '1'


config subscription_schedule 'main_0300'
    option enabled '1'
    option hour '3'
    option minute '0'
    option jitter '1800'
    option force '0'
```

After editing, apply cron:

```sh
/usr/bin/podkop-sub-cron-sync
/etc/init.d/cron restart
```

Run updater manually:

```sh
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop --force
```

## LuCI configuration

Open Podkop and switch to the `Подписки` tab.

A subscription group contains:

- target Podkop section;
- subscription URL;
- local key list toggle;
- regex filter;
- group type: `urltest` or `selector`;
- maximum key count;
- maximum ping;
- forced cleanup;
- SNI deduplication;
- update schedule.

After changing settings, press **Save & Apply**.

## Group options

### `target_section`

Podkop section where the updater writes the final key list.

```text
option target_section 'main'
```

### `source`

Subscription URL. Multiple `list source` lines are allowed.

```text
list source 'https://example.com/subscription-1'
list source 'https://example.com/subscription-2'
```

### `use_local_links`

Adds keys from `/etc/config/podkop-local-links`.

```text
option use_local_links '1'
```

`1` — use local links.  
`0` — do not use local links.

### `regex`

Filter by key name or key string.

Empty value means no filtering.

```text
option regex 'Netherlands|Нидерланды|NL'
```

### `match_mode`

Filtering mode.

```text
option match_mode 'ifmatch'
```

`ifmatch` — keep matching links only.  
`ifnotmatch` — exclude matching links.

### `on_empty`

What to do if filtering leaves no links.

```text
option on_empty 'skip'
```

`skip` — skip source.  
`all` — use all source links.

`skip` is usually safer.

### `proxy_type`

Podkop group type:

```text
option proxy_type 'urltest'
```

Allowed values:

- `urltest`
- `selector`

### `max_links`

Maximum number of keys in the target section.

```text
option max_links '50'
```

`0` or empty means no limit.

If adding new links would exceed the limit, the updater removes the worst unprotected keys first and adds only as many new links as fit into the freed slots.

### `max_latency_ms`

Maximum ping based on Podkop URLTest data.

```text
option max_latency_ms '500'
```

`0` or empty means no ping-based cleanup.

This option is used during trimming and forced cleanup.

### `force_cleanup`

Aggressive cleanup.

```text
option force_cleanup '0'
```

`1` — the updater may remove keys with `fail_count >= 2` and keys above `max_latency_ms`, even if `max_links` is not exceeded.  
`0` — normal mode.

Use carefully.

### `dedupe_sni_rotation`

Collapse SNI rotations.

```text
option dedupe_sni_rotation '1'
```

If a new subscription key differs from an old key only by the `sni` parameter, the old variant is replaced by the new one.

Comparison uses the technical key body. The key name is not used.

Local keys from `/etc/config/podkop-local-links` are not replaced.

## Local keys

Open:

```sh
vi /etc/config/podkop-local-links
```

Add one proxy link per line:

```text
vless://...
trojan://...
ss://...
```

To use these keys together with subscriptions, set this in `/etc/config/podkop_subscriptions`:

```text
option use_local_links '1'
```

## Cron

### Recommended method

Schedule is stored in `/etc/config/podkop_subscriptions`.

After changing it, run:

```sh
/usr/bin/podkop-sub-cron-sync
/etc/init.d/cron restart
```

Check current cron jobs:

```sh
cat /etc/crontabs/root
```

### Manual cron setup

Hourly passive key status observation:

```text
0 * * * * /usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop 2>&1 | logger -t podkop-sub-health
```

Daily subscription update at 03:00:

```text
0 3 * * * /usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop 2>&1 | logger -t podkop-updater-cron
```

After manual cron editing:

```sh
/etc/init.d/cron restart
```

## Manual commands

Show installed version:

```sh
/usr/bin/podkop-sub-updater.py --version
```

Update key statistics without changing Podkop config or restarting services:

```sh
/usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop
```

Run subscription update manually:

```sh
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop --force
```

Start update in the background, same as LuCI:

```sh
/usr/bin/podkop-sub-run-now
```

Show background run status:

```sh
/usr/bin/podkop-sub-run-now --status
```

Sync cron with configuration:

```sh
/usr/bin/podkop-sub-cron-sync
```

## Update process

1. The updater reads `/etc/config/podkop_subscriptions`.
2. Loads subscription sources.
3. Adds local links if `use_local_links` is enabled.
4. Applies regex filter.
5. Removes duplicates.
6. Collapses SNI rotations if `dedupe_sni_rotation` is enabled.
7. Compares new links with the current Podkop section.
8. Adds only missing links.
9. Removes links according to `fail_count`, `max_links`, `max_latency_ms`, and `force_cleanup`.
10. If the final list changed, writes it to `/etc/config/podkop` and restarts Podkop.

If a subscription fails or filtering returns no valid links, the current Podkop section is not cleared.

## Podkop manual latency test note

When there are many keys, Podkop's manual latency test button may not finish checking the full list. Some keys may temporarily show `N/A`.

Podkop automatic URLTest continues to update on its own schedule. For automatic cleanup, accumulated `observe-only` statistics are more important than a single manual latency test.

## Uninstall

```sh
wget -O /tmp/podkop-sub-uninstall.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/uninstall.sh && sh /tmp/podkop-sub-uninstall.sh
```

Remove local links too:

```sh
sh /tmp/podkop-sub-uninstall.sh --purge-config
```
