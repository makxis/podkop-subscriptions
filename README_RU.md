# Podkop Subscriptions

Дополнение для [Podkop](https://github.com/itdoginfo/podkop), которое загружает proxy-ссылки из подписок, добавляет их в выбранную секцию Podkop и может автоматически убирать нерабочие, медленные и устаревшие варианты ключей.

Проект устанавливается отдельно от Podkop. Сам Podkop должен быть установлен и настроен заранее.

[English documentation](README.md)

## Проверенная конфигурация

Проверялось на:

- OpenWrt: `24.10.3`–`24.10.6`
- Podkop: `v0.7.17`
- LuCI App Podkop: `v0.7.17`
- Sing-box: `1.12.22`

На других версиях может работать, но совместимость не гарантируется.

## Что умеет

- Загружает proxy-ссылки из HTTP/HTTPS-подписок.
- Поддерживает локальный список ключей из `/etc/config/podkop-local-links`.
- Поддерживает `vless://`, `ss://`, `trojan://`, `socks4://`, `socks4a://`, `socks5://`, `hy2://`, `hysteria2://`.
- Читает plain-text и base64-подписки.
- Фильтрует ключи через regex.
- Добавляет только новые ключи, не очищая секцию при ошибке подписки.
- Ведёт счётчик неработающих ключей через `fail_count`.
- Может ограничивать максимальное количество ключей в секции.
- Может удалять ключи с высоким ping.
- Может схлопывать SNI-ротации, когда провайдер меняет только `sni`.
- Может работать только из консоли, без установки LuCI-вкладки.
- Опционально добавляет вкладку `Подписки` в интерфейс Podkop.

## Установка

Интерактивная установка:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh
```

Установить только скрипт, без LuCI-вкладки:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --no-panel
```

Установить скрипт и LuCI-вкладку:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --with-panel
```

При обычной установке мастер может сразу создать рабочую настройку: выбрать секцию Podkop, добавить URL подписки, включить локальный список, задать лимиты и расписание.

## Используемые файлы

### `/etc/config/podkop_subscriptions`

Главный файл настройки Podkop Subscriptions.

В нём указываются источники подписок, целевая секция Podkop, фильтры, лимиты, SNI-схлопывание и расписание.

### `/etc/config/podkop`

Родной конфиг Podkop.

Updater читает из него существующие секции и записывает туда итоговый список proxy-ссылок.

### `/etc/config/podkop-local-links`

Локальный список proxy-ссылок, которые пользователь добавляет вручную.

Эти ключи защищены от автоудаления. Чтобы использовать этот список, включите:

```text
option use_local_links '1'
```

### `/etc/podkop-subscriptions/state.json`

Служебное состояние updater: `fail_count`, недавно удалённые ключи, технические идентификаторы.

Обычно этот файл не редактируют вручную.

### `/tmp/podkop-sub-updater.log`

Лог последнего ручного запуска из LuCI или через `/usr/bin/podkop-sub-run-now`.

## Настройка без LuCI

Откройте главный файл настройки:

```sh
vi /etc/config/podkop_subscriptions
```

Минимальный пример:

```text
config subscription_group 'main'
    option enabled '1'

    # Секция Podkop, куда будут записаны ключи
    option target_section 'main'

    # URL подписки
    list source 'https://example.com/subscription'

    # Использовать локальный список /etc/config/podkop-local-links
    option use_local_links '1'

    # Фильтр. Пусто — без фильтра.
    option regex ''
    option match_mode 'ifnotmatch'
    option on_empty 'skip'

    # Тип группы Podkop
    option proxy_type 'urltest'

    # Ограничения. 0 — выключено.
    option max_links '50'
    option max_latency_ms '500'

    # Агрессивная чистка
    option force_cleanup '0'

    # Схлопывать ключи, отличающиеся только sni
    option dedupe_sni_rotation '1'


config subscription_schedule 'main_0300'
    option enabled '1'
    option hour '3'
    option minute '0'
    option jitter '1800'
    option force '0'
```

После редактирования примените расписание:

```sh
/usr/bin/podkop-sub-cron-sync
/etc/init.d/cron restart
```

Запустить обновление вручную:

```sh
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop --force
```

## Настройка через LuCI

Откройте Podkop и перейдите во вкладку `Подписки`.

В группе подписок задаются:

- секция Podkop, куда будут записаны ключи;
- URL подписки;
- использование локального списка ключей;
- regex-фильтр;
- тип группы `urltest` или `selector`;
- максимальное количество ключей;
- максимальный ping;
- принудительная чистка;
- схлопывание SNI-дубликатов;
- расписание обновления.

После изменения настроек нажмите **Save & Apply**. Это сохранит параметры в `/etc/config/podkop_subscriptions`, но не загрузит подписки автоматически. Чтобы загрузить подписки и записать ключи в выбранную секцию Podkop, нажмите **Запустить updater** во вкладке `Подписки` или выполните ручную команду из SSH. После успешного запуска updater обновите страницу Podkop, чтобы увидеть актуальный список ключей.

## Параметры группы подписок

### `target_section`

Секция Podkop, в которую updater будет записывать итоговый список ключей.

```text
option target_section 'main'
```

### `source`

URL подписки. Можно указать несколько строк `list source`.

```text
list source 'https://example.com/subscription-1'
list source 'https://example.com/subscription-2'
```

### `use_local_links`

Добавляет ключи из `/etc/config/podkop-local-links`.

```text
option use_local_links '1'
```

`1` — использовать локальный список.  
`0` — не использовать.

### `regex`

Фильтр по названию или строке ключа.

Пустое значение — без фильтра.

В фильтре используется синтаксис регулярных выражений. Для перечисления нескольких вариантов используйте разделитель `|`, который означает «или».

Примеры:

```text
option regex 'Netherlands|Нидерланды|NL'
option regex 'Финляндия|Finland|FI'
option regex 'Torrents Free|Messengers'
```

Запятая `,` не является разделителем условий. Если написать `Нидерланды,Финляндия`, это будет восприниматься как один текстовый шаблон.

Если нужно искать специальные символы регулярных выражений как обычный текст, их надо экранировать. Например точка пишется как `\.`, скобки — как `\(` и `\)`.


### `match_mode`

Режим фильтрации.

```text
option match_mode 'ifmatch'
```

`ifmatch` — оставить только совпавшие.  
`ifnotmatch` — исключить совпавшие.

### `on_empty`

Что делать, если после фильтрации ничего не осталось.

```text
option on_empty 'skip'
```

`skip` — пропустить источник.  
`all` — использовать все ссылки источника.

Обычно безопаснее `skip`.

### `proxy_type`

Тип группы Podkop:

```text
option proxy_type 'urltest'
```

Доступные значения:

- `urltest`
- `selector`

### `max_links`

Максимальное количество ключей в целевой секции.

```text
option max_links '50'
```

`0` или пусто — без ограничения.

Если при добавлении новых ключей лимит будет превышен, updater удалит худшие незащищённые ключи и добавит новые только в освободившиеся места.

### `max_latency_ms`

Максимальный ping по данным URLTest Podkop.

```text
option max_latency_ms '500'
```

`0` или пусто — не удалять по ping.

Этот параметр используется при отсеивании и при принудительной чистке.

### `force_cleanup`

Агрессивная чистка.

```text
option force_cleanup '0'
```

`1` — updater может удалять ключи с `fail_count >= 2` и ключи выше `max_latency_ms`, даже если лимит `max_links` не превышен.  
`0` — обычный режим.

Включайте осторожно.

### `dedupe_sni_rotation`

Схлопывает SNI-ротации.

```text
option dedupe_sni_rotation '1'
```

Если новый ключ из подписки отличается от старого только параметром `sni`, старый вариант будет заменён новым.

Сравнение выполняется по технической части ключа, название не используется.

Локальные ключи из `/etc/config/podkop-local-links` не заменяются.

## Локальные ключи

Откройте файл:

```sh
vi /etc/config/podkop-local-links
```

Добавьте по одному ключу на строку:

```text
vless://...
trojan://...
ss://...
```

Чтобы использовать эти ключи вместе с подпиской, в `/etc/config/podkop_subscriptions` должно быть:

```text
option use_local_links '1'
```

## Cron

### Рекомендуемый способ

Расписание хранится в `/etc/config/podkop_subscriptions`.

После изменения расписания выполните:

```sh
/usr/bin/podkop-sub-cron-sync
/etc/init.d/cron restart
```

Проверить текущие cron-задания:

```sh
cat /etc/crontabs/root
```

### Ручная настройка cron

Пассивная проверка состояния ключей каждый час:

```text
0 * * * * /usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop 2>&1 | logger -t podkop-sub-health
```

Обновление подписок каждый день в 03:00:

```text
0 3 * * * /usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop 2>&1 | logger -t podkop-updater-cron
```

После ручного редактирования cron:

```sh
/etc/init.d/cron restart
```

## Ручные команды

Показать версию:

```sh
/usr/bin/podkop-sub-updater.py --version
```

Пассивно обновить статистику ключей. Конфиг Podkop не меняется, сервис не перезапускается:

```sh
/usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop
```

Запустить обновление подписок вручную:

```sh
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop --force
```

Запустить обновление в фоне так же, как из LuCI:

```sh
/usr/bin/podkop-sub-run-now
```

Посмотреть статус фонового запуска:

```sh
/usr/bin/podkop-sub-run-now --status
```

Синхронизировать cron с настройками:

```sh
/usr/bin/podkop-sub-cron-sync
```

## Как работает обновление

1. Updater читает `/etc/config/podkop_subscriptions`.
2. Загружает источники подписок.
3. Добавляет локальные ключи, если включён `use_local_links`.
4. Применяет regex-фильтр.
5. Убирает дубликаты.
6. Схлопывает SNI-ротации, если включён `dedupe_sni_rotation`.
7. Сравнивает новые ключи с текущей секцией Podkop.
8. Добавляет только отсутствующие ключи.
9. Удаляет ключи по правилам `fail_count`, `max_links`, `max_latency_ms` и `force_cleanup`.
10. Если итоговый список изменился, записывает его в `/etc/config/podkop` и перезапускает Podkop.

Если подписка не загрузилась или после фильтрации не дала валидных ссылок, текущая секция Podkop не очищается.

## Особенности ручной проверки задержки Podkop

При большом количестве ключей ручная кнопка проверки задержки в Podkop может не успеть проверить весь список. В таком случае часть ключей может временно отображаться как `N/A`.

Автоматический URLTest Podkop продолжает работать по своему расписанию. Для автоочистки важнее накопленная статистика `observe-only`, а не единичная ручная проверка.

## Удаление

```sh
wget -O /tmp/podkop-sub-uninstall.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/uninstall.sh && sh /tmp/podkop-sub-uninstall.sh
```

Удалить вместе с локальными ключами:

```sh
sh /tmp/podkop-sub-uninstall.sh --purge-config
```
