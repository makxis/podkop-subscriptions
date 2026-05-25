# Podkop Subscriptions

Дополнение для Podkop, которое добавляет автоматическое обновление proxy-ссылок из подписок и опциональную LuCI-вебморду для управления подписками.

Проект ставится отдельно от Podkop. Сам Podkop должен быть установлен заранее.

## Что делает

- загружает proxy-ссылки из HTTP/HTTPS-подписок или локальных файлов;
- поддерживает `vless://`, `ss://`, `trojan://`, `socks4://`, `socks4a://`, `socks5://`, `hy2://`, `hysteria2://`;
- умеет читать plain-text и base64-подписки;
- фильтрует ссылки через regex;
- записывает найденные proxy в выбранные секции Podkop;
- поддерживает режимы `urltest` и `selector`;
- синхронизирует расписание обновлений с cron;
- добавляет вкладку «Подписки» в LuCI-интерфейс Podkop;
- позволяет хранить локальные proxy-ссылки в `/etc/config/podkop-local-links`.

## Установка

Podkop должен быть установлен до запуска этого инсталлятора.

Интерактивная установка:

    wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh

Только updater и cron-sync, без LuCI-панели:

    wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --no-panel

Updater, cron-sync и LuCI-панель сразу:

    wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --with-panel

## Установка из локальной папки

Если архив уже распакован на роутере:

    cd /tmp/podkop-subscriptions-clean
    sh install.sh --local --with-panel

или только updater:

    sh install.sh --local --no-panel

## Файлы, которые устанавливаются

Обязательная часть:

    /usr/bin/podkop-sub-updater.py
    /usr/bin/podkop-sub-cron-sync
    /etc/config/podkop-local-links

Опциональная LuCI-панель:

    /www/luci-static/resources/view/podkop/podkop.js
    /www/luci-static/resources/view/podkop/main.js
    /www/luci-static/resources/view/podkop/subscriptions.js
    /usr/share/rpcd/acl.d/luci-app-podkop.json

## Настройка через LuCI

После установки LuCI-панели открой:

    LuCI -> Services -> Podkop -> Подписки

Во вкладке можно настроить:

- группы подписок;
- целевые секции Podkop;
- источники подписок;
- regex-фильтр;
- режим фильтрации `ifmatch` или `ifnotmatch`;
- тип proxy-группы `urltest` или `selector`;
- поведение при пустом результате фильтра;
- расписание обновлений;
- локальные proxy-ссылки.

После изменения настроек нужно нажать `Save & Apply`.

## Ручной запуск updater

    /usr/bin/podkop-sub-updater.py --subs /etc/config/podkop --config /etc/config/podkop --force

## Синхронизация cron

    /usr/bin/podkop-sub-cron-sync

## Локальные ссылки

Локальные proxy-ссылки можно хранить в файле:

    /etc/config/podkop-local-links

Пример:

    vless://UUID@example.org:443?security=reality&type=tcp&pbk=PUBLIC_KEY&sni=example.org#Example
    ss://method:password@example.org:8388#Example

Не публикуйте реальные proxy-ссылки, UUID, ключи, токены подписок и содержимое `/etc/config/podkop`.

## Удаление

    wget -O /tmp/podkop-sub-uninstall.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/uninstall.sh && sh /tmp/podkop-sub-uninstall.sh

или из локальной папки:

    sh uninstall.sh

## Безопасность

Нельзя выкладывать в публичный репозиторий:

    /etc/config/podkop
    /etc/config/podkop-local-links с реальными ссылками
    /etc/crontabs/root
    резервные копии OpenWrt
    архивы с роутера
    реальные vless/ss/trojan/hy2/socks ссылки

В репозитории должны быть только скрипты, LuCI-файлы и обезличенные примеры.