# Podkop Subscriptions

[Русская документация](README_RU.md)

Add-on for Podkop that updates proxy links from subscriptions and can add a `Подписки` tab to the existing Podkop LuCI interface.

Podkop itself must be installed first.

## Tested configuration

- OpenWrt: `24.10.3`-`24.10.5`
- Podkop: `v0.7.17`
- LuCI App Podkop: `v0.7.17`
- Sing-box: `1.12.22`

## Install

Interactive install:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh
```

Core only:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --no-panel
```

Core and LuCI panel:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --with-panel
```

## Files

Main add-on config:

```text
/etc/config/podkop_subscriptions
```

Native Podkop config updated by the updater:

```text
/etc/config/podkop
```

Local manual proxy links:

```text
/etc/config/podkop-local-links
```

Runtime state:

```text
/etc/podkop-subscriptions/state.json
```

Manual run log:

```text
/tmp/podkop-sub-updater.log
```

## Core-only configuration

Edit:

```sh
vi /etc/config/podkop_subscriptions
```

Example:

```text
config subscription_group 'main'
    option enabled '1'
    option target_section 'main'

    list source 'https://example.com/subscription-1'
    list source 'https://example.com/subscription-2'

    option use_local_links '1'

    option regex ''
    option match_mode 'ifnotmatch'
    option on_empty 'skip'
    option proxy_type 'urltest'

    option max_links '50'
    option max_latency_ms '500'
    option force_cleanup '0'
    option dedupe_sni_rotation '1'

config subscription_schedule 'main_0300'
    option enabled '1'
    option hour '3'
    option minute '0'
    option jitter '1800'
    option force '0'
```

`option use_local_links '1'` enables `/etc/config/podkop-local-links`. Adding `file:///etc/config/podkop-local-links` manually is no longer needed.

After editing:

```sh
/usr/bin/podkop-sub-cron-sync
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop --force
```

## Cron without LuCI

Recommended:

```sh
/usr/bin/podkop-sub-cron-sync
cat /etc/crontabs/root
/etc/init.d/cron restart
```

Manual example:

```text
0 * * * * /usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop 2>&1 | logger -t podkop-sub-health
0 3 * * * /usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop 2>&1 | logger -t podkop-updater-cron
```

## Logs

Logs are sanitized. Subscription URLs, proxy links, UUIDs, `sni`, `pbk`, `sid`, and tokens are not printed to stdout/syslog/LuCI logs. Sources are shown as `source 1`, `source 2`, or `local list`.

## Manual commands

```sh
/usr/bin/podkop-sub-updater.py --version
/usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop --force
/usr/bin/podkop-sub-run-now
/usr/bin/podkop-sub-run-now --status
```
