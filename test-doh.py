#!/usr/bin/env python3

import sys
import time
import signal
import subprocess

LIST = sys.argv[1] if len(sys.argv) > 1 else "/tmp/servers.txt"

DNSPROXY = "/usr/bin/dnsproxy"

# Максимум одновременно работающих тестовых dnsproxy.
MAX_WORKERS = 5

# Рабочий dnsproxy у тебя на 127.0.0.10:53.
# Для тестов используем 127.0.0.11 ... 127.0.0.15.
START_ADDR = 11
PORT = "53"

# Первый запрос — прогрев, в итоговую статистику не входит.
WARMUP_DOMAIN = "example.com"

# Три реальных измерения.
TEST_DOMAINS = [
    "github.com",
    "raw.githubusercontent.com",
    "release-assets.githubusercontent.com",
]

# Bootstrap используется только для разрешения имени самого DoH/DoT upstream.
BOOTSTRAPS = [
    "8.8.4.4",
    "1.0.0.1",
    "9.9.9.9",
]

# Максимальное время одного DNS-запроса.
QUERY_TIMEOUT = 7.0

# Даём dnsproxy немного времени после запуска.
START_DELAY = 1.0

# Частота проверки завершившихся процессов.
POLL_DELAY = 0.02

workers = []


def name_from_url(url):
    """
    Короткое имя только для промежуточного прогресса.
    В итоговой таблице используется полный URL.
    Никакого urllib — работает на урезанном Python OpenWrt.
    """
    s = url.strip()

    if "://" in s:
        s = s.split("://", 1)[1]

    if "@" in s:
        s = s.rsplit("@", 1)[1]

    s = s.split("/", 1)[0]

    if s.startswith("["):
        end = s.find("]")
        if end != -1:
            return s[1:end]

    if ":" in s:
        s = s.split(":", 1)[0]

    return s or url


def fmt(value):
    if value is None:
        return "FAIL"
    return f"{value:.1f}"


def stop_proc(proc):
    if proc is None or proc.poll() is not None:
        return

    try:
        proc.terminate()
        proc.wait(timeout=1)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=1)
        except Exception:
            pass


def cleanup_all():
    for w in workers:
        stop_proc(w.get("query_proc"))
        stop_proc(w.get("dns_proc"))

        log = w.get("log")
        if log:
            try:
                log.close()
            except Exception:
                pass


def handle_signal(signum, frame):
    cleanup_all()
    sys.exit(130)


signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)


# ---------------------------------------------------------
# Читаем список upstream
# ---------------------------------------------------------

urls = []

with open(LIST, "r", encoding="utf-8", errors="ignore") as f:
    for raw in f:
        raw = raw.strip()

        if not raw or raw.startswith("#"):
            continue

        # Разрешаем комментарий после URL.
        url = raw.split()[0]

        if url.startswith(("https://", "tls://", "quic://")):
            urls.append(url)


if not urls:
    print(f"В {LIST} не найдено DNS upstream.")
    sys.exit(1)


# ---------------------------------------------------------
# Проверяем, что наши loopback-адреса свободны
# ---------------------------------------------------------

test_addresses = [
    f"127.0.0.{START_ADDR + i}"
    for i in range(MAX_WORKERS)
]

try:
    check = subprocess.run(
        ["netstat", "-lnup"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        universal_newlines=True,
    )

    for addr in test_addresses:
        if f"{addr}:{PORT}" in check.stdout:
            print(f"ERROR: {addr}:{PORT} уже занят")
            sys.exit(1)

except Exception:
    pass


# ---------------------------------------------------------
# Итоговые результаты
# ---------------------------------------------------------

results = []

next_index = 0
finished = 0


# ---------------------------------------------------------
# Запуск следующего upstream в свободном слоте
# ---------------------------------------------------------

def start_worker(slot):
    global next_index

    while next_index < len(urls):

        index = next_index
        next_index += 1

        url = urls[index]
        name = name_from_url(url)

        ip = f"127.0.0.{START_ADDR + slot}"

        log_path = f"/tmp/doh-test-{index + 1}.log"
        log = open(log_path, "w")

        cmd = [
            DNSPROXY,
            "--listen", ip,
            "--port", PORT,
            "--upstream", url,
            "--ipv6-disabled",
            "--timeout", f"{int(QUERY_TIMEOUT)}s",
            "--http3",
        ]

        for bootstrap in BOOTSTRAPS:
            cmd += ["--bootstrap", bootstrap]

        try:
            dns_proc = subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
            )

        except Exception:
            log.close()

            results.append({
                "name": name,
                "url": url,
                "times": [None, None, None],
                "success": 0,
                "avg": None,
                "state": "STARTFAIL",
            })

            continue

        return {
            "slot": slot,
            "index": index,

            "url": url,
            "name": name,
            "ip": ip,

            "dns_proc": dns_proc,
            "query_proc": None,

            "log": log,
            "log_path": log_path,

            # startup -> warmup -> test0 -> test1 -> test2
            "stage": "startup",

            "stage_started": time.monotonic(),
            "query_started": None,

            "times": [None, None, None],
        }

    return None


# ---------------------------------------------------------
# Запуск одного nslookup
# ---------------------------------------------------------

def start_query(w, domain):
    try:
        w["query_proc"] = subprocess.Popen(
            ["nslookup", domain, w["ip"]],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        w["query_started"] = time.monotonic()

    except Exception:
        w["query_proc"] = None
        w["query_started"] = None


# ---------------------------------------------------------
# Завершение одного upstream
# ---------------------------------------------------------

def finish_worker(w):
    global finished

    stop_proc(w.get("query_proc"))
    stop_proc(w.get("dns_proc"))

    try:
        w["log"].close()
    except Exception:
        pass

    good = [x for x in w["times"] if x is not None]
    success = len(good)

    avg = sum(good) / len(good) if good else None

    if success == 3:
        state = "GOOD"
    elif success > 0:
        state = "UNSTABLE"
    else:
        state = "DEAD"

    results.append({
        "name": w["name"],
        "url": w["url"],
        "times": w["times"],
        "success": success,
        "avg": avg,
        "state": state,
    })

    finished += 1

    print(
        f"[{finished:02d}/{len(urls):02d}] "
        f"{w['name'][:35]:35s} "
        f"{success}/3  "
        f"{fmt(w['times'][0])}/"
        f"{fmt(w['times'][1])}/"
        f"{fmt(w['times'][2])} ms  "
        f"avg={fmt(avg)}"
    )

    # Слот сразу получает следующий upstream.
    return start_worker(w["slot"])


# ---------------------------------------------------------
# Старт
# ---------------------------------------------------------

print(f"Найдено upstream: {len(urls)}")
print(f"Параллельных тестов максимум: {MAX_WORKERS}")
print("Освободившийся слот сразу получает следующий upstream.")
print("Первый DNS-запрос — прогрев и в статистику не входит.")
print()


for slot in range(min(MAX_WORKERS, len(urls))):
    w = start_worker(slot)

    if w is not None:
        workers.append(w)


# ---------------------------------------------------------
# Основной event loop
# ---------------------------------------------------------

while workers:

    now = time.monotonic()

    for pos in range(len(workers) - 1, -1, -1):
        w = workers[pos]

        # -------------------------------------------------
        # dnsproxy неожиданно завершился
        # -------------------------------------------------

        if (
            w["dns_proc"] is not None
            and w["dns_proc"].poll() is not None
            and w["stage"] != "done"
        ):
            stop_proc(w.get("query_proc"))

            try:
                w["log"].close()
            except Exception:
                pass

            results.append({
                "name": w["name"],
                "url": w["url"],
                "times": w["times"],
                "success": 0,
                "avg": None,
                "state": "STARTFAIL",
            })

            finished += 1

            print(
                f"[{finished:02d}/{len(urls):02d}] "
                f"{w['name'][:35]:35s} START FAIL"
            )

            replacement = start_worker(w["slot"])

            if replacement:
                workers[pos] = replacement
            else:
                workers.pop(pos)

            continue

        # -------------------------------------------------
        # Startup delay
        # -------------------------------------------------

        if w["stage"] == "startup":

            if now - w["stage_started"] >= START_DELAY:
                w["stage"] = "warmup"
                start_query(w, WARMUP_DOMAIN)

            continue

        # -------------------------------------------------
        # Ждём текущий nslookup
        # -------------------------------------------------

        q = w["query_proc"]

        if q is None:
            query_done = True
            success = False
            latency = None

        else:
            rc = q.poll()

            if rc is not None:
                query_done = True

                latency = (
                    time.monotonic() - w["query_started"]
                ) * 1000

                success = rc == 0

            elif (
                time.monotonic() - w["query_started"]
                >= QUERY_TIMEOUT
            ):
                stop_proc(q)

                query_done = True
                success = False
                latency = None

            else:
                query_done = False

        if not query_done:
            continue

        w["query_proc"] = None

        # -------------------------------------------------
        # Прогрев завершён
        # -------------------------------------------------

        if w["stage"] == "warmup":
            w["stage"] = "test0"
            start_query(w, TEST_DOMAINS[0])
            continue

        # -------------------------------------------------
        # GitHub
        # -------------------------------------------------

        if w["stage"] == "test0":

            w["times"][0] = latency if success else None

            w["stage"] = "test1"
            start_query(w, TEST_DOMAINS[1])
            continue

        # -------------------------------------------------
        # Raw
        # -------------------------------------------------

        if w["stage"] == "test1":

            w["times"][1] = latency if success else None

            w["stage"] = "test2"
            start_query(w, TEST_DOMAINS[2])
            continue

        # -------------------------------------------------
        # Assets
        # -------------------------------------------------

        if w["stage"] == "test2":

            w["times"][2] = latency if success else None

            replacement = finish_worker(w)

            if replacement:
                workers[pos] = replacement
            else:
                workers.pop(pos)

            continue

    time.sleep(POLL_DELAY)


# ---------------------------------------------------------
# Сортировка
#
# 1. Больше успешных ответов
# 2. Меньше средняя задержка
# ---------------------------------------------------------

results.sort(
    key=lambda r: (
        -r["success"],
        r["avg"] if r["avg"] is not None else 9999999,
    )
)


# ---------------------------------------------------------
# Итоговая таблица
# ---------------------------------------------------------

WIDTH = 150

print()
print("=" * WIDTH)
print("ИТОГ")
print("=" * WIDTH)

print(
    f"{'#':>2} "
    f"{'DNS URL':<72} "
    f"{'OK':>5} "
    f"{'GitHub':>10} "
    f"{'Raw':>10} "
    f"{'Assets':>10} "
    f"{'AVG':>10} "
    f"{'STATUS':>10}"
)

print("-" * WIDTH)


for pos, r in enumerate(results, 1):

    t = r["times"]

    print(
        f"{pos:>2} "
        f"{r['url']:<72} "
        f"{r['success']}/3".rjust(5) + " "
        f"{fmt(t[0]):>10} "
        f"{fmt(t[1]):>10} "
        f"{fmt(t[2]):>10} "
        f"{fmt(r['avg']):>10} "
        f"{r['state']:>10}"
    )


print()
print("GitHub = github.com")
print("Raw    = raw.githubusercontent.com")
print("Assets = release-assets.githubusercontent.com")
print()
print("GOOD      3/3")
print("UNSTABLE  1/3 или 2/3")
print("DEAD      0/3")
print("STARTFAIL dnsproxy не смог запуститься")
print()
print("Логи отдельных тестов: /tmp/doh-test-*.log")
