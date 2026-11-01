"""Tachyon (Dushnilin/tachyon, форк Forkop, ранее Podkop Plus): /etc/config/tachyon.

Tachyon ставится вместо Podkop, держит секции в своём пакете UCI и устроен
иначе. Ключи всегда лежат в selector_proxy_links, у секции action
'connection', а URLTest — отдельная секция `config urltest` со ссылкой на
целевую. Поля Podkop (connection_type, proxy_config_type,
urltest_proxy_links) — устаревшие метки, Tachyon переносит их только при
обновлении своего пакета, а записанные сейчас оставили бы секцию без ключей.

С 1.7 ключи живут в отдельных источниках (`config outbound_source` с
proxy_links), а секция ссылается на них через outbound_source_refs. Рантайм
1.7 собирает ключи секции из обоих мест, сначала её собственные
selector_proxy_links, поэтому запись туда работает и нумерация
<секция>-N-out для наших ключей не сдвигается. Старый `config urltest` 1.7
тоже понимает.
"""
import os
from . import singbox

# Ссылка на ядро (podkop-sub-updater.py), её ставит load_backend().
core = None

NAME = 'tachyon'
TITLE = 'Tachyon'
CONFIG_PATH = '/etc/config/tachyon'
UCI_PACKAGE = 'tachyon'
CLI = '/usr/bin/tachyon'
INIT_SCRIPT = '/etc/init.d/tachyon'
STRIPPED_PREFIXES = ('list selector_proxy_links', 'option action')
TACHYON_URLTEST_DEFAULTS = (
    ('name', 'Fastest'),
    ('check_interval', '3m'),
    ('tolerance', '50'),
    ('testing_url', 'https://www.gstatic.com/generate_204'),
    ('filter_mode', 'disabled'),
    ('detect_server_country', 'flag_emoji'),
    ('interrupt_exist_connections', '1'),
    ('pin_dashboard', '1'),
)
# Tachyon converts links itself (subscription/parser.uc) and knows more
# transports than Podkop. xhttp is not in upstream sing-box: Tachyon only
# accepts it when the installed build has it (extended/lx, or a with_xhttp
# tag), and so do we, otherwise the key would pass here and break the config.
TACHYON_TRANSPORTS = singbox.VALID_TRANSPORTS | {'http', 'h2', 'httpupgrade'}
# С 1.5.16 движок Tachyon называется tachyon-core. Это либо его собственное
# ядро на Rust, либо симлинк на sing-box, а /usr/bin/sing-box бывает обратным
# симлинком или отсутствует. Tachyon запускает и проверяет конфиг тем, что
# найдёт первым (command -v tachyon-core || command -v sing-box), мы так же.
TACHYON_CORE_BINARY = 'tachyon-core'


def tachyon_core_binary():
    for directory in os.environ.get('PATH', '/usr/sbin:/usr/bin:/sbin:/bin').split(os.pathsep):
        path = os.path.join(directory or '.', TACHYON_CORE_BINARY)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return TACHYON_CORE_BINARY
    return 'sing-box'



def configure(config_path):
    singbox.PROFILE['core'] = tachyon_core_binary()
    xhttp = singbox.sing_box_supports_xhttp()
    singbox.PROFILE.update(transports=TACHYON_TRANSPORTS | ({'xhttp'} if xhttp else set()),
                           xhttp_refusal='xhttp_unsupported_singbox', outbound_hook=None)


def tachyon_urltest_owners(config_path):
    owners = set()
    for s in core.parse_uci_sections(config_path):
        if s['type'].lower() == 'urltest':
            owner = s['options'].get('section', '').strip().lower()
            if owner:
                owners.add(owner)
    return owners


def load_sections(config_path):
    # Ключ словаря — имя в нижнем регистре, как в target_section, а 'name' —
    # имя ровно как в конфиге: теги outbound'ов <секция>-N-out регистр не меняют.
    result = {}
    urltest_owners = tachyon_urltest_owners(config_path)
    for s in core.parse_uci_sections(config_path):
        if s['type'].lower() != 'section' or not s['name']:
            continue
        name = s['name'].strip().lower()
        is_urltest = name in urltest_owners or s['options'].get('urltest_enabled') == '1'
        result[name] = {
            'name': s['name'].strip(),
            'ptype': 'urltest' if is_urltest else 'selector',
            'links': list(s['lists'].get('selector_proxy_links', [])),
            'section': s
        }
    return result


def validate_links(sec, links):
    return singbox.validate_links(sec, links)


def _section_lines(sec, upd):
    lines = ["\toption action 'connection'\n"]
    lines += [f"\tlist selector_proxy_links {core.uci_quote(link)}\n" for link in upd['links']]
    return lines


def rewrite_config(config_path, updates):
    urltest_owners = tachyon_urltest_owners(config_path)
    old, out_lines, found_names = core.rewrite_managed_sections(config_path, updates, STRIPPED_PREFIXES, _section_lines)
    for sec in sorted(found_names):
        ptype = updates[sec]['ptype']
        if ptype == 'urltest' and sec not in urltest_owners:
            if out_lines and not out_lines[-1].endswith('\n'):
                out_lines.append('\n')
            out_lines.append("\nconfig urltest\n")
            out_lines.append(f"\toption section {core.uci_quote(found_names.get(sec, sec))}\n")
            for key, value in TACHYON_URLTEST_DEFAULTS:
                out_lines.append(f"\toption {key} {core.uci_quote(value)}\n")
            core.log("INFO", f"[{sec}]: в Tachyon добавлена группа URLTest 'Fastest' для секции")
        elif ptype == 'selector' and sec in urltest_owners:
            core.log("INFO", f"[{sec}]: proxy_type=selector, но у секции в Tachyon есть группа URLTest; она оставлена как есть")
    return old, ''.join(out_lines)


def _uci_commands(sec, upd):
    commands = [['-q', 'delete', f'tachyon.{sec}.selector_proxy_links'], ['set', f'tachyon.{sec}.action=connection']]
    return commands + [['add_list', f'tachyon.{sec}.selector_proxy_links={link}'] for link in upd.get('links') or []]


def apply_uci(config_path, updates):
    core.apply_uci_updates(config_path, UCI_PACKAGE, CONFIG_PATH, updates, _uci_commands)
