# Podkop Subscriptions + optional LuCI panel

This repository is a cleaned overlay for `procudin/podkop-subscriptions` with an optional LuCI tab for managing subscription groups and schedules inside the existing Podkop LuCI page.

## What it does

- Installs `podkop-sub-updater.py` to `/usr/bin/podkop-sub-updater.py`.
- Installs `podkop-sub-cron-sync` to `/usr/bin/podkop-sub-cron-sync`.
- Does **not** install Podkop itself. Install Podkop and its LuCI app separately first.
- Uses `/etc/config/podkop` as the default UCI source for subscription groups.
- Optionally overlays the existing Podkop LuCI app with a new `Подписки` tab.
- Keeps private proxy links out of GitHub. Put local/manual proxy links on the router in `/etc/config/podkop-local-links`.

## One-line install

After uploading this repository to GitHub, replace `aisiq/podkop-subscriptions` with your actual fork if needed:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/aisiq/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh
```

Non-interactive variants:

```sh
# Core updater only, no LuCI panel
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/aisiq/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --no-panel

# Core updater + LuCI panel, no question
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/aisiq/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --with-panel
```

If the repository name or branch is different:

```sh
REPO=your-login/your-repo BRANCH=main sh -c "$(wget -qO- https://raw.githubusercontent.com/your-login/your-repo/main/install.sh)"
```

## Manual test

```sh
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop --config /etc/config/podkop --force
logread -e podkop-updater
```

## LuCI usage

Open LuCI → Podkop → `Подписки`.

The tab adds:

- subscription groups;
- source list per group;
- regex filter and match mode;
- `urltest` or `selector` target type;
- cron schedules with jitter;
- local proxy links editor for `/etc/config/podkop-local-links`;
- manual updater run button.

Press **Save & Apply** before using the manual run button. After saving, `podkop-sub-cron-sync` regenerates only updater-managed cron lines.

## Important security note

Do not commit real `/etc/config/podkop`, `/etc/crontabs/root`, or `/etc/config/podkop-local-links` from your router. These files may contain proxy URLs, UUIDs, public keys, SNI values, comments, and other sensitive operational details.

## Uninstall

```sh
wget -O /tmp/podkop-sub-uninstall.sh https://raw.githubusercontent.com/aisiq/podkop-subscriptions/main/uninstall.sh && sh /tmp/podkop-sub-uninstall.sh
```

To also remove `/etc/config/podkop-local-links`:

```sh
sh /tmp/podkop-sub-uninstall.sh --purge-config
```
