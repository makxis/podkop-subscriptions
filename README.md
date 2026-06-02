# Podkop Subscriptions

Add-on for Podkop. It loads proxy links from subscriptions, writes the final list into a selected Podkop section, and keeps the list up to date.

Starting with version `3.3`, the web UI is not embedded into the native Podkop page. It is installed as a separate LuCI page:

```text
Services → Подписки Podkop
```

Podkop remains separate. Podkop Subscriptions only reads Podkop/URLTest status, updates the proxy list, and writes the final result into `/etc/config/podkop`.

## Tested configuration

```text
OpenWrt: 24.10.3–24.10.6; 25.12.4
Podkop: v0.7.17–v0.7.19
LuCI App Podkop: v0.7.17–v0.7.19
Sing-box: 1.12.17; 1.12.22
```

## Installation

Interactive install:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh
```

Install without LuCI:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --no-panel
```

Install with LuCI:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --with-panel
```


## Main files

```text
/etc/config/podkop_subscriptions
/etc/config/podkop
/etc/config/podkop-local-links
/etc/podkop-subscriptions/state.json
/tmp/podkop-sub-updater.log
```

## LuCI

Open:

```text
Services → Подписки Podkop
```

After changing settings, press **Save & Apply**. This only saves settings. To load subscriptions for the first time, click **Run updater** at the bottom of the page.

## Regex filter

Use `|` as “or”:

```text
option regex 'Netherlands|Нидерланды|NL'
option regex 'Finland|Финляндия|FI'
option regex 'Torrents Free|Messengers'
```

Comma is not an alternative separator.

## Status

Normal status is short:

```text
Состояние: OK — обновлено: 2026-05-31 23:49, источники: 3/3, ключей в секции: 75, рабочих: нет данных, удалено: 0, локальных: 2.
```

Errors are verbose only when there is a real emergency.

## Catch-up after downtime

Five minutes after boot, the updater checks whether the last successful subscription update is older than 24 hours. If it is stale, subscriptions are updated immediately. If the update fails or produces no valid keys, retry mode runs every 30 minutes until a successful update.

## Manual commands

```sh
/usr/bin/podkop-sub-updater.py --version
/usr/bin/podkop-sub-updater.py --status-summary
/usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop --force
/usr/bin/podkop-sub-run-now
/usr/bin/podkop-sub-run-now --status
/usr/bin/podkop-sub-cron-sync
```



## IP/domain duplicates

If enabled:

```text
option dedupe_endpoint_host '1'
```

and several keys point to the same IP address or domain, the updater keeps the last variant from the subscription.

Port, transport, `sni`, and other parameters are ignored for this comparison. The filter is disabled by default because some subscriptions may intentionally publish different valid ports on the same IP or domain.

Local keys from `/etc/config/podkop-local-links` are not replaced.

## Uninstall

```sh
wget -O /tmp/podkop-sub-uninstall.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/uninstall.sh && sh /tmp/podkop-sub-uninstall.sh
```

## Podkop link compatibility

Before writing links into `/etc/config/podkop`, the updater validates them. For `vless://` and `trojan://` links without the `type` parameter, it explicitly adds `type=tcp`, because Podkop treats an empty transport as `Unknown transport '' detected`.

Section processing summary logs are printed in Russian: added, removed, final key count, removal reasons, collapsed SNI/IP-domain duplicates, and skipped keys. The summary line uses readable phrases instead of technical underscored field names.

When run directly in a terminal, the section summary line highlights important numbers with ANSI colors. The format remains one-line. ANSI codes are not added to syslog, LuCI, or piped output.
