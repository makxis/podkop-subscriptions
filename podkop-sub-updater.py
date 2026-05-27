#!/usr/bin/env python3
import os
import sys
import subprocess
import base64
import re
import syslog
import argparse
import hashlib
import platform
import json
import time


APP_VERSION = "2.6"
USER_AGENT = f"Podkop-Subscription-Updater/{APP_VERSION}"
VALID_PROTOCOLS = ('vless://', 'ss://', 'trojan://', 'socks4://', 'socks4a://', 'socks5://', 'hy2://', 'hysteria2://')
VALID_PTYPES = {'urltest', 'selector'}
VALID_ON_EMPTY = {'all', 'skip'}
VALID_MATCH_MODES = {'ifmatch', 'ifnotmatch'}
STATE_PATH_DEFAULT = '/etc/podkop-subscriptions/state.json'
LOCAL_LINKS_PATH_DEFAULT = '/etc/config/podkop-local-links'
DELETE_AFTER_FAIL_COUNT_DEFAULT = 72
MIN_KEEP_PER_SECTION_DEFAULT = 1
SOURCE_TIMEOUT_DEFAULT = 45
SOURCE_RETRIES_DEFAULT = 3


def setup_syslog():
    syslog.openlog("podkop-updater", syslog.LOG_PID, syslog.LOG_USER)


def log(level, msg):
    print(f"[{level}] {msg}")
    syslog_level = syslog.LOG_WARNING if level in ["ERROR", "WARN"] else syslog.LOG_INFO
    try:
        syslog.syslog(syslog_level, f"[{level}] {msg}")
    except Exception:
        pass


def parse_args():
    parser = argparse.ArgumentParser(description="Обновление подписок для Podkop")
    parser.add_argument('--version', action='store_true', help='Показать версию podkop-subscriptions и выйти')
    parser.add_argument('--config', default='/etc/config/podkop', help='Путь к UCI конфигу podkop')
    parser.add_argument('--subs', default='/etc/config/podkop', help='Путь к UCI конфигу подписок')
    parser.add_argument('--force', action='store_true', help='Принудительно перезаписать конфиг podkop и перезапустить сервис')
    parser.add_argument('--observe-only', action='store_true', help='Только обновить fail_count по текущему состоянию Podkop URLTest; конфиг не менять')
    parser.add_argument('--state', default=STATE_PATH_DEFAULT, help='Путь к state.json')
    parser.add_argument('--delete-after-fails', type=int, default=DELETE_AFTER_FAIL_COUNT_DEFAULT, help='Удалять ключ после N подряд неудачных наблюдений')
    parser.add_argument('--min-keep', type=int, default=MIN_KEEP_PER_SECTION_DEFAULT, help='Минимум ключей, которые надо оставить в секции')
    return parser.parse_args()


def unquote_percent(s):
    """Small percent-decoder without urllib dependency.

    OpenWrt python3-light may not include urllib.parse, so keep this self-contained.
    Invalid %XX sequences are kept as-is.
    """
    s = str(s or '')
    if '%' not in s:
        return s

    out = bytearray()
    i = 0
    raw = s.encode('utf-8', 'surrogatepass')
    hexdigits = b'0123456789abcdefABCDEF'

    while i < len(raw):
        if raw[i:i+1] == b'%' and i + 2 < len(raw) and raw[i+1] in hexdigits and raw[i+2] in hexdigits:
            try:
                out.append(int(raw[i+1:i+3], 16))
                i += 3
                continue
            except Exception:
                pass
        out.append(raw[i])
        i += 1

    try:
        return out.decode('utf-8', 'replace')
    except Exception:
        return s


def parse_uci_value(raw):
    raw = (raw or '').strip()
    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1]
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    return raw


def uci_quote(value):
    # UCI single-quoted strings can contain many URL characters safely; escape single quote defensively.
    return "'" + str(value).replace("'", "'\\''") + "'"


def parse_uci_sections(config_path):
    sections = []
    current = None
    with open(config_path, 'r', encoding='utf-8', errors='replace') as f:
        for line_num, line in enumerate(f, 1):
            raw = line.strip()
            if not raw or raw.startswith('#'):
                continue
            m = re.match(r"^config\s+([A-Za-z0-9_-]+)(?:\s+(.+))?$", raw)
            if m:
                if current:
                    sections.append(current)
                current = {
                    'type': m.group(1),
                    'name': parse_uci_value(m.group(2) or ''),
                    'options': {},
                    'lists': {},
                    'line_num': line_num
                }
                continue
            if not current:
                continue
            m = re.match(r"^option\s+([A-Za-z0-9_-]+)\s+(.+)$", raw)
            if m:
                current['options'][m.group(1)] = parse_uci_value(m.group(2))
                continue
            m = re.match(r"^list\s+([A-Za-z0-9_-]+)\s+(.+)$", raw)
            if m:
                key = m.group(1)
                val = parse_uci_value(m.group(2))
                current['lists'].setdefault(key, []).append(val)
                continue
    if current:
        sections.append(current)
    return sections


def stable_id(link):
    # Убираем название после #, чтобы переименование узла не создавало новый ключ.
    base = (link or '').split('#', 1)[0].strip()
    return hashlib.sha256(base.encode('utf-8', 'ignore')).hexdigest()[:24]


def link_name(link):
    if '#' not in link:
        return ''
    return unquote_percent(link.split('#', 1)[1])[:120]


def dedupe_links_keep_order(links):
    result = []
    seen = set()
    duplicates = 0
    for link in links:
        sid = stable_id(link)
        if sid in seen:
            duplicates += 1
            continue
        seen.add(sid)
        result.append(link)
    return result, duplicates


def get_mac_address():
    interfaces = ['br-lan', 'eth0', 'eth1', 'lan']
    for iface in interfaces:
        path = f"/sys/class/net/{iface}/address"
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    mac = f.read().strip()
                    if mac and mac != '00:00:00:00:00:00':
                        return mac.replace(':', '').lower()
            except Exception:
                continue
    return "000000000000"


def get_device_model():
    paths = ['/tmp/sysinfo/model', '/proc/device-tree/model']
    for path in paths:
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    model = f.read().replace('\x00', '').strip()
                    if model:
                        return model
            except Exception:
                continue
    return "Generic OpenWrt Device"


def get_kernel_version():
    return platform.release()


def load_jobs_from_uci_podkop(config_path):
    sections = parse_uci_sections(config_path)
    groups = [s for s in sections if s['type'] == 'subscription_group']
    if not groups:
        return None
    jobs = {}
    for g in groups:
        opt = g['options']
        lists = g['lists']
        if opt.get('enabled', '1') == '0':
            continue
        sec_name = opt.get('target_section', g['name']).strip().lower()
        sources = lists.get('source', [])
        regex_pattern = opt.get('regex', '').strip()
        match_mode = opt.get('match_mode', 'ifnotmatch').strip().lower()
        ptype = opt.get('proxy_type', 'urltest').strip().lower()
        on_empty = opt.get('on_empty', 'skip').strip().lower()
        if not sec_name:
            log("WARN", f"subscription_group '{g['name']}': не задана целевая секция Podkop. Пропуск.")
            continue
        if not sources:
            log("WARN", f"subscription_group '{g['name']}': нет ни одного source. Пропуск.")
            continue
        if match_mode not in VALID_MATCH_MODES:
            log("WARN", f"subscription_group '{g['name']}': недопустимый match_mode '{match_mode}'. Пропуск.")
            continue
        if ptype not in VALID_PTYPES:
            log("WARN", f"subscription_group '{g['name']}': недопустимый proxy_type '{ptype}'. Пропуск.")
            continue
        if on_empty not in VALID_ON_EMPTY:
            log("WARN", f"subscription_group '{g['name']}': недопустимый on_empty '{on_empty}'. Пропуск.")
            continue
        if sec_name not in jobs:
            jobs[sec_name] = {'ptype': ptype, 'entries': [], 'links': [], 'source_errors': 0, 'source_success': 0}
        if jobs[sec_name]['ptype'] != ptype:
            log("WARN", f"subscription_group '{g['name']}': target_section '{sec_name}' уже использует '{jobs[sec_name]['ptype']}', нельзя смешивать с '{ptype}'. Пропуск.")
            continue
        for source in sources:
            source = source.strip()
            if not source:
                continue
            jobs[sec_name]['entries'].append({
                'source': source,
                'regex': regex_pattern,
                'match_mode': match_mode,
                'on_empty': on_empty,
                'line_num': g['line_num']
            })
    return jobs


def load_jobs_from_flat_file(subs_path):
    jobs = {}
    with open(subs_path, 'r', encoding='utf-8', errors='replace') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = [p.strip() for p in line.split('::')]
            if len(parts) != 6:
                log("WARN", f"Строка {line_num}: неверный формат. Нужно 6 колонок через '::'. Пропуск.")
                continue
            sec_name, source, regex_pattern, match_mode, ptype, on_empty = parts
            sec_name = sec_name.lower()
            match_mode = match_mode.lower()
            ptype = ptype.lower()
            on_empty = on_empty.lower()
            if not sec_name or not source:
                log("WARN", f"Строка {line_num}: отсутствует имя секции или источник. Пропуск.")
                continue
            if match_mode not in VALID_MATCH_MODES:
                log("WARN", f"Строка {line_num}: недопустимый режим '{match_mode}'. Пропуск.")
                continue
            if ptype not in VALID_PTYPES:
                log("WARN", f"Строка {line_num}: недопустимый тип '{ptype}'. Пропуск.")
                continue
            if on_empty not in VALID_ON_EMPTY:
                log("WARN", f"Строка {line_num}: недопустимое действие '{on_empty}'. Пропуск.")
                continue
            if sec_name not in jobs:
                jobs[sec_name] = {'ptype': ptype, 'entries': [], 'links': [], 'source_errors': 0, 'source_success': 0}
            if jobs[sec_name]['ptype'] != ptype:
                log("WARN", f"Строка {line_num}: секция '{sec_name}' уже объявлена как '{jobs[sec_name]['ptype']}', нельзя смешивать с '{ptype}'. Пропуск.")
                continue
            jobs[sec_name]['entries'].append({
                'source': source,
                'regex': regex_pattern,
                'match_mode': match_mode,
                'on_empty': on_empty,
                'line_num': line_num
            })
    return jobs


def load_jobs(subs_path):
    subs_abs = os.path.abspath(subs_path)
    if not os.path.exists(subs_path):
        if subs_abs == '/etc/config/podkop_subscriptions':
            log("INFO", "Конфиг подписок не найден. Подписки не настроены.")
            return {}
        log("ERROR", f"Файл подписок {subs_path} не найден")
        sys.exit(1)
    # Если читаем основной /etc/config/podkop, flat-file fallback не нужен.
    if os.path.abspath(subs_path) == '/etc/config/podkop':
        uci_jobs = load_jobs_from_uci_podkop(subs_path)
        if uci_jobs is not None:
            return uci_jobs
        log("INFO", "subscription_group не найдены. Подписки не настроены.")
        return {}
    if subs_abs == '/etc/config/podkop_subscriptions':
        uci_jobs = load_jobs_from_uci_podkop(subs_path)
        if uci_jobs is not None:
            return uci_jobs
        log("INFO", "subscription_group не найдены. Подписки не настроены.")
        return {}
    uci_jobs = load_jobs_from_uci_podkop(subs_path)
    if uci_jobs is not None:
        return uci_jobs
    return load_jobs_from_flat_file(subs_path)


def is_url_source(source):
    return bool(re.match(r'^https?://', source, re.IGNORECASE))


def read_source_payload(source, hwid, device_model, kernel_ver, cache):
    if source in cache:
        return cache[source]

    retries = SOURCE_RETRIES_DEFAULT
    timeout = SOURCE_TIMEOUT_DEFAULT

    if is_url_source(source):
        cmd = ['wget', '-qO-', f'--user-agent={USER_AGENT}']
        cmd.extend(['--header', f'X-HWID: {hwid}'])
        cmd.extend(['--header', 'X-Device-OS: OpenWrt Linux'])
        cmd.extend(['--header', f'X-Device-Model: {device_model}'])
        cmd.extend(['--header', f'X-Ver-OS: {kernel_ver}'])
        cmd.append(source)

        last_error = 'неизвестная ошибка'

        for attempt in range(1, retries + 1):
            try:
                log('INFO', f"Источник {source}: попытка {attempt}/{retries}, timeout={timeout}s")
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=timeout
                )

                if result.returncode == 0 and result.stdout and result.stdout.strip():
                    if attempt > 1:
                        log('INFO', f"Источник {source}: успешно загружен с попытки {attempt}/{retries}")
                    cache[source] = result.stdout
                    return result.stdout

                last_error = result.stderr.strip() or 'Пустой ответ'
                log('WARN', f"Источник {source}: попытка {attempt}/{retries} неуспешна -> {last_error}")

            except subprocess.TimeoutExpired:
                last_error = f"timeout {timeout}s"
                log('WARN', f"Источник {source}: попытка {attempt}/{retries} превысила timeout {timeout}s")

            except Exception as e:
                last_error = str(e)
                log('WARN', f"Источник {source}: попытка {attempt}/{retries} завершилась ошибкой -> {last_error}")

            if attempt < retries:
                time.sleep(1)

        raise RuntimeError(f"не удалось загрузить после {retries} попыток по {timeout}s: {last_error}")

    path = source[7:] if source.startswith('file://') else source
    if not os.path.exists(path):
        raise RuntimeError(f"локальный файл не найден: {path}")
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        payload = f.read()
    if not payload.strip():
        raise RuntimeError(f"локальный файл пустой: {path}")
    cache[source] = payload
    return payload


def extract_links_from_payload(payload):
    text = payload.strip()
    plain_links = []
    for ln in text.splitlines():
        ln = ln.strip()
        if ln.startswith(VALID_PROTOCOLS):
            plain_links.append(ln)
    if plain_links:
        return plain_links, 'plain'
    compact = ''.join(text.split())
    compact += '=' * (-len(compact) % 4)
    try:
        decoded_text = base64.b64decode(compact).decode('utf-8')
    except Exception:
        return [], 'invalid'
    b64_links = []
    for ln in decoded_text.splitlines():
        ln = ln.strip()
        if ln.startswith(VALID_PROTOCOLS):
            b64_links.append(ln)
    if b64_links:
        return b64_links, 'base64'
    return [], 'empty'



def load_local_protected_ids(path=LOCAL_LINKS_PATH_DEFAULT):
    """Return stable_id set for user-managed local links.

    Links from /etc/config/podkop-local-links are treated as manually managed.
    They may be added by the subscription updater, but must not be removed by
    automatic cleanup even if fail_count reaches the deletion threshold.
    """
    protected = set()
    if not path or not os.path.exists(path):
        return protected
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            payload = f.read()
        links, fmt = extract_links_from_payload(payload)
        for link in links:
            protected.add(stable_id(link))
        if protected:
            log("INFO", f"Локальные пользовательские ключи защищены от автоудаления: {len(protected)} ({path}, {fmt})")
    except Exception as e:
        log("WARN", f"Не удалось прочитать локальные ключи для защиты от удаления: {path}: {e}")
    return protected

def filter_links(links_raw, regex_pattern, match_mode, on_empty, sec, source):
    if not regex_pattern:
        return links_raw
    filtered_links = []
    for ln in links_raw:
        target = unquote_percent(ln)
        try:
            is_match = bool(re.search(regex_pattern, target, re.IGNORECASE))
        except re.error:
            log("ERROR", f"[{sec}]: Некорректное регулярное выражение '{regex_pattern}' для источника {source}")
            return []
        if (match_mode == 'ifmatch' and is_match) or (match_mode == 'ifnotmatch' and not is_match):
            filtered_links.append(ln)
    if not filtered_links:
        if on_empty == 'all':
            log("INFO", f"[{sec}]: По фильтру пусто, используются все ссылки из источника {source} (on_empty=all).")
            return links_raw
        log("INFO", f"[{sec}]: По фильтру пусто, источник пропущен: {source} (on_empty=skip).")
        return []
    return filtered_links


def fetch_links(jobs, hwid, device_model, kernel_ver):
    payload_cache = {}
    for sec, job in jobs.items():
        log("DEBUG", f"--- Обработка секции: [{sec}] ---")
        section_links = []
        job['source_errors'] = 0
        job['source_success'] = 0
        for entry in job['entries']:
            source = entry['source']
            try:
                payload = read_source_payload(source, hwid, device_model, kernel_ver, payload_cache)
            except subprocess.TimeoutExpired:
                log("ERROR", f"[{sec}]: Превышено время ожидания ответа сервера: {source}")
                job['source_errors'] += 1
                continue
            except Exception as e:
                log("ERROR", f"[{sec}]: Ошибка чтения источника {source} -> {e}")
                job['source_errors'] += 1
                continue
            links_raw, payload_type = extract_links_from_payload(payload)
            if payload_type == 'invalid':
                log("ERROR", f"[{sec}]: Источник не plain-text URI и не валидный Base64: {source}")
                job['source_errors'] += 1
                continue
            if not links_raw:
                log("WARN", f"[{sec}]: В источнике нет ссылок поддерживаемого типа: {source}")
                job['source_success'] += 1
                continue
            filtered_links = filter_links(links_raw, entry['regex'], entry['match_mode'], entry['on_empty'], sec, source)
            if not filtered_links:
                job['source_success'] += 1
                continue
            section_links.extend(filtered_links)
            job['source_success'] += 1
            log("INFO", f"[{sec}]: источник {source} ({payload_type}) -> ссылок после фильтра: {len(filtered_links)}")
        job['links'], dup_count = dedupe_links_keep_order(section_links)
        if dup_count:
            log("INFO", f"[{sec}]: Дубликатов в новых ссылках подписки отброшено: {dup_count}")
        if job['links']:
            log("INFO", f"[{sec}]: Итого уникальных новых ссылок из подписок: {len(job['links'])}")
        else:
            log("WARN", f"[{sec}]: После обработки всех источников не осталось ссылок.")


def load_current_podkop_sections(config_path):
    result = {}
    for s in parse_uci_sections(config_path):
        if s['type'].lower() != 'section' or not s['name']:
            continue
        name = s['name'].strip().lower()
        ptype = s['options'].get('proxy_config_type', 'urltest').strip().lower()
        if ptype not in VALID_PTYPES:
            ptype = 'urltest'
        links = []
        if ptype == 'selector':
            links = s['lists'].get('selector_proxy_links', [])
        else:
            links = s['lists'].get('urltest_proxy_links', [])
        # Fallback: если тип не совпал, но ссылки есть в другом списке.
        if not links:
            if s['lists'].get('urltest_proxy_links'):
                ptype = 'urltest'
                links = s['lists'].get('urltest_proxy_links', [])
            elif s['lists'].get('selector_proxy_links'):
                ptype = 'selector'
                links = s['lists'].get('selector_proxy_links', [])
        result[name] = {'ptype': ptype, 'links': list(links), 'section': s}
    return result


def default_state():
    return {'version': 2, 'sections': {}, 'health': {}}


def load_state(path):
    if not os.path.exists(path):
        return default_state()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            st = json.load(f)
        if not isinstance(st, dict):
            return default_state()
        st.setdefault('version', 2)
        st.setdefault('sections', {})
        st.setdefault('health', {})
        return st
    except Exception as e:
        log("WARN", f"Не удалось прочитать state.json: {e}. Будет создан новый state.")
        return default_state()


def save_state(path, state):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write('\n')
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def ensure_state_section(state, sec, ptype=None):
    sections = state.setdefault('sections', {})
    if sec not in sections or not isinstance(sections.get(sec), dict):
        sections[sec] = {'proxy_type': ptype or 'urltest', 'links': {}}
    sections[sec].setdefault('proxy_type', ptype or sections[sec].get('proxy_type', 'urltest'))
    sections[sec].setdefault('links', {})
    return sections[sec]


def import_current_links_to_state(state, current_sections):
    now = int(time.time())
    imported = 0
    updated = 0
    for sec, data in current_sections.items():
        st_sec = ensure_state_section(state, sec, data.get('ptype'))
        st_sec['proxy_type'] = data.get('ptype', st_sec.get('proxy_type', 'urltest'))
        for link in data.get('links', []):
            sid = stable_id(link)
            item = st_sec['links'].get(sid)
            if not item:
                st_sec['links'][sid] = {
                    'url': link,
                    'name': link_name(link),
                    'fail_count': 0,
                    'first_seen': now,
                    'last_seen_in_config': now,
                    'last_seen_in_subscription': None,
                    'last_status': 'unknown',
                    'last_delay': None,
                    'last_checked': None
                }
                imported += 1
            else:
                if item.get('url') != link:
                    item['url'] = link
                    item['name'] = link_name(link)
                    updated += 1
                item['last_seen_in_config'] = now
    return imported, updated


def mark_subscription_seen(state, sec, links):
    now = int(time.time())
    st_sec = ensure_state_section(state, sec)
    for link in links:
        sid = stable_id(link)
        if sid in st_sec['links']:
            st_sec['links'][sid]['last_seen_in_subscription'] = now


def load_podkop_proxies():
    cmd = ['/usr/bin/podkop', 'clash_api', 'get_proxies']
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=25)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"command failed: {' '.join(cmd)}")
    if not result.stdout.strip():
        raise RuntimeError("empty get_proxies response")
    try:
        data = json.loads(result.stdout)
    except Exception as e:
        raise RuntimeError(f"invalid get_proxies JSON: {e}")
    if isinstance(data, dict) and isinstance(data.get('proxies'), dict):
        proxies = data['proxies']
    elif isinstance(data, dict):
        proxies = data
    else:
        raise RuntimeError("get_proxies JSON has no proxies dictionary")
    if not proxies:
        raise RuntimeError("get_proxies returned empty proxies dictionary")
    return proxies


def proxy_status(proxy):
    if not isinstance(proxy, dict):
        return 'missing', None
    hist = proxy.get('history')
    if isinstance(hist, list) and hist:
        last = hist[-1]
        if isinstance(last, dict):
            delay = last.get('delay')
            if isinstance(delay, (int, float)) and delay > 0:
                return 'ok', int(delay)
            return 'bad', delay
    delay = proxy.get('delay')
    if isinstance(delay, (int, float)):
        return ('ok' if delay > 0 else 'bad'), int(delay) if isinstance(delay, (int, float)) else None
    alive = proxy.get('alive')
    if isinstance(alive, bool):
        return ('ok' if alive else 'bad'), None
    # Для Podkop/sing-box N/A сейчас выглядит как пустой history.
    return 'bad', None


def observe_only(config_path, state_path):
    log("INFO", "=== ЗАПУСК ПАССИВНОЙ ПРОВЕРКИ КЛЮЧЕЙ ===")
    state = load_state(state_path)
    current_sections = load_current_podkop_sections(config_path)
    imported, updated = import_current_links_to_state(state, current_sections)
    if imported or updated:
        log("INFO", f"State импортирован из текущего Podkop config: новых={imported}, обновлено={updated}")
    if not current_sections:
        log("WARN", "В /etc/config/podkop нет секций Podkop с proxy-ссылками. Проверка пропущена.")
        save_state(state_path, state)
        return
    try:
        proxies = load_podkop_proxies()
    except Exception as e:
        log("WARN", f"Не удалось получить состояние Podkop URLTest: {e}. fail_count не изменён.")
        state.setdefault('health', {})['last_scan_status'] = 'api_error'
        state['health']['last_scan_error'] = str(e)
        state['health']['last_scan'] = int(time.time())
        save_state(state_path, state)
        return
    now = int(time.time())
    total = ok = bad = missing = 0
    for sec, data in current_sections.items():
        links, dup_count = dedupe_links_keep_order(data.get('links', []))
        st_sec = ensure_state_section(state, sec, data.get('ptype'))
        for idx, link in enumerate(links, 1):
            sid = stable_id(link)
            item = st_sec['links'].setdefault(sid, {'url': link, 'name': link_name(link), 'fail_count': 0, 'first_seen': now})
            tag = f"{sec}-{idx}-out"
            status, delay = proxy_status(proxies.get(tag))
            total += 1
            item['url'] = link
            item['name'] = link_name(link)
            item['last_checked'] = now
            item['last_tag'] = tag
            item['last_delay'] = delay
            item['last_status'] = status
            if status == 'ok':
                item['fail_count'] = 0
                item['last_ok'] = now
                ok += 1
            else:
                old = item.get('fail_count', 0)
                try:
                    old = int(old)
                except Exception:
                    old = 0
                item['fail_count'] = old + 1
                if status == 'missing':
                    missing += 1
                else:
                    bad += 1
        if dup_count:
            log("INFO", f"[{sec}]: в config есть дубликаты по stable_id: {dup_count}; observe считает только уникальные ключи")
    state.setdefault('health', {})['last_scan'] = now
    state['health']['last_scan_status'] = 'ok'
    state['health']['total'] = total
    state['health']['ok'] = ok
    state['health']['bad'] = bad
    state['health']['missing'] = missing
    save_state(state_path, state)
    log("INFO", f"Проверка завершена: total={total}, ok={ok}, bad={bad}, missing={missing}. Конфиг Podkop не изменялся.")


def build_final_links_for_section(sec, job, current_sections, state, delete_after_fails, min_keep, protected_ids=None):
    now = int(time.time())
    protected_ids = protected_ids or set()
    current = current_sections.get(sec, {'ptype': job.get('ptype', 'urltest'), 'links': []})
    current_links = list(current.get('links', []))
    current_unique, current_dups = dedupe_links_keep_order(current_links)
    current_ids = {stable_id(x) for x in current_unique}
    st_sec = ensure_state_section(state, sec, job.get('ptype') or current.get('ptype'))
    st_sec['proxy_type'] = job.get('ptype') or current.get('ptype', st_sec.get('proxy_type', 'urltest'))
    # Синхронизируем state с текущим конфигом.
    for link in current_unique:
        sid = stable_id(link)
        item = st_sec['links'].setdefault(sid, {'url': link, 'name': link_name(link), 'fail_count': 0, 'first_seen': now})
        item['url'] = link
        item['name'] = link_name(link)
        item['last_seen_in_config'] = now
        if sid in protected_ids:
            item['protected_local'] = True
    # Подписка только добавляет. Если источник упал или 0 валидных — секцию не меняем вообще.
    if job.get('source_errors', 0) > 0:
        log("WARN", f"[{sec}]: есть ошибки источников подписки; секция не изменяется, чтобы не потерять текущие ключи")
        return None, {'skip': True, 'reason': 'source_errors'}
    if job.get('source_success', 0) == 0 or not job.get('links'):
        log("WARN", f"[{sec}]: подписка не дала валидных ссылок; секция не изменяется")
        return None, {'skip': True, 'reason': 'no_subscription_links'}
    mark_subscription_seen(state, sec, job['links'])
    final_links = list(current_unique)
    added = 0
    for link in job.get('links', []):
        sid = stable_id(link)
        item = st_sec['links'].get(sid)
        if item:
            item['last_seen_in_subscription'] = now
        if sid not in current_ids:
            final_links.append(link)
            current_ids.add(sid)
            st_sec['links'][sid] = {
                'url': link,
                'name': link_name(link),
                'fail_count': 0,
                'first_seen': now,
                'last_seen_in_subscription': now,
                'last_seen_in_config': now,
                'last_status': 'new',
                'last_delay': None,
                'last_checked': None,
                'protected_local': sid in protected_ids
            }
            added += 1
    # Удаляем только ночью/в maintenance, только если fail_count >= threshold.
    remove_ids = []
    for sid, item in list(st_sec['links'].items()):
        if sid not in current_ids:
            continue
        try:
            fc = int(item.get('fail_count', 0))
        except Exception:
            fc = 0
        if fc >= delete_after_fails:
            if sid in protected_ids or item.get('protected_local'):
                item['protected_local'] = True
                log("INFO", f"[{sec}]: ключ защищён как локальный пользовательский, автоудаление пропущено: {item.get('name','')[:80]}")
                continue
            remove_ids.append(sid)
    # Не оставляем секцию пустой.
    max_remove = max(0, len(final_links) - max(0, min_keep))
    if len(remove_ids) > max_remove:
        log("WARN", f"[{sec}]: кандидатов на удаление {len(remove_ids)}, но min_keep={min_keep}; часть ключей оставлена")
        remove_ids = remove_ids[:max_remove]
    if remove_ids:
        remove_set = set(remove_ids)
        final_links = [x for x in final_links if stable_id(x) not in remove_set]
        for sid in remove_ids:
            st_sec['links'].pop(sid, None)
    changed = added > 0 or len(remove_ids) > 0 or current_dups > 0 or final_links != current_links
    return final_links, {'skip': False, 'added': added, 'removed': len(remove_ids), 'duplicates': current_dups, 'changed': changed}


def update_uci_config_with_final_links(config_path, updates):
    if not os.path.exists(config_path):
        log("ERROR", f"Конфиг {config_path} не найден. Настройте Podkop в интерфейсе.")
        sys.exit(1)
    with open(config_path, 'r', encoding='utf-8', errors='replace') as f:
        old_lines = f.readlines()
    out_lines = []
    current_sec = None
    current_type = None
    found_sections = set()
    skip_multiline = False

    def is_target(sec_type, sec_name):
        return sec_type and sec_name and sec_type.lower() == 'section' and sec_name.lower() in updates

    def flush_section(sec_type, sec_name):
        if not is_target(sec_type, sec_name):
            return
        sec = sec_name.lower()
        upd = updates[sec]
        ptype = upd['ptype']
        links = upd['links']
        out_lines.append(f"\toption connection_type 'proxy'\n")
        out_lines.append(f"\toption proxy_config_type '{ptype}'\n")
        for link in links:
            out_lines.append(f"\tlist {ptype}_proxy_links {uci_quote(link)}\n")

    for line in old_lines:
        m = re.match(r"^\s*config\s+([a-zA-Z0-9_-]+)\s+['\"]?([a-zA-Z0-9_-]+)['\"]?", line, re.IGNORECASE)
        if m:
            if current_sec:
                flush_section(current_type, current_sec)
            current_type = m.group(1)
            current_sec = m.group(2)
            if is_target(current_type, current_sec):
                found_sections.add(current_sec.lower())
            out_lines.append(line)
            skip_multiline = False
            continue
        if is_target(current_type, current_sec):
            sline = line.strip()
            if skip_multiline:
                if "'" in sline:
                    skip_multiline = False
                continue
            if any(sline.startswith(prefix) for prefix in (
                'list urltest_proxy_links',
                'list selector_proxy_links',
                'option connection_type',
                'option proxy_config_type',
                'option proxy_string'
            )):
                if line.count("'") % 2 != 0:
                    skip_multiline = True
                continue
        out_lines.append(line)
    if current_sec:
        flush_section(current_type, current_sec)
    for sec in updates:
        if sec not in found_sections:
            log("WARN", f"Секция '{sec}' собрана, но отсутствует как config section '{sec}' в {config_path}")
    return ''.join(old_lines), ''.join(out_lines)


def apply_updates_with_uci(config_path, updates):
    if os.path.abspath(config_path) != '/etc/config/podkop':
        return
    changed = False
    for sec, upd in updates.items():
        links = upd.get('links') or []
        ptype = upd.get('ptype', 'urltest')
        exists = subprocess.run(['uci', '-q', 'get', f'podkop.{sec}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        if not exists:
            log("WARN", f"[{sec}]: секция отсутствует в /etc/config/podkop, UCI-применение пропущено")
            continue
        commands = [
            ['uci', '-q', 'delete', f'podkop.{sec}.urltest_proxy_links'],
            ['uci', '-q', 'delete', f'podkop.{sec}.selector_proxy_links'],
            ['uci', '-q', 'delete', f'podkop.{sec}.proxy_string'],
            ['uci', 'set', f'podkop.{sec}.connection_type=proxy'],
            ['uci', 'set', f'podkop.{sec}.proxy_config_type={ptype}'],
        ]
        for cmd in commands:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for link in links:
            subprocess.run(['uci', 'add_list', f'podkop.{sec}.{ptype}_proxy_links={link}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        changed = True
    if changed:
        subprocess.run(['uci', 'commit', 'podkop'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("INFO", "UCI-поля секций Podkop зафиксированы через uci commit.")


def normalize_config(text):
    text = re.sub(r'sid=[a-zA-Z0-9]+', '', text)
    text = re.sub(r"#[^'\n\r]*", '', text)
    return text.replace('\n', '').replace('\r', '')


def main():
    setup_syslog()
    args = parse_args()
    if args.version:
        print(f"podkop-subscriptions {APP_VERSION}")
        return
    if args.observe_only:
        observe_only(args.config, args.state)
        return
    log("INFO", "=== ЗАПУСК НОЧНОГО ОБНОВЛЕНИЯ ПОДПИСОК ===")
    mac = get_mac_address()
    device_model = get_device_model()
    kernel_ver = get_kernel_version()
    raw_hwid_str = f"{mac}{device_model}"
    hwid = hashlib.md5(raw_hwid_str.encode('utf-8')).hexdigest()[:16]
    log("INFO", f"Устройство: {device_model} (Ядро: {kernel_ver})")
    log("INFO", f"Сгенерирован X-HWID: {hwid}")
    jobs = load_jobs(args.subs)
    if not jobs:
        log("INFO", "Нет настроенных подписок. Выход без изменений.")
        return
    current_sections = load_current_podkop_sections(args.config)
    state = load_state(args.state)
    imported, updated = import_current_links_to_state(state, current_sections)
    if imported or updated:
        log("INFO", f"State синхронизирован с текущим Podkop config: новых={imported}, обновлено={updated}")
    fetch_links(jobs, hwid, device_model, kernel_ver)
    protected_local_ids = load_local_protected_ids()
    updates = {}
    summary_changed = False
    for sec, job in jobs.items():
        final_links, info = build_final_links_for_section(sec, job, current_sections, state, args.delete_after_fails, args.min_keep, protected_local_ids)
        if info.get('skip'):
            log("INFO", f"[{sec}]: пропуск изменения секции ({info.get('reason')})")
            continue
        log("INFO", f"[{sec}]: added={info.get('added',0)}, removed={info.get('removed',0)}, duplicates={info.get('duplicates',0)}, final_links={len(final_links)}")
        if info.get('changed'):
            updates[sec] = {'ptype': job.get('ptype', 'urltest'), 'links': final_links}
            summary_changed = True
    save_state(args.state, state)
    if not updates:
        if args.force:
            log("INFO", "Изменений нет. Конфиг Podkop не изменялся, перезапуск не выполняется.")
        else:
            log("INFO", "Изменений не обнаружено. Конфиг и Podkop не трогаются.")
        return
    old_content, new_content = update_uci_config_with_final_links(args.config, updates)
    is_content_changed = normalize_config(old_content) != normalize_config(new_content)
    if not args.force and not is_content_changed:
        log("INFO", "После сборки изменений в конфиге не обнаружено. Перезапуск не требуется.")
        return
    try:
        backup_path = f"{args.config}.podkop-subscriptions.bak"
        try:
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(old_content)
        except Exception as e:
            log("WARN", f"Не удалось создать backup {backup_path}: {e}")
        tmp_path = f"{args.config}.tmp.{os.getpid()}"
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        os.replace(tmp_path, args.config)
        apply_updates_with_uci(args.config, updates)
        os.system("/etc/init.d/podkop restart")
        log("INFO", "Успешно завершено: конфиг обновлён, Podkop перезапущен.")
    except Exception as e:
        log("ERROR", f"Ошибка при сохранении конфига: {e}")


if __name__ == "__main__":
    main()
