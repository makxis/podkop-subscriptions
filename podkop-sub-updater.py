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


APP_VERSION = "3.6"
USER_AGENT = f"Podkop-Subscription-Updater/{APP_VERSION}"
VALID_PROTOCOLS = ('vless://', 'ss://', 'trojan://', 'socks4://', 'socks4a://', 'socks5://', 'hy2://', 'hysteria2://')
VALID_PTYPES = {'urltest', 'selector'}
VALID_ON_EMPTY = {'all', 'skip'}
VALID_MATCH_MODES = {'ifmatch', 'ifnotmatch'}
STATE_PATH_DEFAULT = '/etc/podkop-subscriptions/state.json'
SUBSCRIPTIONS_CONFIG_DEFAULT = '/etc/config/podkop_subscriptions'
LOCAL_LINKS_PATH_DEFAULT = '/etc/config/podkop-local-links'
DELETE_AFTER_FAIL_COUNT_DEFAULT = 72
MIN_KEEP_PER_SECTION_DEFAULT = 1
SOURCE_TIMEOUT_DEFAULT = 45
SOURCE_RETRIES_DEFAULT = 3
CATCHUP_AFTER_HOURS_DEFAULT = 24
VALID_TIME_MIN_TS = 1700000000
SINGBOX_CHECK_TIMEOUT_DEFAULT = 15
SINGBOX_CHECK_MAX_RUNS_DEFAULT = 40



def setup_syslog():
    syslog.openlog("podkop-updater", syslog.LOG_PID, syslog.LOG_USER)


def sanitize_log_message(msg):
    msg = str(msg)
    # Hide subscription URLs and proxy links before writing to stdout/syslog/LuCI logs.
    msg = re.sub(r'(?i)\b(vless|trojan|ss|socks4a?|socks5|hy2|hysteria2)://\S+', '<proxy-link>', msg)
    msg = re.sub(r'(?i)https?://\S+', '<remote-url>', msg)
    msg = re.sub(r'(?i)(pbk|sid|sni|password|token|uuid)=([^\s&]+)', r'\1=<hidden>', msg)
    msg = re.sub(r'(?i)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '<uuid>', msg)
    return msg



REMOVE_REASON_LABELS_RU = {
    'endpoint_host_rotation': 'IP/домен-ротация',
    'sni_rotation': 'SNI-ротация',
    'latency': 'ping выше лимита',
    'force_fail_count': 'принудительно по fail_count',
    'limit_fail_count': 'лимит: плохой fail_count',
    'limit_na': 'лимит: нет ответа',
    'limit_slowest': 'лимит: самый медленный',
    'fail_count_threshold': 'долго не работает',
    'recently_removed': 'недавно удалён',
}


def format_removed_reasons_ru(reasons):
    if not reasons:
        return ''
    parts = []
    for key, value in sorted(reasons.items(), key=lambda kv: str(kv[0])):
        label = REMOVE_REASON_LABELS_RU.get(str(key), str(key))
        parts.append(f'{label}:{value}')
    return ', '.join(parts)


def build_section_result_log_ru(sec, info, final_count):
    parts = [
        f'добавлено={colored_count(info.get("added", 0), "good_positive")}',
        f'удалено={colored_count(info.get("removed", 0), "warn_positive")}',
        f'дубликатов в текущем конфиге={colored_count(info.get("duplicates", 0), "warn_positive")}',
        f'ключей итого={colored_count(final_count, "info_positive")}',
    ]

    removed_reasons = format_removed_reasons_ru(info.get('removed_by_reason') or {})
    if removed_reasons:
        parts.append(f'удалено по причинам={val_warn(removed_reasons)}')

    if info.get('incoming_sni_collapsed'):
        parts.append(f'схлопнуто SNI-ротаций из подписок={colored_count(info.get("incoming_sni_collapsed"), "info_positive")}')
    if info.get('incoming_endpoint_collapsed'):
        parts.append(f'схлопнуто IP/домен-дублей из подписок={colored_count(info.get("incoming_endpoint_collapsed"), "info_positive")}')
    if info.get('skipped_sni_local'):
        parts.append(f'пропущено SNI-дублей локальных={colored_count(info.get("skipped_sni_local"), "warn_positive")}')
    if info.get('skipped_endpoint_local'):
        parts.append(f'пропущено IP/домен-дублей локальных={colored_count(info.get("skipped_endpoint_local"), "warn_positive")}')
    if info.get('skipped_by_limit'):
        parts.append(f'не добавлено из-за лимита={colored_count(info.get("skipped_by_limit"), "warn_positive")}')
    if info.get('skipped_recent'):
        parts.append(f'пропущено недавно удалённых={colored_count(info.get("skipped_recent"), "warn_positive")}')
    if info.get('skipped_removed_this_run'):
        parts.append(f'пропущено удалённых в этом запуске={colored_count(info.get("skipped_removed_this_run"), "warn_positive")}')

    return f'[{sec}]: итог: ' + ', '.join(parts)


ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def strip_ansi(s):
    return ANSI_RE.sub('', str(s))


def terminal_colors_enabled():
    if os.environ.get('NO_COLOR'):
        return False
    color_env = os.environ.get('PODKOP_SUB_COLOR', '').lower()
    if color_env in ('0', 'false', 'no', 'off'):
        return False
    if color_env in ('1', 'true', 'yes', 'on', 'always'):
        return True
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def ansi(text, code):
    text = str(text)
    if not terminal_colors_enabled():
        return text
    return f'\033[{code}m{text}\033[0m'


def val_good(value):
    return ansi(value, '32;1')


def val_warn(value):
    return ansi(value, '33;1')


def val_bad(value):
    return ansi(value, '31;1')


def val_dim(value):
    return ansi(value, '2')


def val_info(value):
    return ansi(value, '36;1')


def colored_count(value, mode='auto'):
    try:
        n = int(value or 0)
    except Exception:
        n = 0

    if mode == 'good_positive':
        return val_good(n) if n > 0 else val_dim(n)
    if mode == 'bad_positive':
        return val_bad(n) if n > 0 else val_good(n)
    if mode == 'warn_positive':
        return val_warn(n) if n > 0 else val_good(n)
    if mode == 'info_positive':
        return val_info(n) if n > 0 else val_dim(n)
    return val_info(n)


def log(level, msg):
    safe_msg = sanitize_log_message(msg)
    print(f"[{level}] {safe_msg}")
    syslog_level = syslog.LOG_WARNING if level in ["ERROR", "WARN"] else syslog.LOG_INFO
    try:
        syslog.syslog(syslog_level, f"[{level}] {strip_ansi(safe_msg)}")
    except Exception:
        pass

def parse_args():
    parser = argparse.ArgumentParser(description="Обновление подписок для Podkop")
    parser.add_argument('--version', action='store_true', help='Показать версию podkop-subscriptions и выйти')
    parser.add_argument('--config', default='/etc/config/podkop', help='Путь к UCI конфигу podkop')
    parser.add_argument('--subs', default=SUBSCRIPTIONS_CONFIG_DEFAULT, help='Путь к UCI конфигу подписок')
    parser.add_argument('--force', action='store_true', help='Принудительно перезаписать конфиг podkop и перезапустить сервис')
    parser.add_argument('--observe-only', action='store_true', help='Только обновить fail_count по текущему состоянию Podkop URLTest; конфиг не менять')
    parser.add_argument('--catch-up', action='store_true', help='После загрузки: обновить подписки, если последнее успешное обновление старше 24 часов')
    parser.add_argument('--catch-up-retry', action='store_true', help='Повторять catch-up только если предыдущий catch-up завершился ошибкой')
    parser.add_argument('--status-summary', action='store_true', help='Показать краткое состояние для LuCI')
    parser.add_argument('--fail-count', action='store_true', help='Показать текущие fail_count по state.json без вывода proxy-ссылок')
    parser.add_argument('--state', default=STATE_PATH_DEFAULT, help='Путь к state.json')
    parser.add_argument('--delete-after-fails', type=int, default=DELETE_AFTER_FAIL_COUNT_DEFAULT, help='Удалять ключ после N подряд неудачных наблюдений')
    parser.add_argument('--min-keep', type=int, default=MIN_KEEP_PER_SECTION_DEFAULT, help='Минимум ключей, которые надо оставить в секции')
    return parser.parse_args()


def is_enabled_value(value):
    return str(value or '0').strip().lower() in ('1', 'true', 'yes', 'on', 'enabled')


def parse_positive_int(value, default=0):
    try:
        v = int(str(value or '').strip())
        return v if v > 0 else default
    except Exception:
        return default


def merge_min_positive(current, value):
    value = parse_positive_int(value, 0)
    if value <= 0:
        return current
    if not current or current <= 0:
        return value
    return min(current, value)


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


def _url_without_fragment(link):
    return (link or '').split('#', 1)[0].strip()


def _split_query_param(part):
    key = part.split('=', 1)[0].strip().lower()
    return key


def has_query_param(link, name):
    base = _url_without_fragment(link)
    if '?' not in base:
        return False
    query = base.split('?', 1)[1]
    name = str(name or '').strip().lower()
    for part in query.split('&'):
        if _split_query_param(part) == name:
            return True
    return False


def canonical_url_without_query_params(link, ignored_names):
    """Canonicalize URL for soft duplicate detection.

    Used only for optional SNI-rotation dedupe. The normal stable_id remains
    strict and includes every technical parameter except the human name after #.
    """
    base = _url_without_fragment(link)
    if '?' not in base:
        return base

    prefix, query = base.split('?', 1)
    ignored = {str(x).strip().lower() for x in (ignored_names or [])}
    kept = []
    for part in query.split('&'):
        if not part:
            continue
        key = _split_query_param(part)
        if key in ignored:
            continue
        kept.append(part)

    # Sorting makes detection stable if a subscription changes query parameter order.
    kept.sort(key=lambda p: (_split_query_param(p), p))
    return prefix + ('?' + '&'.join(kept) if kept else '')


def has_sni_param(link):
    return has_query_param(link, 'sni')


def sni_rotation_id(link):
    base = canonical_url_without_query_params(link, {'sni'})
    return hashlib.sha256(base.encode('utf-8', 'ignore')).hexdigest()[:24]


def dedupe_sni_rotation_links_keep_last(links):
    """Collapse incoming subscription SNI rotations, keeping the last occurrence.

    Only links that actually contain the sni= query parameter participate.
    Links without sni= are not collapsed by this soft dedupe rule.
    """
    out_rev = []
    seen_rotation = set()
    collapsed = 0

    for link in reversed(list(links or [])):
        if has_sni_param(link):
            rid = sni_rotation_id(link)
            if rid in seen_rotation:
                collapsed += 1
                continue
            seen_rotation.add(rid)
        out_rev.append(link)

    return list(reversed(out_rev)), collapsed



def endpoint_host_value(link):
    """Return canonical host/domain/IP for optional endpoint dedupe.

    This intentionally groups by host only, not by port or protocol.
    The feature is optional because some providers may intentionally publish
    several different ports on the same host.
    """
    try:
        parts = _parse_link_parts(link)
        host = str(parts.get('host') or '').strip().lower()
    except Exception:
        # Conservative fallback: best-effort authority parsing without rejecting the link here.
        base = _url_without_fragment(link)
        if '://' not in base:
            return ''
        rest = base.split('://', 1)[1].split('?', 1)[0].split('/', 1)[0]
        if '@' in rest:
            rest = rest.rsplit('@', 1)[1]
        host, _port = _parse_hostport(rest)
        host = str(host or '').strip().lower()
    if host.startswith('[') and host.endswith(']'):
        host = host[1:-1]
    return host


def endpoint_host_id(link):
    host = endpoint_host_value(link)
    if not host:
        return ''
    return hashlib.sha256(host.encode('utf-8', 'ignore')).hexdigest()[:24]


def dedupe_endpoint_host_links_keep_last(links):
    """Collapse incoming links by server host/domain/IP, keeping the last occurrence."""
    out_rev = []
    seen = set()
    collapsed = 0

    for link in reversed(list(links or [])):
        eid = endpoint_host_id(link)
        if eid:
            if eid in seen:
                collapsed += 1
                continue
            seen.add(eid)
        out_rev.append(link)

    return list(reversed(out_rev)), collapsed



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
        sources = list(lists.get('source', []))
        if is_enabled_value(opt.get('use_local_links', '0')) and LOCAL_LINKS_PATH_DEFAULT not in sources and ('file://' + LOCAL_LINKS_PATH_DEFAULT) not in sources:
            sources.append('file://' + LOCAL_LINKS_PATH_DEFAULT)
        regex_pattern = opt.get('regex', '').strip()
        match_mode = opt.get('match_mode', 'ifnotmatch').strip().lower()
        ptype = opt.get('proxy_type', 'urltest').strip().lower()
        on_empty = opt.get('on_empty', 'skip').strip().lower()
        max_links = parse_positive_int(opt.get('max_links', '0'), 0)
        max_latency_ms = parse_positive_int(opt.get('max_latency_ms', '0'), 0)
        force_cleanup = is_enabled_value(opt.get('force_cleanup', '0'))
        dedupe_sni_rotation = is_enabled_value(opt.get('dedupe_sni_rotation', '0'))
        dedupe_endpoint_host = is_enabled_value(opt.get('dedupe_endpoint_host', '0'))
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
            jobs[sec_name] = {
                'ptype': ptype,
                'entries': [],
                'links': [],
                'source_errors': 0,
                'source_success': 0,
                'max_links': 0,
                'max_latency_ms': 0,
                'force_cleanup': False,
                'dedupe_sni_rotation': False,
                'dedupe_endpoint_host': False
            }
        if jobs[sec_name]['ptype'] != ptype:
            log("WARN", f"subscription_group '{g['name']}': target_section '{sec_name}' уже использует '{jobs[sec_name]['ptype']}', нельзя смешивать с '{ptype}'. Пропуск.")
            continue
        jobs[sec_name]['max_links'] = merge_min_positive(jobs[sec_name].get('max_links', 0), max_links)
        jobs[sec_name]['max_latency_ms'] = merge_min_positive(jobs[sec_name].get('max_latency_ms', 0), max_latency_ms)
        jobs[sec_name]['force_cleanup'] = bool(jobs[sec_name].get('force_cleanup')) or force_cleanup
        jobs[sec_name]['dedupe_sni_rotation'] = bool(jobs[sec_name].get('dedupe_sni_rotation')) or dedupe_sni_rotation
        jobs[sec_name]['dedupe_endpoint_host'] = bool(jobs[sec_name].get('dedupe_endpoint_host')) or dedupe_endpoint_host
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
                jobs[sec_name] = {'ptype': ptype, 'entries': [], 'links': [], 'source_errors': 0, 'source_success': 0, 'max_links': 0, 'max_latency_ms': 0, 'force_cleanup': False, 'dedupe_sni_rotation': False, 'dedupe_endpoint_host': False}
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
    if not os.path.exists(subs_path):
        log("ERROR", f"Файл настроек подписок не найден: {subs_path}")
        log("INFO", "Создайте /etc/config/podkop_subscriptions или запустите install.sh в интерактивном режиме.")
        sys.exit(1)
    uci_jobs = load_jobs_from_uci_podkop(subs_path)
    if uci_jobs is not None:
        return uci_jobs
    # Legacy flat-file remains only for custom --subs files. Recommended config is UCI /etc/config/podkop_subscriptions.
    return load_jobs_from_flat_file(subs_path)

def is_url_source(source):
    return bool(re.match(r'^https?://', source, re.IGNORECASE))


def source_display_label(source, label=None):
    if label:
        return label
    if source == LOCAL_LINKS_PATH_DEFAULT or source == 'file://' + LOCAL_LINKS_PATH_DEFAULT:
        return 'локальный список'
    return 'источник'


def read_source_payload(source, hwid, device_model, kernel_ver, cache, label=None):
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
                log('INFO', f"{source_display_label(source, label)}: попытка {attempt}/{retries}, timeout={timeout}s")
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=timeout
                )

                if result.returncode == 0 and result.stdout and result.stdout.strip():
                    if attempt > 1:
                        log('INFO', f"{source_display_label(source, label)}: успешно загружен с попытки {attempt}/{retries}")
                    cache[source] = result.stdout
                    return result.stdout

                last_error = result.stderr.strip() or 'Пустой ответ'
                log('WARN', f"{source_display_label(source, label)}: попытка {attempt}/{retries} неуспешна -> {last_error}")

            except subprocess.TimeoutExpired:
                last_error = f"timeout {timeout}s"
                log('WARN', f"{source_display_label(source, label)}: попытка {attempt}/{retries} превысила timeout {timeout}s")

            except Exception as e:
                last_error = str(e)
                log('WARN', f"{source_display_label(source, label)}: попытка {attempt}/{retries} завершилась ошибкой -> {last_error}")

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
            log("INFO", f"Локальные пользовательские ключи защищены от автоудаления: {len(protected)} (локальный список, {fmt})")
    except Exception as e:
        log("WARN", f"Не удалось прочитать локальные ключи для защиты от удаления: локальный список: {e}")
    return protected

def filter_links(links_raw, regex_pattern, match_mode, on_empty, sec, source_label):
    if not regex_pattern:
        return links_raw
    filtered_links = []
    for ln in links_raw:
        target = unquote_percent(ln)
        try:
            is_match = bool(re.search(regex_pattern, target, re.IGNORECASE))
        except re.error:
            log("ERROR", f"[{sec}]: Некорректное регулярное выражение '{regex_pattern}' для {source_label}")
            return []
        if (match_mode == 'ifmatch' and is_match) or (match_mode == 'ifnotmatch' and not is_match):
            filtered_links.append(ln)
    if not filtered_links:
        if on_empty == 'all':
            log("INFO", f"[{sec}]: По фильтру пусто, используются все ссылки из {source_label} (on_empty=all).")
            return links_raw
        log("INFO", f"[{sec}]: По фильтру пусто, {source_label} пропущен (on_empty=skip).")
        return []
    return filtered_links



class LinkValidationError(Exception):
    def __init__(self, reason, detail=''):
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


VALID_TRANSPORTS = {'', 'tcp', 'raw', 'ws', 'grpc'}
VALID_SECURITY = {'', 'none', 'tls', 'reality'}
SUPPORTED_SCHEMES = {'vless', 'ss', 'trojan', 'socks4', 'socks4a', 'socks5', 'hy2', 'hysteria2'}


def validation_reason_text(reason):
    mapping = {
        'invalid_url': 'некорректная ссылка',
        'unsupported_scheme': 'неподдерживаемый тип proxy',
        'missing_host': 'не указан host',
        'missing_port': 'не указан port',
        'invalid_port': 'некорректный port',
        'missing_userinfo': 'не указан пользователь/пароль',
        'invalid_userinfo': 'некорректный userinfo',
        'unsupported_transport': 'неподдерживаемый transport',
        'unsupported_security': 'неподдерживаемый security',
        'missing_reality_public_key': 'для reality отсутствует pbk',
        'invalid_transport_param': 'некорректный параметр transport',
        'singbox_not_found': 'sing-box не найден',
        'singbox_check_failed': 'не прошёл sing-box check',
        'singbox_check_limit': 'превышен лимит sing-box check',
        'normalized_missing_transport': 'добавлен type=tcp для совместимости с Podkop'
    }
    return mapping.get(reason, reason or 'неизвестная ошибка')


def _split_no_fragment(link):
    return (link or '').split('#', 1)[0].strip()


def _split_scheme(link):
    base = _split_no_fragment(link)
    if '://' not in base:
        raise LinkValidationError('invalid_url')
    scheme, rest = base.split('://', 1)
    scheme = scheme.strip().lower()
    if not scheme:
        raise LinkValidationError('invalid_url')
    return scheme, rest


def _split_query(rest):
    if '?' in rest:
        main, query = rest.split('?', 1)
    else:
        main, query = rest, ''
    return main, query


def _parse_query(query):
    result = {}
    for part in (query or '').split('&'):
        if not part:
            continue
        if '=' in part:
            k, v = part.split('=', 1)
        else:
            k, v = part, ''
        k = unquote_percent(k).strip()
        v = unquote_percent(v).strip()
        if k and k not in result:
            result[k] = v
    return result


def _b64_decode_text(value):
    compact = ''.join(str(value or '').split())
    if not compact:
        return ''
    compact = compact.replace('-', '+').replace('_', '/')
    compact += '=' * (-len(compact) % 4)
    try:
        return base64.b64decode(compact).decode('utf-8', 'replace')
    except Exception:
        return ''


def _parse_hostport(hostport):
    hostport = str(hostport or '').strip()
    if not hostport:
        return '', ''
    if hostport.startswith('['):
        end = hostport.find(']')
        if end <= 0:
            return '', ''
        host = hostport[1:end]
        rest = hostport[end + 1:]
        if rest.startswith(':'):
            return host, rest[1:]
        return host, ''
    if ':' not in hostport:
        return hostport, ''
    host, port = hostport.rsplit(':', 1)
    return host.strip(), port.strip()


def _parse_link_parts(link):
    scheme, rest = _split_scheme(link)
    if scheme not in SUPPORTED_SCHEMES:
        raise LinkValidationError('unsupported_scheme')
    main, query = _split_query(rest)
    q = _parse_query(query)

    # SIP002 shadowsocks иногда встречается как ss://base64(method:password@host:port)
    # без явного userinfo@host:port. Декодируем только для разбора, сам ключ не меняем.
    if scheme == 'ss' and '@' not in main:
        decoded = _b64_decode_text(main)
        if decoded and '@' in decoded:
            main = decoded

    # Отбрасываем path после authority. Для proxy URI он не должен влиять на host/port.
    authority = main.split('/', 1)[0]
    if '@' in authority:
        userinfo, hostport = authority.rsplit('@', 1)
    else:
        userinfo, hostport = '', authority
    host, port = _parse_hostport(hostport)
    return {
        'scheme': scheme,
        'userinfo': unquote_percent(userinfo),
        'host': unquote_percent(host),
        'port': unquote_percent(port),
        'query': q,
    }


def _port_int(port):
    try:
        p = int(str(port).strip())
        if 1 <= p <= 65535:
            return p
    except Exception:
        pass
    raise LinkValidationError('invalid_port')


def _validate_hysteria2_ports(port):
    port = str(port or '').strip()
    if not port:
        raise LinkValidationError('missing_port')
    if ',' not in port and '-' not in port:
        return {'server_port': _port_int(port)}
    items = []
    for raw in port.split(','):
        item = raw.strip()
        if not item:
            continue
        if '-' in item:
            a, b = item.split('-', 1)
            ai = _port_int(a)
            bi = _port_int(b)
            if ai > bi:
                raise LinkValidationError('invalid_port')
            items.append(f'{ai}:{bi}')
        else:
            p = _port_int(item)
            items.append(f'{p}:{p}')
    if not items:
        raise LinkValidationError('invalid_port')
    return {'server_ports': items}


def _split_csv(value):
    return [x.strip() for x in str(value or '').split(',') if x.strip()]


def _build_tls_object(parts):
    scheme = parts['scheme']
    q = parts['query']
    security = (q.get('security', '') or '').strip().lower()
    if not security and scheme in ('hy2', 'hysteria2'):
        security = 'tls'
    if security not in VALID_SECURITY:
        raise LinkValidationError('unsupported_security')
    if security in ('', 'none'):
        return None

    tls = {'enabled': True}
    sni = q.get('sni') or q.get('peer') or ''
    if sni:
        tls['server_name'] = sni
    insecure = (q.get('allowInsecure') or q.get('insecure') or '').strip().lower()
    if insecure in ('1', 'true', 'yes', 'on'):
        tls['insecure'] = True
    alpn = _split_csv(q.get('alpn', ''))
    if alpn:
        tls['alpn'] = alpn
    fp = q.get('fp', '')
    if fp and scheme not in ('hy2', 'hysteria2'):
        tls['utls'] = {'enabled': True, 'fingerprint': fp}
    if security == 'reality':
        pbk = q.get('pbk', '')
        if not pbk:
            raise LinkValidationError('missing_reality_public_key')
        tls['reality'] = {'enabled': True, 'public_key': pbk}
        sid = q.get('sid', '')
        if sid:
            tls['reality']['short_id'] = sid
    return tls


def _build_transport_object(parts):
    q = parts['query']
    transport = (q.get('type', '') or '').strip().lower()
    if transport not in VALID_TRANSPORTS:
        raise LinkValidationError('unsupported_transport')
    if transport in ('', 'tcp', 'raw'):
        return None
    if transport == 'ws':
        ws = {'type': 'ws', 'path': q.get('path', '')}
        host = q.get('host', '')
        if host:
            ws['headers'] = {'Host': host}
        ed = q.get('ed', '')
        if ed:
            try:
                ws['max_early_data'] = int(ed)
            except Exception:
                raise LinkValidationError('invalid_transport_param')
        eh = q.get('eh', '') or q.get('earlyDataHeaderName', '')
        if eh:
            ws['early_data_header_name'] = eh
        return ws
    if transport == 'grpc':
        grpc = {'type': 'grpc'}
        service_name = q.get('serviceName', '') or q.get('service_name', '')
        if service_name:
            grpc['service_name'] = service_name
        return grpc
    raise LinkValidationError('unsupported_transport')


def _parse_ss_userinfo(userinfo):
    userinfo = unquote_percent(userinfo or '')
    if ':' not in userinfo:
        decoded = _b64_decode_text(userinfo)
        if decoded:
            userinfo = decoded
    if ':' not in userinfo:
        raise LinkValidationError('invalid_userinfo')
    method, password = userinfo.split(':', 1)
    if not method or not password:
        raise LinkValidationError('invalid_userinfo')
    return method, password



def _query_has_param(q, name):
    name = str(name or '').strip()
    return name in q


def _set_query_param_preserve_fragment(link, key, value):
    """Set or add a query parameter without touching the fragment/name.

    Podkop treats an empty/missing transport type for vless/trojan as an
    unknown transport. For common tcp links the URI often omits type=tcp,
    while sing-box itself accepts the equivalent outbound without a transport
    object. We normalize only the stored URI so Podkop parses it safely.
    """
    link = str(link or '').strip()
    if '#' in link:
        base, frag = link.split('#', 1)
        frag = '#' + frag
    else:
        base, frag = link, ''

    if '?' in base:
        prefix, query = base.split('?', 1)
        parts = []
        changed = False
        for part in query.split('&'):
            if not part:
                continue
            k = part.split('=', 1)[0]
            if unquote_percent(k).strip().lower() == key.lower():
                parts.append(f'{k}={value}')
                changed = True
            else:
                parts.append(part)
        if not changed:
            parts.append(f'{key}={value}')
        return prefix + '?' + '&'.join(parts) + frag
    return base + '?' + f'{key}={value}' + frag


def normalize_link_for_podkop(link):
    """Normalize URI quirks that Podkop does not tolerate but sing-box does.

    Current Podkop calls _add_outbound_transport for vless/trojan and logs
    Unknown transport '' when the `type` query parameter is missing/empty.
    For these protocols missing type is commonly intended as plain TCP, so we
    make it explicit before writing the link to /etc/config/podkop.
    """
    parts = _parse_link_parts(link)
    scheme = parts['scheme']
    q = parts['query']
    if scheme in ('vless', 'trojan'):
        transport = (q.get('type', '') or '').strip().lower()
        if transport == '':
            return _set_query_param_preserve_fragment(link, 'type', 'tcp'), 'normalized_missing_transport'
    return link, ''

def proxy_link_to_singbox_outbound(link, tag):
    parts = _parse_link_parts(link)
    scheme = parts['scheme']
    host = parts['host']
    port = parts['port']
    userinfo = parts['userinfo']
    q = parts['query']

    if not host:
        raise LinkValidationError('missing_host')
    if not port:
        raise LinkValidationError('missing_port')

    outbound = {'tag': tag}

    if scheme in ('socks4', 'socks4a', 'socks5'):
        outbound.update({
            'type': 'socks',
            'server': host,
            'server_port': _port_int(port),
            'version': scheme.replace('socks', '')
        })
        if scheme == 'socks5' and userinfo:
            if ':' in userinfo:
                username, password = userinfo.split(':', 1)
                if username:
                    outbound['username'] = username
                if password:
                    outbound['password'] = password
            else:
                outbound['username'] = userinfo

    elif scheme == 'ss':
        method, password = _parse_ss_userinfo(userinfo)
        outbound.update({
            'type': 'shadowsocks',
            'server': host,
            'server_port': _port_int(port),
            'method': method,
            'password': password
        })

    elif scheme == 'vless':
        uuid = userinfo
        if not uuid:
            raise LinkValidationError('missing_userinfo')
        if not re.match(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$', uuid):
            raise LinkValidationError('invalid_userinfo')
        outbound.update({
            'type': 'vless',
            'server': host,
            'server_port': _port_int(port),
            'uuid': uuid
        })
        flow = q.get('flow', '')
        if flow:
            outbound['flow'] = flow
        packet_encoding = q.get('packetEncoding', '')
        if packet_encoding:
            outbound['packet_encoding'] = packet_encoding

    elif scheme == 'trojan':
        if not userinfo:
            raise LinkValidationError('missing_userinfo')
        outbound.update({
            'type': 'trojan',
            'server': host,
            'server_port': _port_int(port),
            'password': userinfo
        })

    elif scheme in ('hy2', 'hysteria2'):
        if not userinfo:
            raise LinkValidationError('missing_userinfo')
        outbound.update({
            'type': 'hysteria2',
            'server': host,
            'password': userinfo
        })
        outbound.update(_validate_hysteria2_ports(port))
        obfs_type = q.get('obfs', '')
        obfs_password = q.get('obfs-password', '')
        if obfs_type and obfs_password:
            outbound['obfs'] = {'type': obfs_type, 'password': obfs_password}
        for src, dst in (('upmbps', 'up_mbps'), ('downmbps', 'down_mbps')):
            val = q.get(src, '')
            if val:
                try:
                    outbound[dst] = int(val)
                except Exception:
                    raise LinkValidationError('invalid_transport_param')

    else:
        raise LinkValidationError('unsupported_scheme')

    tls = _build_tls_object(parts)
    if tls:
        outbound['tls'] = tls
    transport = _build_transport_object(parts)
    if transport:
        outbound['transport'] = transport
    return outbound


def _singbox_config_for_links(links):
    outbounds = []
    for idx, link in enumerate(links, 1):
        outbounds.append(proxy_link_to_singbox_outbound(link, f'podkop-sub-test-{idx}'))
    return {'log': {'disabled': True}, 'outbounds': outbounds}


def run_singbox_check_for_links(links, timeout=SINGBOX_CHECK_TIMEOUT_DEFAULT):
    if not links:
        return True, ''
    tmp_path = f'/tmp/podkop-sub-singbox-check-{os.getpid()}-{int(time.time() * 1000)}.json'
    try:
        cfg = _singbox_config_for_links(links)
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, separators=(',', ':'))
            f.write('\n')
        try:
            result = subprocess.run(
                ['sing-box', '-c', tmp_path, 'check'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout
            )
        except FileNotFoundError:
            return False, 'singbox_not_found'
        except subprocess.TimeoutExpired:
            return False, 'singbox_check_failed'
        if result.returncode == 0:
            return True, ''
        return False, 'singbox_check_failed'
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def hard_validate_links_with_singbox(links, max_runs=SINGBOX_CHECK_MAX_RUNS_DEFAULT):
    if not links:
        return [], {'ok': True, 'runs': 0, 'rejected': 0, 'rejected_by_reason': {}}

    runs = {'n': 0}
    bad_ids = set()
    bad_reason = {}

    def check(batch):
        if not batch:
            return True, ''
        if runs['n'] >= max_runs:
            raise LinkValidationError('singbox_check_limit')
        runs['n'] += 1
        return run_singbox_check_for_links(batch)

    try:
        ok, reason = check(links)
        if ok:
            return list(links), {'ok': True, 'runs': runs['n'], 'rejected': 0, 'rejected_by_reason': {}}
        if reason == 'singbox_not_found':
            return [], {'ok': False, 'runs': runs['n'], 'rejected': len(links), 'rejected_by_reason': {'singbox_not_found': len(links)}}

        def isolate(batch):
            if not batch:
                return
            if len(batch) == 1:
                sid = stable_id(batch[0])
                bad_ids.add(sid)
                bad_reason[sid] = 'singbox_check_failed'
                return
            mid = len(batch) // 2
            left = batch[:mid]
            right = batch[mid:]
            for part in (left, right):
                if not part:
                    continue
                ok_part, reason_part = check(part)
                if not ok_part:
                    if reason_part == 'singbox_not_found':
                        for link in part:
                            sid = stable_id(link)
                            bad_ids.add(sid)
                            bad_reason[sid] = 'singbox_not_found'
                    else:
                        isolate(part)

        isolate(list(links))
        good = [x for x in links if stable_id(x) not in bad_ids]
        if good:
            ok_final, reason_final = check(good)
            if not ok_final:
                return [], {'ok': False, 'runs': runs['n'], 'rejected': len(links), 'rejected_by_reason': {reason_final or 'singbox_check_failed': len(links)}}
        by_reason = {}
        for sid in bad_ids:
            r = bad_reason.get(sid, 'singbox_check_failed')
            by_reason[r] = by_reason.get(r, 0) + 1
        return good, {'ok': True, 'runs': runs['n'], 'rejected': len(bad_ids), 'rejected_by_reason': by_reason}
    except LinkValidationError as e:
        return [], {'ok': False, 'runs': runs['n'], 'rejected': len(links), 'rejected_by_reason': {e.reason: len(links)}}


def validate_links_for_podkop_singbox(sec, links):
    stats = {
        'input': len(links or []),
        'formal_ok': 0,
        'rejected': 0,
        'rejected_by_reason': {},
        'singbox_runs': 0,
        'hard_rejected': 0,
        'hard_check_ok': False,
    }
    formally_ok = []
    normalized_count = 0
    for link in links or []:
        try:
            normalized_link, normalize_reason = normalize_link_for_podkop(link)
            if normalize_reason:
                normalized_count += 1
                stats['rejected_by_reason'][normalize_reason] = stats['rejected_by_reason'].get(normalize_reason, 0) + 1
            # Генерация outbound сама является формальной проверкой всех поддерживаемых параметров.
            proxy_link_to_singbox_outbound(normalized_link, 'podkop-sub-formal-test')
            formally_ok.append(normalized_link)
        except LinkValidationError as e:
            stats['rejected'] += 1
            stats['rejected_by_reason'][e.reason] = stats['rejected_by_reason'].get(e.reason, 0) + 1

    if normalized_count:
        log('INFO', f"[{sec}]: для совместимости с Podkop добавлен type=tcp в ключах: {normalized_count}")

    # Нормализация могла сделать несколько URI одинаковыми, поэтому повторно убираем дубли.
    formally_ok, normalized_dups = dedupe_links_keep_order(formally_ok)
    if normalized_dups:
        log('INFO', f"[{sec}]: после нормализации URI отброшено дублей: {normalized_dups}")

    stats['formal_ok'] = len(formally_ok)
    if stats['rejected']:
        log('WARN', f"[{sec}]: быстрая проверка совместимости отбросила ключей: {stats['rejected']}")
        for reason, count in sorted(stats['rejected_by_reason'].items()):
            if reason == 'normalized_missing_transport':
                continue
            log('WARN', f"[{sec}]: {validation_reason_text(reason)}: {count}")

    if not formally_ok:
        stats['hard_check_ok'] = False
        return [], stats

    valid, hard = hard_validate_links_with_singbox(formally_ok)
    stats['singbox_runs'] = int(hard.get('runs', 0) or 0)
    stats['hard_rejected'] = int(hard.get('rejected', 0) or 0)
    for reason, count in (hard.get('rejected_by_reason') or {}).items():
        stats['rejected_by_reason'][reason] = stats['rejected_by_reason'].get(reason, 0) + int(count or 0)
    stats['rejected'] += stats['hard_rejected']
    stats['hard_check_ok'] = bool(hard.get('ok')) and len(valid) > 0

    if hard.get('ok'):
        log('INFO', f"[{sec}]: проверка совместимости Podkop/sing-box: принято {len(valid)}, отброшено {stats['rejected']}, sing-box check запусков: {stats['singbox_runs']}")
    else:
        log('ERROR', f"[{sec}]: проверка совместимости Podkop/sing-box не смогла собрать безопасный список; секция не будет изменена")
        for reason, count in sorted((hard.get('rejected_by_reason') or {}).items()):
            log('ERROR', f"[{sec}]: {validation_reason_text(reason)}: {count}")

    return valid, stats


def validate_jobs_links_for_podkop(jobs):
    for sec, job in jobs.items():
        links = list(job.get('links') or [])
        if not links:
            job['validation'] = {
                'input': 0,
                'formal_ok': 0,
                'rejected': 0,
                'rejected_by_reason': {},
                'singbox_runs': 0,
                'hard_rejected': 0,
                'hard_check_ok': False,
            }
            continue
        valid, stats = validate_links_for_podkop_singbox(sec, links)
        job['validation'] = stats
        job['links'] = valid
        if stats.get('input') and not valid:
            job['validation_failed'] = True

def fetch_links(jobs, hwid, device_model, kernel_ver):
    payload_cache = {}
    for sec, job in jobs.items():
        log("DEBUG", f"--- Обработка секции: [{sec}] ---")
        section_links = []
        job['source_errors'] = 0
        job['source_success'] = 0
        job['source_stats'] = []
        job['raw_links_count'] = 0
        job['filtered_links_count'] = 0
        for src_idx, entry in enumerate(job['entries'], 1):
            source = entry['source']
            label = 'локальный список' if source in (LOCAL_LINKS_PATH_DEFAULT, 'file://' + LOCAL_LINKS_PATH_DEFAULT) else f'источник {src_idx}'
            st = {'label': label, 'raw': 0, 'filtered': 0, 'format': '', 'status': 'unknown'}
            try:
                payload = read_source_payload(source, hwid, device_model, kernel_ver, payload_cache, label)
            except subprocess.TimeoutExpired:
                st['status'] = 'download_failed'
                job['source_stats'].append(st)
                log("ERROR", f"[{sec}]: Превышено время ожидания ответа сервера: {label}")
                job['source_errors'] += 1
                continue
            except Exception as e:
                st['status'] = 'download_failed'
                job['source_stats'].append(st)
                log("ERROR", f"[{sec}]: Ошибка чтения источника {label} -> {e}")
                job['source_errors'] += 1
                continue
            links_raw, payload_type = extract_links_from_payload(payload)
            st['format'] = payload_type
            st['raw'] = len(links_raw)
            job['raw_links_count'] += len(links_raw)
            if payload_type == 'invalid':
                st['status'] = 'unsupported_format'
                job['source_stats'].append(st)
                log("ERROR", f"[{sec}]: {label} не plain-text URI и не валидный Base64")
                job['source_errors'] += 1
                continue
            if not links_raw:
                st['status'] = 'empty_subscription'
                job['source_stats'].append(st)
                log("WARN", f"[{sec}]: в {label} нет ссылок поддерживаемого типа")
                job['source_success'] += 1
                continue
            filtered_links = filter_links(links_raw, entry['regex'], entry['match_mode'], entry['on_empty'], sec, label)
            st['filtered'] = len(filtered_links)
            job['filtered_links_count'] += len(filtered_links)
            if not filtered_links:
                st['status'] = 'empty_after_filter'
                job['source_stats'].append(st)
                job['source_success'] += 1
                continue
            section_links.extend(filtered_links)
            job['source_success'] += 1
            st['status'] = 'ok'
            job['source_stats'].append(st)
            log("INFO", f"[{sec}]: {label} ({payload_type}) -> ссылок после фильтра: {len(filtered_links)}")
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
    return {'version': 3, 'sections': {}, 'health': {}, 'recently_removed': {}, 'meta': {}}


def load_state(path):
    if not os.path.exists(path):
        return default_state()
    try:
        with open(path, 'r', encoding='utf-8') as f:
            st = json.load(f)
        if not isinstance(st, dict):
            return default_state()
        st.setdefault('version', 3)
        st.setdefault('sections', {})
        st.setdefault('health', {})
        st.setdefault('recently_removed', {})
        st.setdefault('meta', {})
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



def fmt_time(ts):
    try:
        ts = int(ts or 0)
    except Exception:
        ts = 0
    if ts <= 0:
        return 'нет данных'
    try:
        return time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))
    except Exception:
        return str(ts)


def status_text_for_code(code):
    mapping = {
        'ok': 'OK',
        'partial_ok': 'частично OK',
        'download_failed': 'ошибка загрузки подписок',
        'empty_response': 'источник вернул пустой ответ',
        'unsupported_format': 'неподдерживаемый формат подписки',
        'empty_subscription': 'в подписке не найдено proxy-ссылок',
        'empty_after_filter': 'все ключи отброшены фильтром',
        'no_subscription_links': 'валидных ключей из подписок не найдено',
        'no_jobs': 'нет настроенных подписок',
        'config_write_error': 'ошибка записи конфига',
        'validation_failed': 'ключи не прошли проверку совместимости с Podkop/sing-box',
        'time_not_ready': 'время роутера не синхронизировано',
        'unknown': 'нет данных'
    }
    return mapping.get(str(code or 'unknown'), str(code or 'нет данных'))


def state_status_summary(state):
    meta = state.get('meta') if isinstance(state, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    health = state.get('health') if isinstance(state, dict) else {}
    if not isinstance(health, dict):
        health = {}

    status = meta.get('last_subscription_status') or 'unknown'
    success_ts = meta.get('last_subscription_success_ts')
    attempt_ts = meta.get('last_subscription_attempt_ts')

    if not success_ts and not attempt_ts:
        return 'Состояние: первичная настройка — нажмите кнопку «Запустить updater» внизу страницы для первого подтягивания конфигов.'

    ok = health.get('ok', meta.get('last_working_links', 0))
    bad = health.get('bad', meta.get('last_bad_links', 0))
    missing = health.get('missing', 0)
    removed = meta.get('last_removed', 0)
    local = meta.get('last_local_links', 0)
    final_links = meta.get('last_final_links', 0)
    unique_links = meta.get('last_unique_links', 0)
    src_ok = meta.get('last_sources_ok', 0)
    src_total = meta.get('last_sources_total', None)

    if src_total is None:
        try:
            src_total = int(meta.get('last_sources_ok', 0) or 0) + int(meta.get('last_sources_failed', 0) or 0)
        except Exception:
            src_total = 0

    try:
        ok = int(ok or 0)
    except Exception:
        ok = 0
    try:
        bad = int(bad or 0) + int(missing or 0)
    except Exception:
        bad = 0
    try:
        removed = int(removed or 0)
    except Exception:
        removed = 0
    try:
        local = int(local or 0)
    except Exception:
        local = 0
    try:
        final_links = int(final_links or 0)
    except Exception:
        final_links = 0
    try:
        unique_links = int(unique_links or 0)
    except Exception:
        unique_links = 0
    try:
        src_ok = int(src_ok or 0)
    except Exception:
        src_ok = 0
    try:
        src_total = int(src_total or 0)
    except Exception:
        src_total = 0

    # Если подписки успешно обработаны, это OK даже до первой observe-проверки.
    # В этом случае рабочих ключей может быть 0 просто потому, что URLTest ещё не накопил статистику.
    if status in ('ok', 'partial_ok'):
        if ok > 0:
            keys_part = 'рабочих ключей: %d, проблемных: %d' % (ok, bad)
        elif final_links > 0:
            keys_part = 'ключей в секции: %d, рабочих: нет данных' % final_links
        elif unique_links > 0:
            keys_part = 'загружено ключей: %d, рабочих: нет данных' % unique_links
        else:
            keys_part = 'рабочих ключей: нет данных'

        return (
            'Состояние: OK — обновлено: %s, источники: %d/%d, %s, удалено: %d, локальных: %d.'
            % (fmt_time(success_ts), src_ok, src_total, keys_part, removed, local)
        )

    lines = []
    lines.append('Состояние: авария — ' + status_text_for_code(status) + '.')
    lines.append('Последнее успешное обновление: ' + fmt_time(success_ts) + '.')
    if attempt_ts:
        lines.append('Последняя попытка: ' + fmt_time(attempt_ts) + '.')
    lines.append('Источники: %d/%d.' % (src_ok, src_total))
    lines.append('Рабочих ключей: %d, проблемных: %d, ключей в секции: %d, удалено: %d, локальных: %d.' % (ok, bad, final_links, removed, local))

    detail = meta.get('last_subscription_message')
    if detail:
        lines.append(str(detail))

    source_details = meta.get('last_source_details') or []
    problem_rows = []
    for st in source_details:
        if not isinstance(st, dict):
            continue
        code = st.get('status')
        if code == 'ok':
            continue

        label = st.get('label') or 'источник'
        try:
            raw = int(st.get('raw', 0) or 0)
        except Exception:
            raw = 0

        if code == 'download_failed':
            problem_rows.append(f'{label}: ошибка загрузки.')
        elif code == 'unsupported_format':
            problem_rows.append(f'{label}: неподдерживаемый формат подписки.')
        elif code == 'empty_after_filter':
            problem_rows.append(f'{label}: после фильтра осталось 0 ключей из {raw}.')
        elif code == 'empty_subscription':
            problem_rows.append(f'{label}: поддерживаемых proxy-ссылок не найдено.')
        elif code:
            problem_rows.append(f'{label}: {status_text_for_code(code)}.')
        else:
            problem_rows.append(f'{label}: неизвестная ошибка.')

    if problem_rows:
        lines.append('Проблемы источников:')
        lines.extend(problem_rows[:12])

    validation_rejected = int(meta.get('last_validation_rejected', 0) or 0)
    if validation_rejected:
        lines.append('Проверка совместимости отклонила ключей: %d.' % validation_rejected)
        by_reason = meta.get('last_validation_rejected_by_reason') or {}
        for reason, count in sorted(by_reason.items()):
            lines.append('%s: %s.' % (validation_reason_text(reason), count))

    if meta.get('catchup_retry_active'):
        lines.append('Повтор обновления активен: следующая попытка будет выполнена по cron.')

    return '\n'.join(lines)



def print_fail_count_summary(state_path):
    state = load_state(state_path)
    sections = state.get('sections', {}) if isinstance(state, dict) else {}
    if not isinstance(sections, dict) or not sections:
        print('fail_count: данных пока нет')
        return

    print('fail_count: текущее состояние')
    print('state: ' + state_path)
    printed_any = False

    for sec in sorted(sections.keys()):
        sec_data = sections.get(sec) or {}
        links = sec_data.get('links', {})
        if not isinstance(links, dict):
            links = {}

        total = len(links)
        fc0 = 0
        fc_gt0 = 0
        fc_ge2 = 0
        fc_ge_threshold = 0
        local = 0
        rows = []

        for sid, item in links.items():
            if not isinstance(item, dict):
                item = {}
            try:
                fc = int(item.get('fail_count', 0) or 0)
            except Exception:
                fc = 0
            if fc == 0:
                fc0 += 1
            if fc > 0:
                fc_gt0 += 1
            if fc >= 2:
                fc_ge2 += 1
            if fc >= DELETE_AFTER_FAIL_COUNT_DEFAULT:
                fc_ge_threshold += 1
            if item.get('protected_local'):
                local += 1
            if fc > 0:
                name = item.get('name') or '(без названия)'
                rows.append((fc, str(sid), str(name), bool(item.get('protected_local'))))

        print('')
        print('Секция: ' + str(sec))
        print('  всего ключей в state: %d' % total)
        print('  fail_count=0: %d' % fc0)
        print('  fail_count>0: %d' % fc_gt0)
        print('  fail_count>=2: %d' % fc_ge2)
        print('  fail_count>=%d: %d' % (DELETE_AFTER_FAIL_COUNT_DEFAULT, fc_ge_threshold))
        print('  локальных: %d' % local)

        if rows:
            printed_any = True
            print('  Топ проблемных:')
            rows.sort(key=lambda x: (-x[0], x[2]))
            for fc, sid, name, is_local in rows[:30]:
                mark = 'LOCAL' if is_local else 'SUB'
                print('    fail_count=%d [%s] key=%s name="%s"' % (fc, mark, sid[:12], name[:120]))

    if not printed_any:
        print('')
        print('Проблемных ключей с fail_count>0 нет.')

def print_status_summary(state_path):
    state = load_state(state_path)
    print(state_status_summary(state))


def is_router_time_valid(state):
    now = int(time.time())
    if now >= VALID_TIME_MIN_TS:
        return True
    meta = state.setdefault('meta', {})
    meta['last_subscription_status'] = 'time_not_ready'
    meta['last_subscription_message'] = 'Время роутера ещё не синхронизировано, catch-up отложен.'
    return False


def subscription_run_success(state):
    meta = state.get('meta', {})
    return meta.get('last_subscription_status') in ('ok', 'partial_ok')


def set_catchup_retry(state, active, reason=''):
    meta = state.setdefault('meta', {})
    meta['catchup_retry_active'] = bool(active)
    if active:
        meta['catchup_retry_fail_count'] = int(meta.get('catchup_retry_fail_count', 0) or 0) + 1
        meta['catchup_retry_last_error'] = reason
        meta['catchup_retry_last_attempt_ts'] = int(time.time())
    else:
        meta['catchup_retry_fail_count'] = 0
        meta['catchup_retry_last_error'] = ''


def run_subprocess_update(args):
    cmd = [
        sys.executable or 'python3', os.path.abspath(__file__),
        '--subs', args.subs, '--config', args.config, '--state', args.state,
        '--delete-after-fails', str(args.delete_after_fails), '--min-keep', str(args.min_keep), '--force'
    ]
    return subprocess.run(cmd).returncode


def catch_up(args, retry_only=False):
    state = load_state(args.state)
    meta = state.setdefault('meta', {})
    if not is_router_time_valid(state):
        save_state(args.state, state)
        log('WARN', 'catch-up: время роутера ещё не синхронизировано, обновление отложено')
        return
    if retry_only and not meta.get('catchup_retry_active'):
        log('INFO', 'catch-up retry: активного аварийного повтора нет, выход')
        return
    now = int(time.time())
    last_success = int(meta.get('last_subscription_success_ts', 0) or 0)
    stale_after = CATCHUP_AFTER_HOURS_DEFAULT * 3600
    if not retry_only and last_success and now - last_success < stale_after:
        age_h = int((now - last_success) / 3600)
        log('INFO', f'catch-up: последнее успешное обновление было {age_h} ч. назад, обновление не требуется')
        return
    if retry_only:
        log('INFO', 'catch-up retry: предыдущий catch-up завершился ошибкой, пробую обновить ещё раз')
    else:
        log('INFO', 'catch-up: последнее успешное обновление устарело или отсутствует, запускаю обновление подписок')
    rc = run_subprocess_update(args)
    state = load_state(args.state)
    if rc == 0 and subscription_run_success(state):
        set_catchup_retry(state, False)
        save_state(args.state, state)
        log('INFO', 'catch-up: обновление прошло успешно, аварийный retry выключен')
    else:
        reason = state.get('meta', {}).get('last_subscription_status', 'update_failed')
        set_catchup_retry(state, True, reason)
        save_state(args.state, state)
        log('WARN', f'catch-up: обновление не удалось или не дало валидных ключей, retry включён: {reason}')


def classify_empty_status(jobs):
    total_errors = total_success = total_raw = total_filtered = 0
    saw_invalid = saw_empty = saw_after_filter = saw_download_error = False
    saw_validation_failed = False
    for job in jobs.values():
        if job.get('validation_failed') or int((job.get('validation') or {}).get('rejected', 0) or 0) > 0:
            saw_validation_failed = True
        total_errors += int(job.get('source_errors', 0) or 0)
        total_success += int(job.get('source_success', 0) or 0)
        total_raw += int(job.get('raw_links_count', 0) or 0)
        total_filtered += int(job.get('filtered_links_count', 0) or 0)
        for st in job.get('source_stats', []) or []:
            code = st.get('status')
            if code == 'unsupported_format': saw_invalid = True
            elif code in ('empty_subscription', 'empty_response'): saw_empty = True
            elif code == 'empty_after_filter': saw_after_filter = True
            elif code == 'download_failed': saw_download_error = True
    if saw_validation_failed:
        return 'validation_failed'
    if total_raw > 0 and total_filtered == 0:
        return 'empty_after_filter'
    if saw_invalid:
        return 'unsupported_format'
    if saw_after_filter:
        return 'empty_after_filter'
    if saw_empty:
        return 'empty_subscription'
    if saw_download_error or total_errors > 0:
        return 'download_failed'
    return 'no_subscription_links'


def update_subscription_meta(state, jobs, processed_sections, total_added, total_removed, total_final, protected_local_count, status=None, message=None):
    now = int(time.time())
    meta = state.setdefault('meta', {})
    meta['last_subscription_attempt_ts'] = now
    meta['last_subscription_attempt_iso'] = fmt_time(now)
    source_errors = sum(int(j.get('source_errors', 0) or 0) for j in jobs.values())
    source_success = sum(int(j.get('source_success', 0) or 0) for j in jobs.values())
    raw_links = sum(int(j.get('raw_links_count', 0) or 0) for j in jobs.values())
    filtered_links = sum(int(j.get('filtered_links_count', 0) or 0) for j in jobs.values())
    unique_links = sum(len(j.get('links', []) or []) for j in jobs.values())
    if status is None:
        if unique_links > 0:
            status = 'partial_ok' if source_errors else 'ok'
        else:
            status = classify_empty_status(jobs)
    if message is None:
        if status in ('ok', 'partial_ok'):
            message = ''
        elif status == 'empty_after_filter':
            message = 'Подписки загружены, но после фильтрации не осталось валидных ключей. Проверьте Regex-фильтр и режим фильтрации.'
        elif status == 'unsupported_format':
            message = 'Источник вернул данные, но формат подписки не распознан: нет plain-text URI и нет валидного Base64 со ссылками.'
        elif status == 'download_failed':
            message = 'Источники подписок не загрузились. Возможны проблемы с сетью, DNS, сервером подписки или timeout.'
        elif status == 'empty_subscription':
            message = 'Подписки загружены, но в них не найдено поддерживаемых proxy-ссылок.'
        elif status == 'validation_failed':
            message = 'Ключи из подписок загружены, но не прошли проверку совместимости с Podkop/sing-box. Текущая секция Podkop не изменялась.'
        else:
            message = 'Валидных ключей из подписок не найдено.'
    validation_rejected = 0
    validation_checked = 0
    validation_singbox_runs = 0
    validation_by_reason = {}
    for job in jobs.values():
        vst = job.get('validation') or {}
        validation_checked += int(vst.get('input', 0) or 0)
        validation_rejected += int(vst.get('rejected', 0) or 0)
        validation_singbox_runs += int(vst.get('singbox_runs', 0) or 0)
        for reason, count in (vst.get('rejected_by_reason') or {}).items():
            validation_by_reason[reason] = validation_by_reason.get(reason, 0) + int(count or 0)

    meta['last_validation_checked'] = validation_checked
    meta['last_validation_rejected'] = validation_rejected
    meta['last_validation_singbox_runs'] = validation_singbox_runs
    meta['last_validation_rejected_by_reason'] = validation_by_reason
    meta['last_subscription_status'] = status
    meta['last_subscription_message'] = message
    source_total = source_success + source_errors
    source_details = []
    for job in jobs.values():
        for st in job.get('source_stats', []) or []:
            source_details.append({
                'label': st.get('label', ''),
                'status': st.get('status', ''),
                'raw': int(st.get('raw', 0) or 0),
                'filtered': int(st.get('filtered', 0) or 0),
                'format': st.get('format', '')
            })

    meta['last_sources_ok'] = source_success
    meta['last_sources_failed'] = source_errors
    meta['last_sources_total'] = source_total
    meta['last_source_details'] = source_details[:30]
    meta['last_raw_links'] = raw_links
    meta['last_after_filter_links'] = filtered_links
    meta['last_unique_links'] = unique_links
    meta['last_added'] = int(total_added or 0)
    meta['last_removed'] = int(total_removed or 0)
    meta['last_final_links'] = int(total_final or 0)
    meta['last_local_links'] = int(protected_local_count or 0)
    meta['last_processed_sections'] = int(processed_sections or 0)
    health = state.get('health', {})
    if isinstance(health, dict):
        meta['last_working_links'] = int(health.get('ok', 0) or 0)
        meta['last_bad_links'] = int(health.get('bad', 0) or 0) + int(health.get('missing', 0) or 0)
    if status in ('ok', 'partial_ok'):
        meta['last_subscription_success_ts'] = now
        meta['last_subscription_success_iso'] = fmt_time(now)
        meta['catchup_retry_active'] = False
        meta['catchup_retry_fail_count'] = 0

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



def add_recently_removed(state, sid, sec, item, reason):
    rr = state.setdefault('recently_removed', {})
    rr[sid] = {
        'removed_at': int(time.time()),
        'section': sec,
        'reason': reason,
        'name': (item or {}).get('name', '')
    }


def proxy_snapshot_for_links(sec, links, proxies):
    snap = {}
    if not proxies:
        return snap
    for idx, link in enumerate(links, 1):
        sid = stable_id(link)
        tag = f"{sec}-{idx}-out"
        status, delay = proxy_status(proxies.get(tag))
        snap[sid] = {'tag': tag, 'status': status, 'delay': delay}
    return snap


def removal_candidates(sec, links, st_sec, protected_ids, proxy_snap, delete_after_fails, max_latency_ms, limit_active, force_cleanup):
    candidates = []
    for idx, link in enumerate(links, 1):
        sid = stable_id(link)
        item = st_sec['links'].get(sid, {})
        if sid in protected_ids or item.get('protected_local'):
            continue
        try:
            fc = int(item.get('fail_count', 0) or 0)
        except Exception:
            fc = 0
        ps = proxy_snap.get(sid, {}) if proxy_snap else {}
        status = ps.get('status') or item.get('last_status') or 'unknown'
        delay = ps.get('delay')
        if not isinstance(delay, int):
            ld = item.get('last_delay')
            delay = ld if isinstance(ld, int) else None

        reason = None
        priority = None
        # Обычная мягкая автоочистка: 72 подряд плохих наблюдения.
        if fc >= delete_after_fails:
            reason = 'fail_count_threshold'
            priority = 0
        # Экстремальная чистка: два плохих наблюдения подряд уже достаточно.
        elif force_cleanup and fc >= 2:
            reason = 'force_fail_count'
            priority = 1
        # При переполнении секции ускоренно убираем всё, что уже попадало в плохие наблюдения.
        elif limit_active and fc > 0:
            reason = 'limit_fail_count'
            priority = 2
        # Ping-фильтр.
        elif max_latency_ms and delay is not None and delay > max_latency_ms:
            reason = 'latency'
            priority = 3
        # При переполнении секции N/A тоже кандидат на освобождение места.
        elif limit_active and status in ('bad', 'missing'):
            reason = 'limit_na'
            priority = 4

        if reason:
            # Сортировка: сначала самые плохие по priority, затем больший fail_count, затем больший delay.
            candidates.append({
                'sid': sid,
                'idx': idx,
                'link': link,
                'item': item,
                'reason': reason,
                'priority': priority,
                'fail_count': fc,
                'delay': delay if isinstance(delay, int) else -1,
                'status': status
            })
    candidates.sort(key=lambda x: (x['priority'], -x['fail_count'], -x['delay'], x['idx']))
    return candidates


def slowest_unprotected_candidates(sec, links, st_sec, protected_ids, proxy_snap, already_remove):
    result = []
    for idx, link in enumerate(links, 1):
        sid = stable_id(link)
        if sid in already_remove or sid in protected_ids:
            continue
        item = st_sec['links'].get(sid, {})
        if item.get('protected_local'):
            continue
        ps = proxy_snap.get(sid, {}) if proxy_snap else {}
        delay = ps.get('delay')
        if not isinstance(delay, int):
            ld = item.get('last_delay')
            delay = ld if isinstance(ld, int) else 0
        try:
            fc = int(item.get('fail_count', 0) or 0)
        except Exception:
            fc = 0
        result.append({
            'sid': sid,
            'idx': idx,
            'link': link,
            'item': item,
            'reason': 'limit_slowest',
            'priority': 9,
            'fail_count': fc,
            'delay': delay,
            'status': ps.get('status') or item.get('last_status') or 'unknown'
        })
    # Самые медленные и/или с большим fail_count уходят первыми.
    result.sort(key=lambda x: (-x['fail_count'], -x['delay'], x['idx']))
    return result


def build_final_links_for_section(sec, job, current_sections, state, delete_after_fails, min_keep, protected_ids=None, proxies=None, recent_skip_ids=None):
    now = int(time.time())
    protected_ids = protected_ids or set()
    recent_skip_ids = recent_skip_ids or set()
    current = current_sections.get(sec, {'ptype': job.get('ptype', 'urltest'), 'links': []})
    current_links = list(current.get('links', []))
    current_unique, current_dups = dedupe_links_keep_order(current_links)
    st_sec = ensure_state_section(state, sec, job.get('ptype') or current.get('ptype'))
    st_sec['proxy_type'] = job.get('ptype') or current.get('ptype', st_sec.get('proxy_type', 'urltest'))

    max_links = parse_positive_int(job.get('max_links', 0), 0)
    max_latency_ms = parse_positive_int(job.get('max_latency_ms', 0), 0)
    force_cleanup = bool(job.get('force_cleanup'))
    dedupe_sni_rotation = bool(job.get('dedupe_sni_rotation'))
    dedupe_endpoint_host = bool(job.get('dedupe_endpoint_host'))
    state.setdefault('recently_removed', {})

    # Синхронизируем state с текущим конфигом.
    for link in current_unique:
        sid = stable_id(link)
        item = st_sec['links'].setdefault(sid, {'url': link, 'name': link_name(link), 'fail_count': 0, 'first_seen': now})
        item['url'] = link
        item['name'] = link_name(link)
        item['last_seen_in_config'] = now
        if sid in protected_ids:
            item['protected_local'] = True

    # Если источники не дали ни одной валидной ссылки — ничего не чистим и не меняем.
    if job.get('source_success', 0) == 0 or not job.get('links'):
        log("WARN", f"[{sec}]: источники подписок не дали валидных ссылок; секция не изменяется")
        return None, {'skip': True, 'reason': 'no_subscription_links'}
    if job.get('source_errors', 0) > 0:
        log("WARN", f"[{sec}]: часть источников подписки недоступна, но валидные ссылки получены; обслуживание продолжается")

    subscription_links = list(job.get('links', []))
    incoming_sni_collapsed = 0
    incoming_endpoint_collapsed = 0
    skipped_sni_local = 0
    skipped_endpoint_local = 0

    if dedupe_sni_rotation:
        subscription_links, incoming_sni_collapsed = dedupe_sni_rotation_links_keep_last(subscription_links)
        if incoming_sni_collapsed:
            log("INFO", f"[{sec}]: SNI-ротации внутри подписки схлопнуты: {incoming_sni_collapsed}")

    if dedupe_endpoint_host:
        subscription_links, incoming_endpoint_collapsed = dedupe_endpoint_host_links_keep_last(subscription_links)
        if incoming_endpoint_collapsed:
            log("INFO", f"[{sec}]: IP/домен-дубликаты внутри подписки схлопнуты: {incoming_endpoint_collapsed}")

    mark_subscription_seen(state, sec, subscription_links)

    current_ids = {stable_id(x) for x in current_unique}
    protected_rotation_ids = set()
    if dedupe_sni_rotation:
        for link in current_unique:
            sid = stable_id(link)
            item = st_sec['links'].get(sid, {})
            if has_sni_param(link) and (sid in protected_ids or item.get('protected_local')):
                protected_rotation_ids.add(sni_rotation_id(link))

    protected_endpoint_ids = set()
    if dedupe_endpoint_host:
        for link in current_unique:
            sid = stable_id(link)
            item = st_sec['links'].get(sid, {})
            eid = endpoint_host_id(link)
            if eid and (sid in protected_ids or item.get('protected_local')):
                protected_endpoint_ids.add(eid)

    incoming_rotation = {}
    if dedupe_sni_rotation:
        for link in subscription_links:
            if has_sni_param(link):
                incoming_rotation[sni_rotation_id(link)] = link

    incoming_endpoint = {}
    if dedupe_endpoint_host:
        for link in subscription_links:
            eid = endpoint_host_id(link)
            if eid:
                incoming_endpoint[eid] = link

    initial_new_candidates = []
    skipped_recent = 0
    for link in subscription_links:
        sid = stable_id(link)
        item = st_sec['links'].get(sid)
        if item:
            item['last_seen_in_subscription'] = now
        if sid in current_ids:
            continue
        if sid in recent_skip_ids:
            skipped_recent += 1
            continue
        if dedupe_sni_rotation and has_sni_param(link) and sni_rotation_id(link) in protected_rotation_ids:
            skipped_sni_local += 1
            continue
        if dedupe_endpoint_host and endpoint_host_id(link) in protected_endpoint_ids:
            skipped_endpoint_local += 1
            continue
        initial_new_candidates.append(link)

    potential_count = len(current_unique) + len(initial_new_candidates)
    limit_active = bool(max_links and potential_count > max_links)
    if max_links:
        log("INFO", f"[{sec}]: limit max_links={max_links}, current={len(current_unique)}, new_candidates={len(initial_new_candidates)}, potential={potential_count}")
    if max_latency_ms:
        log("INFO", f"[{sec}]: max_latency_ms={max_latency_ms}")
    if force_cleanup:
        log("WARN", f"[{sec}]: включена принудительная чистка: fail_count>=2 и ping выше лимита могут быть удалены")
    if dedupe_sni_rotation:
        log("INFO", f"[{sec}]: включено схлопывание SNI-ротаций")
    if dedupe_endpoint_host:
        log("INFO", f"[{sec}]: включено схлопывание IP/домен-дубликатов")
    if skipped_recent:
        log("INFO", f"[{sec}]: ключей из одноразового списка недавно удалённых пропущено при добавлении: {skipped_recent}")
    if skipped_sni_local:
        log("INFO", f"[{sec}]: SNI-дубликатов защищённых локальных ключей пропущено: {skipped_sni_local}")
    if skipped_endpoint_local:
        log("INFO", f"[{sec}]: IP/домен-дубликатов защищённых локальных ключей пропущено: {skipped_endpoint_local}")

    proxy_snap = proxy_snapshot_for_links(sec, current_unique, proxies)
    remove = []
    remove_ids = set()

    if dedupe_sni_rotation and incoming_rotation:
        for idx, link in enumerate(current_unique, 1):
            if not has_sni_param(link):
                continue
            sid = stable_id(link)
            item = st_sec['links'].get(sid, {})
            if sid in protected_ids or item.get('protected_local'):
                continue
            rid = sni_rotation_id(link)
            new_link = incoming_rotation.get(rid)
            if not new_link:
                continue
            new_sid = stable_id(new_link)
            if new_sid == sid:
                continue
            remove.append({
                'sid': sid,
                'idx': idx,
                'link': link,
                'item': item,
                'reason': 'sni_rotation',
                'priority': -1,
                'fail_count': 0,
                'delay': -1,
                'status': 'sni_rotation'
            })
            remove_ids.add(sid)

    if dedupe_endpoint_host and incoming_endpoint:
        for idx, link in enumerate(current_unique, 1):
            sid = stable_id(link)
            item = st_sec['links'].get(sid, {})
            if sid in protected_ids or item.get('protected_local'):
                continue
            eid = endpoint_host_id(link)
            if not eid:
                continue
            new_link = incoming_endpoint.get(eid)
            if not new_link:
                continue
            new_sid = stable_id(new_link)
            if new_sid == sid:
                continue
            remove.append({
                'sid': sid,
                'idx': idx,
                'link': link,
                'item': item,
                'reason': 'endpoint_host_rotation',
                'priority': -1,
                'fail_count': 0,
                'delay': -1,
                'status': 'endpoint_host_rotation'
            })
            remove_ids.add(sid)

    # Базовые кандидаты: fail_count, ping, N/A при переполнении, force_cleanup.
    candidates = removal_candidates(sec, current_unique, st_sec, protected_ids, proxy_snap, delete_after_fails, max_latency_ms, limit_active, force_cleanup)

    # Без лимита и без force_cleanup оставляем только мягкое удаление fail_count>=72.
    if not limit_active and not force_cleanup:
        candidates = [c for c in candidates if c['reason'] == 'fail_count_threshold']

    # При force_cleanup удаляем все его кандидаты. При limit_active тоже удаляем плохих/медленных кандидатов.
    for c in candidates:
        if c['sid'] not in remove_ids:
            remove.append(c)
            remove_ids.add(c['sid'])

    # Если текущая секция сама выше max_links — добиваем самыми медленными до лимита.
    if max_links and len(current_unique) - len(remove_ids) > max_links:
        need_extra = len(current_unique) - len(remove_ids) - max_links
        for c in slowest_unprotected_candidates(sec, current_unique, st_sec, protected_ids, proxy_snap, remove_ids):
            if need_extra <= 0:
                break
            remove.append(c)
            remove_ids.add(c['sid'])
            need_extra -= 1

    # Не оставляем секцию ниже min_keep, если можно избежать.
    max_remove = max(0, len(current_unique) - max(0, min_keep))
    if len(remove) > max_remove:
        log("WARN", f"[{sec}]: кандидатов на удаление {len(remove)}, но min_keep={min_keep}; часть ключей оставлена")
        remove = remove[:max_remove]
        remove_ids = {c['sid'] for c in remove}

    remaining_links = [x for x in current_unique if stable_id(x) not in remove_ids]
    removed_by_reason = {}
    for c in remove:
        sid = c['sid']
        item = st_sec['links'].pop(sid, c.get('item') or {})
        add_recently_removed(state, sid, sec, item, c['reason'])
        removed_by_reason[c['reason']] = removed_by_reason.get(c['reason'], 0) + 1

    # Добавляем новые только в свободные места, если задан max_links. Иначе добавляем все новые.
    remaining_ids = {stable_id(x) for x in remaining_links}
    removed_this_run = set(remove_ids)
    final_links = list(remaining_links)
    added = 0
    skipped_by_limit = 0
    skipped_removed_this_run = 0

    add_candidates = []
    for link in initial_new_candidates:
        sid = stable_id(link)
        if sid in removed_this_run:
            skipped_removed_this_run += 1
            continue
        if sid in remaining_ids:
            continue
        add_candidates.append(link)

    if max_links:
        free_slots = max(0, max_links - len(final_links))
        allowed = add_candidates[:free_slots]
        skipped_by_limit = max(0, len(add_candidates) - len(allowed))
    else:
        allowed = add_candidates

    for link in allowed:
        sid = stable_id(link)
        if sid in remaining_ids:
            continue
        final_links.append(link)
        remaining_ids.add(sid)
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

    protected_count = sum(1 for x in final_links if stable_id(x) in protected_ids)
    if max_links and protected_count > max_links:
        log("WARN", f"[{sec}]: защищённых локальных ключей {protected_count}, это больше max_links={max_links}; локальные ключи не удаляются")

    changed = added > 0 or len(remove_ids) > 0 or current_dups > 0 or final_links != current_links
    info = {
        'skip': False,
        'added': added,
        'removed': len(remove_ids),
        'duplicates': current_dups,
        'changed': changed,
        'max_links': max_links,
        'max_latency_ms': max_latency_ms,
        'force_cleanup': force_cleanup,
        'dedupe_sni_rotation': dedupe_sni_rotation,
        'dedupe_endpoint_host': dedupe_endpoint_host,
        'incoming_sni_collapsed': incoming_sni_collapsed,
        'incoming_endpoint_collapsed': incoming_endpoint_collapsed,
        'skipped_sni_local': skipped_sni_local,
        'skipped_endpoint_local': skipped_endpoint_local,
        'skipped_by_limit': skipped_by_limit,
        'skipped_recent': skipped_recent,
        'skipped_removed_this_run': skipped_removed_this_run,
        'removed_by_reason': removed_by_reason
    }
    return final_links, info

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


def validate_file_argument(path, arg_name):
    if os.path.isdir(path):
        log("ERROR", f"{arg_name} должен указывать на файл, а не на директорию: {path}")
        sys.exit(2)
    if not os.path.exists(path):
        log("ERROR", f"{arg_name} не найден: {path}")
        sys.exit(2)


def main():
    setup_syslog()
    args = parse_args()
    if args.version:
        print(f"podkop-subscriptions {APP_VERSION}")
        return
    if args.status_summary:
        print_status_summary(args.state)
        return
    if args.fail_count:
        print_fail_count_summary(args.state)
        return
    if args.catch_up:
        catch_up(args, retry_only=False)
        return
    if args.catch_up_retry:
        catch_up(args, retry_only=True)
        return
    if args.observe_only:
        validate_file_argument(args.config, '--config')
        observe_only(args.config, args.state)
        return
    validate_file_argument(args.config, '--config')
    validate_file_argument(args.subs, '--subs')
    log("INFO", "=== ЗАПУСК ОБНОВЛЕНИЯ ПОДПИСОК ===")
    mac = get_mac_address()
    device_model = get_device_model()
    kernel_ver = get_kernel_version()
    raw_hwid_str = f"{mac}{device_model}"
    hwid = hashlib.md5(raw_hwid_str.encode('utf-8')).hexdigest()[:16]
    log("INFO", f"Устройство: {device_model} (Ядро: {kernel_ver})")
    log("INFO", "X-HWID сгенерирован для запроса подписок")
    jobs = load_jobs(args.subs)
    if not jobs:
        state = load_state(args.state)
        update_subscription_meta(state, {}, 0, 0, 0, 0, 0, status='no_jobs', message='Нет включённых групп подписок.')
        save_state(args.state, state)
        log("INFO", "Нет настроенных подписок. Выход без изменений.")
        return
    current_sections = load_current_podkop_sections(args.config)
    state = load_state(args.state)

    saved_recently_removed = state.get('recently_removed', {})
    if not isinstance(saved_recently_removed, dict):
        saved_recently_removed = {}
    recent_skip_ids = set(saved_recently_removed.keys())

    # recently_removed работает как одноразовый список:
    # ключи, удалённые в прошлом запуске, пропускаются один раз при добавлении,
    # затем старый список очищается. Новые удаления текущего запуска попадут сюда
    # и будут использованы только на следующем запуске.
    state['recently_removed'] = {}
    if recent_skip_ids:
        log("INFO", f"Одноразовый список недавно удалённых ключей загружен: {len(recent_skip_ids)}")

    imported, updated = import_current_links_to_state(state, current_sections)
    if imported or updated:
        log("INFO", f"State синхронизирован с текущим Podkop config: новых={imported}, обновлено={updated}")
    fetch_links(jobs, hwid, device_model, kernel_ver)
    validate_jobs_links_for_podkop(jobs)
    protected_local_ids = load_local_protected_ids()

    need_proxies = any(
        parse_positive_int(job.get('max_links', 0), 0)
        or parse_positive_int(job.get('max_latency_ms', 0), 0)
        or bool(job.get('force_cleanup'))
        for job in jobs.values()
    )
    proxies = None
    if need_proxies:
        try:
            proxies = load_podkop_proxies()
            log("INFO", "Текущее состояние Podkop URLTest получено для отсеивателя.")
        except Exception as e:
            log("WARN", f"Не удалось получить текущее состояние Podkop URLTest для отсеивателя: {e}. Будет использован только state.json.")

    updates = {}
    summary_changed = False
    processed_sections = 0
    total_added = 0
    total_removed = 0
    total_final_links = 0
    for sec, job in jobs.items():
        final_links, info = build_final_links_for_section(sec, job, current_sections, state, args.delete_after_fails, args.min_keep, protected_local_ids, proxies, recent_skip_ids)
        if info.get('skip'):
            log("INFO", f"[{sec}]: пропуск изменения секции ({info.get('reason')})")
            continue
        processed_sections += 1
        log("INFO", build_section_result_log_ru(sec, info, len(final_links)))
        total_added += int(info.get('added', 0) or 0)
        total_removed += int(info.get('removed', 0) or 0)
        total_final_links += len(final_links)
        if info.get('changed'):
            updates[sec] = {'ptype': job.get('ptype', 'urltest'), 'links': final_links}
            summary_changed = True
    if processed_sections == 0 and recent_skip_ids:
        # Подписки не дали валидных ключей / все секции пропущены.
        # Одноразовый список не считаем использованным и возвращаем как был.
        state['recently_removed'] = saved_recently_removed
    elif recent_skip_ids:
        log("INFO", f"Одноразовый список недавно удалённых ключей использован и очищен: {len(recent_skip_ids)}; новых записей для следующего запуска: {len(state.get('recently_removed', {}))}")

    update_subscription_meta(state, jobs, processed_sections, total_added, total_removed, total_final_links, len(protected_local_ids))
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
        state = load_state(args.state)
        update_subscription_meta(state, jobs, processed_sections, total_added, total_removed, total_final_links, len(protected_local_ids), status='config_write_error', message=f'Ошибка при сохранении конфига: {e}')
        save_state(args.state, state)
        log("ERROR", f"Ошибка при сохранении конфига: {e}")


if __name__ == "__main__":
    main()
