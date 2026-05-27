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

## Local archive install

If the repository is not pushed to GitHub yet and the archive is already unpacked on OpenWrt:

```sh
cd /tmp/podkop-subscriptions-clean
sh install.sh --local --no-panel
sh install.sh --local --with-panel
```

When run from an unpacked archive, `install.sh` uses local files by default. Use `--remote` to force downloading from GitHub.

## Новая логика обновления и автоочистки

Начиная с stateful-версии, подписка больше не используется как полный источник истины для перезаписи секции Podkop.

Алгоритм стал таким:

- подписка только добавляет новые proxy-ключи;
- уже существующие ключи не удаляются только потому, что исчезли из подписки;
- если подписка не загрузилась или вернула 0 валидных ключей, текущая секция Podkop не меняется;
- каждый час запускается пассивный режим `--observe-only`, который читает текущее состояние URLTest из Podkop и обновляет `fail_count` в `/etc/podkop-subscriptions/state.json`;
- если ключ рабочий, `fail_count` сбрасывается в `0`;
- если ключ отображается в Podkop как `N/A` или без delay/history, `fail_count` увеличивается на `1`;
- ключ удаляется только во время ночного запуска updater'а, если `fail_count >= 72`;
- ключи из `/etc/config/podkop-local-links` считаются локальными пользовательскими ключами и защищены от автоматического удаления, даже если `fail_count >= 72`;
- днём `--observe-only` не меняет `/etc/config/podkop` и не перезапускает Podkop;
- Podkop перезапускается только если ночью реально были добавлены новые ключи, удалены умершие ключи или очищены дубликаты.

Это сделано, чтобы временные проблемы связи, DNS, мобильного покрытия или белых списков не приводили к немедленному удалению ключей.
Если роутер был выключен несколько дней, счётчик `fail_count` не растёт. После включения подсчёт продолжается с прежнего значения.

Файл состояния:

    /etc/podkop-subscriptions/state.json

Ручная пассивная проверка:

    /usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop

Ручное ночное обслуживание:

    /usr/bin/podkop-sub-updater.py --subs /etc/config/podkop --config /etc/config/podkop --force

`podkop-sub-cron-sync` автоматически добавляет hourly observer в cron:

    0 * * * * /usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop 2>&1 | logger -t podkop-sub-health

А ночные обновления берутся из расписаний, созданных во вкладке «Подписки».

## Поведение при медленной или недоступной подписке

HTTP/HTTPS-источник подписки загружается с фиксированными повторными попытками: 3 попытки, каждая с таймаутом 45 секунд. Эти параметры специально не вынесены наружу, чтобы поведение updater было одинаковым при ручном запуске и при запуске из cron.

Если источник не ответил после всех трёх попыток, updater считает этот источник недоступным, пишет ошибку в лог и не меняет текущие рабочие ключи по этой причине.



## Примечание к OpenWrt python3-light

Скрипт не требует `python3-urllib`: percent-decoding реализован внутри, чтобы работать на минимальном `python3-light`.

## Ручной запуск из LuCI без XHR timeout

Кнопка `Запустить updater` во вкладке «Подписки» запускает обновление в фоне через `/usr/bin/podkop-sub-run-now` и сразу возвращает ответ в LuCI. Это нужно потому, что источник подписки может опрашиваться до трёх раз по 45 секунд, и обычный синхронный XHR-запрос LuCI не должен ждать завершения всего updater.

Лог последнего ручного запуска:

    /tmp/podkop-sub-updater.log

Посмотреть из SSH:

    tail -n 80 /tmp/podkop-sub-updater.log

Если updater уже выполняется, повторный запуск через кнопку будет пропущен по lock-файлу `/tmp/podkop-sub-updater.lock`.
