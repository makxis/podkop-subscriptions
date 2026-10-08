#!/bin/sh
# Автоматическая установка и настройка AdGuard dnsproxy для OpenWrt.
#
# Что делает:
#   1. Определяет ветку OpenWrt, менеджер пакетов (apk или opkg) и архитектуру.
#   2. Устанавливает dnsproxy из штатного репозитория OpenWrt.
#   3. Скачивает подходящий luci-app-dnsproxy из Fantastic Packages.
#   4. Настраивает dnsproxy на 127.0.0.10:53.
#   5. Проверяет публичные DNS (1.1.1.1, 8.8.8.8 и др.) и пишет в bootstrap и fallback только ответившие.
#   6. Добавляет текущие IPv4 DNS-серверы провайдера в fallback и bootstrap без проверки.
#   7. Печатает инструкцию, как направить Podkop на 127.0.0.10.
#      Сам конфиг Podkop не изменяется, если не указан --configure-podkop.
#
# Повторный запуск безопасен: конфиги предварительно сохраняются в /root.
# Он же приводит уже настроенный роутер к актуальной лёгкой конфигурации
# (без HTTP/3, без собственного кеша и прочих лишних функций), сохраняя
# ранее подобранные upstream, включая правила вида [/домен/]сервер.
# bootstrap/fallback каждый раз собираются из публичных DNS, которые реально
# отвечают с этого роутера, плюс DNS провайдера.

set -eu

SCRIPT_VERSION="1.8.0"
FANTASTIC_ROOT="https://fantastic-packages.github.io/releases"
REPO="${REPO:-makxis/podkop-subscriptions}"
BRANCH="${BRANCH:-main}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/${REPO}/${BRANCH}}"
LISTEN_ADDR="127.0.0.10"
LISTEN_PORT="53"
LOCK_DIR="/tmp/install-dnsproxy.lock"
LOCK_PID_FILE="$LOCK_DIR/pid"
LOG_FILE="/tmp/install-dnsproxy.log"
CONFIGURE_PODKOP=0
RESTART_PODKOP=1
ADD_ISP_DNS=1
KEEP_SERVERS=1
INSTALL_PACKAGES=1
INSTALL_LUCI=1
TEST_SERVERS=0
SERVERS_LIST_OVERRIDE=""
OPENWRT_SERIES_OVERRIDE=""
LUCI_REPOSITORY_ARCH_OVERRIDE=""

SCRIPT_PATH="$0"
case "$SCRIPT_PATH" in
    */*) SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)" ;;
    *) SCRIPT_DIR="$(pwd)" ;;
esac

log() {
    printf '%s\n' "[dnsproxy-installer] $*" | tee -a "$LOG_FILE"
}

warn() {
    printf '%s\n' "[dnsproxy-installer] WARNING: $*" | tee -a "$LOG_FILE" >&2
}

die() {
    printf '%s\n' "[dnsproxy-installer] ERROR: $*" | tee -a "$LOG_FILE" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Использование:
  sh install-dnsproxy.sh [параметры]

Параметры:
  --configure-podkop   Направить DNS Podkop на dnsproxy автоматически.
                       По умолчанию /etc/config/podkop не изменяется, а в конце
                       печатается инструкция, что выставить руками.
  --no-podkop          Ничего не делать с Podkop. Поведение по умолчанию,
                       параметр оставлен для совместимости.
  --no-podkop-restart  С --configure-podkop: настроить Podkop, но не
                       перезапускать его.
  --reset-servers      Не сохранять ранее настроенные upstream, а записать
                       список по умолчанию. bootstrap/fallback и так
                       перепроверяются при каждом запуске.
  --no-isp-dns         Не добавлять DNS-серверы провайдера в fallback и bootstrap.
  --config-only        Не устанавливать пакеты, только записать конфиг.
  --no-luci            Не устанавливать luci-app-dnsproxy.
  --release 24.10      Принудительно указать ветку Fantastic Packages.
  --arch x86_64        Каталог архитектуры Fantastic Packages, откуда брать
                       luci-app-dnsproxy. На сам dnsproxy не влияет.
  --test-servers       После установки прогнать upstream-сервера из
                       servers.txt через test-doh.sh (без Python) и
                       напечатать таблицу с задержками.
  --servers-list PATH  С --test-servers: свой список серверов вместо
                       servers.txt рядом со скриптом.
  --help               Показать эту справку.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --configure-podkop)
            CONFIGURE_PODKOP=1
            shift
            ;;
        --no-podkop)
            CONFIGURE_PODKOP=0
            shift
            ;;
        --no-podkop-restart)
            RESTART_PODKOP=0
            shift
            ;;
        --reset-servers)
            KEEP_SERVERS=0
            shift
            ;;
        --no-isp-dns)
            ADD_ISP_DNS=0
            shift
            ;;
        --config-only)
            INSTALL_PACKAGES=0
            shift
            ;;
        --no-luci)
            INSTALL_LUCI=0
            shift
            ;;
        --test-servers)
            TEST_SERVERS=1
            shift
            ;;
        --servers-list)
            [ "$#" -ge 2 ] || die "После --servers-list нужен путь, например /root/servers.txt"
            SERVERS_LIST_OVERRIDE="$2"
            shift 2
            ;;
        --release)
            [ "$#" -ge 2 ] || die "После --release нужна версия, например 24.10"
            OPENWRT_SERIES_OVERRIDE="$2"
            shift 2
            ;;
        --arch)
            [ "$#" -ge 2 ] || die "После --arch нужна архитектура, например x86_64"
            LUCI_REPOSITORY_ARCH_OVERRIDE="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            die "Неизвестный параметр: $1"
            ;;
    esac
done

[ "$(id -u)" = "0" ] || die "Скрипт нужно запускать от root"
[ -r /etc/openwrt_release ] || die "Не найден /etc/openwrt_release: это не OpenWrt"
command -v uci >/dev/null 2>&1 || die "Не найдена команда uci"
command -v wget >/dev/null 2>&1 || die "Не найдена команда wget"

TMP_DIR="/tmp/install-dnsproxy.$$"
BACKUP_DIR="/root/dnsproxy-backup-$(date +%Y%m%d-%H%M%S)"

# При конфликте лока не просто падаем: если старый процесс жив, в интерактивном
# терминале спрашиваем, убить его или присоединиться и просто досмотреть лог
# (полезно после обрыва SSH и переподключения — раньше в этом случае приходилось
# вручную лезть в ps/логи, чтобы понять, жив ли ещё старый запуск). Без терминала
# (например, второй запуск из cron/CI) по умолчанию присоединяемся, а не убиваем
# чужой процесс без спроса. Лок без живого владельца (процесс упал, не оставив
# lock через trap) подчищается автоматически.
acquire_lock() {
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        echo "$$" > "$LOCK_PID_FILE"
        : > "$LOG_FILE"
        return 0
    fi

    old_pid="$(cat "$LOCK_PID_FILE" 2>/dev/null || true)"
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        if [ -t 0 ] && [ -t 1 ]; then
            printf '[dnsproxy-installer] Установщик уже выполняется (PID %s).\n' "$old_pid"
            printf '[dnsproxy-installer] Убить его и начать заново? [y/N] (N — присоединиться и досмотреть лог до завершения): '
            read -r ans || ans=""
        else
            ans=""
        fi
        case "$ans" in
            y|Y|yes|YES|Yes|д|Д|да|Да|ДА)
                kill "$old_pid" 2>/dev/null || true
                waited=0
                while kill -0 "$old_pid" 2>/dev/null && [ "$waited" -lt 10 ]; do
                    sleep 1
                    waited=$((waited + 1))
                done
                if kill -0 "$old_pid" 2>/dev/null; then
                    kill -9 "$old_pid" 2>/dev/null || true
                    sleep 1
                fi
                kill -0 "$old_pid" 2>/dev/null && die "Не удалось остановить PID $old_pid"
                rm -rf "$LOCK_DIR"
                acquire_lock
                return $?
                ;;
            *)
                printf '[dnsproxy-installer] Присоединяюсь к PID %s, лог: %s\n' "$old_pid" "$LOG_FILE"
                [ -f "$LOG_FILE" ] || : > "$LOG_FILE"
                tail -f "$LOG_FILE" &
                tail_pid=$!
                while kill -0 "$old_pid" 2>/dev/null; do
                    sleep 1
                done
                sleep 1
                kill "$tail_pid" 2>/dev/null || true
                wait "$tail_pid" 2>/dev/null || true
                printf '[dnsproxy-installer] PID %s завершился.\n' "$old_pid"
                exit 0
                ;;
        esac
        return 0
    fi

    # Лок остался от процесса, которого уже нет (упал без отработки trap) —
    # это не конфликт, а мусор, можно смело подчистить и продолжить.
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR" 2>/dev/null || die "Не удалось создать $LOCK_DIR"
    echo "$$" > "$LOCK_PID_FILE"
    : > "$LOG_FILE"
}

acquire_lock
mkdir -p "$TMP_DIR" "$BACKUP_DIR"

cleanup() {
    rm -rf "$TMP_DIR" "$LOCK_DIR"
}
trap cleanup 0 1 2 15

backup_file() {
    src="$1"
    name="$2"
    if [ -e "$src" ]; then
        cp -p "$src" "$BACKUP_DIR/$name"
    fi
}

backup_file /etc/config/podkop podkop.config

# Состояние до вмешательства. Снимается до первого изменения, потому что откат,
# собранный по факту поломки, обычно опирается ровно на то, что уже сломано.
DNSPROXY_WAS_INSTALLED=0
if command -v dnsproxy >/dev/null 2>&1; then
    DNSPROXY_WAS_INSTALLED=1
fi

DNSPROXY_WAS_ENABLED=0
if [ -x /etc/init.d/dnsproxy ] && /etc/init.d/dnsproxy enabled 2>/dev/null; then
    DNSPROXY_WAS_ENABLED=1
fi

PODKOP_DNS_TYPE_OLD="$(uci -q get podkop.settings.dns_type 2>/dev/null || true)"
PODKOP_DNS_SERVER_OLD="$(uci -q get podkop.settings.dns_server 2>/dev/null || true)"

# Проверять в конце «работает ли DNS роутера» имеет смысл, только если он
# работал в начале. Иначе упавший WAN выглядел бы как поломка от установки, и
# скрипт откатывал бы совершенно исправную настройку.
BASELINE_DNS_OK=0
if nslookup openwrt.org >/dev/null 2>&1; then
    BASELINE_DNS_OK=1
else
    warn "Разрешение имён на роутере не работает ещё до установки"
fi

# Получаем major.minor, например 24.10 из 24.10.2.
# shellcheck disable=SC1091
. /etc/openwrt_release
DETECTED_RELEASE="${DISTRIB_RELEASE:-}"
OPENWRT_SERIES="$(printf '%s\n' "$DETECTED_RELEASE" | sed -n 's/^\([0-9][0-9]*\.[0-9][0-9]*\).*/\1/p')"
[ -n "$OPENWRT_SERIES_OVERRIDE" ] && OPENWRT_SERIES="$OPENWRT_SERIES_OVERRIDE"
[ -n "$OPENWRT_SERIES" ] || die "Не удалось определить ветку OpenWrt из: $DETECTED_RELEASE"

log "OpenWrt: ${DETECTED_RELEASE:-неизвестно}; ветка пакетов: $OPENWRT_SERIES"

# OpenWrt 24.10 и старше используют opkg, 25.12 и новее — apk. Форматы пакетов
# и индексов у них разные, поэтому дальше всё ветвится по этой переменной.
if command -v apk >/dev/null 2>&1; then
    PKG_MANAGER="apk"
elif command -v opkg >/dev/null 2>&1; then
    PKG_MANAGER="opkg"
else
    die "Не найден ни apk, ни opkg: неподдерживаемая система"
fi
log "Менеджер пакетов: $PKG_MANAGER"

install_packages() {
    # Повторный запуск не должен зависеть от доступности репозиториев. Списки
    # пакетов нужны только чтобы поставить dnsproxy; если он уже стоит, обновление
    # ничего не даёт, а один недоступный фид роняет весь скрипт на ровном месте.
    if command -v dnsproxy >/dev/null 2>&1; then
        log "dnsproxy уже установлен — списки пакетов не обновляю"
        return 0
    fi

    log "Обновляю списки пакетов"
    case "$PKG_MANAGER" in
        apk)  apk update || warn "apk update завершился с ошибкой; ставлю с тем, что есть" ;;
        opkg) opkg update || warn "opkg update завершился с ошибкой; ставлю с тем, что есть" ;;
    esac

    log "Устанавливаю dnsproxy и сертификаты"
    case "$PKG_MANAGER" in
        apk)  apk add ca-bundle ca-certificates dnsproxy || die "Не удалось установить пакет dnsproxy" ;;
        opkg) opkg install ca-bundle ca-certificates dnsproxy || die "Не удалось установить пакет dnsproxy" ;;
    esac
}

read_packages_gz() {
    file="$1"
    if command -v zcat >/dev/null 2>&1; then
        zcat "$file"
    else
        gzip -dc "$file"
    fi
}

arch_candidates() {
    if [ -n "$LUCI_REPOSITORY_ARCH_OVERRIDE" ]; then
        printf '%s\n' "$LUCI_REPOSITORY_ARCH_OVERRIDE"
        return 0
    fi

    # DISTRIB_ARCH из /etc/openwrt_release совпадает с именами каталогов
    # Fantastic Packages и есть на обеих ветках, поэтому он идёт первым.
    [ -n "${DISTRIB_ARCH:-}" ] && printf '%s\n' "$DISTRIB_ARCH"

    # x86/legacy собирается под i386_pentium-mmx, и каталога с таким именем в
    # Fantastic Packages нет. Но luci-app-dnsproxy лежит там как *_all.ipk, то
    # есть не зависит от архитектуры, поэтому для x86 годится каталог x86_64.
    case "${DISTRIB_TARGET:-}" in
        x86/*)
            printf '%s\n' "x86_64"
            ;;
    esac

    case "$PKG_MANAGER" in
        apk)
            apk --print-arch 2>/dev/null || true
            ;;
        opkg)
            opkg print-architecture 2>/dev/null \
                | awk '$2 != "all" && $2 != "noarch" { print $3, $2 }' \
                | sort -nr \
                | awk '{ print $2 }'
            ;;
    esac
}

detect_package_arch() {
    seen=""
    for arch in $(arch_candidates); do
        case " $seen " in *" $arch "*) continue ;; esac
        seen="$seen $arch"
        # index.json отдают обе ветки репозитория, поэтому проверка одна.
        if wget -q -O "$TMP_DIR/index-$arch.json" \
            "$FANTASTIC_ROOT/$OPENWRT_SERIES/packages/$arch/luci/index.json"; then
            printf '%s\n' "$arch"
            return 0
        fi
    done
    return 1
}

# index.json выглядит так:
#   {"version": 2, "architecture": "x86_64",
#    "packages": {"luci-app-dnsproxy": "26.057.52546~67a7f3f"}}
# то есть версии лежат внутри packages, а не в корне.
lookup_index_version() {
    file="$1"
    name="$2"
    value=""

    if command -v jsonfilter >/dev/null 2>&1; then
        value="$(jsonfilter -i "$file" -e "@.packages[\"$name\"]" 2>/dev/null || true)"

        # Проверяется именно результат: jsonfilter завершается успешно и когда
        # ничего не нашёл, поэтому по коду возврата судить нельзя.
        if [ -n "$value" ]; then
            printf '%s\n' "$value"
            return 0
        fi
    fi

    sed -n "s/.*\"$name\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$file" | head -n 1
}

# Веб-интерфейс — необязательная надстройка над уже работающим DNS, поэтому
# внутри нет ни одного die: любая неудача здесь возвращает 1, а вызывающий код
# ограничивается предупреждением. Раньше отсутствие каталога архитектуры в
# Fantastic Packages обрывало скрипт после установки dnsproxy, но до записи
# конфига, и роутер оставался с пакетом без настройки.
install_luci_package() {
    if ! PACKAGE_ARCH="$(detect_package_arch)"; then
        warn "Fantastic Packages не содержит LuCI-пакеты для архитектур этого роутера (ветка $OPENWRT_SERIES)"
        return 1
    fi
    LUCI_BASE="$FANTASTIC_ROOT/$OPENWRT_SERIES/packages/$PACKAGE_ARCH/luci"
    log "Каталог LuCI-пакетов: $PACKAGE_ARCH"

    if [ "$PKG_MANAGER" = "apk" ]; then
        # У apk индекс packages.adb бинарный, поэтому имя файла собирается
        # из версии в index.json: <имя>-<версия>.apk.
        index_file="$TMP_DIR/index-$PACKAGE_ARCH.json"
        if [ ! -s "$index_file" ]; then
            if ! wget -q -O "$index_file" "$LUCI_BASE/index.json"; then
                warn "Не удалось скачать индекс Fantastic Packages: $LUCI_BASE"
                return 1
            fi
        fi

        luci_version="$(lookup_index_version "$index_file" luci-app-dnsproxy)"
        if [ -z "$luci_version" ]; then
            warn "В репозитории не найден luci-app-dnsproxy"
            return 1
        fi

        LUCI_FILENAME="luci-app-dnsproxy-${luci_version}.apk"
        local_file="$TMP_DIR/luci-app-dnsproxy.apk"
    else
        # Индекс Fantastic Packages для opkg — это Packages.gz, и распаковать
        # его нечем на образах без gzip.
        if ! command -v zcat >/dev/null 2>&1 && ! command -v gzip >/dev/null 2>&1; then
            if ! opkg install gzip; then
                warn "Не удалось установить gzip для чтения Packages.gz"
                return 1
            fi
        fi

        packages_gz="$TMP_DIR/Packages-$PACKAGE_ARCH.gz"
        if ! wget -q -O "$packages_gz" "$LUCI_BASE/Packages.gz"; then
            warn "Не удалось скачать индекс Fantastic Packages: $LUCI_BASE"
            return 1
        fi

        LUCI_FILENAME="$(read_packages_gz "$packages_gz" | awk '
            $1 == "Package:" { wanted = ($2 == "luci-app-dnsproxy") }
            wanted && $1 == "Filename:" { print $2; exit }
        ')"
        if [ -z "$LUCI_FILENAME" ]; then
            warn "В репозитории не найден luci-app-dnsproxy"
            return 1
        fi

        local_file="$TMP_DIR/luci-app-dnsproxy.ipk"
    fi

    log "Скачиваю $LUCI_FILENAME"
    if ! wget -O "$local_file" "$LUCI_BASE/$LUCI_FILENAME"; then
        warn "Не удалось скачать luci-app-dnsproxy"
        return 1
    fi

    case "$PKG_MANAGER" in
        apk)
            # Пакет не подписан ключом, который знает роутер, и ставится файлом,
            # а не из подключённого репозитория — apk требует оба флага.
            if ! apk add --allow-untrusted --force-non-repository "$local_file"; then
                warn "Не удалось установить luci-app-dnsproxy"
                return 1
            fi
            ;;
        opkg)
            if ! opkg install "$local_file"; then
                warn "Не удалось установить luci-app-dnsproxy"
                return 1
            fi
            ;;
    esac
}

if [ "$INSTALL_PACKAGES" = "1" ]; then
    install_packages
else
    command -v dnsproxy >/dev/null 2>&1 || die "--config-only указан, но dnsproxy не установлен"
fi

# Бэкап делается после установки: на чистом роутере /etc/config/dnsproxy
# появляется только вместе с пакетом, и до установки откатывать было бы нечего.
backup_file /etc/config/dnsproxy dnsproxy.config

# Откат — отдельный скрипт рядом с копиями конфигов, со всеми значениями,
# подставленными заранее. Он не зависит ни от переменных этого запуска, ни от
# того, докуда установка успела дойти, поэтому не может споткнуться на том же,
# на чём споткнулась установка. И запустить его можно хоть через неделю руками.
# Значения из uci подставляются в скрипт отката как есть, поэтому кавычатся:
# одна одинарная кавычка в значении иначе превратила бы откат в синтаксическую
# ошибку — ровно в тот момент, когда он единственное, что осталось.
shquote() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

write_rollback_kit() {
    podkop_touched="$1"

    cat > "$BACKUP_DIR/rollback.sh" <<EOF
#!/bin/sh
# Откат установки dnsproxy, установщик $SCRIPT_VERSION.
# Создан автоматически, запускать от root: sh $BACKUP_DIR/rollback.sh
set -u

BACKUP_DIR=$(shquote "$BACKUP_DIR")
PODKOP_TOUCHED=$(shquote "$podkop_touched")
PODKOP_DNS_TYPE_OLD=$(shquote "$PODKOP_DNS_TYPE_OLD")
PODKOP_DNS_SERVER_OLD=$(shquote "$PODKOP_DNS_SERVER_OLD")
DNSPROXY_WAS_INSTALLED=$(shquote "$DNSPROXY_WAS_INSTALLED")
DNSPROXY_WAS_ENABLED=$(shquote "$DNSPROXY_WAS_ENABLED")
EOF

    cat >> "$BACKUP_DIR/rollback.sh" <<'EOF'

say() {
    printf '%s\n' "[dnsproxy-rollback] $*"
}

if [ -f "$BACKUP_DIR/dnsproxy.config" ]; then
    cp "$BACKUP_DIR/dnsproxy.config" /etc/config/dnsproxy
    say "Конфиг dnsproxy восстановлен из бэкапа"
else
    rm -f /etc/config/dnsproxy
    say "Конфига dnsproxy до установки не было, удалён"
fi

if [ "$PODKOP_TOUCHED" = "1" ]; then
    if [ -n "$PODKOP_DNS_TYPE_OLD" ]; then
        uci set podkop.settings.dns_type="$PODKOP_DNS_TYPE_OLD"
    else
        uci -q delete podkop.settings.dns_type 2>/dev/null || true
    fi

    if [ -n "$PODKOP_DNS_SERVER_OLD" ]; then
        uci set podkop.settings.dns_server="$PODKOP_DNS_SERVER_OLD"
    else
        uci -q delete podkop.settings.dns_server 2>/dev/null || true
    fi

    uci commit podkop
    say "DNS-настройки Podkop возвращены к прежним значениям"

    if [ -x /etc/init.d/podkop ]; then
        /etc/init.d/podkop restart >/dev/null 2>&1 || true
    fi
fi

if [ -x /etc/init.d/dnsproxy ]; then
    if [ "$DNSPROXY_WAS_INSTALLED" = "1" ] && [ -f /etc/config/dnsproxy ]; then
        /etc/init.d/dnsproxy restart >/dev/null 2>&1 || true
        if [ "$DNSPROXY_WAS_ENABLED" != "1" ]; then
            /etc/init.d/dnsproxy disable >/dev/null 2>&1 || true
        fi
    else
        # Пакета здесь до установки не было: оставлять запущенным нечего,
        # а без конфига он всё равно не поднимется.
        /etc/init.d/dnsproxy stop >/dev/null 2>&1 || true
        /etc/init.d/dnsproxy disable >/dev/null 2>&1 || true
        say "dnsproxy остановлен и выключен из автозапуска"
    fi
fi

if nslookup openwrt.org >/dev/null 2>&1; then
    say "Разрешение имён на роутере работает"
else
    say "ВНИМАНИЕ: имена не разрешаются и после отката, проверьте DNS вручную"
fi

say "Откат завершён"
EOF

    chmod 0700 "$BACKUP_DIR/rollback.sh"
}

rollback() {
    warn "Откатываю изменения: $BACKUP_DIR/rollback.sh"
    sh "$BACKUP_DIR/rollback.sh" || warn "Откат завершился с ошибкой, разбирайтесь через $BACKUP_DIR"
}

write_rollback_kit 0

collect_isp_dns() {
    out="$1"
    raw="$TMP_DIR/isp-dns.raw"
    : > "$raw"

    # Основной источник: DNS, полученные netifd через DHCP/PPPoE/модем.
    for resolv in /tmp/resolv.conf.d/resolv.conf.auto /tmp/resolv.conf.auto; do
        if [ -r "$resolv" ]; then
            awk '$1 == "nameserver" { print $2 }' "$resolv" >> "$raw"
        fi
    done

    # Резервный способ: читаем dns-server активных интерфейсов через ubus.
    if command -v ubus >/dev/null 2>&1 && command -v jsonfilter >/dev/null 2>&1; then
        for object in $(ubus list 'network.interface.*' 2>/dev/null || true); do
            status="$(ubus call "$object" status 2>/dev/null || true)"
            [ -n "$status" ] || continue
            is_up="$(printf '%s\n' "$status" | jsonfilter -e '@.up' 2>/dev/null || true)"
            [ "$is_up" = "true" ] || continue
            printf '%s\n' "$status" | jsonfilter -e '@["dns-server"][*]' 2>/dev/null >> "$raw" || true
        done
    fi

    # Используем только IPv4. Loopback исключаем, чтобы не создать DNS-петлю.
    awk '
        function ipv4(s) { return s ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ }
        ipv4($1) && $1 !~ /^127\./ && $1 != "0.0.0.0" && !seen[$1]++ { print $1 }
    ' "$raw" > "$out"
}

ISP_DNS_FILE="$TMP_DIR/isp-dns.list"
: > "$ISP_DNS_FILE"
if [ "$ADD_ISP_DNS" = "1" ]; then
    collect_isp_dns "$ISP_DNS_FILE"
fi

# Ранее настроенные upstream (подобранные test-doh.sh или руками, включая
# правила вида [/nalog.ru/]адрес) при повторном запуске сохраняются: задача
# повторного запуска — убрать лишнее, а не сбросить чужой выбор серверов.
# Значения без пробелов, поэтому вывод uci get можно делить по словам.
OLD_UPSTREAM=""
OLD_PLAIN=""
if [ "$KEEP_SERVERS" = "1" ] && [ -f /etc/config/dnsproxy ]; then
    OLD_UPSTREAM="$(uci -q get dnsproxy.servers.upstream 2>/dev/null || true)"
    if [ -n "$OLD_UPSTREAM" ]; then
        OLD_PLAIN="$(uci -q get dnsproxy.servers.bootstrap 2>/dev/null || true) $(uci -q get dnsproxy.servers.fallback 2>/dev/null || true)"
        log "Сохраняю ранее настроенные upstream (--reset-servers, чтобы сбросить)"
    fi
fi

# bootstrap разрешает имена самих DoH-upstream, fallback — последняя линия,
# когда шифрованные upstream недоступны. Оба работают открытым текстом, и
# у части провайдеров отдельные публичные адреса на 53 порту режутся, поэтому
# список не статичный: каждый кандидат проверяется запросом с роутера, и в
# конфиг попадают только ответившие. Ранее записанные адреса тоже
# перепроверяются. DNS провайдера добавляются в конец без проверки: это
# свой резолвер сети, он нужен всегда.
PLAIN_CANDIDATES="$TMP_DIR/plain-candidates.list"
# Кандидаты — голые IPv4 из servers.txt (тот же список, что у test-doh.sh);
# если его нет ни рядом, ни в репозитории — короткий встроенный набор.
SERVERS_TXT=""
if [ -f "$SCRIPT_DIR/servers.txt" ]; then
    SERVERS_TXT="$SCRIPT_DIR/servers.txt"
elif wget -q -O "$TMP_DIR/servers.txt" "$RAW_BASE/servers.txt" 2>/dev/null; then
    SERVERS_TXT="$TMP_DIR/servers.txt"
fi
{
    if [ -n "$SERVERS_TXT" ]; then
        awk '$1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ { print $1 }' "$SERVERS_TXT"
    fi
    printf '%s\n' \
        1.1.1.1 1.0.0.1 \
        8.8.8.8 8.8.4.4 \
        9.9.9.9 149.112.112.112 \
        94.140.14.140 94.140.14.141 \
        208.67.222.222 208.67.220.220 \
        77.88.8.8 77.88.8.1
    for dns in $OLD_PLAIN; do
        printf '%s\n' "$dns"
    done
} | awk '
    $1 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ && !seen[$1]++ { print $1 }
' > "$PLAIN_CANDIDATES.all"
grep -vxF -f "$ISP_DNS_FILE" "$PLAIN_CANDIDATES.all" > "$PLAIN_CANDIDATES" || true

log "Проверяю, какие DNS отвечают по обычному 53 порту"
PROBE_DIR="$TMP_DIR/probe"
mkdir -p "$PROBE_DIR"
n=0
while IFS= read -r dns; do
    n=$((n + 1))
    (
        if timeout 4 nslookup openwrt.org "$dns" >/dev/null 2>&1; then
            : > "$PROBE_DIR/$n"
        fi
    ) &
done < "$PLAIN_CANDIDATES"
wait

PLAIN_OK="$TMP_DIR/plain-ok.list"
: > "$PLAIN_OK"
n=0
while IFS= read -r dns; do
    n=$((n + 1))
    if [ -e "$PROBE_DIR/$n" ]; then
        printf '%s\n' "$dns" >> "$PLAIN_OK"
        log "  + $dns"
    else
        log "  - $dns (не ответил)"
    fi
done < "$PLAIN_CANDIDATES"

if [ ! -s "$PLAIN_OK" ]; then
    # Ни один не ответил: скорее всего, сеть сейчас вообще недоступна, а не
    # заблокированы все сразу. Пустой список хуже непроверенного.
    warn "Ни один публичный DNS не ответил — записываю список без проверки"
    cp "$PLAIN_CANDIDATES" "$PLAIN_OK"
fi

BOOTSTRAP_FILE="$TMP_DIR/bootstrap.list"
FALLBACK_FILE="$TMP_DIR/fallback.list"
cat "$PLAIN_OK" "$ISP_DNS_FILE" | awk 'NF && !seen[$0]++' > "$BOOTSTRAP_FILE"
cp "$BOOTSTRAP_FILE" "$FALLBACK_FILE"

# Конфиг минимальный намеренно. На роутерах с ~256 МБ RAM dnsproxy с HTTP/3
# и собственным кешем разрастался до ~60 МБ RSS, без них держится в
# единицах мегабайт. Здесь он только раздаёт запросы dnsmasq на несколько
# upstream в режиме parallel: кеширует уже dnsmasq, второй кеш не нужен, а
# QUIC для DoH ничего не даёт, зато стоит памяти. Секции перечислены явно с
# enabled '0', чтобы LuCI и повторный запуск видели, что они выключены.
DNSPROXY_CONFIG_TMP="$TMP_DIR/dnsproxy.config"
cat > "$DNSPROXY_CONFIG_TMP" <<EOF
config dnsproxy 'global'
	list listen_addr '$LISTEN_ADDR'
	list listen_port '$LISTEN_PORT'
	option refuse_any '1'
	option http3 '0'
	option ipv6_disabled '1'
	option enabled '1'
	option verbose '0'
	option upstream_mode 'parallel'

config dnsproxy 'bogus_nxdomain'

config dnsproxy 'cache'
	option enabled '0'
	option cache_optimistic '0'

config dnsproxy 'dns64'
	option enabled '0'

config dnsproxy 'edns'
	option enabled '0'

config dnsproxy 'hosts'
	option enabled '0'

config dnsproxy 'private_rdns'
	option enabled '0'

config dnsproxy 'servers'
EOF

while IFS= read -r dns; do
    [ -n "$dns" ] || continue
    printf "\tlist bootstrap '%s'\n" "$dns" >> "$DNSPROXY_CONFIG_TMP"
done < "$BOOTSTRAP_FILE"

printf '\n' >> "$DNSPROXY_CONFIG_TMP"

while IFS= read -r dns; do
    [ -n "$dns" ] || continue
    printf "\tlist fallback '%s'\n" "$dns" >> "$DNSPROXY_CONFIG_TMP"
done < "$FALLBACK_FILE"

printf '\n' >> "$DNSPROXY_CONFIG_TMP"

# upstream — основной путь, только шифрованные резолверы, режим parallel:
# запрос уходит во все разом, побеждает первый ответ. Четыре независимых
# оператора взяты намеренно — это отправная точка, а не оптимум: скорость и
# доступность публичных резолверов сильно зависят от провайдера и страны,
# поэтому свои стоит проверить (как — описано в README).
if [ -n "$OLD_UPSTREAM" ]; then
    set -f
    for dns in $OLD_UPSTREAM; do
        printf "\tlist upstream '%s'\n" "$dns" >> "$DNSPROXY_CONFIG_TMP"
    done
    set +f
else
    cat >> "$DNSPROXY_CONFIG_TMP" <<'EOF'
	list upstream 'https://dns.cloudflare.com/dns-query'
	list upstream 'https://freedns.controld.com/p0'
	list upstream 'https://dns.quad9.net/dns-query'
	list upstream 'https://dns.adguard-dns.com/dns-query'
EOF
fi

cat >> "$DNSPROXY_CONFIG_TMP" <<'EOF'

config dnsproxy 'tls'
	option enabled '0'
EOF

mkdir -p /etc/config
cp "$DNSPROXY_CONFIG_TMP" /etc/config/dnsproxy
chmod 0600 /etc/config/dnsproxy

log "Настроенные fallback DNS:"
sed 's/^/  - /' "$FALLBACK_FILE"

/etc/init.d/dnsproxy enable >/dev/null 2>&1 || true
if ! /etc/init.d/dnsproxy restart; then
    warn "dnsproxy не запустился"
    rollback
    die "Не удалось запустить dnsproxy. Ничего не изменилось, всё вернул как было"
fi

# restart асинхронный: procd сначала гасит старый процесс (до ~5 с, затем
# SIGKILL), и только потом новый занимает порт и поднимает DoH-сессии.
# Одна проверка через 2 с попадала в это окно и откатывала рабочий конфиг.
dns_up=0
tries=0
while [ "$tries" -lt 10 ]; do
    sleep 2
    if nslookup openwrt.org "$LISTEN_ADDR" >/dev/null 2>&1; then
        dns_up=1
        break
    fi
    tries=$((tries + 1))
done
if [ "$dns_up" != "1" ]; then
    warn "Тестовый DNS-запрос через $LISTEN_ADDR не прошёл"
    log "Последние сообщения dnsproxy:"
    logread -e dnsproxy 2>/dev/null | tail -n 30 || true

    rollback
    die "dnsproxy не прошёл проверку. Ничего не изменилось, всё вернул как было"
fi

log "dnsproxy отвечает на $LISTEN_ADDR:$LISTEN_PORT"

# Podkop — чужой сервис, и по умолчанию скрипт его не трогает: dnsproxy живёт
# на своём адресе и никому не мешает, а переключение DNS — решение владельца
# роутера. Плюс так не возникает скрытой связи: снести dnsproxy, забыв вернуть
# dns_server, значит оставить Podkop с мёртвым резолвером.
PODKOP_PRESENT=0
if [ -f /etc/config/podkop ] && uci -q get podkop.settings >/dev/null 2>&1; then
    PODKOP_PRESENT=1
fi

if [ "$PODKOP_PRESENT" = "1" ] && [ "$CONFIGURE_PODKOP" = "1" ]; then
    # Порт в dns_server не указывается: udp-резолвер и так опрашивается по 53,
    # а с портом диагностика Podkop сообщает об ошибке.
    log "Направляю Podkop на $LISTEN_ADDR"
    uci set podkop.settings.dns_type='udp'
    uci set podkop.settings.dns_server="$LISTEN_ADDR"
    uci commit podkop

    # С этого момента откат обязан возвращать и настройки Podkop.
    write_rollback_kit 1

    if [ "$RESTART_PODKOP" = "1" ] && [ -x /etc/init.d/podkop ]; then
        /etc/init.d/podkop restart || warn "Podkop настроен, но автоматический restart завершился ошибкой"
    fi
elif [ "$PODKOP_PRESENT" = "0" ] && [ "$CONFIGURE_PODKOP" = "1" ]; then
    log "Podkop не найден — его конфиг не изменялся"
fi

# Веб-интерфейс ставится последним, когда DNS уже настроен и проверен, а Podkop
# переключён. Так неудача с ним не может оставить роутер в промежуточном
# состоянии: он либо появится, либо нет, и это ни на что не повлияет.
LUCI_INSTALLED=0
if [ "$INSTALL_PACKAGES" = "1" ] && [ "$INSTALL_LUCI" = "1" ]; then
    log "Устанавливаю luci-app-dnsproxy"
    if install_luci_package; then
        LUCI_INSTALLED=1
    else
        warn "Не удалось установить luci-app-dnsproxy"
        warn "DNS настроен и продолжит работать без веб-интерфейса"
    fi
fi

rm -f /tmp/luci-indexcache* 2>/dev/null || true
rm -rf /tmp/luci-modulecache 2>/dev/null || true
/etc/init.d/rpcd restart >/dev/null 2>&1 || true
/etc/init.d/uhttpd restart >/dev/null 2>&1 || true

# Итоговая проверка. Промежуточные проверки уже были, но между ними и этим
# местом успели произойти установка LuCI-пакета и перезапуски сервисов, а с
# --configure-podkop ещё и перезапуск Podkop. DNS — та вещь, сломав которую
# теряешь возможность чинить остальное, поэтому подтверждается конечное
# состояние, а не то, что было в середине.
verify_final() {
    tries=0
    while [ "$tries" -lt 3 ]; do
        if nslookup openwrt.org "$LISTEN_ADDR" >/dev/null 2>&1; then
            break
        fi
        tries=$((tries + 1))
        sleep 2
    done

    if [ "$tries" -ge 3 ]; then
        warn "dnsproxy не отвечает на $LISTEN_ADDR"
        return 1
    fi

    # Главная проверка: не пострадало ли обычное разрешение имён на роутере.
    # Спрашивается только с тех, у кого оно работало до установки.
    if [ "$BASELINE_DNS_OK" = "1" ] && ! nslookup openwrt.org >/dev/null 2>&1; then
        warn "Разрешение имён на роутере перестало работать"
        return 1
    fi

    return 0
}

log "Проверяю, что всё получилось"
if ! verify_final; then
    log "Последние сообщения dnsproxy:"
    logread -e dnsproxy 2>/dev/null | tail -n 30 || true
    warn "Проверка не прошла — установка не подтвердилась"
    rollback
    die "Не вышло, извините. Всё вернул как было, роутер в прежнем состоянии"
fi

log "Проверка пройдена"

# luci-app-trafficctl вешает свои HTB/IFB qdisc (br-lan, tctl-ifb0). Вместе с
# SQM/CAKE это двойной шейпинг, поэтому при включённом SQM он удаляется.
# Без SQM не трогаем: иначе роутер молча останется вовсе без шейпинга.
remove_trafficctl() {
    pkg_installed=0
    case "$PKG_MANAGER" in
        apk)  apk info -e luci-app-trafficctl >/dev/null 2>&1 && pkg_installed=1 ;;
        opkg) opkg list-installed 2>/dev/null | grep -q '^luci-app-trafficctl ' && pkg_installed=1 ;;
    esac
    [ "$pkg_installed" = "1" ] || [ -x /etc/init.d/trafficctl ] || return 0

    if ! uci -q show sqm 2>/dev/null | grep -q "\.enabled='1'"; then
        warn "Найден luci-app-trafficctl, но SQM не включён — оставляю как есть"
        return 0
    fi

    log "Удаляю luci-app-trafficctl: шейпинг уже делает SQM/CAKE"
    if [ -x /etc/init.d/trafficctl ]; then
        /etc/init.d/trafficctl stop >/dev/null 2>&1 || true
        /etc/init.d/trafficctl disable >/dev/null 2>&1 || true
    fi
    case "$PKG_MANAGER" in
        apk)  apk del luci-app-trafficctl >/dev/null 2>&1 || warn "Не удалось удалить luci-app-trafficctl" ;;
        opkg) opkg remove luci-app-trafficctl >/dev/null 2>&1 || warn "Не удалось удалить luci-app-trafficctl" ;;
    esac
    if command -v tc >/dev/null 2>&1; then
        tc qdisc del dev br-lan root >/dev/null 2>&1 || true
        tc qdisc del dev br-lan ingress >/dev/null 2>&1 || true
    fi
    ip link del tctl-ifb0 >/dev/null 2>&1 || true
    # SQM мог делить интерфейсы с trafficctl — пересобираем его qdisc начисто.
    if [ -x /etc/init.d/sqm ]; then
        /etc/init.d/sqm restart >/dev/null 2>&1 || true
    fi
}
remove_trafficctl

# Диагностика памяти: после свежего старта ориентир — единицы мегабайт RSS.
# Если со временем стабильно растёт до 50–100 МБ, это утечка, которую надо
# разбирать отдельно, а не заливать swap.
show_dnsproxy_diag() {
    PID="$(pidof dnsproxy 2>/dev/null | awk '{print $1}')"
    if [ -z "$PID" ] || [ ! -r "/proc/$PID/cmdline" ]; then
        warn "Процесс dnsproxy не найден, диагностика пропущена"
        return 0
    fi
    CMDLINE="$(tr '\0' ' ' < "/proc/$PID/cmdline")"

    echo "===== DNSPROXY COMMAND ====="
    echo "$CMDLINE"
    echo "===== DNSPROXY MEMORY ====="
    grep -E '^(VmRSS|RssAnon|RssFile|VmData|VmSwap|Threads):' "/proc/$PID/status"
    echo "===== SYSTEM MEMORY ====="
    free -h 2>/dev/null || free

    case " $CMDLINE " in
        *" --http3 "*) warn "В командной строке dnsproxy есть --http3 — HTTP/3 должен быть выключен" ;;
    esac
    case " $CMDLINE " in
        *" --upstream-mode parallel "*|*" --upstream-mode=parallel "*) ;;
        *) warn "В командной строке dnsproxy нет --upstream-mode parallel" ;;
    esac
    case " $CMDLINE " in
        *" --ipv6-disabled "*) ;;
        *) warn "В командной строке dnsproxy нет --ipv6-disabled" ;;
    esac
}
show_dnsproxy_diag
log "Готово"
log "Версия установщика: $SCRIPT_VERSION"
log "Резервные копии: $BACKUP_DIR"
log "Откат: sh $BACKUP_DIR/rollback.sh"
if [ "$LUCI_INSTALLED" = "1" ]; then
    log "LuCI: Сервисы -> DNS Proxy"
fi
log "dnsproxy слушает: $LISTEN_ADDR:$LISTEN_PORT"

# Тест серверов выполняется до финальной инструкции по Podkop, а не после:
# у него собственная многострочная таблица, и инструкция обязана остаться
# последней на экране (см. комментарий ниже).
if [ "$TEST_SERVERS" = "1" ]; then
    log ""
    log "Тестирую upstream-сервера из списка..."

    if [ -f "$SCRIPT_DIR/test-doh.sh" ]; then
        TEST_DOH_PATH="$SCRIPT_DIR/test-doh.sh"
    else
        TEST_DOH_PATH="$TMP_DIR/test-doh.sh"
        if ! wget -q -O "$TEST_DOH_PATH" "$RAW_BASE/test-doh.sh"; then
            warn "Не удалось скачать test-doh.sh, тест пропущен"
            TEST_DOH_PATH=""
        fi
    fi

    if [ -n "$TEST_DOH_PATH" ]; then
        if [ -n "$SERVERS_LIST_OVERRIDE" ]; then
            SERVERS_LIST_PATH="$SERVERS_LIST_OVERRIDE"
        elif [ -f "$SCRIPT_DIR/servers.txt" ]; then
            SERVERS_LIST_PATH="$SCRIPT_DIR/servers.txt"
        else
            SERVERS_LIST_PATH="$TMP_DIR/servers.txt"
            if ! wget -q -O "$SERVERS_LIST_PATH" "$RAW_BASE/servers.txt"; then
                warn "Не удалось скачать servers.txt, тест пропущен"
                SERVERS_LIST_PATH=""
            fi
        fi

        if [ -n "${SERVERS_LIST_PATH:-}" ]; then
            sh "$TEST_DOH_PATH" "$SERVERS_LIST_PATH" || warn "Тест серверов завершился с ошибкой"
        fi
    fi
fi

# Инструкция печатается последней, чтобы остаться на экране: сам по себе
# dnsproxy работает вхолостую, пока в него никто не ходит.
if [ "$PODKOP_PRESENT" = "1" ] && [ "$CONFIGURE_PODKOP" != "1" ]; then
    cat <<EOF

Осталось направить Podkop на dnsproxy. Скрипт этого не делает — вы делаете это
сами, один раз:

  1. Скопируйте адрес: $LISTEN_ADDR
  2. Откройте LuCI -> Сервисы -> Podkop, основные настройки
  3. Тип DNS: udp
  4. В поле DNS-сервера вставьте скопированный адрес, без порта:
     udp-резолвер и так опрашивается по 53, а с портом диагностика Podkop
     сообщает об ошибке
  5. Нажмите Save & Apply и дождитесь перезапуска Podkop, это несколько секунд
  6. Проверьте, что сайты открываются и прокси работает

То же самое из консоли:

  uci set podkop.settings.dns_type='udp'
  uci set podkop.settings.dns_server='$LISTEN_ADDR'
  uci commit podkop
  /etc/init.d/podkop restart

Если после этого что-то сломается: прежний конфиг Podkop лежит в
$BACKUP_DIR/podkop.config, а вернуть роутер к состоянию до установки целиком
можно так:

  sh $BACKUP_DIR/rollback.sh
EOF
fi
