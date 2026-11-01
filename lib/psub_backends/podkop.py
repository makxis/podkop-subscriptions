"""Podkop 0.7 (itdoginfo/podkop): /etc/config/podkop и /usr/bin/podkop-скрипт.

Ключи секции лежат в urltest_proxy_links или selector_proxy_links, тип
группы — в proxy_config_type, ссылку в outbound превращает конвертер самого
Podkop (sing_box_config_facade.sh), поэтому и проверка повторяет его правила.
Модуль podkop_exp (Podkop 0.8) берёт отсюда всё, кроме проверки ключей.
"""
import json
import re
import subprocess
from . import singbox

# Ссылка на ядро (podkop-sub-updater.py), её ставит load_backend().
core = None

NAME = 'podkop'
TITLE = 'Podkop'
CONFIG_PATH = '/etc/config/podkop'
UCI_PACKAGE = 'podkop'
CLI = '/usr/bin/podkop'
INIT_SCRIPT = '/etc/init.d/podkop'
# Строки секции, которые апдейтер пишет заново: старые ключи и поля, которыми
# Podkop выбирает список. proxy_string — прежний способ задать ключ вручную.
STRIPPED_PREFIXES = (
    'list urltest_proxy_links',
    'list selector_proxy_links',
    'option connection_type',
    'option proxy_config_type',
    'option proxy_string',
)


def configure(config_path):
    singbox.PROFILE.update(transports=singbox.VALID_TRANSPORTS, core='sing-box',
                           xhttp_refusal='', outbound_hook=xhttp_outbound)


def load_sections(config_path):
    # Ключ словаря — имя в нижнем регистре, как в target_section, а 'name' —
    # имя ровно как в конфиге. Podkop строит теги outbound'ов как
    # <секция>-N-out без смены регистра, и для секции YouTube поиск по тегу
    # youtube-1-out не находил ни одного ключа: все считались пропавшими, а
    # через 72 часа fail_count вычищал их из секции.
    result = {}
    for s in core.parse_uci_sections(config_path):
        if s['type'].lower() != 'section' or not s['name']:
            continue
        name = s['name'].strip().lower()
        ptype = s['options'].get('proxy_config_type', 'urltest').strip().lower()
        if ptype not in core.VALID_PTYPES:
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
        result[name] = {'name': s['name'].strip(), 'ptype': ptype, 'links': list(links), 'section': s}
    return result


def validate_links(sec, links):
    return singbox.validate_links(sec, links)


def _section_lines(sec, upd):
    ptype = upd['ptype']
    lines = ["\toption connection_type 'proxy'\n", f"\toption proxy_config_type '{ptype}'\n"]
    lines += [f"\tlist {ptype}_proxy_links {core.uci_quote(link)}\n" for link in upd['links']]
    return lines


def rewrite_config(config_path, updates):
    old, out_lines, _names = core.rewrite_managed_sections(config_path, updates, STRIPPED_PREFIXES, _section_lines)
    return old, ''.join(out_lines)


def _uci_commands(sec, upd):
    ptype = upd.get('ptype', 'urltest')
    commands = [
        ['-q', 'delete', f'podkop.{sec}.urltest_proxy_links'],
        ['-q', 'delete', f'podkop.{sec}.selector_proxy_links'],
        ['-q', 'delete', f'podkop.{sec}.proxy_string'],
        ['set', f'podkop.{sec}.connection_type=proxy'],
        ['set', f'podkop.{sec}.proxy_config_type={ptype}'],
    ]
    return commands + [['add_list', f'podkop.{sec}.{ptype}_proxy_links={link}'] for link in upd.get('links') or []]


def apply_uci(config_path, updates):
    core.apply_uci_updates(config_path, UCI_PACKAGE, CONFIG_PATH, updates, _uci_commands)


def xhttp_outbound(link, tag):
    """outbound для xhttp-ссылки, которую Podkop собирает через decode-link,
    или None: остальные ссылки собирает общий конвертер."""
    transport = podkop_decoded_transport(link)
    if not transport:
        return None
    blocker = podkop_xhttp_blocker()
    if blocker:
        raise core.LinkValidationError(blocker, transport)
    return decode_link_outbound(link, tag)


# xhttp своим конвертером Podkop не разбирает. С 0.7.23 ссылку vless или
# trojan с type=xhttp или splithttp он целиком отдаёт «sing-box tools
# decode-link» и кладёт в конфиг напечатанный outbound. Эта команда есть только
# в podkop-engine (в «sing-box version» строка Features: с tools.decode-link).
# Без неё Podkop на первом же таком ключе пишет fatal и выходит, и точно так же
# он выходит на ключе, который decode-link не принял. Поэтому xhttp-ключ
# пропускается, только когда умеют оба, и outbound для sing-box check берётся
# от того же decode-link.
PODKOP_FACADE_PATH = '/usr/lib/podkop/sing_box_config_facade.sh'
PODKOP_DECODED_TRANSPORTS = ('xhttp', 'splithttp')
# Как url_get_query_param в helpers.sh Podkop: последний type= в ссылке без
# фрагмента, без раскодирования и с учётом регистра.
PODKOP_TYPE_PARAM_RE = re.compile(r'.*[?&]type=([^&?#]*)', re.S)
DECODE_LINK_TIMEOUT = 15
PODKOP_XHTTP = {}
DECODED_LINKS = {}


def podkop_decoded_transport(link):
    """'xhttp' или 'splithttp', если Podkop отдаст ссылку decode-link, иначе ''.

    Решение повторяет case в sing_box_cf_add_proxy_outbound. Наш разбор
    запроса для него не годится: он берёт первое значение и приводит его к
    нижнему регистру, а type=XHTTP Podkop понесёт в свой конвертер, и тот
    соберёт обычный TCP.
    """
    link = str(link or '')
    if link.split('://', 1)[0] not in ('vless', 'trojan'):
        return ''
    m = PODKOP_TYPE_PARAM_RE.match(link.split('#', 1)[0])
    if m and m.group(1) in PODKOP_DECODED_TRANSPORTS:
        return m.group(1)
    return ''


def podkop_xhttp_blocker():
    """'' если Podkop и sing-box вместе собирают xhttp, иначе причина отказа.

    Считается при первом xhttp-ключе, так что прогоны без них лишний раз
    sing-box не запускают.
    """
    if 'reason' not in PODKOP_XHTTP:
        PODKOP_XHTTP['reason'] = _podkop_xhttp_blocker()
    return PODKOP_XHTTP['reason']


def _podkop_xhttp_blocker():
    try:
        with open(PODKOP_FACADE_PATH, encoding='utf-8', errors='replace') as f:
            facade = f.read()
    except OSError:
        facade = ''
    if 'decode-link' not in facade or 'xhttp' not in facade:
        return 'xhttp_unsupported_podkop'
    # Как sing_box_has_feature в helpers.sh Podkop: список через запятую из
    # строки Features:, имя сравнивается целиком.
    for line in singbox.sing_box_version_text().splitlines():
        if line.startswith('Features:'):
            if 'tools.decode-link' in line[len('Features:'):].strip().split(','):
                return ''
            break
    return 'xhttp_needs_podkop_engine'


def decode_link_outbound(link, tag):
    """Outbound, который Podkop соберёт из ссылки через decode-link.

    Результат запоминается: один ключ проверяется и формально, и в каждом
    запуске sing-box check, а на слабом роутере каждый запуск sing-box заметен.
    """
    if link not in DECODED_LINKS:
        DECODED_LINKS[link] = _run_decode_link(link)
    outbound, reason, detail = DECODED_LINKS[link]
    if reason:
        raise core.LinkValidationError(reason, detail)
    # Podkop делает то же самое: $raw_outbound + {tag: $tag}.
    return dict(outbound, tag=tag)


def _run_decode_link(link):
    try:
        result = subprocess.run(
            ['sing-box', 'tools', 'decode-link', '--compact', link],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding='utf-8', errors='replace', timeout=DECODE_LINK_TIMEOUT)
    except FileNotFoundError:
        return None, 'singbox_not_found', ''
    except (OSError, subprocess.SubprocessError):
        return None, 'decode_link_failed', 'sing-box не ответил'
    messages = [ln.strip() for ln in (result.stderr or '').splitlines() if ln.strip()]
    if result.returncode != 0:
        detail = messages[0] if messages else f'код {result.returncode}'
        return None, 'decode_link_failed', re.sub(r'^error:\s*', '', detail)
    try:
        outbound = json.loads(result.stdout or '')
    except ValueError:
        outbound = None
    if not isinstance(outbound, dict) or not outbound.get('type'):
        return None, 'decode_link_failed', 'sing-box не вернул outbound'
    # С предупреждениями Podkop ключ всё равно собирает и пишет их в лог при
    # каждом запуске: так decode-link называет то, чего outbound не сделает.
    name = core.link_name(link)
    for message in messages:
        core.log('DEBUG', 'decode-link' + (f" ({name})" if name else '') + f": {message}")
    return outbound, '', ''
