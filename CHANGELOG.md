# Changelog

## 3.8.2

- Sections with capital letters in their name lost their keys. The updater
  lowercased the section name everywhere, while Podkop builds outbound tags
  from it as is: for a section `YouTube` it looked up `youtube-1-out`, found
  nothing, counted every key as missing, and after 72 hourly observations the
  fail_count cleanup removed them. Tags and `uci` calls now use the name as it
  is in the config; Tachyon's `config urltest` points at it the same way.
- Health was read from the wrong tag after a duplicate link. Podkop numbers
  every entry of the list, duplicates included, while the updater counted
  positions in the deduplicated list, so every key after the first duplicate
  got its neighbour's state.
- `state.json` no longer grows without bound. Keys that left a section other
  than through the updater (edited by hand, rotated by the provider, whole
  section deleted) stayed in it forever: on a live router 257 of about 700
  entries were still in the config, and the 650 KB file is rewritten every
  hour. Entries are now dropped once the key is gone from the config; an
  unreadable or empty config drops nothing.
- UCI values are read the way `uci` reads them. An apostrophe is stored as
  `'a'\''b'`, and the old parser returned that literally, which broke a regex
  or a source URL containing `'` and the names of local keys.
- Expanding domains into IPs has a 30 s DNS budget per source. The timeout
  passed to the resolver never applied (`getaddrinfo` ignores socket
  timeouts), so a subscription with dozens of domains on a slow resolver could
  stall the update for minutes.
- The installer no longer leaves a `*.bak.<date>` copy next to every program
  file on each upgrade, and removes the ones earlier versions left: on a
  router upgraded 25 times they took 2.8 MB of flash in `/usr/bin` and the
  LuCI directories. The upgrade archive in `/root` is taken before the new
  files are copied (it used to hold the new version, so it could not roll
  anything back), and only the three newest archives are kept.
- The interactive setup's config backup goes to `/etc/podkop-subscriptions/`
  instead of `/etc/config`, where uci parses every file; old copies are moved
  there.
- `uninstall.sh` removes `podkop-sub-clean-temp` and the old `*.bak.*` copies
  too. README no longer promises that uninstalling restores
  `/etc/config/podkop` from a backup: the code to do it was never called, and
  the keys stay in the Podkop sections.
- `podkop-sub-clean-temp` removes the `/tmp/podkop-sub-upgrade.sh` that the
  README upgrade command downloads, and lists the current path of the local
  links.
- `podkop-sub-run-now` keeps the PID of the background run in its lock. A run
  killed before its trap fired (kill -9, OOM) used to leave the lock until
  reboot, with LuCI showing "running" and refusing to start the updater.

`install-dnsproxy.sh`, `test-doh.sh` and `servers.txt` are fetched from `main`
directly, so the changes below were live as soon as they were pushed.

- `install-dnsproxy.sh` (installer 1.5.0 -> 1.9.0) writes a lean dnsproxy
  config, and re-running it is now how a router set up by an older version
  gets it. On a router with 256 MB of RAM, dnsproxy with HTTP/3 and its own
  cache had grown to about 61 MB RSS with about 45 MB of system memory left;
  without them it starts at a few megabytes.
  - HTTP/3, dnsproxy's own cache and optimistic cache (dnsmasq already
    caches), DNS64, EDNS Client Subnet, verbose logging, hosts processing,
    private reverse DNS and the local DoH/DoT server are off. The upstream
    timeout is 5 s instead of the default 10 s: while the upstreams are
    unreachable every query waits out the upstream timeout and then the
    fallback one, and those pending queries pile up in memory.
  - Upstreams chosen earlier, `[/domain/]address` rules included, are kept.
    `--reset-servers` writes the defaults instead.
  - Sections the script does not own are carried over. Newer
    `luci-app-dnsproxy` versions create `config profile 'preset_…'` once at
    install time, and rewriting the whole file removed them from LuCI for
    good. The assembled config is parsed by `uci` before it replaces the live
    one.
  - Bootstrap and fallback are no longer a fixed list. The plain resolvers
    from `servers.txt` are queried from the router, and four responding ones
    from different operators are written, followed by the ISP's resolvers
    without a test. dnsproxy queries every bootstrap and every fallback
    address at once, so a longer list only adds goroutines per query while
    the upstreams are down.
  - 1.7.0 and 1.8.0 probed those resolvers with `timeout`, which stock
    OpenWrt does not have (it comes with `coreutils-timeout`): every candidate
    counted as dead and the list was written unverified. With `--no-isp-dns`
    both lists came out empty, because busybox `grep -vxF -f` with an empty
    pattern file prints nothing. Re-run the installer on routers set up by
    those versions.
  - After a restart the check waits for dnsproxy for up to 20 s instead of
    asking once after 2 s. procd gives the old process 5 s to stop before
    killing it, and the single early check rolled back a working config.
  - The run ends with the dnsproxy command line and memory, and warns when
    `--http3` is present or `--upstream-mode parallel` or `--ipv6-disabled` is
    missing.
  - `luci-app-trafficctl` is removed when SQM is enabled: its HTB/IFB qdiscs
    on `br-lan` and `tctl-ifb0` shaped traffic a second time on top of
    SQM/CAKE. Without SQM it stays, with a warning.
  - A re-run no longer downloads an installed `luci-app-dnsproxy` again,
    restarts rpcd and uhttpd only when the panel was just installed, and does
    not print the Podkop instructions when Podkop already points at
    `127.0.0.10`.
  - Ctrl-C or a dropped SSH session ends the run. The cleanup was the signal
    handler itself, so ash ran it and carried on installing without its temp
    files and lock.
- `test-doh.sh` tests plain resolvers as well as DoH and applies both lists
  at once: up to five upstreams, and up to four bootstrap/fallback addresses
  followed by the ISP's resolvers.
  - Plain resolvers are tested first, and the fastest serve as bootstrap for
    the DoH test. The fixed `8.8.4.4 1.0.0.1 9.9.9.9` failed every DoH server
    on networks where those addresses are blocked on port 53.
  - The best server of each operator is picked before second addresses of
    the same operator: five Cloudflare endpoints are not five independent
    upstreams.
  - No busy-waiting. Each query used to spin a shell loop at 100% of a core,
    which also took CPU from the dnsproxy being measured. Waiting now relies
    on `nslookup -timeout`, time comes from `/proc/uptime` through `read`
    instead of an `awk` per sample, the throwaway dnsproxy is killed with
    SIGKILL (on SIGTERM it lingered for about 2 s per server), and a server is
    not queried again after its first failure.
  - `[/domain/]address` rules in upstream survive applying, and a failed write
    reverts the staged uci changes instead of leaving them for the next commit.
  - A work directory or an orphaned test dnsproxy left by a killed run is
    cleaned up on the next start; the latter would otherwise hold
    `127.0.0.11:53` and memory until reboot.
- `servers.txt` has an operator column, more DoH candidates, and plain IPv4
  candidates for bootstrap and fallback. `family.adguard-dns.com` is gone (an
  adult-content filter in a `parallel` pool blocks sites at random), and so is
  Comss DNS, which answers with its own proxies for some services. Yandex DoH
  points at `common.dot.dns.yandex.net`.
- The example config written by `install.sh` no longer claims that a plain
  `uci commit` resyncs cron. Over SSH it takes
  `uci commit podkop_subscriptions && reload_config`; Save & Apply in LuCI
  does it by itself.
- README: install and first-setup commands come first, and the reference
  material is folded into collapsible sections. The duplicate "HTTP headers"
  section is merged into "Client fingerprint", and the dnsproxy rollback
  command now picks the newest backup instead of the first one the glob
  matched.

## 3.8.1

- With Tachyon as the target, the link filter no longer applies Podkop's
  limits. Tachyon converts links itself and knows `http`, `h2` and
  `httpupgrade`, so those pass now. `xhttp` passes only when the installed
  sing-box can run it: `-extended` or `-lx` in `sing-box version`, or a
  `with_xhttp` build tag, the same test Tachyon uses. On a plain build the key
  is rejected with a reason that names sing-box, not Podkop.
  - The outbound built for `sing-box check` follows Tachyon's parser, including
    its xhttp defaults. Without `x_padding_bytes` sing-box-extended refuses the
    transport outright, which rejected all ten xhttp keys on the test router.
  - Log lines and rejection reasons name the actual target, Tachyon or Podkop.
- The service restart no longer leaks the init script's output into the log.
  Tachyon printed `Command failed: ubus call service delete ...
  tachyon-steer-zapret (Not found)` on every run; the log now keeps only a
  non-zero exit code.

## 3.8.0

- Works with [Tachyon](https://github.com/Dushnilin/tachyon), the Podkop Plus
  fork that replaces Podkop. On such a router `/etc/config/podkop` does not
  exist, so every run died with `--config не найден`.
  - `--config /etc/config/podkop` falls back to `/etc/config/tachyon` when only
    the latter exists, so existing cron entries keep working.
  - Links are written in Tachyon's own schema: `selector_proxy_links` plus
    `action 'connection'`, URLTest as a `config urltest` child. Podkop options
    are not written: Tachyon only migrates them on a package upgrade, and until
    then the section would carry no links at all.
  - Health comes from `tachyon clash_api get_proxies`, the service restarted is
    `/etc/init.d/tachyon`. Its `<section>-N-out` tags follow the link order the
    same way Podkop's do (checked on 61 links against the generated config).
  - The panel and the installer list Tachyon's sections when Podkop is absent.
- An anonymous section after the target (`config urltest` in Tachyon) was taken
  for part of the target: the links were written into it a second time. The
  section header regex now accepts a missing name.
- Rewriting the config keeps its file mode. Tachyon holds its config at 0600
  because it stores bot and API tokens; the temp file used to widen it to 0644.

## 3.7.5

- Fixes 3.7.4's panel check, which never saw a hidden panel. The check read
  `menu.d` at the start of `install_panel`, but `install_core` runs first and
  its `cleanup_old_luci_leftovers` deletes that very file along with the other
  old LuCI files. By the time the check ran, a hidden installation looked
  exactly like no installation at all, so the upgrade put the menu entry back —
  the behaviour the check was added to prevent.
  - The state is now read once at the top of the run, before `install_core`
    touches anything, and `cleanup_old_luci_leftovers` carries a note saying
    what it destroys.

## 3.7.4

- `expand_domain_ips` is on by default now, for new configs and for existing
  groups that never carried the option. A domain with several addresses behind
  it is measured by URLTest as one key, and which server that key reaches is
  whatever DNS returned at the time, so the fastest of them was being picked by
  chance. A group that has the option set to `0` keeps it off.
- The source download log no longer reads as a promise to wait 45 seconds. The
  45s is a per-attempt ceiling: a refused connection or a missing DNS record
  fails in milliseconds, and an unanswered handshake fails on the connect
  timeout, which is 15s. Measured on a dead port, three attempts took 2.0s in
  total while the log said `timeout=45s` three times.
  - Each attempt now names both limits before it runs and how long it actually
    took afterwards, and the final error gives the elapsed time of the whole
    cycle instead of the number that was never reached.
  - `--connect-timeout` moved out of the command into a named constant next to
    the other one, and the subprocess timeout now sits 5s above curl's
    `--max-time`. They used to be the same number, so which timer fired first
    was a toss-up: curl's message and exit code, or a bare TimeoutExpired.
- Keys from the local list are logged with their own line and their own count,
  and the summary counter that quietly duplicated them was removed. They are
  still deliberately kept out of `last_sources_ok` and `last_unique_links`, so a
  local file can never make a failed subscription download look successful.
- An upgrade now keeps the panel the way it was installed. The documented
  upgrade command is `install.sh --remote --with-panel --no-config`, and
  `--with-panel` used to mean "visible": every run wrote a fresh `menu.d` entry
  with a title, so a panel installed with `--with-panel-hidden` came back into
  Services on the next upgrade, silently and without anything in the output
  saying so.
  - `--with-panel`, and the interactive prompt answered with Y, now mean
    "install the panel" only. Before the files are written the installer reads
    the existing `menu.d` entry: no title there means the panel is hidden, and
    that is how it is reinstalled. The run says so instead of staying quiet.
  - Visibility is changed only when it is stated outright. `--with-panel-hidden`
    hides a visible panel as before, and the new `--with-panel-visible` is the
    way to bring a hidden one back into the menu.
  - A missing, unreadable or foreign `menu.d` file, or a router without
    python3, is treated as "not installed hidden", so a first install still
    lands in the menu.

## 3.7.3

- The check that runs before `sing-box check` now says what it rejected and
  why. It reported `неподдерживаемый transport: 1` and nothing else, which read
  as a stricter duplicate of the sing-box check that followed it. It is neither
  stricter nor a duplicate: the link is converted into an outbound by Podkop's
  own `/usr/lib/podkop/sing_box_config_facade.sh`, and that converter is the
  ceiling. Verified on a router: a `type=xhttp` link, which sing-box does have
  transports for, is turned by Podkop into an outbound with no transport at all
  — a valid config that passes `sing-box check` and a node that cannot work —
  while a `vmess://` link makes Podkop exit with `Unsupported proxy vmess type.
  Aborted.`, leaving the router with no proxy. Neither is visible after
  conversion, and the converter is also what builds the config the sing-box
  check reads.
  - Rejected keys are now named in the log at DEBUG level together with the
    value that was refused: `ключ не для Podkop (🇳🇱 Нидерланды): transport не
    поддерживается Podkop: xhttp`.
  - The reason texts now name Podkop as the limiting side, and the sets of
    schemes, transports and security values carry a comment pointing at the
    converter they mirror.
  - Both READMEs gained a "why there are two checks" section.

## 3.7.2

- The compatibility check now asks sing-box which key it tripped over instead
  of guessing. `sing-box check` stops at the first outbound it cannot accept
  and names it — `outbounds[3].transport: unknown transport type` when decoding
  the config, `initialize outbound[1]: unknown method` when bringing it up —
  and the number is the position in the very array we handed it. Dropping that
  one key and asking again costs one run per bad key plus a confirming run,
  where the old bisection cost about twelve runs per bad key.
  - That bisection is kept as the fallback for a failure sing-box reports
    without a position, and the run budget, which now scales with the length of
    the list, guards only that path.
  - The budget used to be reachable in ordinary use: a section of 65 keys with
    five bad ones needed more than the 40 runs allowed, and a section whose
    check ran out of budget was left untouched entirely — the whole update
    silently did nothing for it. Measured on the router, the same 65 keys now
    take 6 runs instead of 40, and the five bad keys are dropped instead of
    freezing the section.
  - Each rejected key is now named in the log at DEBUG level, so it is possible
    to tell which node a panel is serving broken.

## 3.7.1

- The LuCI page no longer leaves labels pointing at ids that do not exist.
  LuCI titles every option with `<label for="widget.cbid.<config>.<section>.<option>">`,
  but only widgets built around an input, select or textarea publish that id:
  a flag keeps it in `data-widget-id` and gives the checkbox a random id
  instead, while a dummy value and a button have nothing focusable at all. The
  browser reported 19 such labels on this page, and a screen reader announced
  those controls unlabelled. Each option's rendered frame is now repaired:
  a flag's label is pointed at the checkbox itself, and a label with nothing to
  address loses its `for`. The label is replaced by a clone in the process,
  which drops LuCI's own click handler — with `for` resolving, the browser
  activates the checkbox itself, and keeping both would toggle it twice per
  click on the title.

## 3.7.0

- The request fingerprint is no longer hardcoded. A `config fingerprint`
  section holds the headers as an ordered list of `Name: value` lines, which is
  what preserves the letter case and order panels actually check, and the LuCI
  page edits it as a single block so a captured profile can be pasted whole.
  A group picks a profile with `option fingerprint`; empty means `default`.
  - `X-HWID` is an ordinary line of that block. `{hwid}` in it, or no `X-HWID`
    line at all, means one gets generated from `/dev/urandom` on the first run
    and written back, staying stable from then on: to a panel a changed HWID
    looks like a new device. A config without a fingerprint section gets one
    created on first run.
  - **The old hardcoded `SUBSCRIPTION_HWID` was a real device identifier**, so
    every installation announced itself as the same phone. It is gone, and the
    value was purged from the repository history.
  - Several profiles are tried in turn until a subscription reads, so panels
    wanting different clients need no manual mapping.
- Subscriptions are fetched with `curl` when it is present, because OpenWrt's
  stock `wget` is `uclient-fetch` and always rewrites `User-Agent` with its own
  capitalisation and position, which defeats the point of a captured
  fingerprint. This adds no dependency: Podkop itself requires `curl`. The wget
  path remains as a fallback and says so in the log. `User-Agent` used to be
  sent twice, via both `--user-agent` and `--header`; now once.
- JSON subscriptions are understood: Clash/Mihomo proxy objects, and arrays of
  complete Xray configs with the node name in `remarks`. No `python3-yaml` is
  needed for either. The conversion rules are ported from a mature open
  implementation rather than inferred, and verified against its output: the same
  node list in both forms produces identical links, matching on every query
  parameter.
- Refusals are recognised instead of being counted as empty subscriptions:
  anti-bot stub pages served under a 200, and placeholder nodes on `0.0.0.0:1`
  whose names carry the reason ("Вы достигли максимального числа устройств для
  вашей подписки"). Those keys no longer reach the Podkop config, and the
  panel's own wording goes to the log.
- Base64 detection is strict now — length, alphabet including the url-safe one,
  and a sanity check on the decoded text — instead of trying to decode anything
  that had no direct links in it.
- Keys from subscriptions carry their source's number in the name, `[2] 🇳🇱
  Нидерланды`, matching the "источник 2" lines in the log, so the Podkop list
  shows where each node came from. Keys already in the section are renamed as
  well when the same node arrives again. The number is replaced rather than
  appended on each run, and since `stable_id` ignores everything after `#`,
  renaming creates no new keys and loses no failure history.
- New per-group option `expand_domain_ips`: a domain resolving to two or more
  addresses also yields one key per IP, with the domain key kept. URLTest can
  then pick the fastest server rather than whatever DNS returned. Only the host
  is replaced, so `sni` and `host` keep pointing at the domain.
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
