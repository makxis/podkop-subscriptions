# Podkop Subscriptions

Podkop Subscriptions — дополнение для Podkop на OpenWrt. Оно берёт ключи из HTTP/HTTPS-подписок, фильтрует их, проверяет совместимость с Podkop/sing-box и записывает результат в выбранную секцию `/etc/config/podkop`.

Главная задача — безопасность: если подписка временно не загрузилась, вернула пустой ответ или содержит кривые ключи, рабочая секция Podkop не должна ломаться.

Начиная с v3.3 веб-панель не встраивается в родную страницу Podkop. Она ставится отдельной страницей LuCI:

```text
Services → Подписки Podkop
```

## Проверено на

```text
OpenWrt: 24.10.3–24.10.6; 25.12.4
Podkop: v0.7.17–v0.7.19
LuCI App Podkop: v0.7.17–v0.7.19
Sing-box: 1.12.17; 1.12.22
```

## Что умеет

- загружает plain-text и base64-подписки;
- поддерживает `vless://`, `trojan://`, `ss://`, `socks4://`, `socks4a://`, `socks5://`, `hy2://`, `hysteria2://`;
- пишет ключи в выбранную секцию Podkop;
- работает через SSH/cron даже без LuCI;
- имеет отдельную LuCI-страницу;
- поддерживает локальный список ключей `/etc/config/podkop-local-links`;
- фильтрует ключи через regex;
- проверяет ключи через Python-проверку и `sing-box check` до записи в Podkop;
- не перезаписывает рабочую секцию, если после загрузки/проверки не осталось валидных ключей;
- считает `fail_count` для ключей, которые долго не работают;
- умеет ограничивать количество ключей в секции;
- умеет удалять ключи с ping выше лимита;
- умеет схлопывать SNI-ротации и IP/домен-дубликаты;
- после долгого отключения роутера догоняет пропущенное обновление;
- при обновлении поверх сохраняет конфиг, локальные ключи и state.

## Установка

Обычная установка с вопросами:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh
```

Установка с веб-панелью:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --with-panel
```

Установка без веб-панели:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --no-panel
```

Установка поверх существующей настройки без пересоздания конфига:

```sh
sh install.sh --local --with-panel --no-config
```

После установки временные архивы, распакованные папки и кэш LuCI можно убрать командой:

```sh
/usr/bin/podkop-sub-clean-temp
```

Команда не удаляет настройки, локальные ключи, state, backup и лог последнего запуска updater.

## Основные файлы

```text
/etc/config/podkop_subscriptions
```

Главный конфиг Podkop Subscriptions: группы подписок, источники, фильтры, лимиты, расписание.

```text
/etc/config/podkop
```

Родной конфиг Podkop. Updater читает из него секции и записывает туда итоговый список ключей.

```text
/etc/config/podkop-local-links
```

Локальный список ключей пользователя. Один ключ на строку. Эти ключи защищены от автоматического удаления.

```text
/etc/podkop-subscriptions/state.json
```

Служебное состояние: `fail_count`, последний статус, catch-up/retry, недавно удалённые ключи.

```text
/tmp/podkop-sub-updater.log
```

Лог последнего ручного запуска через LuCI или `/usr/bin/podkop-sub-run-now`.

## Настройка через LuCI

Откройте:

```text
Services → Подписки Podkop
```

В группе подписок задаются:

- целевая секция Podkop;
- URL подписок;
- использование локального списка ключей;
- regex-фильтр;
- режим фильтрации;
- тип секции Podkop: `urltest` или `selector`;
- максимум ключей;
- максимальный ping;
- принудительная чистка;
- схлопывание SNI-ротаций;
- схлопывание IP/домен-дубликатов.

После изменения настроек нажмите **Save & Apply**. Это только сохраняет параметры. Чтобы сразу подтянуть подписки и записать ключи в Podkop, нажмите **Запустить updater**.

Если первой синхронизации ещё не было, сверху будет подсказка:

```text
Состояние: первичная настройка — нажмите кнопку «Запустить updater» внизу страницы для первого подтягивания конфигов.
```

## Настройка без LuCI

Откройте конфиг:

```sh
vi /etc/config/podkop_subscriptions
```

Минимальный пример:

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

После изменения расписания:

```sh
/usr/bin/podkop-sub-cron-sync
/etc/init.d/cron restart
```

Ручной запуск:

```sh
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop --force
```


### Важно про схлопывание IP/домен-дубликатов

Опция «Схлопывать IP/домен:порт-дубликаты» сравнивает не только адрес сервера, но и порт.

Это сделано специально: один и тот же IP или домен может отдавать несколько разных рабочих вариантов на разных портах. Например:

```text
server.example.com:443
server.example.com:8443
```

Теперь такие ключи считаются разными и не удаляют друг друга. Схлопываются только варианты с одинаковым IP/доменом и одинаковым портом. Если отличаются SNI, название, путь, fingerprint или другие параметры, но адрес и порт одинаковые — остаётся последний вариант из подписки.

## Regex-фильтр

Фильтр применяется ко всей proxy-ссылке после декодирования percent-encoded символов. Регистр не важен: `YouTube`, `youtube` и `YOUTUBE` считаются одинаково.

Режимы:

```text
match_mode = ifmatch     # оставить только совпавшие
match_mode = ifnotmatch  # исключить совпавшие
```

Для исключающего фильтра обычно используется:

```text
match_mode = ifnotmatch
on_empty = skip
```

Пример фильтра, который исключает `xhttp` в любом месте ключа, а остальные слова ищет только в названии после `#`:

```text
xhttp|#.*(YouTube|youtube|Ютуб|ютуб|YT|без рекламы|Messengers|MultiIP|Белый|список|Россия|Финляндия|🇦🇺|🇫🇮|\bAI\b)
```

Как читать этот пример:

```text
xhttp      — сработает по всей proxy-ссылке;
#.*(...)   — всё внутри скобок ищется только после решётки, то есть в названии ключа;
\bAI\b     — AI только отдельным словом, чтобы не задеть Premium+Main;
YouTube    — нужен отдельно: YT не совпадает с YouTube.
```

Если нужно искать IP или часть IP, точки надо экранировать:

```text
107\.150\.93\.
```

Быстрая диагностика после изменения фильтра:

```sh
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop --force

grep -nE "Premium|LTE|YouTube|Финляндия|YT" /etc/config/podkop
```

Если в логе есть:

```text
проверка совместимости Podkop/sing-box: принято N, отброшено 0
```

значит ключи не отбрасывал валидатор или `sing-box check`. Тогда смотрите regex-фильтр, дедупликацию, лимиты и принудительную чистку.

## Локальные ключи

Файл:

```sh
vi /etc/config/podkop-local-links
```

Формат:

```text
vless://...
trojan://...
ss://...
```

Один ключ на строку. Чтобы добавить их в группу:

```text
option use_local_links '1'
```

Локальные ключи не удаляются автоматической чисткой и не заменяются при схлопывании SNI/IP-дубликатов.

## Проверка совместимости ключей

Перед записью в Podkop updater проверяет, что ключи не сломают генерацию sing-box-конфига.

Порядок обработки:

```text
скачать подписки
→ извлечь proxy-ссылки
→ применить regex
→ удалить дубликаты
→ нормализовать type=tcp для vless/trojan без type
→ выполнить Python-проверку формата
→ выполнить sing-box check на временном конфиге
→ записать результат в Podkop
```

Это не ping и не проверка скорости. Это защита от кривых ссылок, которые могут положить Podkop.

Если после проверки не осталось совместимых ключей, текущая секция Podkop не перезаписывается.

## SNI-ротации

```text
option dedupe_sni_rotation '1'
```

Если новый ключ отличается от старого только `sni`, старый вариант заменяется новым.

## IP/домен-дубликаты

```text
option dedupe_endpoint_host '1'
```

Если несколько ключей ведут на один IP или домен, updater оставит последний вариант из подписки.

Эта опция выключена по умолчанию. Включайте её только если понимаете, что разные ключи на одном host действительно являются ротацией, а не разными рабочими вариантами.

## Лимиты, ping и fail_count

```text
option max_links '50'
```

Максимум ключей в секции. `0` или пусто — без ограничения.

```text
option max_latency_ms '500'
```

Удалять ключи с ping выше лимита по данным Podkop URLTest. `0` или пусто — не удалять по ping.

```text
option force_cleanup '0'
```

Принудительная чистка. Если включить, updater может удалять ключи с `fail_count >= 2` и ключи выше `max_latency_ms`, даже если лимит `max_links` не превышен.

Смотреть `fail_count` без вывода proxy-ссылок:

```sh
/usr/bin/podkop-sub-updater.py --fail-count
```

## Статус в LuCI

Нормальный статус короткий:

```text
Состояние: OK — обновлено: 2026-05-31 23:49, источники: 3/3, ключей в секции: 75, рабочих: нет данных, удалено: 0, локальных: 2.
```

Если часть источников не загрузилась, но валидные ключи получены, это не авария. Несколько подписок часто используются именно для резервирования.

Подробности показываются только при аварии: пустые подписки, неподдерживаемый формат, все ключи отброшены, не удалось записать конфиг и т.п.

## Catch-up после отключения роутера

Cron не выполняет пропущенные задания, когда роутер выключен. Поэтому updater добавляет catch-up:

- через 5 минут после старта проверяет возраст последнего успешного обновления;
- если прошло больше 24 часов — обновляет подписки;
- если не получилось — повторяет каждые 30 минут, пока обновление не пройдёт успешно.

## Cron

`podkop-sub-cron-sync` создаёт служебные строки:

```text
0 * * * * /usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop 2>&1 | logger -t podkop-sub-health # podkop-sub-health:auto
@reboot sleep 300 && /usr/bin/podkop-sub-updater.py --catch-up --subs /etc/config/podkop_subscriptions --config /etc/config/podkop 2>&1 | logger -t podkop-sub-catchup # podkop-sub-catchup:boot
*/30 * * * * /usr/bin/podkop-sub-updater.py --catch-up-retry --subs /etc/config/podkop_subscriptions --config /etc/config/podkop 2>&1 | logger -t podkop-sub-catchup # podkop-sub-catchup:retry
```

Плановые обновления добавляются из `subscription_schedule`.

Проверка cron:

```sh
cat /etc/crontabs/root | grep -E 'podkop-sub-health|podkop-sub-updater|podkop-sub-catchup'
```

## HTTP-заголовки при загрузке подписок

При прямых HTTP-запросах к подпискам updater не отправляет реальную модель OpenWrt-роутера и версию ядра. Используется фиксированный профиль:

```text
User-Agent: v2raytun/android
X-HWID: 2CB6745020B32B99
X-Device-OS: Android
X-Ver-OS: Android 11
X-Device-Model: OnePlus MT2110
X-App-Version: 5.23.74
```

Это нужно, чтобы прямой запрос с роутера и внешний загрузчик подписок представлялись сервису подписок как одно устройство.

## Ручные команды

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

## Удаление

```sh
wget -O /tmp/podkop-sub-uninstall.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/uninstall.sh && sh /tmp/podkop-sub-uninstall.sh
```

Удалить вместе с настройками:

```sh
sh /tmp/podkop-sub-uninstall.sh --purge-config
```
