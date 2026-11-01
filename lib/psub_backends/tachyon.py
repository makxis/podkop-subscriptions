"""Tachyon (Dushnilin/tachyon, форк Forkop, ранее Podkop Plus): /etc/config/tachyon.

Tachyon ставится вместо Podkop, держит секции в своём пакете UCI и устроен
иначе. У секции action 'connection', поля Podkop (connection_type,
proxy_config_type, urltest_proxy_links) — устаревшие метки, Tachyon переносит
их только при обновлении своего пакета, а записанные сейчас оставили бы
секцию без ключей.

До 1.7 ключи лежат в selector_proxy_links самой секции, URLTest — отдельная
секция `config urltest` со ссылкой на целевую.

С 1.7 («modular entities») ключи живут в источниках (`config outbound_source`
с source_type 'proxy_links' и list proxy_links), секция ссылается на них
через outbound_source_refs, а URLTest — `config balancer` с balancer_type
'urltest' в balancer_refs секции. Так пишет и мастер панели. Наши ключи
лежат в своём источнике src_psub_<секция>, и он стоит первым в
outbound_source_refs: рантайм (connection_urls в config/connections.uc)
нумерует outbound'ы <секция>-N-out подряд — сначала строки самой секции,
затем источники в порядке ссылок, — так что номера наших ключей совпадают
с нашим списком.
"""
import os
import re
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

# Схема 1.7. Признак тот же, что у панели Tachyon (alreadyMigrated в
# views/tachyon.ts): миграция записана в settings.applied_migrations. Её
# ставят и свежая установка 1.7, и обновление с 1.6 и старше.
MODULAR_MIGRATION = 'modular_entities_v1'
SOURCE_PREFIX = 'src_psub_'
SOURCE_NAME_SUFFIX = ' (podkop-subscriptions)'
BALANCER_PREFIX = 'bal_psub_'
# Как мастер подключения панели 1.7 (connectionWizard.ts); имя как у нашей
# прежней группы.
BALANCER_DEFAULTS = (
    ('enabled', '1'),
    ('balancer_type', 'urltest'),
    ('name', 'Fastest'),
    ('testing_url', 'https://www.gstatic.com/generate_204'),
    ('check_interval', '3m'),
    ('tolerance', '50'),
    ('idle_timeout', '30m'),
)
# Ссылки бывают и списком, и одной строкой через пробел (whitespace_list_value
# у Tachyon понимает оба), поэтому вырезаются обе формы.
SECTION_STRIPPED_MODULAR = (
    'list selector_proxy_links', 'option action',
    'list outbound_source_refs', 'option outbound_source_refs',
    'list balancer_refs', 'option balancer_refs',
)
SOURCE_STRIPPED = ('list proxy_links', 'option proxy_links', 'option source_type')
BALANCER_SOURCES_STRIPPED = ('list source_refs', 'option source_refs')


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


def _urltest_owners(sections):
    owners = set()
    for s in sections:
        if s['type'].lower() == 'urltest':
            owner = s['options'].get('section', '').strip().lower()
            if owner:
                owners.add(owner)
    return owners


def tachyon_urltest_owners(config_path):
    return _urltest_owners(core.parse_uci_sections(config_path))


def modular_schema(sections):
    for s in sections:
        if s['type'].lower() == 'settings':
            return MODULAR_MIGRATION in _values(s, 'applied_migrations')
    return False


def _values(s, key):
    """Значения списка или опции-строки через пробел."""
    values = list(s['lists'].get(key, []))
    if not values and s['options'].get(key, '').strip():
        values = s['options'][key].split()
    return values


def _index(sections):
    return {(s['type'].lower(), s['name'].lower()): s for s in sections if s['name']}


def _enabled(s):
    return s['options'].get('enabled', '1') != '0'


def source_id(section_name):
    return SOURCE_PREFIX + re.sub(r'[^A-Za-z0-9_]', '_', section_name)


def balancer_id(section_name):
    return BALANCER_PREFIX + re.sub(r'[^A-Za-z0-9_]', '_', section_name)


def _urltest_balancers(s, index):
    found = []
    for ref in _values(s, 'balancer_refs'):
        bal = index.get(('balancer', ref.lower()))
        if bal and _enabled(bal) and bal['options'].get('balancer_type', 'urltest').lower() == 'urltest':
            found.append(ref)
    return found


def _tag_offset(s, refs, sid, index):
    """Сколько outbound'ов рантайм ставит перед нашим источником: строки
    самой секции и источники, на которые она ссылается раньше нашего.
    Повторы одной строки Tachyon схлопывает, поэтому считаются различные."""
    seen = []

    def add(value):
        value = (value or '').strip()
        if value and value not in seen:
            seen.append(value)

    for key in ('selector_proxy_links', 'proxy_links', 'connection_urls'):
        for value in _values(s, key):
            add(value)
    for key in ('selector_amnezia_configs', 'amnezia_configs'):
        for value in s['lists'].get(key, []):
            add(value)
    for line in s['options'].get('selector_proxy_links_text', '').splitlines():
        if line.strip() and not line.strip().startswith('#'):
            add(line)
    for ref in refs:
        if ref == sid:
            break
        src = index.get(('outbound_source', ref.lower()))
        if not src or not _enabled(src):
            continue
        for key in ('proxy_links', 'selector_proxy_links'):
            for value in _values(src, key):
                add(value)
        text = src['options'].get('proxy_links_text') or src['options'].get('selector_proxy_links_text') or ''
        for line in text.splitlines():
            if line.strip() and not line.strip().startswith('#'):
                add(line)
    return len(seen)


def load_sections(config_path):
    # Ключ словаря — имя в нижнем регистре, как в target_section, а 'name' —
    # имя ровно как в конфиге: теги outbound'ов <секция>-N-out регистр не меняют.
    sections = core.parse_uci_sections(config_path)
    modular = modular_schema(sections)
    index = _index(sections)
    urltest_owners = _urltest_owners(sections)
    result = {}
    for s in sections:
        if s['type'].lower() != 'section' or not s['name']:
            continue
        name = s['name'].strip().lower()
        is_urltest = (name in urltest_owners or s['options'].get('urltest_enabled') == '1'
                      or bool(_urltest_balancers(s, index)))
        inline = list(s['lists'].get('selector_proxy_links', []))
        info = {'name': s['name'].strip(), 'ptype': 'urltest' if is_urltest else 'selector', 'section': s}
        if modular:
            sid = source_id(s['name'].strip())
            src = index.get(('outbound_source', sid.lower()))
            refs = _values(s, 'outbound_source_refs')
            if src is None:
                # Ключи, записанные прежними версиями в саму секцию, — наши:
                # при первой записи они переедут в источник со всей статистикой.
                links = inline
            elif inline:
                # Переходное состояние: рантайм ставит строки секции первыми.
                own = _values(src, 'proxy_links')
                links = inline + [link for link in own if link not in inline]
            else:
                links = _values(src, 'proxy_links')
                info['tag_offset'] = _tag_offset(s, refs, sid, index)
            info['relayout'] = bool(inline) or src is None or not refs or refs[0] != sid
        else:
            links = inline
        info['links'] = links
        result[name] = info
    return result


def validate_links(sec, links):
    return singbox.validate_links(sec, links)


def _section_lines(sec, upd):
    lines = ["\toption action 'connection'\n"]
    lines += [f"\tlist selector_proxy_links {core.uci_quote(link)}\n" for link in upd['links']]
    return lines


def _rewrite_legacy(config_path, updates):
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


def _rewrite_modular(config_path, updates, sections):
    quote = core.uci_quote
    index = _index(sections)
    urltest_owners = _urltest_owners(sections)
    blocks, new_blocks = {}, []
    balancer_sources = {}
    for sec, upd in updates.items():
        s = index.get(('section', sec))
        if s is None:
            continue
        name = s['name'].strip()
        sid = source_id(name)
        src = index.get(('outbound_source', sid.lower()))
        other_refs = [ref for ref in _values(s, 'outbound_source_refs') if ref != sid]
        bal_refs = _values(s, 'balancer_refs')
        has_urltest = bool(_urltest_balancers(s, index)) or sec in urltest_owners
        if upd['ptype'] == 'urltest' and not has_urltest:
            bid = balancer_id(name)
            if ('balancer', bid.lower()) not in index:
                new_blocks.append([f"config balancer {quote(bid)}\n"]
                                  + [f"\toption {key} {quote(value)}\n" for key, value in BALANCER_DEFAULTS])
                core.log("INFO", f"[{sec}]: в Tachyon добавлен балансировщик URLTest 'Fastest' ({bid})")
            if bid not in bal_refs:
                bal_refs.append(bid)
        elif upd['ptype'] == 'selector' and has_urltest:
            core.log("INFO", f"[{sec}]: proxy_type=selector, но у секции в Tachyon есть группа URLTest; она оставлена как есть")
        lines = ["\toption action 'connection'\n"]
        lines += [f"\tlist outbound_source_refs {quote(ref)}\n" for ref in [sid] + other_refs]
        lines += [f"\tlist balancer_refs {quote(ref)}\n" for ref in bal_refs]
        blocks[('section', sec)] = (SECTION_STRIPPED_MODULAR, lines)

        src_lines = ["\toption source_type 'proxy_links'\n"]
        src_lines += [f"\tlist proxy_links {quote(link)}\n" for link in upd['links']]
        if src is None:
            new_blocks.append([f"config outbound_source {quote(sid)}\n", "\toption enabled '1'\n",
                               f"\toption name {quote(name + SOURCE_NAME_SUFFIX)}\n"] + src_lines)
            moved = len(s['lists'].get('selector_proxy_links', []))
            core.log("INFO", f"[{sec}]: ключи Tachyon 1.7 хранятся в источнике «{name}{SOURCE_NAME_SUFFIX}» ({sid})"
                             + (f", из секции перенесено: {moved}" if moved else ''))
        else:
            blocks[('outbound_source', sid.lower())] = (SOURCE_STRIPPED, src_lines)
            if not _enabled(src):
                core.log("WARN", f"[{sec}]: источник «{src['options'].get('name') or sid}» выключен в панели Tachyon, "
                                 "его ключи Tachyon не использует; апдейтер его не включает")

        # Балансировщик со списком источников видит только их: без нашего
        # источника URLTest остался бы без наших ключей.
        for ref in bal_refs:
            bal = index.get(('balancer', ref.lower()))
            if bal is None:
                continue
            refs = balancer_sources.get(ref.lower()) or _values(bal, 'source_refs')
            if refs and refs[0] != '*' and sid not in refs:
                refs = refs + [sid]
                balancer_sources[ref.lower()] = refs
                blocks[('balancer', ref.lower())] = (BALANCER_SOURCES_STRIPPED,
                                                     [f"\tlist source_refs {quote(r)}\n" for r in refs])

        copy_id = 'src_links_' + re.sub(r'[^A-Za-z0-9_]', '_', name)
        copy = index.get(('outbound_source', copy_id.lower()))
        if copy is not None and copy_id in other_refs and _enabled(copy):
            ours = set(upd['links'])
            extra = [link for link in _values(copy, 'proxy_links') if link not in ours]
            if extra:
                core.log("WARN", f"[{sec}]: источник «{copy['options'].get('name') or copy_id}» держит ключей вне "
                                 f"подписки: {len(extra)}. Обычно это копия, которую оставила миграция на Tachyon 1.7; "
                                 "апдейтер её не обслуживает. Если эти ключи из подписок, удалите источник во вкладке Outbounds.")
    for sec in updates:
        if ('section', sec) not in index:
            core.log("WARN", f"Секция '{sec}' собрана, но отсутствует как config section '{sec}' в {config_path}")
    old, out_lines, _found = core.rewrite_uci_blocks(config_path, blocks, new_blocks)
    return old, ''.join(out_lines)


def rewrite_config(config_path, updates):
    sections = core.parse_uci_sections(config_path)
    if modular_schema(sections):
        return _rewrite_modular(config_path, updates, sections)
    return _rewrite_legacy(config_path, updates)


def _uci_commands(sec, upd):
    commands = [['-q', 'delete', f'tachyon.{sec}.selector_proxy_links'], ['set', f'tachyon.{sec}.action=connection']]
    return commands + [['add_list', f'tachyon.{sec}.selector_proxy_links={link}'] for link in upd.get('links') or []]


def _replay(kind, name, block, options=(), lists=()):
    """Команды uci, повторяющие часть блока из уже записанного файла."""
    commands = [['set', f'tachyon.{name}={kind}']]
    for key in options:
        if key in block['options']:
            commands.append(['set', f"tachyon.{name}.{key}={block['options'][key]}"])
    for key in lists:
        commands.append(['-q', 'delete', f'tachyon.{name}.{key}'])
        commands += [['add_list', f'tachyon.{name}.{key}={value}'] for value in _values(block, key)]
    return commands


def _uci_commands_modular(index):
    def commands(sec, upd):
        s = index.get(('section', sec.lower()))
        if s is None:
            return []
        name = s['name'].strip()
        cmds = [['-q', 'delete', f'tachyon.{name}.selector_proxy_links']]
        cmds += _replay('section', name, s, ('action',), ('outbound_source_refs', 'balancer_refs'))
        sid = source_id(name)
        src = index.get(('outbound_source', sid.lower()))
        if src is not None:
            cmds += _replay('outbound_source', sid, src, ('enabled', 'name', 'source_type'), ('proxy_links',))
        for ref in _values(s, 'balancer_refs'):
            bal = index.get(('balancer', ref.lower()))
            if bal is None:
                continue
            if ref == balancer_id(name):
                cmds += _replay('balancer', ref, bal, [key for key, _ in BALANCER_DEFAULTS])
            if sid in _values(bal, 'source_refs'):
                cmds += _replay('balancer', ref, bal, (), ('source_refs',))
        return cmds
    return commands


def apply_uci(config_path, updates):
    # Файл к этому моменту уже записан, uci повторяет из него только то, что
    # меняет апдейтер.
    sections = core.parse_uci_sections(config_path)
    if modular_schema(sections):
        core.apply_uci_updates(config_path, UCI_PACKAGE, CONFIG_PATH, updates, _uci_commands_modular(_index(sections)))
        return
    core.apply_uci_updates(config_path, UCI_PACKAGE, CONFIG_PATH, updates, _uci_commands)
