# Podkop Subscriptions — русская документация

Дополнение для Podkop, которое добавляет автоматическое обновление proxy-ссылок из подписок и опциональную LuCI-вебморду для управления подписками.

Проект ставится отдельно от Podkop. Сам Podkop должен быть установлен заранее.

## Проверенная конфигурация

Текущая версия проверялась на следующей конфигурации:

- OpenWrt: `24.10.5`
- Podkop: `v0.7.17`
- LuCI App Podkop: `v0.7.17`
- Sing-box: `1.12.22`

На более старых или более новых версиях OpenWrt, Podkop, LuCI App Podkop или Sing-box работа не гарантируется.

Особенно важно: LuCI-панель устанавливается поверх существующих файлов веб-интерфейса Podkop. Если в другой версии Podkop изменится структура файлов, названия вкладок, методы API или ACL-права, панель может не открыться или работать некорректно.

## Что делает

- загружает proxy-ссылки из HTTP/HTTPS-подписок и локальных файлов;
- поддерживает `vless://`, `ss://`, `trojan://`, `socks4://`, `socks4a://`, `socks5://`, `hy2://`, `hysteria2://`;
- умеет читать plain-text и base64-подписки;
- фильтрует ссылки через regex;
- добавляет только новые ключи, которых ещё нет в текущей секции Podkop;
- не удаляет ключи только потому, что они пропали из подписки;
- ведёт внутренний state-файл со счётчиками неудачных проверок;
- раз в час пассивно читает состояние URLTest из Podkop и обновляет `fail_count`;
- удаляет нерабочие ключи только после 72 подряд неудачных часовых наблюдений;
- не удаляет локальные пользовательские ключи из `/etc/config/podkop-local-links`;
- синхронизирует расписание обновлений с cron;
- добавляет вкладку «Подписки» в LuCI-интерфейс Podkop;
- запускает ручной updater из веб-интерфейса в фоне, чтобы не ловить XHR timeout.

## Установка

Podkop должен быть установлен до запуска этого инсталлятора.

Интерактивная установка:

    wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh

Установить updater и cron-sync без LuCI-панели:

    wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --no-panel

Установить updater, cron-sync и LuCI-панель сразу:

    wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --with-panel

## Проверка версии

После установки можно проверить установленную версию:

    /usr/bin/podkop-sub-updater.py --version
    /usr/bin/podkop-sub-run-now --version
    cat /usr/share/podkop-subscriptions/VERSION

В LuCI во вкладке «Подписки» снизу также отображается версия вида:

    Podkop Subscriptions v2.6

## Что делать после установки

После установки скрипта и LuCI-панели нужно выполнить начальную настройку Podkop.

Важно: updater записывает ссылки только в уже существующие секции Podkop. Поэтому сначала нужно создать хотя бы одну секцию Podkop.

Порядок настройки:

1. Открой LuCI.
2. Перейди в `Services -> Podkop`.
3. Во вкладке секций добавь хотя бы одну секцию Podkop.
4. В эту секцию добавь хотя бы один proxy-ключ. Это может быть любой временный валидный ключ. Он нужен, чтобы секция была создана и появилась в списке для подписок.
5. Нажми `Save & Apply`.
6. Подожди, пока Podkop применит настройки и перезапустится.
7. Перейди во вкладку «Подписки».
8. Создай группу подписок.
9. Укажи целевую секцию Podkop, источник подписки, regex-фильтр при необходимости, режим фильтрации и тип proxy-группы.
10. Нажми `Save & Apply`.
11. Дождись применения настроек.
12. Запусти updater кнопкой во вкладке «Подписки» или вручную из SSH.

## Логика обновления

Новая логика работает по принципу:

    подписка только добавляет новые ключи
    удаление выполняется самим роутером по результатам внутренней проверки

То есть при обновлении подписки секция Podkop не перезаписывается полностью списком из подписки.

Пример:

    текущая секция: A, B, C
    подписка принесла: B, C, D, E
    добавляются только: D, E
    итоговая секция: A, B, C, D, E

Ключ `A` не удаляется только потому, что он пропал из подписки. Он будет удалён только если сам не работает 72 подряд часовые проверки.

## Логика удаления нерабочих ключей

Раз в час запускается пассивная проверка:

    /usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop

Она не трогает `/etc/config/podkop` и не перезапускает Podkop. Скрипт только читает текущее состояние URLTest из Podkop:

    /usr/bin/podkop clash_api get_proxies

Если у proxy есть `history[-1].delay > 0`, ключ считается рабочим:

    fail_count = 0

Если у proxy пустой `history` или нет валидного delay, ключ считается неработающим на текущем часовом наблюдении:

    fail_count += 1

Если роутер выключили на несколько дней, счётчик не растёт. После включения подсчёт продолжается с прежнего значения.

Удаление происходит только во время ночного maintenance-запуска updater'а. Ключ удаляется только если:

    fail_count >= 72

То есть ключ должен не пройти 72 подряд часовых наблюдения.

## Локальные ключи

Файл локальных пользовательских ключей:

    /etc/config/podkop-local-links

Ключи из этого файла защищены от автоудаления. Даже если такой ключ не работает и его `fail_count >= 72`, updater не будет удалять его из секции Podkop.

Это нужно для сценария, когда пользователь вручную добавил ключ, временно отключил его на сервере, а потом хочет снова включить без повторного добавления.

## Повторные попытки загрузки подписок

Для HTTP/HTTPS-источников используется фиксированная логика:

    3 попытки
    каждая попытка ждёт до 45 секунд

Если за 3 попытки источник не загрузился, он считается временно недоступным. Текущие ключи при этом не удаляются и секция Podkop не обнуляется.

## Cron

`podkop-sub-cron-sync` создаёт две группы заданий.

Пассивная часовая проверка:

    0 * * * * /usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop 2>&1 | logger -t podkop-sub-health # podkop-sub-health:auto

Ночные maintenance-запуски берутся из расписаний, настроенных во вкладке «Подписки».

Пример:

    0 3 * * * ... /usr/bin/podkop-sub-updater.py --subs /etc/config/podkop --config /etc/config/podkop ...
    0 4 * * * ... /usr/bin/podkop-sub-updater.py --subs /etc/config/podkop --config /etc/config/podkop ...
    0 5 * * * ... /usr/bin/podkop-sub-updater.py --subs /etc/config/podkop --config /etc/config/podkop ...

После изменения расписания можно вручную синхронизировать cron:

    /usr/bin/podkop-sub-cron-sync
    /etc/init.d/cron restart

## Ручной запуск updater

Обычный maintenance-запуск:

    /usr/bin/podkop-sub-updater.py --subs /etc/config/podkop --config /etc/config/podkop --force

Пассивная проверка без изменения конфига:

    /usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop

Ручной запуск в фоне, как из LuCI-кнопки:

    /usr/bin/podkop-sub-run-now

Лог последнего ручного запуска:

    /tmp/podkop-sub-updater.log

## State-файл

Внутреннее состояние хранится здесь:

    /etc/podkop-subscriptions/state.json

Там хранятся ключи, их stable-id, счётчик `fail_count` и признак `protected_local`.

Обычно этот файл не нужно редактировать вручную.

## Просмотр логов

Логи часовой проверки:

    logread | grep -i podkop-sub-health

Логи ночного updater'а:

    logread | grep -i podkop-updater-cron

Общие логи:

    logread | grep -Ei 'podkop-sub|podkop-updater'

Лог ручного запуска из LuCI:

    tail -n 100 /tmp/podkop-sub-updater.log

## Удаление

Удаление из GitHub:

    wget -O /tmp/podkop-sub-uninstall.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/uninstall.sh && sh /tmp/podkop-sub-uninstall.sh

Или из локальной папки:

    sh uninstall.sh

## Безопасность

Нельзя выкладывать в публичный репозиторий:

    /etc/config/podkop
    /etc/config/podkop-local-links с реальными ссылками
    /etc/crontabs/root
    резервные копии OpenWrt
    архивы с роутера
    реальные vless/ss/trojan/hy2/socks ссылки
    UUID, pbk, sid, токены подписок

В репозитории должны быть только скрипты, LuCI-файлы и обезличенные примеры.
