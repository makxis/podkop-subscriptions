#!/usr/bin/env python3
import os
import sys
import subprocess
import base64
import re
import syslog
import argparse

# ==========================================
# Podkop Subscription Updater
# ==========================================

USER_AGENT = "Podkop-Subscription-Updater/1.0"
VALID_PROTOCOLS = ('vless://', 'vmess://', 'trojan://', 'ss://', 'ssr://', 'hy2://', 'hysteria2://')
VALID_PTYPES = {'urltest', 'selector'}
VALID_ON_EMPTY = {'all', 'skip'}

def setup_syslog():
    syslog.openlog("podkop-updater", syslog.LOG_PID, syslog.LOG_USER)

def log(level, msg):
    print(f"[{level}] {msg}")
    syslog_level = syslog.LOG_WARNING if level in ["ERROR", "WARN"] else syslog.LOG_INFO
    syslog.syslog(syslog_level, f"[{level}] {msg}")

def parse_args():
    parser = argparse.ArgumentParser(description="Обновление подписок для Podkop")
    parser.add_argument('--config', default='/etc/config/podkop', help='Путь к UCI конфигу podkop')
    parser.add_argument('--subs', default='/etc/config/podkop-subs', help='Путь к файлу подписок')
    return parser.parse_args()

def load_jobs(subs_path):
    if not os.path.exists(subs_path):
        log("ERROR", f"Файл подписок {subs_path} не найден")
        sys.exit(1)

    jobs = {}
    with open(subs_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'): 
                continue
            
            parts = [p.strip() for p in line.split('::')]
            if len(parts) != 5:
                log("WARN", f"Строка {line_num}: неверный формат. Ожидается ровно 5 колонок через '::'. Пропуск.")
                continue

            sec_name, url, regex_pattern, ptype, on_empty = parts
            sec_name = sec_name.lower()
            ptype = ptype.lower()
            on_empty = on_empty.lower()

            if not sec_name or not url:
                log("WARN", f"Строка {line_num}: отсутствует имя секции или URL. Пропуск.")
                continue

            if ptype not in VALID_PTYPES:
                log("WARN", f"Строка {line_num}: недопустимый тип '{ptype}'. Разрешены: {', '.join(VALID_PTYPES)}. Пропуск.")
                continue

            if on_empty not in VALID_ON_EMPTY:
                log("WARN", f"Строка {line_num}: недопустимое действие '{on_empty}'. Разрешены: {', '.join(VALID_ON_EMPTY)}. Пропуск.")
                continue

            jobs[sec_name] = {
                'url': url,
                'regex': regex_pattern,
                'ptype': ptype,
                'on_empty': on_empty,
                'links': []
            }
    
    return jobs

def fetch_links(jobs):
    for sec, job in jobs.items():
        log("DEBUG", f"--- Обработка секции: [{sec}] ---")
        try:
            # Используем wget, так как это надежный стандарт для OpenWrt
            cmd = ['wget', '-qO-', f'--user-agent={USER_AGENT}', job['url']]
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
            
            if result.returncode != 0 or not result.stdout:
                log("ERROR", f"[{sec}]: Ошибка скачивания -> {result.stderr.strip() or 'Пустой ответ'}")
                continue
            
            payload = result.stdout.strip()
            # Добавляем padding, если сервер отдал кривой Base64
            payload += '=' * (-len(payload) % 4)
            
            try:
                decoded_text = base64.b64decode(payload).decode('utf-8')
            except Exception:
                log("ERROR", f"[{sec}]: Ответ не является валидным Base64")
                continue

            links_raw = [ln.strip() for ln in decoded_text.splitlines() if ln.strip().startswith(VALID_PROTOCOLS)]
            
            if not links_raw:
                log("WARN", f"[{sec}]: Не найдено ссылок с поддерживаемыми протоколами.")
                continue

            filtered_links = []
            for ln in links_raw:
                if job['regex']:
                    tag = ln.split('#', 1)[1] if '#' in ln else ln
                    try:
                        if re.search(job['regex'], tag, re.IGNORECASE):
                            filtered_links.append(ln)
                    except re.error:
                        log("ERROR", f"[{sec}]: Некорректное регулярное выражение '{job['regex']}'")
                        break
                else:
                    filtered_links.append(ln)

            if not filtered_links:
                if job['on_empty'] == 'all':
                    log("INFO", f"[{sec}]: По фильтру пусто, используются все ссылки (on_empty=all).")
                    filtered_links = links_raw
                else:
                    log("INFO", f"[{sec}]: По фильтру пусто, секция пропущена (on_empty=skip).")
                    continue

            job['links'] = sorted(list(set(filtered_links)))
            log("INFO", f"[{sec}]: Успешно загружено ссылок: {len(job['links'])}")

        except subprocess.TimeoutExpired:
            log("ERROR", f"[{sec}]: Превышено время ожидания ответа сервера (15с).")
        except Exception as e:
            log("ERROR", f"[{sec}]: Непредвиденная ошибка -> {e}")

def update_uci_config(config_path, jobs):
    if not os.path.exists(config_path):
        log("ERROR", f"Конфиг {config_path} не найден. Настройте Podkop в интерфейсе.")
        sys.exit(1)

    with open(config_path, 'r', encoding='utf-8') as f:
        old_lines = f.readlines()

    out_lines = []
    current_sec = None
    found_sections = set()

    def flush_section(sec_name):
        sec_name_lower = sec_name.lower()
        if sec_name_lower in jobs and jobs[sec_name_lower]['links']:
            ptype = jobs[sec_name_lower]['ptype']
            out_lines.append(f"\toption connection_type 'proxy'\n")
            out_lines.append(f"\toption proxy_config_type '{ptype}'\n")
            for link in jobs[sec_name_lower]['links']:
                out_lines.append(f"\tlist {ptype}_proxy_links '{link}'\n")

    for line in old_lines:
        m = re.match(r"^\s*config\s+([a-zA-Z0-9_-]+)\s+['\"]?([a-zA-Z0-9_-]+)['\"]?", line, re.IGNORECASE)
        if m:
            if current_sec:
                flush_section(current_sec)
            
            current_sec = m.group(2)
            if current_sec.lower() in jobs:
                found_sections.add(current_sec.lower())
                
            out_lines.append(line)
            continue
        
        if current_sec and current_sec.lower() in jobs and jobs[current_sec.lower()]['links']:
            sline = line.strip()
            if any(sline.startswith(prefix) for prefix in (
                'list urltest_proxy_links',
                'list selector_proxy_links',
                'option connection_type',
                'option proxy_config_type',
                'option proxy_string'
            )):
                continue 
        
        out_lines.append(line)

    if current_sec:
        flush_section(current_sec)

    for sec in jobs:
        if jobs[sec]['links'] and sec not in found_sections:
            log("WARN", f"Секция '{sec}' успешно скачана, но отсутствует в {config_path} (ожидается config proxy '{sec}')")

    new_content = "".join(out_lines)
    old_content = "".join(old_lines)

    return old_content, new_content

def main():
    setup_syslog()
    args = parse_args()
    
    log("INFO", "=== ЗАПУСК ОБНОВЛЕНИЯ ПОДПИСОК ===")
    
    jobs = load_jobs(args.subs)
    if not jobs:
        log("ERROR", "Не найдено ни одной валидной подписки. Выход.")
        sys.exit(1)

    fetch_links(jobs)
    
    old_content, new_content = update_uci_config(args.config, jobs)

    def normalize(text):
        text = re.sub(r'sid=[a-zA-Z0-9]+', '', text)
        return text.replace('\n', '').replace('\r', '')

    if normalize(old_content) == normalize(new_content):
        log("INFO", "Изменений в ссылках нет. Перезапуск не требуется.")
    else:
        log("INFO", "Применение обновлений и перезапуск Podkop...")
        try:
            with open(args.config, 'w', encoding='utf-8') as f:
                f.write(new_content)
            os.system("/etc/init.d/podkop restart")
            log("INFO", "Успешно завершено.")
        except Exception as e:
            log("ERROR", f"Ошибка при сохранении конфига: {e}")

if __name__ == "__main__":
    main()
