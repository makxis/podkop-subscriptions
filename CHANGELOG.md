# Changelog

## Unreleased

- `install-dnsproxy.sh` (1.4.0 -> 1.5.0) no longer just refuses to start when
  `/tmp/install-dnsproxy.lock` exists. It now checks whether the PID that
  holds it is still alive: a stale lock left by a run that never got to its
  cleanup trap is removed automatically, and a genuinely running instance
  gets a prompt (in an interactive terminal) to either kill it and start over
  or leave it alone and follow its log to completion instead — this is aimed
  at the unstable-SSH case, where a dropped connection used to leave the
  reconnecting user unable to tell whether the old run was still going
  without manually cross-checking `ps`, timestamps and `/tmp/test-doh-*.log`.
  Without a terminal (e.g. a second unattended invocation) it defaults to
  following rather than killing. All installer output is now also appended
  to `/tmp/install-dnsproxy.log`, which is what a second invocation tails.
- Added `test-doh.sh`, a dependency-free tester for the upstream servers in
  `servers.txt`: pure POSIX sh, using only `dnsproxy` and `nslookup`, both
  already required to install dnsproxy in the first place. It works standalone
  on a router that only has `install-dnsproxy.sh` run on it — no `python3`,
  no other component of Podkop Subscriptions needed. `install-dnsproxy.sh`
  (installer 1.3.0 -> 1.4.0) gains `--test-servers` to run it automatically
  after installation and print a latency table. Servers are tested one at a
  time rather than through a worker pool: full parallelism needs
  `wait -n`-style job tracking that behaves inconsistently across busybox
  versions, and a sequential run of a few dozen servers is a one-off task, not
  something that needs to be fast.
  - Each query is capped at 1000ms (`--timeout-ms`), enforced by a background
    `sleep`-based watchdog rather than the `timeout` command: a real OpenWrt
    router had neither `timeout` nor fractional `sleep` (`sleep 0.1` errors
    out), so both the earlier design (wrap `nslookup` in `timeout`) and a
    finer-grained poll loop were dropped in favor of racing the query against
    a whole-second `sleep` and killing whichever loses. A server that misses
    the deadline now scores FAIL on that query instead of GOOD-but-slow,
    which cut a 32-server run from several minutes down to about 80 seconds
    on hardware where two servers were answering in 4-5s.
  - Results are recorded with 0x1F (unit separator) as the field delimiter,
    not a tab. Tab counts as IFS whitespace, so `read` collapsed consecutive
    delimiters around the empty latency fields of FAIL/DEAD rows and shifted
    the rest of that row's columns left — caught by an actual DEAD row on
    real hardware, not by review.
  - After the table, if run in a terminal and at least one server scored 3/3,
    `test-doh.sh` offers to replace dnsproxy's current upstream with the five
    fastest (`y`/`д` accepts, anything else is a no-op). It backs up
    `/etc/config/dnsproxy` to `/root` first, writes the new list with `uci`,
    restarts dnsproxy, and rolls back automatically if a lookup for
    `openwrt.org` fails afterward.
- The LuCI view is installed under a versioned file name, so a browser cannot
  keep executing the previously cached copy. LuCI derives the `?v=` on every
  module URL from the version of luci-base itself, and that does not change
  when this app is upgraded. `install.sh` now writes `content-<tag>.js` and
  `subscriptions-<tag>.js`, rewrites the require between them and the path in
  `menu.d`, and drops the files of earlier versions. The tag is the app version
  plus a short checksum of the content, because the same version is reinstalled
  many times during development and the version alone would keep the old URL.
- `install-dnsproxy.sh` (installer 1.3.0) no longer lets the optional web
  interface abort the installation. `luci-app-dnsproxy` was installed right
  after dnsproxy itself, before `/etc/config/dnsproxy` was written, so a
  missing architecture directory in Fantastic Packages left the router with the
  package installed and nothing configured.
  - The LuCI step now runs last, after DNS is configured, verified and Podkop
    is pointed at it, and every failure inside it is a warning rather than a
    fatal error. `--no-luci` skips it entirely.
  - x86 targets additionally try the `x86_64` directory of Fantastic Packages.
    `x86/legacy` builds package for `i386_pentium-mmx`, no directory of that
    name exists there, and `luci-app-dnsproxy` is architecture independent
    (`_all.ipk`) anyway. `PACKAGE_ARCH_OVERRIDE` is renamed to
    `LUCI_REPOSITORY_ARCH_OVERRIDE`: `--arch` only ever selected the directory
    the LuCI package is taken from, never the architecture of dnsproxy.
  - `index.json` is read as `@.packages["<name>"]`. The index keeps versions
    inside a `packages` object, not at the top level, and the result of
    `jsonfilter` is now tested for content: it exits successfully on a miss.
  - Podkop is no longer reconfigured by default. dnsproxy listens on its own
    loopback address and collides with nothing, so which resolver Podkop uses
    stays the owner's decision, and no hidden coupling is created: removing
    dnsproxy without restoring `dns_server` would otherwise leave Podkop
    pointed at a dead resolver. The run ends with the steps to set `udp` and
    `127.0.0.10` by hand, in LuCI or over uci, and `--configure-podkop`
    restores the previous behaviour. The address is written without a port:
    a udp resolver is queried on 53 anyway, and Podkop's diagnostics report an
    error when the field carries one.
  - The run now ends with a verification of the final state, and rolls
    everything back when it fails. DNS is the one thing whose breakage takes
    away the means of fixing anything else, so a half-applied install is not an
    acceptable outcome: dnsproxy must answer on its own address, and ordinary
    name resolution on the router must still work — the latter only when it
    worked before the run, so that a WAN that is already down does not look
    like damage.
    - The rollback is a generated `rollback.sh`, written into the backup
      directory before the first change, with every value already substituted:
      the previous dnsproxy config, Podkop's previous `dns_type`/`dns_server`,
      and whether dnsproxy was installed and enabled to begin with. It cannot
      trip over the state that broke the installation, and it stays runnable by
      hand long afterwards. Values taken from uci are shell-quoted, so a quote
      inside one cannot turn the rollback into a syntax error.
    - The two earlier failure paths, a dnsproxy that does not start and one
      that does not answer, now go through the same rollback instead of
      restoring the config inline.
  - The package lists are not refreshed when dnsproxy is already installed, and
    a failed refresh is a warning instead of a fatal error. A re-run used to
    stop at `opkg update завершился с ошибкой` over a single unreachable feed.

## 3.6.6

- The manual updater run in LuCI now streams its log live instead of replacing
  the whole output every few seconds. `podkop-sub-run-now` gained a `--tail
  <offset>` mode that answers with `OFFSET`/`STATE` headers, a `BEGIN` line and
  only the bytes appended since the caller's offset, so the view appends to a
  `<pre>` roughly once per 1.5 s and reads like a terminal. It replaces a poll
  that shipped `tail -n 260` every three seconds — the whole tail, re-rendered
  from scratch, forking `sh` and `tail` on a router already saturated by the
  update itself.
  - `OFFSET` counts bytes actually handed out, not the file size sampled at the
    start of the call. Against a growing log the two differ, and the difference
    is duplicated or dropped output.
  - While the run is live only complete lines are sent. A chunk cut mid-line
    can also fall inside a UTF-8 sequence, which would leave rpcd marshalling
    invalid UTF-8 into its JSON reply; the trailing partial line is flushed
    once the run is over and nothing more is coming.
  - `RESET=1` tells the view that the log was truncated and a new run owns the
    file, `SKIPPED=1` that following started from an already large log and only
    the last 256 KB was sent, `EXIT=N` reports the exit code on completion.
  - `tail -c +N` is probed at runtime, since not every BusyBox build has it,
    with `dd` as the fallback.
- Fixed the manual run reporting `Error: XHR request timed out` at the top of
  the page. Every `fs.exec` is an rpcd call inside an XHR that LuCI aborts
  after `rpctimeout` (20 s by default), and the updater restarts podkop halfway
  through, which stalls ubus and the browser connection well past that. A
  failed poll is therefore expected noise, not the end of the run: the view now
  retries from the same offset after 4 s, up to 30 consecutive failures, rather
  than tearing down the loop on the first one and leaving the run invisible
  even though it finished normally. A timeout on the launch call is treated the
  same way — the script forks and returns immediately, so a slow answer means a
  busy router, and the state of the log decides what actually happened.
- The background run now exports `PYTHONUNBUFFERED=1`. The log is a file, so
  python was block-buffering `print()` in 4 KB chunks and the live tail would
  have arrived in bursts instead of line by line.
- The log box keeps its scroll position when the user scrolls up to read
  something, and follows the tail again only from the bottom.

## 3.6.5

- Reworked the default DNS servers in `install-dnsproxy.sh` (installer 1.2.0),
  after benchmarking 24 public resolvers from the router itself — a throwaway
  dnsproxy instance per candidate, 10 domains times 3 rounds each, ranked by
  p90 so that latency and stability count together.
  - `upstream` is now Cloudflare, ControlD, Quad9 and AdGuard, all DoH.
    Dropped `tls://unfiltered.adguard-dns.com`, which answered none of its 30
    queries; `h3://dns.google/dns-query`, the slowest working entry at 232 ms
    median against 67-100 ms for the rest; and `https://dns.alidns.com`.
  - `bootstrap` now also receives the ISP resolvers, appended last. It is a
    hidden failure point: with only remote addresses in it, the upstreams fail
    to start because nothing can resolve their host names, even though the
    upstreams themselves are reachable. Being last, they change no priorities.
  - `223.5.5.5` replaced with `9.9.9.9` in both `bootstrap` and `fallback`.
  - `fallback` otherwise unchanged, and still deliberately unencrypted: it
    exists for the case where the encrypted upstreams cannot be reached, so it
    keeps resolvers that survive blocking.
  - `--no-isp-dns` now covers `bootstrap` as well as `fallback`.
- Documented the whole DNS path and the three server lists in both READMEs,
  with a diagram, so the design is legible before installing rather than after.
  The diagram sets `diagramPadding` so GitHub's zoom and copy controls, which
  float over the top-right corner, stop covering the first node.

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
