"""Podkop 0.8 (podkop-exp из podkop-net/podkop-experimental).

Конфиг и схема секций те же, что у Podkop 0.7, их берём из модуля podkop. Но
/usr/bin/podkop здесь уже не скрипт, а один бинарник с sing-box внутри: ни
конвертера sing_box_config_facade.sh, ни отдельного sing-box у него нет
(podkop-engine при установке удаляется), так что проверка для Podkop 0.7
отбросила бы все ключи как «sing-box не найден». Зато есть «podkop
check_links»: на каждый ключ он отвечает {ok, error} ровно так, как его
примет движок этой сборки.
"""
import json
import subprocess
from . import podkop

# Ссылка на ядро (podkop-sub-updater.py), её ставит load_backend().
core = None

NAME = 'podkop_exp'
TITLE = podkop.TITLE
CONFIG_PATH = podkop.CONFIG_PATH
UCI_PACKAGE = podkop.UCI_PACKAGE
CLI = podkop.CLI
INIT_SCRIPT = podkop.INIT_SCRIPT
load_sections = podkop.load_sections
rewrite_config = podkop.rewrite_config
apply_uci = podkop.apply_uci


def configure(config_path):
    # Профиль Podkop 0.7 нужен на случай, если check_links не ответит.
    podkop.configure(config_path)


def validate_links(sec, links):
    checked = validate_links_with_podkop_check_links(sec, links)
    if checked is not None:
        return checked
    core.log('WARN', f"[{sec}]: podkop check_links не ответил, ключи проверяются как для Podkop 0.7")
    return podkop.validate_links(sec, links)


CHECK_LINKS_TIMEOUT = 120
# Ключи идут аргументами: один запуск на пачку, чтобы не упереться в лимит
# длины командной строки на длинных подписках.
CHECK_LINKS_BATCH_BYTES = 64 * 1024


def run_podkop_check_links(links):
    """([(ok, error), ...] по ключам, число запусков) или None, если podkop
    check_links не ответил."""
    results = []
    batch, size = [], 0
    batches = []
    for link in links:
        if batch and size + len(link) > CHECK_LINKS_BATCH_BYTES:
            batches.append(batch)
            batch, size = [], 0
        batch.append(link)
        size += len(link) + 1
    if batch:
        batches.append(batch)
    for batch in batches:
        try:
            result = subprocess.run(
                [core.PODKOP_BINARY_PATH, 'check_links'] + batch,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                encoding='utf-8', errors='replace', timeout=CHECK_LINKS_TIMEOUT)
            answer = json.loads(result.stdout or '')
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        if not isinstance(answer, list) or len(answer) != len(batch):
            return None
        for item in answer:
            if not isinstance(item, dict):
                return None
            results.append((item.get('ok') is True, str(item.get('error') or '')))
    return results, len(batches)


def validate_links_with_podkop_check_links(sec, links):
    """Проверка ключей самим Podkop 0.8; None, если check_links недоступен."""
    links, _dups = core.dedupe_links_keep_order(list(links or []))
    answer = run_podkop_check_links(links)
    if answer is None:
        return None
    results, runs = answer
    valid = []
    errors = {}
    for link, (ok, error) in zip(links, results):
        if ok:
            valid.append(link)
            continue
        errors[error] = errors.get(error, 0) + 1
        name = core.link_name(link)
        core.log('DEBUG', f"[{sec}]: podkop check_links не принял ключ"
                     + (f" ({name})" if name else '') + (f": {error}" if error else ''))
    rejected = len(links) - len(valid)
    stats = {
        'input': len(links),
        'formal_ok': len(links),
        'rejected': rejected,
        'rejected_by_reason': {'check_links_rejected': rejected} if rejected else {},
        'singbox_runs': runs,
        'hard_rejected': rejected,
        'hard_check_ok': len(valid) > 0,
    }
    core.log('INFO', f"[{sec}]: проверка ключей podkop check_links: принято {len(valid)}, отброшено {rejected}")
    for error, count in sorted(errors.items(), key=lambda kv: -kv[1]):
        core.log('WARN', f"[{sec}]: {error or 'без причины'}: {count}")
    return valid, stats
