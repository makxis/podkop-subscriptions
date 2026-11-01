"""Проверка ключей через sing-box: общая часть для Podkop 0.7 и Tachyon.

Ссылка превращается в outbound так, как её соберёт целевое ПО, а затем весь
список проходит «<ядро> -c <файл> check». Чем именно ПО отличается (какие
транспорты оно умеет, чьим бинарником проверять, как собирать xhttp), модуль
бэкенда задаёт в PROFILE через configure().
"""
import json
import os
import re
import subprocess
import time

# Ссылка на ядро (podkop-sub-updater.py), её ставит load_backend().
core = None

SINGBOX_CHECK_TIMEOUT_DEFAULT = 15
# Потолок числа запусков sing-box check на секцию. Он не про размер подписки, а
# про защиту от вырождения поиска битого ключа: сам sing-box называет позицию,
# на которой споткнулся, поэтому обычно запусков ровно на один больше, чем
# битых ключей. Потолок остаётся страховкой для случая, когда позицию
# разобрать не удалось и приходится делить список пополам.
SINGBOX_CHECK_MAX_RUNS_DEFAULT = 40
SINGBOX_CHECK_TAG_PREFIX = 'podkop-sub-test-'
# «decode config at /tmp/...: outbounds[3].transport: unknown transport type»
# и «initialize outbound[1]: unknown method» — единственное и множественное
# число, обе формы встречаются у sing-box 1.12.
SINGBOX_CHECK_INDEX_RE = re.compile(r'outbounds?\[(\d+)\]')
SINGBOX_CHECK_TAG_RE = re.compile(re.escape(SINGBOX_CHECK_TAG_PREFIX) + r'(\d+)')

# Эти три набора — не наше представление о прекрасном и не возможности
# sing-box, а ровно то, что разбирает конвертер Podkop в
# /usr/lib/podkop/sing_box_config_facade.sh: case по схеме в
# sing_box_cf_add_proxy_outbound, по security в _add_outbound_security и по
# type в _add_outbound_transport. Podkop и есть потолок, потому что ссылку в
# outbound превращает он, а не мы и не sing-box.
#
# Почему без этой проверки нельзя обойтись, хотя следом идёт sing-box check:
# на неизвестной схеме Podkop пишет «Unsupported proxy type. Aborted.» и
# выходит с ошибкой, то есть один такой ключ оставляет роутер без proxy
# вообще; на неизвестном transport он всего лишь пишет в лог «Unknown
# transport» и собирает outbound без транспорта, то есть обычный TCP. Такой
# конфиг sing-box check проходит успешно, а узел не работает и молча висит в
# URLTest. Поймать это можно только до конвертации. Ключи с xhttp Podkop
# разбирает не этим конвертером, для них модуль Podkop ставит свой обработчик.
VALID_TRANSPORTS = {'', 'tcp', 'raw', 'ws', 'grpc'}
VALID_SECURITY = {'', 'none', 'tls', 'reality'}

# Чем целевое ПО отличается при сборке outbound. Заполняет configure() модуля:
#   transports    — допустимые значения type= в ссылке;
#   core          — бинарник для «version» и «check»;
#   xhttp_refusal — причина отказа, когда xhttp нет в transports (без неё
#                   xhttp отбраковывается как любой неизвестный транспорт);
#   outbound_hook — функция (link, tag) -> outbound или None: ПО собирает
#                   такую ссылку своим способом, а не общим конвертером.
PROFILE = {'transports': VALID_TRANSPORTS, 'core': 'sing-box', 'xhttp_refusal': '', 'outbound_hook': None}

SING_BOX_VERSION = {}


def core_binary():
    """Бинарник, которым проверяются ключи и спрашивается версия ядра."""
    return PROFILE.get('core') or 'sing-box'


def sing_box_version_text():
    """Вывод «<ядро> version»: один запуск на весь прогон, и только по нужде."""
    binary = core_binary()
    if binary not in SING_BOX_VERSION:
        try:
            SING_BOX_VERSION[binary] = subprocess.run(
                [binary, 'version'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                encoding='utf-8', errors='replace', timeout=15).stdout or ''
        except Exception:
            SING_BOX_VERSION[binary] = ''
    return SING_BOX_VERSION[binary]


def sing_box_supports_xhttp():
    """Как профили ядер в core_profile.uc Tachyon: у lx, extended,
    extended-compressed и tachyon-core xhttp есть всегда, у обычного sing-box
    только со сборочным тегом with_xhttp."""
    out = sing_box_version_text()
    first = out.split('\n', 1)[0].strip()
    if re.search(r'-(extended|lx)\b', first):
        return True
    # Ядро Tachyon отвечает «tachyon-core 0.2.9», его сборки sing-box несут
    # -tachyon. в номере версии.
    if first.startswith('tachyon-core') or '-tachyon.' in first:
        return True
    return 'with_xhttp' in re.split(r'[,:\s]+', out)


def _validate_hysteria2_ports(port):
    port = str(port or '').strip()
    if not port:
        raise core.LinkValidationError('missing_port')
    if ',' not in port and '-' not in port:
        return {'server_port': core._port_int(port)}
    items = []
    for raw in port.split(','):
        item = raw.strip()
        if not item:
            continue
        if '-' in item:
            a, b = item.split('-', 1)
            ai = core._port_int(a)
            bi = core._port_int(b)
            if ai > bi:
                raise core.LinkValidationError('invalid_port')
            items.append(f'{ai}:{bi}')
        else:
            p = core._port_int(item)
            items.append(f'{p}:{p}')
    if not items:
        raise core.LinkValidationError('invalid_port')
    return {'server_ports': items}


def _build_tls_object(parts):
    scheme = parts['scheme']
    q = parts['query']
    security = (q.get('security', '') or '').strip().lower()
    if not security and scheme in ('hy2', 'hysteria2'):
        security = 'tls'
    if security not in VALID_SECURITY:
        raise core.LinkValidationError('unsupported_security', security)
    if security in ('', 'none'):
        return None

    tls = {'enabled': True}
    sni = q.get('sni') or q.get('peer') or ''
    if sni:
        tls['server_name'] = sni
    insecure = (q.get('allowInsecure') or q.get('insecure') or '').strip().lower()
    if insecure in ('1', 'true', 'yes', 'on'):
        tls['insecure'] = True
    alpn = core._split_csv(q.get('alpn', ''))
    if alpn:
        tls['alpn'] = alpn
    fp = q.get('fp', '')
    if fp and scheme not in ('hy2', 'hysteria2'):
        tls['utls'] = {'enabled': True, 'fingerprint': fp}
    if security == 'reality':
        pbk = q.get('pbk', '')
        if not pbk:
            raise core.LinkValidationError('missing_reality_public_key')
        tls['reality'] = {'enabled': True, 'public_key': pbk}
        sid = q.get('sid', '')
        if sid:
            tls['reality']['short_id'] = sid
    return tls


def _build_transport_object(parts):
    q = parts['query']
    transport = (q.get('type', '') or '').strip().lower()
    if transport not in PROFILE['transports']:
        if transport == 'xhttp' and PROFILE.get('xhttp_refusal'):
            raise core.LinkValidationError(PROFILE['xhttp_refusal'], transport)
        raise core.LinkValidationError('unsupported_transport', transport)
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
                raise core.LinkValidationError('invalid_transport_param')
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
    # The shapes below follow Tachyon's parser, so sing-box check sees the
    # outbound Tachyon will actually build.
    path = q.get('path', '')
    host = q.get('host', '')
    if transport in ('http', 'h2'):
        http = {'type': 'http'}
        if path:
            http['path'] = path
        if host:
            http['host'] = [h.strip() for h in host.split(',') if h.strip()]
        return http
    if transport == 'httpupgrade':
        upgrade = {'type': 'httpupgrade'}
        if path:
            upgrade['path'] = path
        if host:
            upgrade['host'] = host
        return upgrade
    if transport == 'xhttp':
        mode = q.get('mode', '') or 'auto'
        if mode not in ('auto', 'packet-up', 'stream-up', 'stream-one'):
            mode = 'auto'
        # Tachyon's defaults; sing-box-extended refuses xhttp without
        # x_padding_bytes ("x_padding_bytes cannot be disabled").
        xhttp = {
            'type': 'xhttp', 'mode': mode, 'path': path or '/',
            'x_padding_bytes': '100-1000', 'no_grpc_header': False,
            'sc_max_each_post_bytes': '1000000', 'sc_min_posts_interval_ms': '30'
        }
        host = host or q.get('sni', '')
        if host:
            xhttp['host'] = host
        return xhttp
    raise core.LinkValidationError('unsupported_transport', transport)


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
            if core.unquote_percent(k).strip().lower() == key.lower():
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
    parts = core._parse_link_parts(link)
    scheme = parts['scheme']
    q = parts['query']
    if scheme in ('vless', 'trojan'):
        transport = (q.get('type', '') or '').strip().lower()
        if transport == '':
            return _set_query_param_preserve_fragment(link, 'type', 'tcp'), 'normalized_missing_transport'
    return link, ''


def proxy_link_to_singbox_outbound(link, tag):
    hook = PROFILE.get('outbound_hook')
    if hook:
        outbound = hook(link, tag)
        if outbound is not None:
            return outbound

    parts = core._parse_link_parts(link)
    scheme = parts['scheme']
    host = parts['host']
    port = parts['port']
    userinfo = parts['userinfo']
    q = parts['query']

    if not host:
        raise core.LinkValidationError('missing_host')
    if not port:
        raise core.LinkValidationError('missing_port')

    outbound = {'tag': tag}

    if scheme in ('socks4', 'socks4a', 'socks5'):
        outbound.update({
            'type': 'socks',
            'server': host,
            'server_port': core._port_int(port),
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
        method, password = core._parse_ss_userinfo(userinfo)
        outbound.update({
            'type': 'shadowsocks',
            'server': host,
            'server_port': core._port_int(port),
            'method': method,
            'password': password
        })

    elif scheme == 'vless':
        uuid = userinfo
        if not uuid:
            raise core.LinkValidationError('missing_userinfo')
        if not re.match(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$', uuid):
            raise core.LinkValidationError('invalid_userinfo')
        outbound.update({
            'type': 'vless',
            'server': host,
            'server_port': core._port_int(port),
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
            raise core.LinkValidationError('missing_userinfo')
        outbound.update({
            'type': 'trojan',
            'server': host,
            'server_port': core._port_int(port),
            'password': userinfo
        })

    elif scheme in ('hy2', 'hysteria2'):
        if not userinfo:
            raise core.LinkValidationError('missing_userinfo')
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
                    raise core.LinkValidationError('invalid_transport_param')

    else:
        raise core.LinkValidationError('unsupported_scheme', parts.get('scheme', ''))

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


def parse_singbox_check_position(text, count):
    """Позиция ключа, на котором sing-box check остановился, или None.

    sing-box разбирает и поднимает outbound'ы по порядку и падает на первом
    неподходящем, называя его: «outbounds[3].transport: ...» при разборе
    конфига и «initialize outbound[1]: ...» при инициализации. Номер в
    сообщении — индекс в том же массиве, который мы ему и передали, поэтому по
    нему сразу видно виновника. Если индекса нет, в ход идёт тег: имена вида
    podkop-sub-test-N мы раздаём сами, по порядку, с единицы.
    """
    text = text or ''

    m = SINGBOX_CHECK_INDEX_RE.search(text)
    if m:
        idx = int(m.group(1))
        if 0 <= idx < count:
            return idx

    m = SINGBOX_CHECK_TAG_RE.search(text)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < count:
            return idx

    return None


def run_singbox_check_for_links(links, timeout=SINGBOX_CHECK_TIMEOUT_DEFAULT):
    """Возвращает (ok, причина, позиция битого ключа или None)."""
    if not links:
        return True, '', None
    tmp_path = f'/tmp/podkop-sub-singbox-check-{os.getpid()}-{int(time.time() * 1000)}.json'
    try:
        cfg = _singbox_config_for_links(links)
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, separators=(',', ':'))
            f.write('\n')
        try:
            result = subprocess.run(
                [core_binary(), '-c', tmp_path, 'check'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout
            )
        except FileNotFoundError:
            return False, 'singbox_not_found', None
        except subprocess.TimeoutExpired:
            return False, 'singbox_check_failed', None
        if result.returncode == 0:
            return True, '', None
        position = parse_singbox_check_position(
            (result.stderr or '') + '\n' + (result.stdout or ''), len(links))
        return False, 'singbox_check_failed', position
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


def hard_validate_links_with_singbox(sec, links, max_runs=None):
    links = list(links or [])
    if not links:
        return [], {'ok': True, 'runs': 0, 'rejected': 0, 'rejected_by_reason': {}}

    # Один запуск на битый ключ плюс подтверждающий, плюс запас на случай, когда
    # позицию разобрать не вышло и список делится пополам.
    if max_runs is None:
        max_runs = max(SINGBOX_CHECK_MAX_RUNS_DEFAULT, len(links) + 8)

    runs = {'n': 0}
    bad_ids = set()
    bad_reason = {}

    def check(batch):
        if not batch:
            return True, '', None
        if runs['n'] >= max_runs:
            raise core.LinkValidationError('singbox_check_limit')
        runs['n'] += 1
        return run_singbox_check_for_links(batch)

    def mark_bad(link, reason):
        sid = core.stable_id(link)
        bad_ids.add(sid)
        bad_reason[sid] = reason or 'singbox_check_failed'

    def isolate(batch):
        """Запасной путь: sing-box не назвал позицию, ищем делением пополам."""
        if not batch:
            return
        if len(batch) == 1:
            mark_bad(batch[0], 'singbox_check_failed')
            return
        mid = len(batch) // 2
        for part in (batch[:mid], batch[mid:]):
            if not part:
                continue
            ok_part, reason_part, _position = check(part)
            if not ok_part:
                if reason_part == 'singbox_not_found':
                    for link in part:
                        mark_bad(link, 'singbox_not_found')
                else:
                    isolate(part)

    try:
        pending = list(links)
        while pending:
            ok, reason, position = check(pending)
            if ok:
                break
            if reason == 'singbox_not_found':
                return [], {'ok': False, 'runs': runs['n'], 'rejected': len(links), 'rejected_by_reason': {'singbox_not_found': len(links)}}

            if position is None:
                isolate(pending)
                pending = [x for x in pending if core.stable_id(x) not in bad_ids]
                if pending:
                    ok_final, reason_final, _position = check(pending)
                    if not ok_final:
                        return [], {'ok': False, 'runs': runs['n'], 'rejected': len(links), 'rejected_by_reason': {reason_final or 'singbox_check_failed': len(links)}}
                break

            culprit = pending.pop(position)
            mark_bad(culprit, reason)
            name = core.link_name(culprit)
            core.log('DEBUG', f"[{sec}]: sing-box отклонил ключ {position + 1}/{len(pending) + 1}" + (f": {name}" if name else ''))

        good = [x for x in links if core.stable_id(x) not in bad_ids]
        by_reason = {}
        for sid in bad_ids:
            r = bad_reason.get(sid, 'singbox_check_failed')
            by_reason[r] = by_reason.get(r, 0) + 1
        return good, {'ok': True, 'runs': runs['n'], 'rejected': len(bad_ids), 'rejected_by_reason': by_reason}
    except core.LinkValidationError as e:
        return [], {'ok': False, 'runs': runs['n'], 'rejected': len(links), 'rejected_by_reason': {e.reason: len(links)}}


def validate_links(sec, links):
    """(принятые ключи, статистика): формальная сборка outbound и sing-box check."""
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
        except core.LinkValidationError as e:
            stats['rejected'] += 1
            stats['rejected_by_reason'][e.reason] = stats['rejected_by_reason'].get(e.reason, 0) + 1
            # Одной сводки «отброшено: 1» мало, чтобы понять, придирается ли
            # проверка или ключ и правда не для Podkop. Поэтому в лог идёт имя
            # ключа и то самое значение, на котором разбор встал.
            name = core.link_name(link)
            detail = f": {e.detail}" if e.detail else ''
            core.log('DEBUG', f"[{sec}]: ключ не для {core.TARGET['title']}"
                         + (f" ({name})" if name else '')
                         + f": {core.validation_reason_text(e.reason)}{detail}")

    if normalized_count:
        core.log('INFO', f"[{sec}]: для совместимости с {core.TARGET['title']} добавлен type=tcp в ключах: {normalized_count}")

    # Нормализация могла сделать несколько URI одинаковыми, поэтому повторно убираем дубли.
    formally_ok, normalized_dups = core.dedupe_links_keep_order(formally_ok)
    if normalized_dups:
        core.log('INFO', f"[{sec}]: после нормализации URI отброшено дублей: {normalized_dups}")

    stats['formal_ok'] = len(formally_ok)
    if stats['rejected']:
        core.log('WARN', f"[{sec}]: {core.TARGET['title']} не умеет собрать outbound из ключей: {stats['rejected']}")
        for reason, count in sorted(stats['rejected_by_reason'].items()):
            if reason == 'normalized_missing_transport':
                continue
            core.log('WARN', f"[{sec}]: {core.validation_reason_text(reason)}: {count}")

    if not formally_ok:
        stats['hard_check_ok'] = False
        return [], stats

    valid, hard = hard_validate_links_with_singbox(sec, formally_ok)
    stats['singbox_runs'] = int(hard.get('runs', 0) or 0)
    stats['hard_rejected'] = int(hard.get('rejected', 0) or 0)
    for reason, count in (hard.get('rejected_by_reason') or {}).items():
        stats['rejected_by_reason'][reason] = stats['rejected_by_reason'].get(reason, 0) + int(count or 0)
    stats['rejected'] += stats['hard_rejected']
    stats['hard_check_ok'] = bool(hard.get('ok')) and len(valid) > 0

    if hard.get('ok'):
        core.log('INFO', f"[{sec}]: проверка совместимости {core.TARGET['title']}/sing-box: принято {len(valid)}, отброшено {stats['rejected']}, sing-box check запусков: {stats['singbox_runs']}")
    else:
        core.log('WARN', f"[{sec}]: проверка совместимости {core.TARGET['title']}/sing-box не смогла собрать безопасный список; секция не будет изменена")
        for reason, count in sorted((hard.get('rejected_by_reason') or {}).items()):
            core.log('WARN', f"[{sec}]: {core.validation_reason_text(reason)}: {count}")

    return valid, stats
