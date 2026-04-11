# Podkop Subscriptions

Реализация функционала подписок для [podkop](https://github.com/itdoginfo/podkop). Представляет собой отдельную службу, которая позволяет:
-   мониторить URL подписок, и в случае обнаружения изменений — обновлять конфигурацию podkop и выполнять его перезапуск;
-   задавать для каждой секции индивидуальную ссылку на подписку;
-   отбирать для каждой секции определенное подмножество ссылок через механизм фильтрации на базе регулярного выражения.
    

## Требования к окружению

```bash
# Для новых версий OpenWrt (25.x+):
apk update
apk add python3-light

# Для старых версий:
opkg update
opkg install python3-light
```

## Формат конфигурации

```txt
# Файл конфигурации подписок для Podkop
# Состоит из 5 колонок, с разделителем ::
# Поля:
# 1: Название секции в /etc/config/podkop (нечувствительно к регистру).
# 2: Ссылка на подписку.
# 3: Фильтр в виде регулярного выражения. Позволяет отфильтровать только нужные ссылки-подключения. Если нужны все — оставить пустым.
# 4: Режим фильтрации (ifmatch - оставить все совпавшие с регуляркой ссылки, ifnotmatch - все несовпавшие).
# 5: Тип подключения в podkop (urltest или selector).
# 6: Поведение, если после фильтрации ссылок не найдено (all - добавить все без фильтра, skip - пропустить секцию).

RUSSIA   :: http://example.org/ :: russia                :: ifmatch    :: urltest :: all
FOREIGN1 :: http://example.org/ :: russia                :: ifnotmatch :: urltest :: all
FOREIGN2 :: http://example.org/ :: latvia|germany|sweden :: ifmatch    :: urltest :: all
```

## Установка и использование

```bash
# 1. Скачать скрипт
wget -O /usr/bin/podkop-sub-updater.py https://raw.githubusercontent.com/procudin/podkop-subscriptions/main/podkop-sub-updater.py
chmod +x /usr/bin/podkop-sub-updater.py

# 2. Создать и заполнить файл с конфигурацией
nano /etc/config/podkop-subs

# 3. Сделать резервную копию конфигурации podkop
cp /etc/config/podkop /etc/config/podkop-backup

# 4. Запустить обновление
/usr/bin/podkop-sub-updater.py

# 5. В случае возникновения проблем — вернуть старый конфиг и перезапустить podkop
cp -f /etc/config/podkop-backup /etc/config/podkop
/etc/init.d/podkop restart
```

## Запуск по расписанию (Cron)

Для автоматического обновления ссылок добавьте скрипт в системный планировщик задач (`crontab -e`).

```txt
# Обновление каждые 15 минут
*/15 * * * * /usr/bin/podkop-sub-updater.py > /dev/null 2>&1
```
