# Podkop Subscriptions

[English documentation](README.md)

Дополнение для Podkop, которое обновляет proxy-ключи из подписок и может добавить вкладку `Подписки` в LuCI.

Проект устанавливается отдельно от Podkop. Сам Podkop должен быть установлен заранее.

## Проверенная конфигурация

- OpenWrt: `24.10.3`-`24.10.5`
- Podkop: `v0.7.17`
- LuCI App Podkop: `v0.7.17`
- Sing-box: `1.12.22`

## Быстрая установка

Интерактивная установка:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh
```

Только core-часть без LuCI:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --no-panel
```

Core-часть и LuCI-панель:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --with-panel
```

## Файлы

Основной конфиг дополнения:

```text
/etc/config/podkop_subscriptions
```

Родной конфиг Podkop, куда updater записывает итоговые ключи:

```text
/etc/config/podkop
```

Локальные ручные ключи:

```text
/etc/config/podkop-local-links
```

Служебное состояние updater:

```text
/etc/podkop-subscriptions/state.json
```

Лог ручного запуска:

```text
/tmp/podkop-sub-updater.log
```

## Настройка без вебморды

Откройте один основной файл:

```sh
vi /etc/config/podkop_subscriptions
```

Пример рабочей группы:

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

`option use_local_links '1'` подключает файл `/etc/config/podkop-local-links`. Ручное добавление `file:///etc/config/podkop-local-links` в список источников больше не требуется.

После изменения конфига:

```sh
/usr/bin/podkop-sub-cron-sync
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop --force
```

## Cron без вебморды

Рекомендуемый способ — через встроенный синхронизатор:

```sh
/usr/bin/podkop-sub-cron-sync
cat /etc/crontabs/root
/etc/init.d/cron restart
```

Ручной вариант:

```text
0 * * * * /usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop 2>&1 | logger -t podkop-sub-health
0 3 * * * /usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop 2>&1 | logger -t podkop-updater-cron
```

## Логика обновления

- Подписка добавляет новые ключи, но не удаляет существующие только потому, что они исчезли из подписки.
- Если источники вернули 0 валидных ключей, конфиг Podkop не меняется.
- `--observe-only` раз в час читает состояние Podkop URLTest и обновляет `fail_count`.
- Обычно ключ удаляется при `fail_count >= 72`.
- При `max_links` включается отсеиватель, если добавление новых ключей превысит лимит.
- При `max_latency_ms` отсеиватель может удалять ключи с ping выше лимита.
- `force_cleanup` — жёсткий режим: может удалять ключи с `fail_count >= 2` и ping выше лимита.
- `dedupe_sni_rotation` заменяет старые SNI-варианты свежими из подписки.
- Ключи из `/etc/config/podkop-local-links` защищены от автоудаления.

## Логи

Логи обезличены: URL подписок, proxy-ссылки, UUID, `sni`, `pbk`, `sid` и токены не должны выводиться в stdout/syslog/LuCI. Источники отображаются как `источник 1`, `источник 2`, `локальный список`.

## Ручные команды

```sh
/usr/bin/podkop-sub-updater.py --version
/usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop --force
/usr/bin/podkop-sub-run-now
/usr/bin/podkop-sub-run-now --status
```

## Удаление

```sh
wget -O /tmp/podkop-sub-uninstall.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/uninstall.sh && sh /tmp/podkop-sub-uninstall.sh
```

Удаление вместе с конфигами дополнения:

```sh
sh /tmp/podkop-sub-uninstall.sh --purge-config
```
