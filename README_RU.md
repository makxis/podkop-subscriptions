# Podkop Subscriptions

[English documentation](README.md)

Дополнение для Podkop, которое добавляет обновление proxy-ключей из подписок и опциональную вкладку `Подписки` в существующий LuCI-интерфейс Podkop.

Проект устанавливается отдельно от Podkop. Сам Podkop должен быть установлен заранее.

## Проверенная конфигурация

Текущая версия проверялась на следующей конфигурации:

- OpenWrt: `24.10.3`-`24.10.5`
- Podkop: `v0.7.17`
- LuCI App Podkop: `v0.7.17`
- Sing-box: `1.12.22`

На более старых или более новых версиях OpenWrt, Podkop, LuCI App Podkop или Sing-box работа не гарантируется. LuCI-часть устанавливается поверх существующих файлов Podkop, поэтому при изменении структуры Podkop в других версиях интерфейс может работать некорректно.

## Что устанавливается

- `/usr/bin/podkop-sub-updater.py` — основной updater подписок и обслуживания ключей.
- `/usr/bin/podkop-sub-cron-sync` — синхронизация расписания с cron.
- `/usr/bin/podkop-sub-run-now` — фоновый ручной запуск updater.
- `/usr/share/podkop-subscriptions/VERSION` — версия установленного дополнения.
- `/etc/podkop-subscriptions/state.json` — внутреннее состояние ключей и счётчики `fail_count`.
- `/etc/config/podkop-local-links` — локальные пользовательские ключи, защищённые от автоудаления.
- Опционально: LuCI-вкладка `Подписки` внутри существующей страницы Podkop.

## Установка

Интерактивная установка:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh
```

Только core-часть, без LuCI-панели:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --no-panel
```

Core-часть и LuCI-панель сразу:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --with-panel
```

## Первичная настройка

Перед настройкой подписок нужно открыть существующий Podkop в LuCI и создать хотя бы одну секцию Podkop с хотя бы одним валидным proxy-ключом.

После этого нужно нажать **Save & Apply** и дождаться применения конфигурации Podkop.

Затем открыть вкладку `Подписки` и настроить:

- целевую секцию Podkop;
- источник подписки или локальный источник;
- regex-фильтр, если нужен;
- режим фильтрации;
- тип proxy-группы: `urltest` или `selector`;
- расписание обновления.

После настройки снова нажать **Save & Apply**, дождаться применения настроек и запустить updater из LuCI или SSH.

## Проверка версии

```sh
/usr/bin/podkop-sub-updater.py --version
/usr/bin/podkop-sub-run-now --version
cat /usr/share/podkop-subscriptions/VERSION
```

Во вкладке `Подписки` внизу также отображается версия установленного дополнения.

## Ручные команды

Пассивная проверка состояния ключей без изменения конфига Podkop:

```sh
/usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop
```

Ручной maintenance-запуск:

```sh
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop --config /etc/config/podkop --force
```

Фоновый ручной запуск:

```sh
/usr/bin/podkop-sub-run-now
tail -n 80 /tmp/podkop-sub-updater.log
```

Синхронизация cron:

```sh
/usr/bin/podkop-sub-cron-sync
cat /etc/crontabs/root
```

## Логика обновления

Подписка используется только как источник новых proxy-ключей. Она не перезаписывает секцию Podkop полностью.

Поведение:

- новые ключи из подписок добавляются к существующим;
- существующие ключи не удаляются только потому, что исчезли из подписки;
- если подписка не загрузилась или вернула 0 валидных ключей, текущая секция Podkop не очищается;
- каждый час `--observe-only` читает состояние URLTest из Podkop и обновляет `fail_count` в `/etc/podkop-subscriptions/state.json`;
- если ключ рабочий, `fail_count` сбрасывается в `0`;
- если ключ отображается как `N/A` или без delay/history, `fail_count` увеличивается на `1`;
- ключ удаляется только во время планового maintenance-запуска, если `fail_count >= 72`;
- ключи из `/etc/config/podkop-local-links` защищены от автоматического удаления;
- `--observe-only` не меняет `/etc/config/podkop` и не перезапускает Podkop;
- Podkop перезапускается только если были добавлены новые ключи, удалены умершие ключи или очищены дубликаты.

Если роутер был выключен несколько дней, `fail_count` не растёт. После включения подсчёт продолжается с прежнего значения.

## Повторные попытки загрузки подписки

HTTP/HTTPS-источник подписки загружается с фиксированными повторными попытками:

- 3 попытки;
- каждая попытка ждёт до 45 секунд.

Если источник не ответил после всех трёх попыток, updater считает его недоступным, пишет ошибку в лог и сохраняет текущие ключи.

## Установка из локального архива

Если архив уже распакован на OpenWrt:

```sh
cd /tmp/podkop-subscriptions-clean-v2.6
sh install.sh --local --no-panel
sh install.sh --local --with-panel
```

При запуске из распакованного архива `install.sh` использует локальные файлы. Для принудительной загрузки из GitHub можно использовать `--remote`.

## Удаление

```sh
wget -O /tmp/podkop-sub-uninstall.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/uninstall.sh && sh /tmp/podkop-sub-uninstall.sh
```

Удаление вместе с `/etc/config/podkop-local-links`:

```sh
sh /tmp/podkop-sub-uninstall.sh --purge-config
```
