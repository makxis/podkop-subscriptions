#!/bin/sh
# Автоматическая установка и настройка AdGuard dnsproxy для OpenWrt.
#
# Что делает:
#   1. Определяет ветку OpenWrt, менеджер пакетов (apk или opkg) и архитектуру.
#   2. Устанавливает dnsproxy из штатного репозитория OpenWrt.
#   3. Скачивает подходящий luci-app-dnsproxy из Fantastic Packages.
#   4. Настраивает dnsproxy на 127.0.0.10:53.
#   5. Добавляет несколько независимых защищённых upstream-серверов.
#   6. Добавляет текущие IPv4 DNS-серверы провайдера в fallback.
#   7. При наличии Podkop направляет его DNS на 127.0.0.10:53.
#
# Повторный запуск безопасен: конфиги предварительно сохраняются в /root.

set -eu

SCRIPT_VERSION="1.1.0"
FANTASTIC_ROOT="https://fantastic-packages.github.io/releases"
LISTEN_ADDR="127.0.0.10"
LISTEN_PORT="53"
CONFIGURE_PODKOP=1
RESTART_PODKOP=1
ADD_ISP_DNS=1
INSTALL_PACKAGES=1
OPENWRT_SERIES_OVERRIDE=""
PACKAGE_ARCH_OVERRIDE=""

log() {
    printf '%s\n' "[dnsproxy-installer] $*"
}

warn() {
    printf '%s\n' "[dnsproxy-installer] WARNING: $*" >&2
}

die() {
    printf '%s\n' "[dnsproxy-installer] ERROR: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Использование:
  sh install-dnsproxy.sh [параметры]

Параметры:
  --no-podkop          Не изменять /etc/config/podkop.
  --no-podkop-restart  Настроить Podkop, но не перезапускать его.
  --no-isp-dns         Не добавлять DNS-серверы провайдера в fallback.
  --config-only        Не устанавливать пакеты, только записать конфиг.
  --release 24.10      Принудительно указать ветку Fantastic Packages.
  --arch x86_64        Принудительно указать архитектуру пакетов.
  --help               Показать эту справку.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --no-podkop)
            CONFIGURE_PODKOP=0
            shift
            ;;
        --no-podkop-restart)
            RESTART_PODKOP=0
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
        --release)
            [ "$#" -ge 2 ] || die "После --release нужна версия, например 24.10"
            OPENWRT_SERIES_OVERRIDE="$2"
            shift 2
            ;;
        --arch)
            [ "$#" -ge 2 ] || die "После --arch нужна архитектура, например x86_64"
            PACKAGE_ARCH_OVERRIDE="$2"
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

LOCK_DIR="/tmp/install-dnsproxy.lock"
TMP_DIR="/tmp/install-dnsproxy.$$"
BACKUP_DIR="/root/dnsproxy-backup-$(date +%Y%m%d-%H%M%S)"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    die "Установщик уже запущен: $LOCK_DIR"
fi
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
    log "Обновляю списки пакетов"
    case "$PKG_MANAGER" in
        apk)  apk update || die "apk update завершился с ошибкой" ;;
        opkg) opkg update || die "opkg update завершился с ошибкой" ;;
    esac

    log "Устанавливаю dnsproxy и сертификаты"
    case "$PKG_MANAGER" in
        apk)  apk add ca-bundle ca-certificates dnsproxy || die "Не удалось установить пакет dnsproxy" ;;
        opkg) opkg install ca-bundle ca-certificates dnsproxy || die "Не удалось установить пакет dnsproxy" ;;
    esac

    # Нужно только ветке opkg: там индекс Fantastic Packages — это Packages.gz.
    if [ "$PKG_MANAGER" = "opkg" ]; then
        if ! command -v zcat >/dev/null 2>&1 && ! command -v gzip >/dev/null 2>&1; then
            opkg install gzip || die "Не удалось установить gzip для чтения Packages.gz"
        fi
    fi
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
    if [ -n "$PACKAGE_ARCH_OVERRIDE" ]; then
        printf '%s\n' "$PACKAGE_ARCH_OVERRIDE"
        return 0
    fi

    # DISTRIB_ARCH из /etc/openwrt_release совпадает с именами каталогов
    # Fantastic Packages и есть на обеих ветках, поэтому он идёт первым.
    [ -n "${DISTRIB_ARCH:-}" ] && printf '%s\n' "$DISTRIB_ARCH"

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

# index.json — плоский словарь {"имя-пакета": "версия"}.
lookup_index_version() {
    file="$1"
    name="$2"
    if command -v jsonfilter >/dev/null 2>&1; then
        jsonfilter -i "$file" -e "@[\"$name\"]" 2>/dev/null && return 0
    fi
    sed -n "s/.*\"$name\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$file" | head -n 1
}

install_luci_package() {
    PACKAGE_ARCH="$(detect_package_arch)" \
        || die "Fantastic Packages не содержит LuCI-пакеты для архитектур этого роутера (ветка $OPENWRT_SERIES)"
    LUCI_BASE="$FANTASTIC_ROOT/$OPENWRT_SERIES/packages/$PACKAGE_ARCH/luci"
    log "Архитектура: $PACKAGE_ARCH"

    if [ "$PKG_MANAGER" = "apk" ]; then
        # У apk индекс packages.adb бинарный, поэтому имя файла собирается
        # из версии в index.json: <имя>-<версия>.apk.
        index_file="$TMP_DIR/index-$PACKAGE_ARCH.json"
        [ -s "$index_file" ] || wget -q -O "$index_file" "$LUCI_BASE/index.json" \
            || die "Не удалось скачать индекс Fantastic Packages: $LUCI_BASE"

        luci_version="$(lookup_index_version "$index_file" luci-app-dnsproxy)"
        [ -n "$luci_version" ] || die "В репозитории не найден luci-app-dnsproxy"

        LUCI_FILENAME="luci-app-dnsproxy-${luci_version}.apk"
        local_file="$TMP_DIR/luci-app-dnsproxy.apk"
    else
        packages_gz="$TMP_DIR/Packages-$PACKAGE_ARCH.gz"
        wget -q -O "$packages_gz" "$LUCI_BASE/Packages.gz" \
            || die "Не удалось скачать индекс Fantastic Packages: $LUCI_BASE"

        LUCI_FILENAME="$(read_packages_gz "$packages_gz" | awk '
            $1 == "Package:" { wanted = ($2 == "luci-app-dnsproxy") }
            wanted && $1 == "Filename:" { print $2; exit }
        ')"
        [ -n "$LUCI_FILENAME" ] || die "В репозитории не найден luci-app-dnsproxy"

        local_file="$TMP_DIR/luci-app-dnsproxy.ipk"
    fi

    log "Скачиваю $LUCI_FILENAME"
    wget -O "$local_file" "$LUCI_BASE/$LUCI_FILENAME" \
        || die "Не удалось скачать luci-app-dnsproxy"

    case "$PKG_MANAGER" in
        apk)
            # Пакет не подписан ключом, который знает роутер, и ставится файлом,
            # а не из подключённого репозитория — apk требует оба флага.
            apk add --allow-untrusted --force-non-repository "$local_file" \
                || die "Не удалось установить luci-app-dnsproxy"
            ;;
        opkg)
            opkg install "$local_file" || die "Не удалось установить luci-app-dnsproxy"
            ;;
    esac
}

if [ "$INSTALL_PACKAGES" = "1" ]; then
    install_packages
    install_luci_package
else
    command -v dnsproxy >/dev/null 2>&1 || die "--config-only указан, но dnsproxy не установлен"
fi

# Бэкап делается после установки: на чистом роутере /etc/config/dnsproxy
# появляется только вместе с пакетом, и до установки откатывать было бы нечего.
backup_file /etc/config/dnsproxy dnsproxy.config

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

FALLBACK_FILE="$TMP_DIR/fallback.list"
cat > "$FALLBACK_FILE" <<'EOF'
8.8.4.4
1.0.0.1
223.5.5.5
94.140.14.140
77.88.8.8
EOF
cat "$ISP_DNS_FILE" >> "$FALLBACK_FILE"
awk 'NF && !seen[$0]++ { print $0 }' "$FALLBACK_FILE" > "$FALLBACK_FILE.unique"
mv "$FALLBACK_FILE.unique" "$FALLBACK_FILE"

DNSPROXY_CONFIG_TMP="$TMP_DIR/dnsproxy.config"
cat > "$DNSPROXY_CONFIG_TMP" <<EOF
config dnsproxy 'global'
	list listen_addr '$LISTEN_ADDR'
	list listen_port '$LISTEN_PORT'
	option refuse_any '1'
	option http3 '1'
	option ipv6_disabled '1'
	option enabled '1'
	option upstream_mode 'parallel'

config dnsproxy 'bogus_nxdomain'

config dnsproxy 'cache'
	option cache_optimistic '1'
	option size '65535'
	option enabled '1'
	option min_ttl '60'
	option max_ttl '3600'

config dnsproxy 'dns64'
	option dns64_prefix '64:ff9b::'

config dnsproxy 'edns'

config dnsproxy 'hosts'
	option enabled '0'
	list hosts_files ''

config dnsproxy 'private_rdns'
	option enabled '0'
	list upstream '127.0.0.1:53'

config dnsproxy 'servers'
	list bootstrap '8.8.4.4'
	list bootstrap '1.0.0.1'
	list bootstrap '223.5.5.5'
	list bootstrap '94.140.14.140'
EOF

while IFS= read -r dns; do
    [ -n "$dns" ] || continue
    printf "\tlist fallback '%s'\n" "$dns" >> "$DNSPROXY_CONFIG_TMP"
done < "$FALLBACK_FILE"

cat >> "$DNSPROXY_CONFIG_TMP" <<'EOF'

	list upstream 'h3://dns.google/dns-query'
	list upstream 'https://dns.cloudflare.com/dns-query'
	list upstream 'https://dns.alidns.com/dns-query'
	list upstream 'tls://unfiltered.adguard-dns.com'

config dnsproxy 'tls'
	option enabled '0'
	option https_port '8443'
	option tls_port '853'
	option quic_port '853'
EOF

mkdir -p /etc/config
cp "$DNSPROXY_CONFIG_TMP" /etc/config/dnsproxy
chmod 0600 /etc/config/dnsproxy

log "Настроенные fallback DNS:"
sed 's/^/  - /' "$FALLBACK_FILE"

/etc/init.d/dnsproxy enable >/dev/null 2>&1 || true
if ! /etc/init.d/dnsproxy restart; then
    warn "dnsproxy не запустился; восстанавливаю прежний конфиг"
    if [ -f "$BACKUP_DIR/dnsproxy.config" ]; then
        cp "$BACKUP_DIR/dnsproxy.config" /etc/config/dnsproxy
        /etc/init.d/dnsproxy restart >/dev/null 2>&1 || true
    fi
    die "Не удалось запустить dnsproxy"
fi

sleep 2
if ! nslookup openwrt.org "$LISTEN_ADDR" >/dev/null 2>&1; then
    warn "Тестовый DNS-запрос через $LISTEN_ADDR не прошёл"
    log "Последние сообщения dnsproxy:"
    logread -e dnsproxy 2>/dev/null | tail -n 30 || true

    if [ -f "$BACKUP_DIR/dnsproxy.config" ]; then
        warn "Восстанавливаю прежний конфиг dnsproxy"
        cp "$BACKUP_DIR/dnsproxy.config" /etc/config/dnsproxy
        /etc/init.d/dnsproxy restart >/dev/null 2>&1 || true
    fi
    die "Настройка отменена: dnsproxy не прошёл проверку"
fi

log "dnsproxy отвечает на $LISTEN_ADDR:$LISTEN_PORT"

if [ "$CONFIGURE_PODKOP" = "1" ] && [ -f /etc/config/podkop ] && uci -q get podkop.settings >/dev/null 2>&1; then
    log "Направляю Podkop на $LISTEN_ADDR:$LISTEN_PORT"
    uci set podkop.settings.dns_type='udp'
    uci set podkop.settings.dns_server="$LISTEN_ADDR:$LISTEN_PORT"
    uci commit podkop

    if [ "$RESTART_PODKOP" = "1" ] && [ -x /etc/init.d/podkop ]; then
        /etc/init.d/podkop restart || warn "Podkop настроен, но автоматический restart завершился ошибкой"
    fi
else
    if [ "$CONFIGURE_PODKOP" = "1" ]; then
        log "Podkop не найден — его конфиг не изменялся"
    fi
fi

rm -f /tmp/luci-indexcache* 2>/dev/null || true
rm -rf /tmp/luci-modulecache 2>/dev/null || true
/etc/init.d/rpcd restart >/dev/null 2>&1 || true
/etc/init.d/uhttpd restart >/dev/null 2>&1 || true

log "Готово"
log "Версия установщика: $SCRIPT_VERSION"
log "Резервные копии: $BACKUP_DIR"
log "LuCI: Сервисы -> DNS Proxy"
log "DNS для Podkop: $LISTEN_ADDR:$LISTEN_PORT"
