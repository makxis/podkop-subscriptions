#!/bin/sh
# Проверка DoH/DoT/DoQ upstream-серверов: только dnsproxy и nslookup, которые
# и так нужны install-dnsproxy.sh, никакого python3.
#
# Тестирует список последовательно, по одному серверу за раз — без пула
# воркеров. Полный параллелизм на busybox ash (wait -n, слежение за
# несколькими pid) значительно сложнее и хрупче на разных версиях busybox;
# для разового прогона тест на 30-40 серверов и без этого укладывается в
# разумное время.
set -eu

DNSPROXY="/usr/bin/dnsproxy"

# Собственный адрес теста, отдельно от рабочего dnsproxy на 127.0.0.10.
LISTEN_ADDR="127.0.0.11"
LISTEN_PORT="53"

WARMUP_DOMAIN="example.com"
TEST_DOMAINS="github.com raw.githubusercontent.com release-assets.githubusercontent.com"

BOOTSTRAP1="8.8.4.4"
BOOTSTRAP2="1.0.0.1"
BOOTSTRAP3="9.9.9.9"

QUERY_TIMEOUT_MS=1000
START_DELAY=1

SCRIPT_PATH="$0"
case "$SCRIPT_PATH" in
    */*) SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)" ;;
    *) SCRIPT_DIR="$(pwd)" ;;
esac

log() { printf '%s\n' "[test-doh] $*"; }
warn() { printf '%s\n' "[test-doh] WARNING: $*" >&2; }
die() { printf '%s\n' "[test-doh] ERROR: $*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Использование:
  sh test-doh.sh [--timeout-ms N] [список-серверов]

По умолчанию берётся servers.txt рядом со скриптом, а если его там нет —
/tmp/servers.txt. Требуется установленный dnsproxy (install-dnsproxy.sh) и
root, поскольку тестовый dnsproxy слушает 127.0.0.11:53.

  --timeout-ms N   Сколько ждать ответа на один запрос, мс (по умолчанию
                    1000). Сервер, не ответивший за это время, засчитывается
                    как FAIL по этому запросу — так медленные upstream не
                    держат весь прогон по несколько секунд каждый.

В конце, если запущено в терминале и найден хотя бы один сервер 3/3,
скрипт спросит, не заменить ли текущий upstream dnsproxy (top-4 по задержке)
на протестированные — ответ y/д применяет, что угодно другое пропускает.
Перед записью текущий /etc/config/dnsproxy сохраняется в /root, а после
перезапуска dnsproxy проверяется ответом на openwrt.org: если не отвечает —
автоматический откат к прежним серверам.
EOF
}

LIST=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --help|-h)
            usage
            exit 0
            ;;
        --timeout-ms)
            [ "$#" -ge 2 ] || die "После --timeout-ms нужно число миллисекунд"
            QUERY_TIMEOUT_MS="$2"
            shift 2
            ;;
        *)
            LIST="$1"
            shift
            ;;
    esac
done

if [ -z "$LIST" ]; then
    if [ -r "$SCRIPT_DIR/servers.txt" ]; then
        LIST="$SCRIPT_DIR/servers.txt"
    else
        LIST="/tmp/servers.txt"
    fi
fi

case "$QUERY_TIMEOUT_MS" in
    ''|*[!0-9]*) die "--timeout-ms должен быть целым числом миллисекунд, получено: $QUERY_TIMEOUT_MS" ;;
esac
[ "$QUERY_TIMEOUT_MS" -ge 100 ] || die "--timeout-ms слишком мал (минимум 100): $QUERY_TIMEOUT_MS"

# dnsproxy получает свой собственный upstream-таймаут в целых секундах,
# округлённый вверх до ближайшей секунды: держать соединение открытым дольше,
# чем мы всё равно готовы ждать ответ, смысла нет.
DNSPROXY_TIMEOUT_S=$(( (QUERY_TIMEOUT_MS + 999) / 1000 ))

[ "$(id -u)" = "0" ] || die "Скрипт нужно запускать от root"
[ -r "$LIST" ] || die "Список серверов не найден: $LIST"
[ -x "$DNSPROXY" ] || command -v dnsproxy >/dev/null 2>&1 || die "dnsproxy не найден. Сначала установите его: install-dnsproxy.sh"
command -v "$DNSPROXY" >/dev/null 2>&1 || DNSPROXY="dnsproxy"
command -v nslookup >/dev/null 2>&1 || die "Не найдена команда nslookup"

if command -v netstat >/dev/null 2>&1; then
    if netstat -lnu 2>/dev/null | grep -q "$LISTEN_ADDR:$LISTEN_PORT "; then
        die "$LISTEN_ADDR:$LISTEN_PORT уже занят"
    fi
fi

# Разделитель полей во временных файлах — 0x1F (unit separator), а не tab:
# tab входит в IFS как "пробельный" символ, и read/awk -F схлопывают подряд
# идущие пробельные разделители, из-за чего пустые поля (FAIL/DEAD без
# t1/t2/t3) съедали соседние колонки и сдвигали всю строку. US не пробельный
# и никогда не встретится в URL, поэтому не схлопывается и ни с чем не
# пересекается.
US="$(printf '\037')"

URLS_FILE="/tmp/test-doh-urls.$$"
RESULTS_FILE="/tmp/test-doh-results.$$"
SORTED_FILE="/tmp/test-doh-sorted.$$"
DNS_PID=""

cleanup() {
    if [ -n "$DNS_PID" ]; then
        kill "$DNS_PID" 2>/dev/null || true
        wait "$DNS_PID" 2>/dev/null || true
    fi
    rm -f "$URLS_FILE" "$RESULTS_FILE" "$SORTED_FILE"
}
trap cleanup EXIT INT TERM

# ---------------------------------------------------------
# Читаем список upstream: пустые строки и строки с # игнорируются,
# из остального берётся первый токен, комментарий после URL разрешён.
# ---------------------------------------------------------

awk '
{
    line = $0
    sub(/^[ \t]+/, "", line)
    sub(/[ \t]+$/, "", line)
    if (line == "" || line ~ /^#/) next
    n = split(line, parts, /[ \t]+/)
    url = parts[1]
    if (url ~ /^(https|tls|quic):\/\//) print url
}
' "$LIST" > "$URLS_FILE"

TOTAL="$(wc -l < "$URLS_FILE" | tr -d ' ')"
[ "$TOTAL" -gt 0 ] || die "В $LIST не найдено DNS upstream"

: > "$RESULTS_FILE"

log "Список: $LIST"
log "Найдено upstream: $TOTAL"
log "Тест последовательный, без параллелизма."
log "Таймаут одного запроса: ${QUERY_TIMEOUT_MS}ms"
log ""

uptime_ms() {
    awk '{printf "%.0f", $1 * 1000}' /proc/uptime
}

name_from_url() {
    s="$1"
    case "$s" in
        *://*) s="${s#*://}" ;;
    esac
    case "$s" in
        *@*) s="${s##*@}" ;;
    esac
    s="${s%%/*}"
    case "$s" in
        \[*)
            s="${s#\[}"
            s="${s%%]*}"
            ;;
        *:*)
            s="${s%%:*}"
            ;;
    esac
    printf '%s' "$s"
}

fmt() {
    [ -n "${1:-}" ] && printf '%s' "$1" || printf 'FAIL'
}

# Спрашивает, не заменить ли текущий upstream dnsproxy на лучшие протестированные
# сервера, и если да — записывает их через uci и перезапускает dnsproxy.
# Ничего не меняет без явного "да": конфиг живого резолвера — не то, что
# трогают по умолчанию.
apply_best_servers() {
    UCI_CONFIG="/etc/config/dnsproxy"

    if [ ! -t 0 ]; then
        log "Неинтерактивный запуск (нет tty) — вопрос про замену upstream пропущен"
        return 0
    fi
    if ! command -v uci >/dev/null 2>&1; then
        warn "uci не найден, менять upstream некому"
        return 0
    fi
    if [ ! -f "$UCI_CONFIG" ]; then
        warn "$UCI_CONFIG не найден — dnsproxy не настроен через install-dnsproxy.sh, пропускаю"
        return 0
    fi

    BEST_FILE="/tmp/test-doh-best.$$"
    awk -F"$US" '$7 == "GOOD" { print }' "$SORTED_FILE" > "$BEST_FILE"

    best_total="$(wc -l < "$BEST_FILE" | tr -d ' ')"
    if [ "$best_total" -eq 0 ]; then
        warn "Ни один сервер не прошёл проверку 3/3 (уложился в ${QUERY_TIMEOUT_MS}ms) — применять нечего"
        rm -f "$BEST_FILE"
        return 0
    fi

    top_n=4
    [ "$best_total" -lt "$top_n" ] && top_n="$best_total"

    printf '\n'
    printf 'Лучшие %s из %s серверов, прошедших 3/3 (по средней задержке):\n' "$top_n" "$best_total"
    i=0
    while IFS="$US" read -r key url t1 t2 t3 success state avg; do
        i=$((i + 1))
        [ "$i" -gt "$top_n" ] && break
        printf '  %s. %s (avg=%sms)\n' "$i" "$url" "$avg"
    done < "$BEST_FILE"

    printf '\n'
    printf 'Записать эти %s сервера(ов) в dnsproxy вместо текущих upstream? [y/N]: ' "$top_n"
    read -r ans || ans=""
    case "$ans" in
        y|Y|yes|YES|Yes|д|Д|да|Да|ДА) ;;
        *)
            rm -f "$BEST_FILE"
            return 0
            ;;
    esac

    ts="$(date +%Y%m%d-%H%M%S 2>/dev/null || echo now)"
    backup="/root/dnsproxy-servers-backup-$ts.config"
    if ! cp -p "$UCI_CONFIG" "$backup" 2>/dev/null; then
        warn "Не удалось сделать бэкап $UCI_CONFIG, отменяю"
        rm -f "$BEST_FILE"
        return 1
    fi
    log "Бэкап текущего конфига: $backup"

    uci -q delete dnsproxy.servers.upstream 2>/dev/null || true
    i=0
    while IFS="$US" read -r key url t1 t2 t3 success state avg; do
        i=$((i + 1))
        [ "$i" -gt "$top_n" ] && break
        uci add_list dnsproxy.servers.upstream="$url" || true
    done < "$BEST_FILE"
    rm -f "$BEST_FILE"

    if ! uci commit dnsproxy; then
        warn "uci commit не удался, откатываю"
        cp -p "$backup" "$UCI_CONFIG" 2>/dev/null || true
        return 1
    fi

    PROD_LISTEN_ADDR="$(uci -q get dnsproxy.global.listen_addr 2>/dev/null | awk '{print $1}')"
    [ -n "$PROD_LISTEN_ADDR" ] || PROD_LISTEN_ADDR="127.0.0.10"

    log "Перезапускаю dnsproxy..."
    if ! /etc/init.d/dnsproxy restart; then
        warn "dnsproxy не перезапустился, откатываю конфиг"
        cp -p "$backup" "$UCI_CONFIG" 2>/dev/null || true
        /etc/init.d/dnsproxy restart >/dev/null 2>&1 || true
        return 1
    fi

    # Одной попытки сразу после restart недостаточно: на реальном роутере
    # dnsproxy после перезапуска ещё поднимает TLS/QUIC-сессии к непрогретым
    # upstream (новый top-4 не участвовал в разогреве, в отличие от списка,
    # который тестировался только что), и первый запрос попадает точно в
    # этот момент — в логе были i/o timeout к фоллбэку и quad9, хотя через
    # несколько секунд тот же адрес отвечал нормально. Троим попыткам с
    # паузами это переживать, как и в финальной проверке install-dnsproxy.sh.
    verify_ok=0
    tries=0
    while [ "$tries" -lt 3 ]; do
        sleep 2
        if nslookup openwrt.org "$PROD_LISTEN_ADDR" >/dev/null 2>&1; then
            verify_ok=1
            break
        fi
        tries=$((tries + 1))
    done

    if [ "$verify_ok" -ne 1 ]; then
        warn "dnsproxy не отвечает на $PROD_LISTEN_ADDR с новыми серверами, откатываю"
        cp -p "$backup" "$UCI_CONFIG" 2>/dev/null || true
        uci commit dnsproxy 2>/dev/null || true
        /etc/init.d/dnsproxy restart >/dev/null 2>&1 || true
        warn "Откат выполнен, upstream остались прежними"
        return 1
    fi

    log "Готово: dnsproxy на $PROD_LISTEN_ADDR теперь использует $top_n протестированных сервера(ов)"
    log "Бэкап предыдущей версии: $backup"
}

# Обрывает запрос через QUERY_TIMEOUT_MS. На busybox из install-dnsproxy.sh
# нет ни команды timeout, ни дробного sleep (проверено на реальном роутере:
# "sleep: invalid number '0.1'"), поэтому таймаут — это второй фоновый
# процесс-часы (sleep в целых секундах), а не обёртка над nslookup. Опрос
# идёт busy-loop'ом по kill -0 у обоих pid: это дороже по CPU, чем сон, но на
# разовой ручной проверке пары десятков серверов не имеет значения, а
# получить не то что нет надёжного дробного таймера без this.
run_query() {
    domain="$1"
    t0="$(uptime_ms)"

    nslookup "$domain" "$LISTEN_ADDR" >/dev/null 2>&1 &
    qpid=$!

    sleep "$DNSPROXY_TIMEOUT_S" &
    clock_pid=$!

    while kill -0 "$qpid" 2>/dev/null && kill -0 "$clock_pid" 2>/dev/null; do
        :
    done

    if kill -0 "$qpid" 2>/dev/null; then
        # Часы сработали первыми: запрос не уложился в таймаут.
        kill "$qpid" 2>/dev/null || true
        wait "$qpid" 2>/dev/null || true
        ok=0
    else
        if wait "$qpid" 2>/dev/null; then
            ok=1
        else
            ok=0
        fi
        kill "$clock_pid" 2>/dev/null || true
        wait "$clock_pid" 2>/dev/null || true
    fi

    if [ "$ok" -eq 1 ]; then
        t1="$(uptime_ms)"
        echo $((t1 - t0))
    else
        echo ""
    fi
}

INDEX=0
while IFS= read -r url; do
    INDEX=$((INDEX + 1))
    name="$(name_from_url "$url")"
    log_file="/tmp/test-doh-$INDEX.log"

    "$DNSPROXY" \
        --listen "$LISTEN_ADDR" \
        --port "$LISTEN_PORT" \
        --upstream "$url" \
        --ipv6-disabled \
        --timeout "${DNSPROXY_TIMEOUT_S}s" \
        --http3 \
        --bootstrap "$BOOTSTRAP1" \
        --bootstrap "$BOOTSTRAP2" \
        --bootstrap "$BOOTSTRAP3" \
        >"$log_file" 2>&1 &
    DNS_PID=$!

    sleep "$START_DELAY"

    if ! kill -0 "$DNS_PID" 2>/dev/null; then
        wait "$DNS_PID" 2>/dev/null || true
        DNS_PID=""
        success=0
        t1=""; t2=""; t3=""; avg=""
        state="STARTFAIL"
    else
        run_query "$WARMUP_DOMAIN" >/dev/null

        n=0
        for domain in $TEST_DOMAINS; do
            n=$((n + 1))
            latency="$(run_query "$domain")"
            eval "t$n=\"\$latency\""
        done

        kill "$DNS_PID" 2>/dev/null || true
        wait "$DNS_PID" 2>/dev/null || true
        DNS_PID=""

        success=0
        [ -n "${t1:-}" ] && success=$((success + 1))
        [ -n "${t2:-}" ] && success=$((success + 1))
        [ -n "${t3:-}" ] && success=$((success + 1))

        avg="$(awk -v a="${t1:-}" -v b="${t2:-}" -v c="${t3:-}" 'BEGIN{
            n = 0; s = 0
            if (a != "") { s += a; n++ }
            if (b != "") { s += b; n++ }
            if (c != "") { s += c; n++ }
            if (n > 0) printf "%.0f", s / n; else printf ""
        }')"

        case "$success" in
            3) state="GOOD" ;;
            0) state="DEAD" ;;
            *) state="UNSTABLE" ;;
        esac
    fi

    printf '[%02d/%02d] %-40s %s/3  %5s/%5s/%5s ms  avg=%-5s  %s\n' \
        "$INDEX" "$TOTAL" "$name" "$success" \
        "$(fmt "${t1:-}")" "$(fmt "${t2:-}")" "$(fmt "${t3:-}")" "$(fmt "$avg")" "$state"

    inv_success=$((3 - success))
    sort_key="$(printf '%d%07d' "$inv_success" "${avg:-9999999}")"
    printf "%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s${US}%s\n" \
        "$sort_key" "$url" "${t1:-}" "${t2:-}" "${t3:-}" "$success" "$state" "${avg:-}" \
        >> "$RESULTS_FILE"
done < "$URLS_FILE"

# ---------------------------------------------------------
# Итоговая таблица: сортировка по числу успешных ответов (больше — лучше),
# затем по средней задержке (меньше — лучше). Ключ в первом поле —
# фиксированной ширины, поэтому обычной лексикографической сортировки
# достаточно и не нужны флаги sort -k/-n, которых нет в урезанном busybox.
# ---------------------------------------------------------

sort "$RESULTS_FILE" > "$SORTED_FILE"

WIDTH=150
SEP="$(printf '%*s' "$WIDTH" '' | tr ' ' '=')"

printf '\n%s\n' "$SEP"
printf 'ИТОГ\n'
printf '%s\n' "$SEP"
printf '%2s %-72s %5s %10s %10s %10s %10s %10s\n' \
    "#" "DNS URL" "OK" "GitHub" "Raw" "Assets" "AVG" "STATUS"
printf '%s\n' "$(printf '%*s' "$WIDTH" '' | tr ' ' '-')"

pos=0
while IFS="$US" read -r key url t1 t2 t3 success state avg; do
    pos=$((pos + 1))
    printf '%2d %-72s %s/3 %10s %10s %10s %10s %10s\n' \
        "$pos" "$url" "$success" "$(fmt "$t1")" "$(fmt "$t2")" "$(fmt "$t3")" "$(fmt "$avg")" "$state"
done < "$SORTED_FILE"

printf '\n'
printf 'GitHub = github.com\n'
printf 'Raw    = raw.githubusercontent.com\n'
printf 'Assets = release-assets.githubusercontent.com\n'
printf '\n'
printf 'GOOD      3/3\n'
printf 'UNSTABLE  1/3 или 2/3\n'
printf 'DEAD      0/3\n'
printf 'STARTFAIL dnsproxy не смог запуститься\n'
printf '\n'
printf 'Логи отдельных тестов: /tmp/test-doh-*.log\n'

apply_best_servers
