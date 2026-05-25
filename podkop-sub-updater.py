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
import shutil

USER_AGENT = "Podkop-Subscription-Updater/1.1"
VALID_PROTOCOLS = ('vless://', 'ss://', 'trojan://', 'socks4://', 'socks4a://', 'socks5://', 'hy2://', 'hysteria2://')
VALID_PTYPES = {'urltest', 'selector'}
VALID_ON_EMPTY = {'all', 'skip'}
VALID_MATCH_MODES = {'ifmatch', 'ifnotmatch'}

def setup_syslog():
    syslog.openlog("podkop-updater", syslog.LOG_PID, syslog.LOG_USER)

def log(level, msg):
    print(f"[{level}] {msg}")
    syslog_level = syslog.LOG_WARNING if level in ["ERROR", "WARN"] else syslog.LOG_INFO
    syslog.syslog(syslog_level, f"[{level}] {msg}")

def parse_args():
    parser = argparse.ArgumentParser(description="Обновление подписок для Podkop")
    parser.add_argument('--config', default='/etc/config/podkop', help='Путь к UCI конфигу podkop')
    parser.add_argument('--subs', default='/etc/config/podkop', help='Путь к UCI конфигу подписок')
    parser.add_argument('--force', action='store_true', help='Принудительно перезаписать конфиг podkop и перезапустить сервис')
    return parser.parse_args()

def unquote(s):
    if '%' not in s:
        return s

    res = bytearray()
    i = 0
    while i < len(s):
        if s[i] == '%' and i + 2 < len(s):
            try:
                res.append(int(s[i+1:i+3], 16))
                i += 3
                continue
            except ValueError:
                pass

        res.extend(s[i].encode('utf-8'))
        i += 1

    return res.decode('utf-8', errors='replace')

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


def parse_uci_value(raw):
    raw = (raw or '').strip()

    if len(raw) >= 2 and raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1]

    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1]

    return raw

def parse_uci_sections(config_path):
    sections = []
    current = None

    with open(config_path, 'r', encoding='utf-8') as f:
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
            jobs[sec_name] = {
                'ptype': ptype,
                'entries': [],
                'links': []
            }

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

    with open(subs_path, 'r', encoding='utf-8') as f:
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
                jobs[sec_name] = {
                    'ptype': ptype,
                    'entries': [],
                    'links': []
                }

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
        if subs_abs in ('/etc/config/podkop', '/etc/config/podkop_subscriptions'):
            log("INFO", "Конфиг подписок не найден. Подписки не настроены.")
            return {}

        log("ERROR", f"Файл подписок {subs_path} не найден")
        sys.exit(1)

    uci_jobs = load_jobs_from_uci_podkop(subs_path)
    if uci_jobs is not None:
        return uci_jobs

    if subs_abs in ('/etc/config/podkop', '/etc/config/podkop_subscriptions'):
        log("INFO", "subscription_group не найдены. Подписки не настроены.")
        return {}

    return load_jobs_from_flat_file(subs_path)

def is_url_source(source):
    return bool(re.match(r'^https?://', source, re.IGNORECASE))

def read_source_payload(source, hwid, device_model, kernel_ver, cache):
    if source in cache:
        return cache[source]

    if is_url_source(source):
        cmd = ['wget', '-qO-', f'--user-agent={USER_AGENT}']

        cmd.extend(['--header', f'X-HWID: {hwid}'])
        cmd.extend(['--header', 'X-Device-OS: OpenWrt Linux'])
        cmd.extend(['--header', f'X-Device-Model: {device_model}'])
        cmd.extend(['--header', f'X-Ver-OS: {kernel_ver}'])

        cmd.append(source)

        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=45
        )

        if result.returncode != 0 or not result.stdout:
            raise RuntimeError(result.stderr.strip() or 'Пустой ответ')

        cache[source] = result.stdout
        return result.stdout

    path = source
    if path.startswith('file://'):
        path = path[7:]

    if not os.path.exists(path):
        raise RuntimeError(f"локальный файл не найден: {path}")

    with open(path, 'r', encoding='utf-8') as f:
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

def filter_links(links_raw, regex_pattern, match_mode, on_empty, sec, source):
    if not regex_pattern:
        return links_raw

    filtered_links = []

    for ln in links_raw:
        target = unquote(ln)

        try:
            is_match = bool(re.search(regex_pattern, target, re.IGNORECASE))
        except re.error:
            log("ERROR", f"[{sec}]: Некорректное регулярное выражение '{regex_pattern}' для источника {source}")
            return []

        if (match_mode == 'ifmatch' and is_match) or \
           (match_mode == 'ifnotmatch' and not is_match):
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

        for entry in job['entries']:
            source = entry['source']

            try:
                payload = read_source_payload(source, hwid, device_model, kernel_ver, payload_cache)
            except subprocess.TimeoutExpired:
                log("ERROR", f"[{sec}]: Превышено время ожидания ответа сервера: {source}")
                continue
            except Exception as e:
                log("ERROR", f"[{sec}]: Ошибка чтения источника {source} -> {e}")
                continue

            links_raw, payload_type = extract_links_from_payload(payload)

            if payload_type == 'invalid':
                log("ERROR", f"[{sec}]: Источник не plain-text URI и не валидный Base64: {source}")
                continue

            if not links_raw:
                log("WARN", f"[{sec}]: В источнике нет ссылок поддерживаемого типа: {source}")
                continue

            filtered_links = filter_links(
                links_raw,
                entry['regex'],
                entry['match_mode'],
                entry['on_empty'],
                sec,
                source
            )

            if not filtered_links:
                continue

            section_links.extend(filtered_links)

            log("INFO", f"[{sec}]: источник {source} ({payload_type}) -> ссылок после фильтра: {len(filtered_links)}")

        job['links'] = sorted(list(set(section_links)))

        if job['links']:
            log("INFO", f"[{sec}]: Итого уникальных ссылок: {len(job['links'])}")
        else:
            log("WARN", f"[{sec}]: После обработки всех источников не осталось ссылок.")


def update_uci_config(config_path, jobs):
    if not os.path.exists(config_path):
        log("ERROR", f"Конфиг {config_path} не найден. Настройте Podkop в интерфейсе.")
        sys.exit(1)

    with open(config_path, 'r', encoding='utf-8') as f:
        old_lines = f.readlines()

    out_lines = []
    current_sec = None
    current_type = None
    found_sections = set()
    skip_multiline = False

    def is_target_podkop_section(sec_type, sec_name):
        return (
            sec_type and
            sec_name and
            sec_type.lower() == 'section' and
            sec_name.lower() in jobs
        )

    def flush_section(sec_type, sec_name):
        if not is_target_podkop_section(sec_type, sec_name):
            return

        sec_name_lower = sec_name.lower()

        if jobs[sec_name_lower]['links']:
            ptype = jobs[sec_name_lower]['ptype']
            out_lines.append(f"\toption connection_type 'proxy'\n")
            out_lines.append(f"\toption proxy_config_type '{ptype}'\n")

            for link in jobs[sec_name_lower]['links']:
                out_lines.append(f"\tlist {ptype}_proxy_links '{link}'\n")

    for line in old_lines:
        m = re.match(r"^\s*config\s+([a-zA-Z0-9_-]+)\s+['\"]?([a-zA-Z0-9_-]+)['\"]?", line, re.IGNORECASE)

        if m:
            if current_sec:
                flush_section(current_type, current_sec)

            current_type = m.group(1)
            current_sec = m.group(2)

            if is_target_podkop_section(current_type, current_sec):
                found_sections.add(current_sec.lower())

            out_lines.append(line)
            skip_multiline = False
            continue

        if is_target_podkop_section(current_type, current_sec) and jobs[current_sec.lower()]['links']:
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

    for sec in jobs:
        if jobs[sec]['links'] and sec not in found_sections:
            log("WARN", f"Секция '{sec}' успешно собрана, но отсутствует как config section '{sec}' в {config_path}")

    new_content = "".join(out_lines)
    old_content = "".join(old_lines)

    return old_content, new_content


def apply_jobs_with_uci(config_path, jobs):
    # Подстраховка: после ручной перезаписи файла гарантированно проставляем
    # обязательные поля Podkop через uci. Это защищает от ситуации, когда
    # section осталась без connection_type/proxy_config_type/proxy_links.
    if os.path.abspath(config_path) != '/etc/config/podkop':
        return

    changed = False

    for sec, job in jobs.items():
        links = job.get('links') or []
        if not links:
            continue

        ptype = job.get('ptype', 'urltest')

        exists = subprocess.run(
            ['uci', '-q', 'get', f'podkop.{sec}'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        ).returncode == 0

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
            subprocess.run(
                ['uci', 'add_list', f'podkop.{sec}.{ptype}_proxy_links={link}'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        changed = True

    if changed:
        subprocess.run(['uci', 'commit', 'podkop'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log("INFO", "UCI-поля секций Podkop дополнительно зафиксированы через uci commit.")

def normalize_config(text):
    text = re.sub(r'sid=[a-zA-Z0-9]+', '', text)
    text = re.sub(r"#[^'\n\r]*", '', text)
    return text.replace('\n', '').replace('\r', '')

def main():
    setup_syslog()
    args = parse_args()

    log("INFO", "=== ЗАПУСК ОБНОВЛЕНИЯ ПОДПИСОК ===")

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

    fetch_links(jobs, hwid, device_model, kernel_ver)

    old_content, new_content = update_uci_config(args.config, jobs)
    is_content_changed = normalize_config(old_content) != normalize_config(new_content)

    if not args.force and not is_content_changed:
        log("INFO", "Изменений не обнаружено. Перезапуск не требуется.")
    else:
        if args.force and not is_content_changed:
            log("INFO", "Изменений не обнаружено, но указан --force. Принудительная перезапись и перезапуск.")
        else:
            log("INFO", "Применение обновлений и перезапуск Podkop.")

        tmp_config = f"{args.config}.tmp.{os.getpid()}"
        backup_config = f"{args.config}.podkop-subscriptions.bak"

        try:
            if os.path.exists(args.config):
                shutil.copy2(args.config, backup_config)
                log("INFO", f"Создана резервная копия: {backup_config}")

            with open(tmp_config, 'w', encoding='utf-8') as f:
                f.write(new_content)

            os.replace(tmp_config, args.config)

            apply_jobs_with_uci(args.config, jobs)

            os.system("/etc/init.d/podkop restart")
            log("INFO", "Успешно завершено.")
        except Exception as e:
            try:
                if os.path.exists(tmp_config):
                    os.unlink(tmp_config)
            except Exception:
                pass
            log("ERROR", f"Ошибка при сохранении конфига: {e}")

if __name__ == "__main__":
    main()
