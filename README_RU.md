# Podkop Subscriptions

Дополнение для Podkop, которое автоматически подтягивает proxy-ссылки из подписок, записывает их в выбранную секцию Podkop и помогает держать список ключей актуальным.

Начиная с версии `3.3`, веб-интерфейс больше не встраивается внутрь страницы Podkop. Он устанавливается отдельным пунктом LuCI:

```text
Services → Подписки Podkop
```

Podkop остаётся отдельным сервисом. Podkop Subscriptions только читает состояние Podkop/URLTest, обновляет список ключей и записывает готовый результат в `/etc/config/podkop`.

## Проверенная конфигурация

```text
OpenWrt: 24.10.3–24.10.6; 25.12.4
Podkop: v0.7.17–v0.7.19
LuCI App Podkop: v0.7.17–v0.7.19
Sing-box: 1.12.17; 1.12.22
```

## Что умеет

- Загружает proxy-ссылки из одной или нескольких HTTP/HTTPS-подписок.
- Поддерживает локальный список ручных ключей: `/etc/config/podkop-local-links`.
- Пишет итоговый список в выбранную секцию Podkop.
- Работает без LuCI, только через SSH и cron.
- Опционально ставит отдельную LuCI-страницу `Services → Подписки Podkop`.
- Поддерживает plain-text и base64-подписки.
- Распознаёт `vless://`, `trojan://`, `ss://`, `socks4://`, `socks4a://`, `socks5://`, `hy2://`, `hysteria2://`.
- Фильтрует ключи через regex.
- Не очищает рабочую секцию Podkop, если подписки не загрузились.
- Проверяет совместимость ключей с Podkop/sing-box перед записью в конфиг.
- Накопительно считает неудачные проверки ключей (`fail_count`) и по этому счётчику определяет, какие ключи долго не работают.
- Удаляет старые мёртвые ключи при плановом обновлении.
- Может ограничивать количество ключей в секции.
- Может удалять ключи с ping выше заданного значения.
- Может схлопывать SNI-ротации, когда новый ключ отличается от старого только `sni`.
- Может схлопывать IP/домен-дубликаты: если несколько ключей ведут на один host, оставляется последний вариант из подписки.
- После долгого отключения роутера может автоматически догнать пропущенное обновление.
- В LuCI показывает краткое состояние сверху страницы.
- При установке поверх существующей настройки сохраняет конфиг, локальные ключи и state.

## Установка

Обычная установка:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh
```

Установка без веб-интерфейса:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --no-panel
```

Установка с веб-интерфейсом:

```sh
wget -O /tmp/podkop-sub-install.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/install.sh && sh /tmp/podkop-sub-install.sh --with-panel
```

Установка поверх существующей настройки без пересоздания конфига:

```sh
sh install.sh --local --with-panel --no-config
```


## Используемые файлы

```text
/etc/config/podkop_subscriptions
```

Настройки Podkop Subscriptions: источники подписок, целевая секция Podkop, фильтры, лимиты, расписание.

```text
/etc/config/podkop
```

Родной конфиг Podkop. Updater читает из него существующие секции и записывает туда итоговый список ключей.

```text
/etc/config/podkop-local-links
```

Локальный список ключей пользователя. Один ключ на строку. Эти ключи не удаляются автоматической чисткой.

```text
/etc/podkop-subscriptions/state.json
```

Служебное состояние: `fail_count`, статус последнего обновления, catch-up/retry, недавно удалённые ключи.

```text
/tmp/podkop-sub-updater.log
```

Лог последнего ручного запуска через LuCI или `/usr/bin/podkop-sub-run-now`.

## Настройка через LuCI

Откройте:

```text
Services → Подписки Podkop
```

В настройках группы указываются:

- включена ли группа;
- секция Podkop, куда писать ключи;
- URL подписок;
- использовать ли локальный список ключей;
- regex-фильтр;
- режим фильтра;
- тип группы Podkop: `urltest` или `selector`;
- максимум ключей в секции;
- максимальный ping;
- принудительная чистка;
- схлопывание SNI-дубликатов.

После изменения настроек нажмите **Save & Apply**.

Это только сохраняет параметры в `/etc/config/podkop_subscriptions`. Чтобы впервые подтянуть подписки и записать ключи в Podkop, нажмите **Запустить updater** внизу страницы.

Если первой синхронизации ещё не было, сверху будет показано:

```text
Состояние: первичная настройка — нажмите кнопку «Запустить updater» внизу страницы для первого подтягивания конфигов.
```

## Настройка без LuCI

Откройте файл:

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


config subscription_schedule 'main_0300'
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

## Фильтр regex

Фильтр работает по названию и строке ключа.

Для перечисления нескольких вариантов используется `|`, то есть «или»:

```text
option regex 'Netherlands|Нидерланды|NL'
option regex 'Finland|Финляндия|FI'
option regex 'Torrents Free|Messengers'
```

Запятая не является разделителем. Строка `Нидерланды,Финляндия` будет воспринята как один текстовый шаблон.

Если нужно искать специальные символы regex как обычный текст, их надо экранировать. Например точка: `\.`, скобки: `\(` и `\)`.

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

Один ключ на строку.

Чтобы использовать локальный список вместе с подпиской:

```text
option use_local_links '1'
```

Локальные ключи не удаляются автоматическим мусорщиком.

## Проверка совместимости ключей

Перед записью ключей в Podkop updater всегда выполняет базовую проверку совместимости. Это не проверка доступности сервера и не ping. Цель — убедиться, что ключ не сломает генерацию sing-box-конфига и не положит Podkop при запуске.

Порядок обработки:

```text
скачать подписки
→ применить regex-фильтры
→ убрать дубликаты
→ выполнить быструю Python-проверку формата
→ собрать временный sing-box config
→ выполнить sing-box check пачкой
→ только после этого записать ключи в Podkop
```

Быстрая проверка отбрасывает явно неподдерживаемые варианты: неизвестный тип proxy, отсутствующий host/port, неподдерживаемый transport, неподдерживаемый security, некорректный Shadowsocks userinfo, Reality без `pbk` и другие ошибки формата.

`sing-box check` выполняется не для каждого ключа отдельно, а для итоговой пачки. Если вся пачка проходит проверку, выполняется только один запуск `sing-box check`. Если пачка не проходит, updater ищет плохие ключи делением пачки на части. Количество запусков ограничено, чтобы не перегружать роутер.

Если после проверки не осталось совместимых ключей, текущая секция Podkop не перезаписывается. Старые ключи остаются на месте, а в LuCI показывается аварийный статус с причиной.

## Статус в LuCI

Нормальное состояние короткое:

```text
Состояние: OK — обновлено: 2026-05-31 23:49, источники: 3/3, ключей в секции: 75, рабочих: нет данных, удалено: 0, локальных: 2.
```

Если URLTest/observe уже накопил статистику:

```text
Состояние: OK — обновлено: 2026-05-31 23:49, источники: 3/3, рабочих ключей: 75, проблемных: 14, удалено: 0, локальных: 2.
```

Если часть источников не загрузилась, но валидные ключи получены, это всё равно `OK`. Несколько подписок нужны именно для резервирования.

Подробности показываются только при аварии:

```text
Состояние: авария — неподдерживаемый формат подписки.
Последнее успешное обновление: 2026-05-30 03:10.
Последняя попытка: 2026-05-31 23:49.
Источники: 0/3.
Рабочих ключей: 0, проблемных: 0, ключей в секции: 0, удалено: 0, локальных: 2.
Проблемы источников:
источник 1: неподдерживаемый формат подписки.
источник 2: после фильтра осталось 0 ключей из 45.
```

Возможные причины аварии:

- подписка не загрузилась;
- сервер подписки вернул пустой ответ;
- сервер отдаёт неподдерживаемый формат;
- в подписке нет поддерживаемых proxy-ссылок;
- все ключи отброшены regex-фильтром;
- не удалось записать конфиг Podkop.

## Catch-up после долгого отключения

Cron не выполняет пропущенные задания, если роутер был выключен. Поэтому добавлен catch-up.

Через 5 минут после загрузки updater проверяет, когда было последнее успешное обновление подписок.

Если прошло меньше 24 часов — ничего не делает.

Если прошло больше 24 часов — обновляет подписки сразу.

Если обновление не удалось или не дало валидных ключей — включается retry. Каждые 30 минут выполняется повтор, пока одно из обновлений не пройдёт успешно.

В обычном режиме получасовой retry быстро выходит и не загружает подписки.

## Cron

`podkop-sub-cron-sync` создаёт строки:

```text
0 * * * * /usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop 2>&1 | logger -t podkop-sub-health # podkop-sub-health:auto

@reboot sleep 300 && /usr/bin/podkop-sub-updater.py --catch-up --subs /etc/config/podkop_subscriptions --config /etc/config/podkop 2>&1 | logger -t podkop-sub-catchup # podkop-sub-catchup:boot

*/30 * * * * /usr/bin/podkop-sub-updater.py --catch-up-retry --subs /etc/config/podkop_subscriptions --config /etc/config/podkop 2>&1 | logger -t podkop-sub-catchup # podkop-sub-catchup:retry
```

Плановые обновления добавляются из `subscription_schedule`.

Проверить cron:

```sh
cat /etc/crontabs/root | grep -E 'podkop-sub-health|podkop-sub-updater|podkop-sub-catchup'
```

## Ручные команды

Показать версию:

```sh
/usr/bin/podkop-sub-updater.py --version
```

Показать краткое состояние:

```sh
/usr/bin/podkop-sub-updater.py --status-summary
```

Пассивно обновить статистику ключей без изменения конфига Podkop:

```sh
/usr/bin/podkop-sub-updater.py --observe-only --config /etc/config/podkop
```

Показать текущий `fail_count` по ключам без вывода proxy-ссылок:

```sh
/usr/bin/podkop-sub-updater.py --fail-count
```

Запустить обновление подписок вручную:

```sh
/usr/bin/podkop-sub-updater.py --subs /etc/config/podkop_subscriptions --config /etc/config/podkop --force
```

Запустить updater в фоне как из LuCI:

```sh
/usr/bin/podkop-sub-run-now
```

Посмотреть статус фонового запуска:

```sh
/usr/bin/podkop-sub-run-now --status
```

Синхронизировать cron:

```sh
/usr/bin/podkop-sub-cron-sync
```

## SNI-ротации

Если включено:

```text
option dedupe_sni_rotation '1'
```

и новый ключ из подписки отличается от старого только параметром `sni`, старый вариант заменяется новым.

Название ключа не используется для сравнения.

Локальные ключи из `/etc/config/podkop-local-links` не заменяются.


## IP/домен-дубликаты

Если включено:

```text
option dedupe_endpoint_host '1'
```

и несколько ключей ведут на один и тот же IP или домен, updater оставит последний вариант из подписки.

При сравнении не учитываются порт, transport, `sni` и другие параметры. Фильтр выключен по умолчанию, потому что некоторые подписки могут намеренно выдавать разные рабочие порты на одном IP или домене.

Локальные ключи из `/etc/config/podkop-local-links` не заменяются.

## Ограничение количества ключей и ping

```text
option max_links '50'
```

Ограничивает количество ключей в целевой секции. `0` или пусто — без ограничения.

```text
option max_latency_ms '500'
```

Удаляет ключи с ping выше заданного значения по данным Podkop URLTest. `0` или пусто — не удалять по ping.

```text
option force_cleanup '0'
```

Принудительная чистка. Если включить, updater может удалять ключи с `fail_count >= 2` и ключи выше `max_latency_ms`, даже если лимит `max_links` не превышен.

Используйте осторожно.

## Удаление

```sh
wget -O /tmp/podkop-sub-uninstall.sh https://raw.githubusercontent.com/makxis/podkop-subscriptions/main/uninstall.sh && sh /tmp/podkop-sub-uninstall.sh
```

Удалить вместе с настройками:

```sh
sh /tmp/podkop-sub-uninstall.sh --purge-config
```

## Совместимость ссылок с Podkop

Updater перед записью в `/etc/config/podkop` проверяет ссылки. Для `vless://` и `trojan://` ссылок без параметра `type` он явно добавляет `type=tcp`, потому что Podkop считает пустой transport ошибкой `Unknown transport '' detected`.

Логи итоговой обработки секции выводятся на русском языке: добавлено, удалено, ключей итого, причины удаления, схлопнутые SNI/IP-домены и пропущенные ключи. Поля итоговой строки написаны обычными фразами без технических подчёркиваний.

При запуске напрямую из терминала итоговая строка обработки секции подсвечивает важные числа цветом. Формат остаётся однострочным. В syslog, LuCI и pipe-вывод ANSI-коды не добавляются.
