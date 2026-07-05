# Podkop Subscriptions

Podkop Subscriptions is a small OpenWrt helper for Podkop. It downloads proxy links from subscription URLs, filters them, validates them against Podkop/sing-box, and writes the final list into a selected `/etc/config/podkop` section.

The main goal is safety: a failed or broken subscription must not overwrite a working Podkop section.

Since v3.3, the LuCI UI is a standalone page:

```text
Services → Podkop Subscriptions
```

## Essential commands

These commands are kept near the top so installation, updating, and basic diagnostics do not require searching through the full README.

### 1. Interactive installation

The installer asks whether to configure subscriptions immediately and whether to install the LuCI panel:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh
```

### 2. Unattended installation with LuCI panel

Installs the core and visible LuCI panel without asking questions. If no config exists, a disabled example config is created and can then be edited in LuCI:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --remote --with-panel --no-config && /usr/bin/podkop-sub-clean-temp
```

### 3. Unattended update

Downloads the current project files from GitHub and updates the installed version. The existing `/etc/config/podkop_subscriptions`, local links, and `state.json` are preserved:

```sh
wget -O /tmp/podkop-sub-upgrade.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-upgrade.sh --remote --with-panel --no-config && /usr/bin/podkop-sub-clean-temp
```

### Run a subscription update now

```sh
/usr/bin/podkop-sub-run-now
```

### Show accumulated fail_count statistics

Shows the current historical failure counters without printing proxy links:

```sh
/usr/bin/podkop-sub-updater.py --fail-count
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
/etc/init.d/podkop_subscriptions      procd trigger for automatic cron synchronization
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




### IP/domain:port deduplication

The “Collapse IP/domain:port duplicates” option compares the server address together with the port. The same IP or domain on different ports is treated as different working variants and is not removed.

For example, these links are treated as different:

```text
server.example.com:443
server.example.com:8443
```

Only links with the same IP/domain and the same port are collapsed. `transport`, `sni`, `path`, `fp`, the link name, and other parameters are not used for this comparison; the last variant from the subscription is kept.

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


## Automatic cron synchronization

Since version 3.6.2 cron is synchronized through two independent paths:

- the LuCI form immediately runs `/usr/bin/podkop-sub-cron-sync` after a successful Save / Save & Apply;
- `/etc/init.d/podkop_subscriptions` registers a procd `config.change` trigger, so any `uci commit podkop_subscriptions` from LuCI, SSH, or another script rebuilds the managed cron entries.

`podkop-sub-cron-sync` is idempotent and uses an atomic kernel `fcntl.flock`; there is no separate lock-creation/PID-write window. Manual execution is only needed for diagnostics or forced recovery.

## Concurrent updater protection

Every execution path uses the same `/tmp/podkop-sub-updater.flock`: scheduled updates, `--observe-only`, catch-up, retry, and manual runs. The observer quietly skips while another update is active; normal and catch-up runs wait up to 300 seconds and return code `75` with a warning if the lock remains busy.

The older `/tmp/podkop-sub-updater.lock` directory in `podkop-sub-run-now` remains only for LuCI manual-run status. The Python updater flock provides the actual race protection.

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


### Individual source failures

If one subscription source fails but other reserve sources still provide valid keys, this is not treated as a fatal error. The updater logs it as `WARN`, not `ERROR`, so LuCI does not flood the interface with pop-up errors.

A fatal error is logged only when no valid keys can be assembled for any section. The previous working Podkop configuration is not overwritten in this case.


### Source counters and errors

Only external subscription sources are counted in status and statistics. The local list `/etc/config/podkop-local-links` is not a network source: it does not increase the successful subscription counter and does not hide problems with external subscription downloads.

If some external sources fail but at least one reserve source provides valid keys, this is not a fatal error. The updater logs a warning and continues. A red error is emitted only when no valid keys can be assembled from external subscriptions for any section. In that case the previous working Podkop configuration is not overwritten.


### Auto-update status

The status line shows not only the latest subscription update result, but also whether the schedule is actually applied in cron:

```text
auto-update: enabled
auto-update: not applied
auto-update: not configured
```

This matters because `/etc/config/podkop_subscriptions` is the saved setting, while `/etc/crontabs/root` is what actually runs. If a schedule exists in settings but not in cron, the UI reports that auto-update is not applied. Version 3.6.2 automatically repairs synchronization after LuCI saves and after any `uci commit podkop_subscriptions`.
