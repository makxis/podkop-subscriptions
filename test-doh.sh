#!/bin/sh
# Проверка DNS-серверов для dnsproxy на самом роутере: только dnsproxy и
# nslookup из busybox, никакого python3. Итог — два списка, применяются разом:
#   Upstream DNS Server             — DoH/DoT/DoQ (URL в servers.txt);
#   Bootstrap и Fallback DNS Server — обычные DNS (голые IPv4 в servers.txt).
#
# Нагрузка намеренно минимальная: роутеры бывают с 256 МБ RAM и слабым CPU.
#   - Серверы проверяются строго по одному. Тестовый dnsproxy в каждый момент
#     запущен максимум один и гасится сразу SIGKILL: по SIGTERM он ещё ~2 с
#     дожидается незавершённых запросов, а состояния, которое стоило бы
#     сохранять, у него нет.
#   - Ожидание ответа — собственный таймаут nslookup (-timeout). Ни циклов
#     опроса, ни фоновых таймеров: пока сервер молчит, процессор свободен.
#   - Время замеряется чтением /proc/uptime встроенным read, без запуска awk.
#   - Сервер, не ответивший хоть раз, дальше не опрашивается: 3/3 он уже не
#     наберёт, а в итоговые списки попадают только серверы с 3/3.
set -eu

DNSPROXY="/usr/bin/dnsproxy"

# Собственный адрес теста, отдельно от рабочего dnsproxy на 127.0.0.10.
LISTEN_ADDR="127.0.0.11"
LISTEN_PORT="53"

WARMUP_DOMAIN="example.com"
TEST_DOMAINS="github.com raw.githubusercontent.com release-assets.githubusercontent.com"

# Сколько серверов попадает в итоговые списки.
UPSTREAM_PICK=5
# Мало намеренно: fallback dnsproxy опрашивает все адреса разом, и при
# недоступных upstream каждый запрос держит по горутине на адрес.
PLAIN_PICK=4

# bootstrap тестового dnsproxy на случай, если ни один обычный DNS не прошёл
# проверку. Дописывается к DNS провайдера, если они известны.
DEFAULT_BOOTSTRAP="8.8.4.4 1.0.0.1 9.9.9.9"

QUERY_TIMEOUT_MS=1000

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

Формат списка — одна строка на сервер: АДРЕС [ОПЕРАТОР].
  URL (https://, tls://, quic://) — кандидат в Upstream DNS Server;
  голый IPv4                       — кандидат в Bootstrap и Fallback DNS Server.
ОПЕРАТОР — необязательная метка, кто держит сервер (см. ниже). Без неё
оператором считается домен второго уровня, а для IP — сам адрес.

  --timeout-ms N   Сколько ждать ответа на один запрос, мс (по умолчанию
                   1000). Не уложился — FAIL по этому запросу.

Сначала проверяются обычные DNS. Лучшие из них вместе с DNS провайдера
служат bootstrap для проверки DoH: если провайдер режет часть публичных DNS
по 53 порту, DoH-серверы всё равно проверяются честно.

В конце, если запущено в терминале и хотя бы один сервер набрал 3/3, скрипт
покажет оба списка и спросит, применить ли их разом (y/д — да, остальное —
нет):
  Upstream DNS Server             — до 5 DoH по задержке;
  Bootstrap и Fallback DNS Server — до 4 обычных DNS по задержке, затем DNS
                                    провайдера без проверки.
Сначала берётся лучший сервер каждого оператора и только потом вторые адреса
тех же операторов: пять адресов Cloudflare — это не пять независимых upstream.
Список, в котором никто не набрал 3/3, остаётся прежним. Правила вида
[/домен/]адрес в upstream сохраняются. Перед записью /etc/config/dnsproxy
сохраняется в /root, после перезапуска dnsproxy проверяется ответом на
openwrt.org, и если ответа нет — автоматический откат.
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

# nslookup принимает таймаут в целых секундах, поэтому он округляется вверх,
# а миллисекунды проверяются по замеру (см. query).
QUERY_TIMEOUT_S=$(( (QUERY_TIMEOUT_MS + 999) / 1000 ))
# Прогреву DoH даётся больше: первый запрос включает bootstrap, TCP и TLS, а в
# работе соединение держится открытым, и важна скорость уже прогретого.
# Таймаут upstream у тестового dnsproxy на секунду короче прогрева: мёртвый
# сервер он успевает признать сам и записать в лог причину, прежде чем
# nslookup сдастся и dnsproxy будет убит.
UPSTREAM_TIMEOUT_S=2
[ "$QUERY_TIMEOUT_S" -le "$UPSTREAM_TIMEOUT_S" ] || UPSTREAM_TIMEOUT_S="$QUERY_TIMEOUT_S"
WARMUP_TIMEOUT_S=$((UPSTREAM_TIMEOUT_S + 1))

[ "$(id -u)" = "0" ] || die "Скрипт нужно запускать от root"
[ -r "$LIST" ] || die "Список серверов не найден: $LIST"
[ -x "$DNSPROXY" ] || command -v dnsproxy >/dev/null 2>&1 || die "dnsproxy не найден. Сначала установите его: install-dnsproxy.sh"
command -v "$DNSPROXY" >/dev/null 2>&1 || DNSPROXY="dnsproxy"
command -v nslookup >/dev/null 2>&1 || die "Не найдена команда nslookup"
# Таймаут и тип запроса есть только у полного nslookup из busybox (в OpenWrt
# он по умолчанию). Урезанный их не знает и отверг бы каждый запрос.
nslookup 2>&1 | grep -q 'type=' || die "nslookup не поддерживает -type/-timeout (урезанный busybox)"

# Тестовый dnsproxy, осиротевший после убитого прошлого запуска (его родитель
# уже init), держал бы порт и с десяток мегабайт памяти до перезагрузки.
killed=0
for p in $(pidof dnsproxy 2>/dev/null); do
    case "$(tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null)" in
        *"--listen $LISTEN_ADDR "*)
            if [ "$(awk '/^PPid:/ { print $2 }' "/proc/$p/status" 2>/dev/null)" = "1" ]; then
                kill -9 "$p" 2>/dev/null && killed=1
            fi
            ;;
    esac
done
# Порт освобождается, когда процесс окончательно завершится, а не в момент kill.
[ "$killed" = "0" ] || sleep 1

if command -v netstat >/dev/null 2>&1; then
    if netstat -lnu 2>/dev/null | grep -q "$LISTEN_ADDR:$LISTEN_PORT "; then
        die "$LISTEN_ADDR:$LISTEN_PORT уже занят"
    fi
fi

# Разделитель полей во временных файлах — 0x1F (unit separator), а не tab:
# tab входит в IFS как "пробельный" символ, и read схлопывает подряд идущие
# пробельные разделители, из-за чего пустые поля съедали соседние колонки.
# US не пробельный и никогда не встретится ни в URL, ни в метке оператора.
US="$(printf '\037')"

WORK_DIR="/tmp/test-doh.$$"
URLS_FILE="$WORK_DIR/urls"
PLAIN_FILE="$WORK_DIR/plain"
RESULTS="$WORK_DIR/results"
SORTED="$WORK_DIR/sorted"
PLAIN_RESULTS="$WORK_DIR/plain-results"
PLAIN_SORTED="$WORK_DIR/plain-sorted"
DNS_PID=""

cleanup() {
    # Повторный сигнал (при обрыве SSH их приходит несколько) не должен
    # прерывать уже начатую уборку.
    trap '' INT TERM HUP
    if [ -n "$DNS_PID" ]; then
        kill -9 "$DNS_PID" 2>/dev/null || true
        wait "$DNS_PID" 2>/dev/null || true
    fi
    rm -rf "$WORK_DIR"
}
# Ловушка на сигналы только завершает скрипт, а уборку делает ловушка EXIT.
# Если повесить уборку прямо на INT, после Ctrl-C ash выполнил бы её и
# продолжил работу — уже без временных файлов и с убитым тестовым dnsproxy.
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

# Каталоги прошлых запусков, убитых так, что уборка не успела (SIGKILL, OOM).
for d in /tmp/test-doh.[0-9]*; do
    [ -d "$d" ] || continue
    kill -0 "${d##*.}" 2>/dev/null || rm -rf "$d"
done

mkdir -p "$WORK_DIR"
# Логи прошлых запусков: номера серверов в них уже не совпадают с текущими.
rm -f /tmp/test-doh-[0-9]*.log

# ---------------------------------------------------------
# Читаем список: пустые строки и строки с # игнорируются. Первое слово —
# адрес, второе (если есть и не начинается с #) — оператор. Без метки
# оператором считается домен второго уровня, у IP — сам адрес.
# ---------------------------------------------------------

awk -v US="$US" -v plain="$PLAIN_FILE" '
function host_group(u,   h, n, p) {
    h = u
    sub(/^[a-z]+:\/\//, "", h)
    sub(/[\/:].*$/, "", h)
    n = split(h, p, ".")
    return (n >= 2) ? p[n - 1] "." p[n] : h
}
NF == 0 || $1 ~ /^#/ { next }
{
    addr = $1
    grp = (NF >= 2 && $2 !~ /^#/) ? $2 : ""
    if (seen[addr]++) next
    if (addr ~ /^(https|tls|quic):\/\//)
        print addr US (grp != "" ? grp : host_group(addr))
    else if (addr ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/)
        print addr US (grp != "" ? grp : addr) > plain
}
' "$LIST" > "$URLS_FILE"
[ -f "$PLAIN_FILE" ] || : > "$PLAIN_FILE"

TOTAL="$(wc -l < "$URLS_FILE" | tr -d ' ')"
PLAIN_TOTAL="$(wc -l < "$PLAIN_FILE" | tr -d ' ')"
[ "$TOTAL" -gt 0 ] || [ "$PLAIN_TOTAL" -gt 0 ] || die "В $LIST не найдено ни DNS upstream, ни обычных DNS"

: > "$RESULTS"
: > "$PLAIN_RESULTS"

# DNS провайдера: в bootstrap для теста DoH и в итоговый Bootstrap/Fallback.
ISP_DNS="$(cat /tmp/resolv.conf.d/resolv.conf.auto /tmp/resolv.conf.auto 2>/dev/null \
    | awk '$1 == "nameserver" && $2 ~ /^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$/ && $2 !~ /^127\./ && $2 != "0.0.0.0" && !seen[$2]++ { print $2 }')"

log "Список: $LIST"
log "Upstream (DoH/DoT/DoQ): $TOTAL, обычных DNS (Bootstrap/Fallback): $PLAIN_TOTAL"
log "Серверы проверяются по одному, таймаут запроса: ${QUERY_TIMEOUT_MS}ms"
log ""

# Один запрос с собственным таймаутом nslookup. Результат — в LAT: задержка в
# мс или пусто, если ответа нет или он не уложился в QUERY_TIMEOUT_MS
# (таймаут nslookup — в целых секундах). /proc/uptime отдаёт сотые доли
# секунды, отсюда шаг 10 мс. Секунды и сотые вычитаются по отдельности:
# uptime целиком в миллисекундах на 32-битной арифметике переполнился бы через
# 24 дня, а ведущий ноль в сотых ("08") ash принял бы за неверное
# восьмеричное число.
query() {
    # $1 — домен, $2 — сервер, $3 — таймаут nslookup в секундах, $4 — попыток
    read -r up _ < /proc/uptime
    s0=${up%.*}; c0=${up#*.}; c0=${c0#0}
    if nslookup -type=a -timeout="$3" -retry="$4" "$1" "$2" </dev/null >/dev/null 2>&1; then
        read -r up _ < /proc/uptime
        s1=${up%.*}; c1=${up#*.}; c1=${c1#0}
        LAT=$(( (s1 - s0) * 1000 + (c1 - c0) * 10 ))
        [ "$LAT" -le "$QUERY_TIMEOUT_MS" ] || LAT=""
    else
        LAT=""
    fi
}

# Три тестовых домена подряд до первой неудачи. Выставляет D1..D3 (мс, FAIL
# или «-», если до домена не дошло), OK — число ответов, AVG — среднее.
measure() {
    # $1 — сервер, $2 — попыток nslookup на запрос
    D1="-"; D2="-"; D3="-"; OK=0; sum=0; n=0
    for domain in $TEST_DOMAINS; do
        n=$((n + 1))
        query "$domain" "$1" "$QUERY_TIMEOUT_S" "$2"
        if [ -z "$LAT" ]; then
            eval "D$n=FAIL"
            break
        fi
        eval "D$n=\$LAT"
        OK=$((OK + 1))
        sum=$((sum + LAT))
    done
    AVG=""
    [ "$OK" -eq 0 ] || AVG=$(( (sum + OK / 2) / OK ))
    case "$OK" in
        3) STATE="GOOD" ;;
        0) STATE="DEAD" ;;
        *) STATE="UNSTABLE" ;;
    esac
}

# Строка результата: ключ, адрес, оператор, D1..D3, OK, статус, AVG. Ключ —
# всегда девятизначное число (сначала больше ответов, затем меньше задержка),
# поэтому sort из busybox упорядочивает его правильно без -n и -k.
record() {
    # $1 — файл, $2 — адрес, $3 — оператор
    key=$(( 100000000 + (3 - OK) * 10000000 + ${AVG:-9999999} ))
    printf '%s\n' "$key$US$2$US$3$US$D1$US$D2$US$D3$US$OK$US$STATE$US$AVG" >> "$1"
}

progress() {
    # $1 — номер, $2 — всего, $3 — подпись
    printf '[%02d/%02d] %-44s %s/3  %5s/%5s/%5s ms  avg=%-5s %s\n' \
        "$1" "$2" "$3" "$OK" "$D1" "$D2" "$D3" "${AVG:--}" "$STATE"
}

# Имя хоста из URL для подписи: схема, userinfo, путь и порт отбрасываются.
name_from_url() {
    NAME="$1"
    case "$NAME" in *://*) NAME="${NAME#*://}" ;; esac
    case "$NAME" in *@*) NAME="${NAME##*@}" ;; esac
    NAME="${NAME%%/*}"
    case "$NAME" in
        \[*) NAME="${NAME#\[}"; NAME="${NAME%%]*}" ;;
        *:*) NAME="${NAME%%:*}" ;;
    esac
}

# ---------------------------------------------------------
# 1. Обычные DNS по IP: напрямую nslookup, без dnsproxy. Прогрев не нужен —
# соединений тут нет. Две попытки внутри таймаута переживают одну потерю
# UDP-пакета.
# ---------------------------------------------------------

if [ "$PLAIN_TOTAL" -gt 0 ]; then
    log "Обычные DNS (кандидаты в Bootstrap и Fallback):"
fi

i=0
while IFS="$US" read -r ip grp; do
    i=$((i + 1))
    measure "$ip" 2
    progress "$i" "$PLAIN_TOTAL" "$ip ($grp)"
    record "$PLAIN_RESULTS" "$ip" "$grp"
done < "$PLAIN_FILE"

sort "$PLAIN_RESULTS" > "$PLAIN_SORTED"

# ---------------------------------------------------------
# 2. DoH/DoT/DoQ через тестовый dnsproxy. bootstrap ему — три самых быстрых
# обычных DNS из шага 1 и DNS провайдера: заранее зашитые адреса у части
# провайдеров режутся, и тогда DoH падал бы не сам, а на разрешении своего
# имени.
# ---------------------------------------------------------

TEST_BOOTSTRAP="$(awk -F"$US" '$8 == "GOOD" { print $2; if (++n == 3) exit }' "$PLAIN_SORTED")"
if [ -z "$TEST_BOOTSTRAP" ]; then
    TEST_BOOTSTRAP="$DEFAULT_BOOTSTRAP"
fi
BOOT_ARGS=""
BOOT_SHOWN=""
for b in $TEST_BOOTSTRAP $ISP_DNS; do
    case " $BOOT_SHOWN " in *" $b "*) continue ;; esac
    BOOT_ARGS="$BOOT_ARGS --bootstrap $b"
    BOOT_SHOWN="$BOOT_SHOWN $b"
done

if [ "$TOTAL" -gt 0 ]; then
    log ""
    log "DoH/DoT/DoQ (кандидаты в Upstream), bootstrap:$BOOT_SHOWN"
fi

i=0
while IFS="$US" read -r url grp; do
    i=$((i + 1))
    name_from_url "$url"
    log_file="/tmp/test-doh-$i.log"

    # BOOT_ARGS разбивается на слова намеренно: в нём только флаги и IPv4.
    # shellcheck disable=SC2086
    "$DNSPROXY" \
        --listen "$LISTEN_ADDR" \
        --port "$LISTEN_PORT" \
        --upstream "$url" \
        --ipv6-disabled \
        --timeout "${UPSTREAM_TIMEOUT_S}s" \
        $BOOT_ARGS \
        </dev/null >"$log_file" 2>&1 &
    DNS_PID=$!

    # Ждём, пока тестовый dnsproxy займёт порт. На слабом роутере старт
    # Go-бинаря заметен, и ранний запрос засчитался бы как отказ сервера.
    waited=0
    while ! grep -qi 'listening to udp' "$log_file"; do
        kill -0 "$DNS_PID" 2>/dev/null || break
        [ "$waited" -lt 3 ] || break
        sleep 1
        waited=$((waited + 1))
    done

    if ! kill -0 "$DNS_PID" 2>/dev/null; then
        wait "$DNS_PID" 2>/dev/null || true
        DNS_PID=""
        D1="-"; D2="-"; D3="-"; OK=0; AVG=""
        STATE="STARTFAIL"
    else
        # Запросы к своему dnsproxy идут по loopback, где пакеты не теряются,
        # поэтому одна попытка: повтор породил бы второй запрос к upstream.
        # У прогрева важен только сам ответ, не его скорость.
        if nslookup -type=a -timeout="$WARMUP_TIMEOUT_S" -retry=1 \
            "$WARMUP_DOMAIN" "$LISTEN_ADDR" </dev/null >/dev/null 2>&1; then
            measure "$LISTEN_ADDR" 1
        else
            # Не ответил даже за WARMUP_TIMEOUT_S: сервер недоступен или так
            # долго устанавливает соединение, что в работе тормозил бы на
            # каждом переподключении. Тестовые домены уже не опрашиваются.
            D1="-"; D2="-"; D3="-"; OK=0; AVG=""
            STATE="DEAD"
        fi
        kill -9 "$DNS_PID" 2>/dev/null || true
        wait "$DNS_PID" 2>/dev/null || true
        DNS_PID=""
    fi

    # Лог нужен, только чтобы разобраться в отказе.
    [ "$STATE" != "GOOD" ] || rm -f "$log_file"

    progress "$i" "$TOTAL" "$NAME"
    record "$RESULTS" "$url" "$grp"
done < "$URLS_FILE"

sort "$RESULTS" > "$SORTED"

# ---------------------------------------------------------
# Итоговые таблицы: больше ответов — выше, при равенстве — меньше задержка.
# ---------------------------------------------------------

LINE="$(printf '%*s' 104 '' | tr ' ' '-')"

print_table() {
    # $1 — файл, $2 — заголовок колонки адреса
    printf '%2s %-52s %3s %8s %8s %8s %6s  %s\n' \
        "#" "$2" "OK" "GitHub" "Raw" "Assets" "AVG" "STATUS"
    printf '%s\n' "$LINE"
    pos=0
    while IFS="$US" read -r key addr grp d1 d2 d3 ok state avg; do
        pos=$((pos + 1))
        printf '%2d %-52s %s/3 %8s %8s %8s %6s  %s\n' \
            "$pos" "$addr" "$ok" "$d1" "$d2" "$d3" "${avg:--}" "$state"
    done < "$1"
}

printf '\n%s\nИТОГ\n%s\n' "$LINE" "$LINE"
if [ -s "$SORTED" ]; then
    print_table "$SORTED" "Upstream DNS Server (DoH/DoT/DoQ)"
fi
if [ -s "$PLAIN_SORTED" ]; then
    printf '\n'
    print_table "$PLAIN_SORTED" "Bootstrap/Fallback DNS Server (IP)"
fi

cat <<'EOF'

GitHub = github.com
Raw    = raw.githubusercontent.com
Assets = release-assets.githubusercontent.com

GOOD      3/3
UNSTABLE  ответил не на все домены
DEAD      не ответил вовремя
STARTFAIL тестовый dnsproxy не запустился
FAIL      нет ответа за отведённое время
-         не проверялся: после первой неудачи 3/3 уже не набрать

Логи неудачных проверок DoH: /tmp/test-doh-*.log
EOF

# ---------------------------------------------------------
# Применение. Ничего не меняется без явного «да»: конфиг живого резолвера —
# не то, что трогают по умолчанию.
# ---------------------------------------------------------

# Выбор из отсортированных результатов: только GOOD, сначала лучший сервер
# каждого оператора, затем, если мест осталось больше, чем операторов, —
# следующие по скорости. Выводит «адрес оператор».
pick() {
    # $1 — файл результатов, $2 — сколько взять
    awk -F"$US" -v n="$2" '
        $8 == "GOOD" { a[++c] = $2; g[c] = $3 }
        END {
            k = 0
            for (i = 1; i <= c && k < n; i++)
                if (!(g[i] in used)) { used[g[i]] = 1; p[i] = 1; k++ }
            for (i = 1; i <= c && k < n; i++)
                if (!(i in p)) { p[i] = 1; k++ }
            for (i = 1; i <= c; i++)
                if (i in p) print a[i], g[i]
        }
    ' "$1"
}

wait_dns() {
    # $1 — адрес dnsproxy. restart асинхронный: procd сначала гасит старый
    # процесс (до ~5 с), и только потом новый занимает порт.
    tries=0
    while [ "$tries" -lt 10 ]; do
        sleep 2
        if nslookup -type=a -timeout=3 openwrt.org "$1" >/dev/null 2>&1; then
            return 0
        fi
        tries=$((tries + 1))
    done
    return 1
}

apply_best_servers() {
    UCI_CONFIG="/etc/config/dnsproxy"

    if [ ! -t 0 ]; then
        log "Неинтерактивный запуск (нет tty) — вопрос про применение списков пропущен"
        return 0
    fi
    if ! command -v uci >/dev/null 2>&1; then
        warn "uci не найден, применять некуда"
        return 0
    fi
    if [ ! -f "$UCI_CONFIG" ]; then
        warn "$UCI_CONFIG не найден — dnsproxy не настроен через install-dnsproxy.sh, пропускаю"
        return 0
    fi

    BEST="$WORK_DIR/best"
    PLAIN_BEST="$WORK_DIR/plain-best"
    pick "$SORTED" "$UPSTREAM_PICK" > "$BEST"
    pick "$PLAIN_SORTED" "$PLAIN_PICK" > "$PLAIN_BEST"
    # DNS провайдера идут в конец без проверки — свой резолвер сети нужен
    # всегда. Но только вместе с проверенными: если никто не прошёл 3/3,
    # Bootstrap/Fallback остаются прежними целиком.
    if [ -s "$PLAIN_BEST" ]; then
        for dns in $ISP_DNS; do
            awk -v d="$dns" '$1 == d { f = 1 } END { exit !f }' "$PLAIN_BEST" \
                || printf '%s провайдер\n' "$dns" >> "$PLAIN_BEST"
        done
    fi

    if [ ! -s "$BEST" ] && [ ! -s "$PLAIN_BEST" ]; then
        warn "Ни один сервер не набрал 3/3 (за ${QUERY_TIMEOUT_MS}ms) — применять нечего"
        return 0
    fi

    printf '\n'
    if [ -s "$BEST" ]; then
        printf 'Upstream DNS Server:\n'
        sed 's/^\([^ ]*\) \(.*\)$/  \1  (\2)/' "$BEST"
    else
        printf 'Upstream DNS Server: ни один не набрал 3/3 — остаются текущие\n'
    fi
    if [ -s "$PLAIN_BEST" ]; then
        printf 'Bootstrap DNS Server и Fallback DNS Server:\n'
        sed 's/^\([^ ]*\) \(.*\)$/  \1  (\2)/' "$PLAIN_BEST"
    else
        printf 'Bootstrap/Fallback DNS Server: ни один не набрал 3/3 — остаются текущие\n'
    fi

    printf '\nПрименить эти списки в dnsproxy? [y/N]: '
    read -r ans || ans=""
    case "$ans" in
        y|Y|yes|YES|Yes|д|Д|да|Да|ДА) ;;
        *) return 0 ;;
    esac

    ts="$(date +%Y%m%d-%H%M%S 2>/dev/null || echo now)"
    backup="/root/dnsproxy-servers-backup-$ts.config"
    if ! cp -p "$UCI_CONFIG" "$backup" 2>/dev/null; then
        warn "Не удалось сделать бэкап $UCI_CONFIG, отменяю"
        return 1
    fi
    log "Бэкап текущего конфига: $backup"

    # Ошибка любой правки отменяет все: иначе недописанные изменения остались
    # бы в /tmp/.uci и применились бы при чужом commit, например из LuCI.
    failed=0
    uci -q get dnsproxy.servers >/dev/null 2>&1 || uci set dnsproxy.servers=dnsproxy || failed=1

    if [ -s "$BEST" ]; then
        # Правила для отдельных доменов ([/nalog.ru/]адрес) — не обычные
        # upstream, их замена на «лучшие» сломала бы эти домены. Переносятся
        # как есть. set -f — чтобы [/…/] не раскрылось как glob.
        keep_rules="$(uci -q get dnsproxy.servers.upstream 2>/dev/null | tr ' ' '\n' | grep '^\[/' || true)"
        uci -q delete dnsproxy.servers.upstream 2>/dev/null || true
        while read -r url _; do
            uci add_list dnsproxy.servers.upstream="$url" || failed=1
        done < "$BEST"
        set -f
        for rule in $keep_rules; do
            uci add_list dnsproxy.servers.upstream="$rule" || failed=1
        done
        set +f
    fi

    if [ -s "$PLAIN_BEST" ]; then
        uci -q delete dnsproxy.servers.bootstrap 2>/dev/null || true
        uci -q delete dnsproxy.servers.fallback 2>/dev/null || true
        while read -r dns _; do
            uci add_list dnsproxy.servers.bootstrap="$dns" || failed=1
            uci add_list dnsproxy.servers.fallback="$dns" || failed=1
        done < "$PLAIN_BEST"
    fi

    if [ "$failed" = "1" ] || ! uci commit dnsproxy; then
        warn "Не удалось записать серверы, конфиг не изменён"
        uci -q revert dnsproxy 2>/dev/null || true
        cp -p "$backup" "$UCI_CONFIG" 2>/dev/null || true
        return 1
    fi

    PROD_LISTEN_ADDR="$(uci -q get dnsproxy.global.listen_addr 2>/dev/null | awk '{print $1}')"
    [ -n "$PROD_LISTEN_ADDR" ] || PROD_LISTEN_ADDR="127.0.0.10"

    log "Перезапускаю dnsproxy..."
    if /etc/init.d/dnsproxy restart && wait_dns "$PROD_LISTEN_ADDR"; then
        log "Готово: dnsproxy на $PROD_LISTEN_ADDR работает с новыми списками серверов"
        log "Бэкап предыдущей версии: $backup"
        return 0
    fi

    warn "dnsproxy не отвечает на $PROD_LISTEN_ADDR с новыми серверами, откатываю"
    cp -p "$backup" "$UCI_CONFIG" 2>/dev/null || true
    /etc/init.d/dnsproxy restart >/dev/null 2>&1 || true
    warn "Откат выполнен, серверы остались прежними"
    return 1
}

apply_best_servers
