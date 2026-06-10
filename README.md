# Podkop Subscriptions

Podkop Subscriptions is a small OpenWrt helper for Podkop. It downloads proxy links from subscription URLs, filters them, validates them against Podkop/sing-box, and writes the final list into a selected `/etc/config/podkop` section.

The main goal is safety: a failed or broken subscription must not overwrite a working Podkop section.

Since v3.3, the LuCI UI is a standalone page:

```text
Services → Podkop Subscriptions
```

## Tested with

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

Install with LuCI panel:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --with-panel
```

Install without LuCI panel:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --no-panel
```

Upgrade over an existing setup without recreating config:

```sh
sh install.sh --local --with-panel --no-config
```

Clean temporary install files:

```sh
/usr/bin/podkop-sub-clean-temp
```

## Main files

```text
/etc/config/podkop_subscriptions    main settings
/etc/config/podkop                  native Podkop config updated by the updater
/etc/config/podkop-local-links      protected local links, one link per line
/etc/podkop-subscriptions/state.json service state and fail_count
/tmp/podkop-sub-updater.log          last manual run log
```

## Minimal configuration

```text
config subscription_group 'main'
    option enabled '1'
    option target_section 'main'
    list source 'https://example.com/subscription'
    option use_local_links '1'
    option regex ''
    option match_mode 'ifnotmatch'
    option on_empty 'skip'
    option proxy_type 'urltest'
    option max_links '50'
    option max_latency_ms '500'
    option force_cleanup '0'
    option dedupe_sni_rotation '1'
    option dedupe_endpoint_host '0'

config subscription_schedule 'main_0310'
    option enabled '1'
    option hour '3'
    option minute '10'
    option jitter '1800'
    option force '0'
```

Manual run:

```sh
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop --force
```

## Regex filtering

Regex is applied to the entire decoded proxy link. Matching is case-insensitive: `YouTube`, `youtube`, and `YOUTUBE` are equivalent.

Modes:

```text
match_mode = ifmatch     # keep matching links only
match_mode = ifnotmatch  # exclude matching links
```

Example excluding filter:

```text
xhttp|#.*(YouTube|youtube|Ютуб|ютуб|YT|без рекламы|Messengers|MultiIP|Белый|список|Россия|Финляндия|🇦🇺|🇫🇮|\bAI\b)
```

Meaning:

```text
xhttp      — matched anywhere in the proxy link;
#.*(...)   — everything inside the brackets is matched only in the node name after #;
\bAI\b     — AI as a standalone word, not inside Premium+Main;
YouTube    — must be listed separately: YT does not match YouTube.
```

## Validation pipeline

Before writing to Podkop:

```text
download subscriptions
→ extract proxy links
→ apply regex
→ remove duplicates
→ normalize missing type=tcp for vless/trojan
→ run Python format checks
→ run sing-box check on a temporary config
→ write to Podkop
```

If no compatible links remain, the current Podkop section is kept unchanged.

## Duplicate handling

```text
option dedupe_sni_rotation '1'
option dedupe_endpoint_host '1'
```

`dedupe_endpoint_host` keeps the last link from the subscription when several links use the same IP/domain. It is disabled by default.

## Limits and fail_count

```text
option max_links '50'
option max_latency_ms '500'
option force_cleanup '0'
```

`force_cleanup` may remove links with `fail_count >= 2` or high ping even when `max_links` is not exceeded. Use carefully.

Show fail counts without printing proxy links:

```sh
/usr/bin/podkop-sub-updater.py --fail-count
```

## HTTP headers for subscription requests

The updater does not send the real OpenWrt model or kernel version to subscription providers. It uses a fixed profile:

```text
User-Agent: v2raytun/android
X-HWID: 2CB6745020B32B99
X-Device-OS: Android
X-Ver-OS: Android 11
X-Device-Model: OnePlus MT2110
X-App-Version: 5.23.74
```

## Useful commands

```sh
/usr/bin/podkop-sub-updater.py --version
/usr/bin/podkop-sub-updater.py --status-summary
/usr/bin/podkop-sub-updater.py --fail-count
/usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop --force
/usr/bin/podkop-sub-run-now
/usr/bin/podkop-sub-run-now --status
/usr/bin/podkop-sub-cron-sync
```
