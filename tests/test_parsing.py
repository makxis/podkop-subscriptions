#!/usr/bin/env python3
"""Проверка разбора подписок на сохранённых образцах.

Запуск: python3 tests/test_parsing.py

Сети не требует и на роутер не ставится: это проверка для разработки. Живой
прогон updater отвечает на вопрос «работает ли сейчас», а этот тест — на
вопрос «не разошлось ли после правки».

Главная проверка — сверка с эталоном. Один и тот же список узлов снят в двух
формах: как JSON-объекты и как уже готовые ссылки, собранные зрелой открытой
реализацией. Вторая форма и есть эталон, с которым сравнивается наш разбор
первой. Образцы в fixtures/ обезличены: домены, UUID, пароли и ключи reality
заменены, структура сохранена.
"""
import base64
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, 'tests', 'fixtures')

spec = importlib.util.spec_from_file_location('updater', os.path.join(ROOT, 'podkop-sub-updater.py'))
u = importlib.util.module_from_spec(spec)
# Модули ПО находят ядро через sys.modules, как при обычном импорте.
sys.modules['updater'] = u
spec.loader.exec_module(u)
sb = u.load_backend('singbox')
pk = u.load_backend('podkop')
pkx = u.load_backend('podkop_exp')
ty = u.load_backend('tachyon')
# Модуль выбирается явно: тест не должен зависеть от того, какое ПО стоит
# на машине, где его запускают.
u.select_backend('/etc/config/podkop', 'podkop')

failures = []


def check(name, condition, detail=''):
    if condition:
        print(f'  ok   {name}')
    else:
        print(f'  FAIL {name}' + (f': {detail}' if detail else ''))
        failures.append(name)


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding='utf-8') as f:
        return f.read()


def parse_link(link):
    """(схема, хост:порт) -> параметры и имя. Порядок параметров не важен."""
    scheme, _, rest = link.partition('://')
    hostport = rest.split('?')[0].split('#')[0].split('@')[-1]
    query = rest.split('?', 1)[1].split('#')[0] if '?' in rest else ''
    params = {}
    for part in query.split('&'):
        if '=' in part:
            key, value = part.split('=', 1)
            params[key] = value
        elif part:
            params[part] = ''
    name = u.unquote_percent(rest.split('#', 1)[1]) if '#' in rest else ''
    return (scheme, hostport), params, name


def index(links):
    return {key: (params, name) for key, params, name in map(parse_link, links)}


def test_clash_matches_reference():
    """Разбор Clash-объектов должен совпасть с эталонными ссылками."""
    print('Clash JSON против эталонных ссылок')
    mine, fmt_mine = u.extract_links_from_payload(fixture('clash_json.txt'))
    ref, fmt_ref = u.extract_links_from_payload(fixture('clash_uri.txt'))

    check('формат JSON распознан', fmt_mine == 'json', fmt_mine)
    check('формат эталона распознан', fmt_ref == 'plain', fmt_ref)
    check('узлов столько же', len(mine) == len(ref), f'{len(mine)} против {len(ref)}')

    a, b = index(mine), index(ref)
    check('те же узлы', set(a) == set(b),
          f'лишние {sorted(set(a) - set(b))[:2]}, отсутствуют {sorted(set(b) - set(a))[:2]}')

    for key in sorted(set(a) & set(b)):
        params_mine, name_mine = a[key]
        params_ref, name_ref = b[key]
        check(f'{key[0]} {key[1]}: параметры', params_mine == params_ref,
              f'лишнее {({k: v for k, v in params_mine.items() if k not in params_ref})}, '
              f'нет {({k: v for k, v in params_ref.items() if k not in params_mine})}, '
              f'значения {({k: (params_mine[k], params_ref[k]) for k in params_mine if k in params_ref and params_mine[k] != params_ref[k]})}')
        check(f'{key[0]} {key[1]}: имя', name_mine == name_ref, f'{name_mine!r} против {name_ref!r}')


def test_xray_configs():
    """Массив полных конфигов Xray: имя узла лежит в remarks рядом с outbounds."""
    print('Конфиги Xray')
    links, fmt = u.extract_links_from_payload(fixture('xray_json.txt'))
    check('формат распознан', fmt == 'json', fmt)
    check('узлы разобраны', len(links) == 11, str(len(links)))
    check('все vless', all(l.startswith('vless://') for l in links))
    check('имена не потеряны', all('#' in l and u.unquote_percent(l.split('#', 1)[1]) for l in links))

    # Транспорт должен доехать таким же, каким лежал в конфиге. В образце их
    # три: ws, xhttp и tcp, — то есть проверка не вырождается в одну ветку.
    import collections
    import json
    configs = json.loads(fixture('xray_json.txt'))
    in_config = collections.Counter(
        ((u._find_proxy_outbound(c) or {}).get('streamSettings') or {}).get('network') or 'tcp'
        for c in configs)
    in_links = collections.Counter(
        part.split('=', 1)[1]
        for link in links
        for part in link.split('?', 1)[1].split('#')[0].split('&')
        if part.startswith('type='))
    check('транспорты не подменены', in_config == in_links, f'{dict(in_config)} против {dict(in_links)}')

    with_path = [l for l in links if 'path=' in l]
    check('путь сохранён у ws и xhttp',
          len(with_path) == in_config['ws'] + in_config['xhttp'], str(len(with_path)))


def test_xray_hysteria():
    """Hysteria в диалекте Xray лежит иначе остальных протоколов.

    Адрес и порт прямо в settings, ключ в streamSettings.hysteriaSettings,
    версия задаёт схему ссылки. Эта ветка была пропущена при переносе, и на
    реальной подписке терялось 11 узлов из 25 — снятый до того образец её не
    содержал, поэтому тест ничего не замечал.
    """
    print('Hysteria в конфигах Xray')
    links, fmt = u.extract_links_from_payload(fixture('xray_hysteria_json.txt'))
    check('формат распознан', fmt == 'json', fmt)
    check('узлы не потеряны', len(links) == 3, str(len(links)))

    hy = [l for l in links if l.startswith('hysteria2://')]
    check('hysteria собран как hysteria2', len(hy) == 1, str(len(hy)))
    if hy:
        _, params, name = parse_link(hy[0])
        check('hysteria: sni из tlsSettings', params.get('sni', '').endswith('example.net'),
              params.get('sni'))
        check('hysteria: alpn перенесён', params.get('alpn') == 'h3', params.get('alpn'))
        check('hysteria: имя из remarks', 'Hysteria' in name, name)
        check('hysteria: ключ в ссылке', '@' in hy[0] and hy[0].split('://')[1].split('@')[0])

    check('vless рядом не пострадал', len([l for l in links if l.startswith('vless://')]) == 2)


def test_trojan_and_ss():
    """Ветки trojan и ss.

    В снятом образце этих протоколов не оказалось, а правила у них свои: sni
    подставляется из адреса сервера, отказ от проверки сертификата называется
    allowInsecure, а не insecure, как у hysteria2. Образец собран вручную,
    ожидаемые значения выписаны по тем же правилам, что и остальной конвертер.
    """
    print('Trojan и Shadowsocks')
    links, fmt = u.extract_links_from_payload(fixture('clash_trojan_ss_json.txt'))
    check('формат распознан', fmt == 'json', fmt)
    check('разобраны все четыре', len(links) == 4, str(len(links)))

    by_scheme = {parse_link(l)[0]: parse_link(l) for l in links}

    key = ('trojan', 'node20.example.net:443')
    check('trojan ws найден', key in by_scheme)
    if key in by_scheme:
        _, params, name = by_scheme[key]
        check('trojan: sni из server, когда не задан', params.get('sni') == 'node20.example.net',
              params.get('sni'))
        check('trojan: skip-cert-verify это allowInsecure',
              params.get('allowInsecure') == '1' and 'insecure' not in params, str(params))
        check('trojan: транспорт и путь', params.get('type') == 'ws' and params.get('path') == '%2Ftj',
              str(params))
        check('trojan: host из заголовков', params.get('host') == 'node20.example.net', params.get('host'))
        check('trojan: alpn склеен', params.get('alpn') == 'h2%2Chttp%2F1.1', params.get('alpn'))
        check('trojan: fp из client-fingerprint', params.get('fp') == 'chrome', params.get('fp'))
        check('trojan: имя', name == 'Trojan WS', name)

    key = ('trojan', 'node21.example.net:8443')
    if key in by_scheme:
        _, params, _ = by_scheme[key]
        check('trojan без network: транспорта в ссылке нет', 'type' not in params, str(params))
        check('trojan: явный sni уважается', params.get('sni') == 'sni21.example.net', params.get('sni'))
        check('trojan без skip-cert-verify: allowInsecure нет', 'allowInsecure' not in params)

    key = ('vless', 'node23.example.net:443')
    if key in by_scheme:
        _, params, _ = by_scheme[key]
        # У grpc режим обязателен, и когда источник его не указал, подставляется
        # gun. Узел специально без _grpc-type, иначе умолчание не проверяется.
        check('grpc: режим по умолчанию gun', params.get('mode') == 'gun', params.get('mode'))
        check('grpc: serviceName перенесён', params.get('serviceName') == 'FixtureService',
              params.get('serviceName'))

    ss = [l for l in links if l.startswith('ss://')]
    check('ss собран', len(ss) == 1)
    if ss:
        userinfo = ss[0].split('://', 1)[1].split('@')[0]
        decoded = base64.urlsafe_b64decode(userinfo + '=' * (-len(userinfo) % 4)).decode()
        check('ss: метод и пароль в base64', decoded == 'aes-256-gcm:fixture-password', decoded)


def test_plain_and_base64():
    print('Прямые ссылки и base64')
    plain = ('vless://00000000-0000-4000-8000-000000000001@node01.example.net:443'
             '?encryption=none&security=tls&type=tcp#Узел\n')
    links, fmt = u.extract_links_from_payload(plain)
    check('plain распознан', fmt == 'plain' and len(links) == 1, fmt)

    encoded = base64.b64encode(plain.encode()).decode()
    links, fmt = u.extract_links_from_payload(encoded)
    check('base64 распознан', fmt == 'base64' and len(links) == 1, fmt)

    urlsafe = base64.urlsafe_b64encode(plain.encode()).decode().rstrip('=')
    links, fmt = u.extract_links_from_payload(urlsafe)
    check('url-safe base64 распознан', fmt == 'base64' and len(links) == 1, fmt)

    # Обычный текст не должен приниматься за base64.
    links, fmt = u.extract_links_from_payload('there are no links here at all\n')
    check('текст не принят за base64', fmt in ('empty', 'invalid') and not links, fmt)


def test_refusals():
    """Отказ панели приходит с кодом 200, поэтому распознаётся по содержимому."""
    print('Отказы панели')
    stub = '<html><head><title>403 Forbidden</title></head><body>403</body></html>'
    links, fmt = u.extract_links_from_payload(stub)
    check('заглушка антибота', fmt == 'blocked' and not links, fmt)

    page = ('<!DOCTYPE html>\n<html lang="ru"><head><title>Подписка</title></head>'
            '<body>Скачайте приложение</body></html>')
    links, fmt = u.extract_links_from_payload(page)
    check('витрина отличается от пустой подписки', fmt == 'html' and not links, fmt)

    placeholder = ('vless://00000000-0000-4000-8000-000000000001@0.0.0.0:1'
                   '?type=tcp&security=none#%D0%9B%D0%B8%D0%BC%D0%B8%D1%82\n')
    links, _ = u.extract_links_from_payload(placeholder)
    real, fake = u.split_placeholders(links)
    check('узел-пустышка отброшен', not real and len(fake) == 1, f'{len(real)}/{len(fake)}')
    check('причина читается', u.link_title(fake[0]) == 'Лимит', u.link_title(fake[0]))

    normal = 'vless://00000000-0000-4000-8000-000000000001@node01.example.net:443?type=tcp#Живой\n'
    links, _ = u.extract_links_from_payload(normal)
    real, fake = u.split_placeholders(links)
    check('рабочий узел не тронут', len(real) == 1 and not fake)


def test_domain_expansion():
    """Резолв подменяется: тест не должен зависеть от DNS и сети."""
    print('Разворачивание доменов в IP')
    original = u.resolve_ipv4
    try:
        u.resolve_ipv4 = lambda host, timeout=4: {
            'node01.example.net': ['198.51.100.7', '198.51.100.9'],
            'single.example.net': ['198.51.100.1'],
            'same.example.net': ['198.51.100.5', '203.0.113.5'],
        }.get(host, [])

        link = ('vless://00000000-0000-4000-8000-000000000001@node01.example.net:443'
                '?type=ws&path=%2Fws&host=node01.example.net#Узел')
        out = u.expand_domain_ips([link], 'тест')
        check('добавлено по ключу на адрес', len(out) == 3, str(len(out)))
        check('исходный доменный ключ остался', link in out)
        check('sni и host остались от домена',
              all('host=node01.example.net' in l for l in out))
        check('имена различимы',
              sorted(u.unquote_percent(l.split('#')[1]) for l in out) == ['Узел', 'Узел-7', 'Узел-9'])

        one = 'vless://00000000-0000-4000-8000-000000000001@single.example.net:443?type=tcp#Один'
        check('единственный адрес не разворачивается', u.expand_domain_ips([one], 'тест') == [one])

        ip_literal = 'vless://00000000-0000-4000-8000-000000000001@198.51.100.2:443?type=tcp#IP'
        check('готовый IP не трогается', u.expand_domain_ips([ip_literal], 'тест') == [ip_literal])

        # Последний октет совпадает — суффиксом должен стать адрес целиком.
        collide = 'vless://00000000-0000-4000-8000-000000000001@same.example.net:443?type=tcp#Имя'
        out = u.expand_domain_ips([collide], 'тест')
        names = sorted(u.unquote_percent(l.split('#')[1]) for l in out)
        check('одинаковые октеты не дают одинаковых имён',
              names == ['Имя', 'Имя-198.51.100.5', 'Имя-203.0.113.5'], str(names))
    finally:
        u.resolve_ipv4 = original


def test_fingerprint_headers():
    print('Разбор заголовков отпечатка')
    check('строка разбирается', u.parse_header_line('X-Device-OS: Android') == ('X-Device-OS', 'Android'))
    check('регистр сохраняется', u.parse_header_line('User-agent: v2raytun/android')[0] == 'User-agent')
    check('Host отбрасывается', u.parse_header_line('Host: example.net') is None)
    check('Accept-Encoding отбрасывается', u.parse_header_line('Accept-Encoding: gzip') is None)
    check('мусор отбрасывается', u.parse_header_line('без двоеточия') is None)
    check('HWID нужного формата', len(u.generate_hwid()) == 16
          and all(c in '0123456789ABCDEF' for c in u.generate_hwid()))


def test_podkop_conversion_limits():
    """Граница проходит по возможностям Podkop, а не sing-box.

    Ссылку в outbound превращает конвертер Podkop, поэтому проверка до
    sing-box check отсеивает ровно то, чего нет в его case-ах. Набор снят с
    /usr/lib/podkop/sing_box_config_facade.sh.
    """
    print('Что Podkop может собрать из ссылки')

    def reject_reason(link):
        try:
            sb.proxy_link_to_singbox_outbound(link, 'тест')
            return None
        except u.LinkValidationError as e:
            return (e.reason, e.detail)

    uuid = '00000000-0000-4000-8000-000000000001'
    ok = f'vless://{uuid}@node.example.net:443?type=ws&security=tls&sni=example.net&path=%2Fws#Узел'
    check('ws через vless проходит', reject_reason(ok) is None)

    grpc = f'vless://{uuid}@node.example.net:443?type=grpc&security=reality&pbk=key&sni=example.net#Узел'
    check('grpc с reality проходит', reject_reason(grpc) is None)

    # sing-box такой transport умеет, а конвертер Podkop про него не знает и
    # собрал бы обычный TCP, поэтому ключ отбраковывается до конвертации.
    # xhttp проверяется отдельно, в test_podkop_xhttp.
    upgrade = f'vless://{uuid}@node.example.net:443?type=httpupgrade&security=tls&sni=example.net#Узел'
    check('httpupgrade отбраковывается с указанием значения',
          reject_reason(upgrade) == ('unsupported_transport', 'httpupgrade'), str(reject_reason(upgrade)))

    # Неизвестная схема роняет podkop целиком, это самый дорогой случай.
    vmess = 'vmess://eyJhZGQiOiJub2RlLmV4YW1wbGUubmV0In0=#Узел'
    check('vmess отбраковывается с указанием схемы',
          reject_reason(vmess) == ('unsupported_scheme', 'vmess'), str(reject_reason(vmess)))

    tuic = f'tuic://{uuid}@node.example.net:443#Узел'
    check('tuic отбраковывается', reject_reason(tuic) == ('unsupported_scheme', 'tuic'))

    reality_no_key = f'vless://{uuid}@node.example.net:443?type=tcp&security=reality&sni=example.net#Узел'
    check('reality без pbk отбраковывается',
          (reject_reason(reality_no_key) or ('', ''))[0] == 'missing_reality_public_key')

    check('текст причины называет Podkop виновником ограничения',
          'Podkop' in u.validation_reason_text('unsupported_transport'))


def test_tachyon_conversion_limits():
    """У Tachyon свой конвертер ссылок, и xhttp зависит от сборки sing-box."""
    print('Что Tachyon может собрать из ссылки')

    def reject_reason(link):
        try:
            return None, sb.proxy_link_to_singbox_outbound(link, 'тест')
        except u.LinkValidationError as e:
            return e.reason, None

    uuid = '00000000-0000-4000-8000-000000000001'
    xhttp = f'vless://{uuid}@node.example.net:443?type=xhttp&security=tls&sni=example.net&path=%2Fx&mode=packet-up#Узел'
    upgrade = f'vless://{uuid}@node.example.net:443?type=httpupgrade&security=tls&sni=example.net&path=%2Fu#Узел'
    original = sb.sing_box_supports_xhttp
    try:
        sb.sing_box_supports_xhttp = lambda: False
        u.select_backend('/etc/config/tachyon', 'tachyon')
        check('без extended xhttp отбракован с понятной причиной',
              reject_reason(xhttp)[0] == 'xhttp_unsupported_singbox', str(reject_reason(xhttp)[0]))
        check('причина называет sing-box, а не Podkop',
              'sing-box' in u.validation_reason_text('xhttp_unsupported_singbox'))
        check('httpupgrade проходит и без extended', reject_reason(upgrade)[0] is None)
        check('сообщения называют Tachyon',
              'Tachyon' in u.validation_reason_text('unsupported_transport'))

        sb.sing_box_supports_xhttp = lambda: True
        u.select_backend('/etc/config/tachyon', 'tachyon')
        reason, outbound = reject_reason(xhttp)
        transport = (outbound or {}).get('transport') or {}
        check('с extended xhttp проходит', reason is None, str(reason))
        check('xhttp собран как у Tachyon',
              transport.get('type') == 'xhttp' and transport.get('mode') == 'packet-up'
              and transport.get('path') == '/x' and transport.get('host') == 'example.net', str(transport))
    finally:
        sb.sing_box_supports_xhttp = original
        u.select_backend('/etc/config/podkop', 'podkop')


def test_backend_modules():
    """Ядро выбирает модуль ПО, а без модуля не трогает конфиг."""
    import contextlib
    import io
    import json
    import tempfile
    print('Модули ПО')
    check('Tachyon по пути конфига', u.detect_backend('/etc/config/tachyon') == 'tachyon')
    required = ('NAME', 'TITLE', 'CONFIG_PATH', 'UCI_PACKAGE', 'CLI', 'INIT_SCRIPT', 'configure',
                'load_sections', 'validate_links', 'rewrite_config', 'apply_uci')
    for name in ('podkop', 'podkop_exp', 'tachyon'):
        module = u.load_backend(name)
        missing = [attr for attr in required if not hasattr(module, attr)]
        check(f'{name}: контракт модуля выполнен', not missing and module.NAME == name, str(missing))
        check(f'{name}: установщик знает его зависимости', name in u.BACKEND_MODULES
              and u.BACKEND_MODULES[name][-1] == name and 'singbox' in u.BACKEND_MODULES[name])
    try:
        u.load_backend('forkop')
        missing_raised = False
    except u.BackendMissing as e:
        missing_raised = e.name == 'forkop'
    check('нет модуля — BackendMissing', missing_raised)

    saved_argv, saved_detect = sys.argv, u.detect_backend
    with tempfile.TemporaryDirectory() as tmp:
        config = os.path.join(tmp, 'tachyon')
        subs = os.path.join(tmp, 'podkop_subscriptions')
        state = os.path.join(tmp, 'state.json')
        for path, text in ((config, "config section 'main'\n\tlist selector_proxy_links 'vless://a@h:1#A'\n"),
                           (subs, "config subscription_group 'main'\n\toption target_section 'main'\n")):
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
        try:
            out = io.StringIO()
            sys.argv = ['podkop-sub-updater.py', '--config', config, '--backend-modules']
            with contextlib.redirect_stdout(out):
                rc = u.main()
            check('--backend-modules называет модули Tachyon', rc == 0 and out.getvalue().split() == ['singbox', 'tachyon'],
                  out.getvalue())

            u.detect_backend = lambda path: 'forkop'
            sys.argv = ['podkop-sub-updater.py', '--config', config, '--subs', subs, '--state', state]
            rc = u.main()
            with open(state, encoding='utf-8') as f:
                meta = json.load(f).get('meta', {})
            with open(config, encoding='utf-8') as f:
                untouched = 'vless://a@h:1#A' in f.read()
            check('без модуля выход с кодом 3', rc == u.BACKEND_MISSING_EXIT_CODE, str(rc))
            check('панель увидит причину и команду', meta.get('last_subscription_status') == 'backend_missing'
                  and 'install.sh' in meta.get('last_subscription_message', ''), str(meta.get('last_subscription_status')))
            check('конфиг не тронут', untouched)
        finally:
            sys.argv, u.detect_backend = saved_argv, saved_detect
            u.select_backend('/etc/config/podkop', 'podkop')


def test_tachyon_core_engine():
    """Ядро Tachyon: tachyon-core вместо sing-box и xhttp по профилю ядра."""
    import stat
    import tempfile
    print('Ядро tachyon-core')
    saved_cache = dict(sb.SING_BOX_VERSION)
    saved_text = sb.sing_box_version_text
    try:
        for text, expected in (
                ('tachyon-core 0.2.9\n', True),
                ('sing-box version 1.13.21\n\nEnvironment: go1.26.10 linux/arm64\n', False),
                ('sing-box version 1.13.0-tachyon.4\n', True),
                ('sing-box version 1.14.1-extended-2\n', True),
                ('sing-box version 1.14.1-extended-compressed-2\n', True),
                ('sing-box version 1.14.2-lx.12\n', True),
                ('sing-box version 1.13.21\nTags: with_quic,with_xhttp\n', True),
                ('', False)):
            sb.sing_box_version_text = lambda text=text: text
            got = sb.sing_box_supports_xhttp()
            check(f'xhttp для «{text.splitlines()[0] if text else "нет ответа"}»: {expected}', got == expected, str(got))
    finally:
        sb.sing_box_version_text = saved_text

    old_path = os.environ.get('PATH', '')
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, 'calls')
        core = os.path.join(tmp, 'tachyon-core')
        with open(core, 'w') as f:
            f.write('#!/bin/sh\necho "$0 $*" >> ' + log + '\n'
                    '[ "$1" = version ] && echo "tachyon-core 0.2.9"\nexit 0\n')
        os.chmod(core, stat.S_IRWXU)
        try:
            # Без системных каталогов: на роутере с Tachyon tachyon-core лежит в /usr/bin.
            os.environ['PATH'] = os.path.join(tmp, 'empty')
            check('без tachyon-core в PATH берётся sing-box', ty.tachyon_core_binary() == 'sing-box')
            os.environ['PATH'] = tmp + os.pathsep + old_path
            sb.SING_BOX_VERSION.clear()
            u.select_backend('/etc/config/tachyon')
            check('Tachyon проверяет ключи через tachyon-core', sb.core_binary() == 'tachyon-core', sb.core_binary())
            check('на tachyon-core xhttp разрешён', 'xhttp' in sb.PROFILE['transports'])
            ok, reason, _ = sb.run_singbox_check_for_links(
                ['vless://00000000-0000-4000-8000-000000000001@node.example.net:443?type=tcp#A'])
            with open(log) as f:
                calls = f.read()
            check('sing-box check запущен у tachyon-core', ok and ' -c ' in calls and calls.rstrip().endswith('check'),
                  calls + reason)
            u.select_backend('/etc/config/podkop', 'podkop')
            check('Podkop по-прежнему проверяет через sing-box', sb.core_binary() == 'sing-box')
        finally:
            os.environ['PATH'] = old_path
            u.select_backend('/etc/config/podkop', 'podkop')
            sb.SING_BOX_VERSION.clear()
            sb.SING_BOX_VERSION.update(saved_cache)


def test_podkop_xhttp():
    """xhttp Podkop 0.7.23 отдаёт sing-box decode-link, и только с podkop-engine.

    Без движка или на ключе, который decode-link не принял, Podkop выходит с
    fatal, поэтому такие ключи не должны доходить до конфига.
    """
    print('xhttp в Podkop')
    import tempfile

    uuid = '00000000-0000-4000-8000-000000000001'
    base = f'vless://{uuid}@node.example.net:443?security=tls&sni=example.net'
    xhttp = base + '&type=xhttp&path=%2Fx#Узел'
    split = 'trojan://secret@node.example.net:443?type=splithttp&security=tls#Узел'
    vision = base + '&type=xhttp&flow=xtls-rprx-vision#Узел'
    upper = base + '&type=XHTTP#Узел'
    last_wins = base + '&type=ws&type=xhttp#Узел'
    in_name = base + '&type=ws#type=xhttp'

    calls = []

    def fake_decode(link):
        calls.append(link)
        if 'flow=' in link:
            return None, 'decode_link_failed', 'flow xtls-rprx-vision is not supported over the xhttp transport'
        return {'type': 'vless', 'tag': 'имя из ссылки', 'transport': {'type': 'xhttp'}}, '', ''

    def outcome(link):
        try:
            return None, sb.proxy_link_to_singbox_outbound(link, 'тест')
        except u.LinkValidationError as e:
            return (e.reason, e.detail), None

    def setup(facade_text, version_text):
        with open(facade, 'w', encoding='utf-8') as f:
            f.write(facade_text)
        sb.sing_box_version_text = lambda: version_text
        pk.PODKOP_XHTTP.clear()
        pk.DECODED_LINKS.clear()
        del calls[:]

    old_facade = 'case "$transport" in\nws) ;;\ngrpc) ;;\nesac\n'
    new_facade = ('xhttp | splithttp)\n    _add_decoded_proxy_outbound "$config" "$section" "$url"\n'
                  'outbound=$(sing-box tools decode-link --compact "$url" 2> "$messages")\n')
    stock = 'sing-box version 1.12.22\n\nEnvironment: go1.24\nTags: with_quic,with_utls\n'
    engine = ('sing-box version 1.13.21-pdk-r12\n\nTags: with_quic,with_utls,podkop_slim\n'
              'Features: urltest.fallbacks,urltest.download_url,transport.xhttp,tools.decode-link\n')

    saved = (pk.PODKOP_FACADE_PATH, sb.sing_box_version_text, pk._run_decode_link)
    with tempfile.TemporaryDirectory() as tmp:
        facade = os.path.join(tmp, 'sing_box_config_facade.sh')
        try:
            pk.PODKOP_FACADE_PATH = facade
            pk._run_decode_link = fake_decode

            setup(old_facade, engine)
            check('Podkop до 0.7.23 xhttp не собирает',
                  outcome(xhttp)[0] == ('xhttp_unsupported_podkop', 'xhttp'), str(outcome(xhttp)[0]))

            setup(new_facade, stock)
            check('без podkop-engine ключ отбракован',
                  outcome(split)[0] == ('xhttp_needs_podkop_engine', 'splithttp'), str(outcome(split)[0]))
            check('и до decode-link не дошёл', not calls, str(calls))
            check('причина называет podkop-engine',
                  'podkop-engine' in u.validation_reason_text('xhttp_needs_podkop_engine'))

            setup(new_facade, engine)
            reason, outbound = outcome(xhttp)
            check('с podkop-engine xhttp проходит', reason is None, str(reason))
            check('outbound взят у decode-link, тег наш',
                  outbound == {'type': 'vless', 'tag': 'тест', 'transport': {'type': 'xhttp'}}, str(outbound))
            outcome(xhttp)
            check('decode-link запускается один раз на ключ', calls.count(xhttp) == 1, str(len(calls)))
            check('splithttp тоже идёт через decode-link', outcome(split)[0] is None and split in calls)
            check('ключ, который decode-link не принял, отбракован',
                  (outcome(vision)[0] or ('',))[0] == 'decode_link_failed')
            check('type=XHTTP Podkop понесёт в свой конвертер, поэтому отбракован',
                  outcome(upper)[0] == ('unsupported_transport', 'xhttp'), str(outcome(upper)[0]))
            check('решает последний type=, как у Podkop',
                  outcome(last_wins)[0] is None and last_wins in calls)
            check('type= в имени ключа не считается', outcome(in_name)[0] is None and in_name not in calls)

            u.select_backend('/etc/config/tachyon', 'tachyon')
            del calls[:]
            outcome(xhttp)
            check('для Tachyon decode-link не используется', not calls, str(calls))
        finally:
            u.select_backend('/etc/config/podkop', 'podkop')
            pk.PODKOP_FACADE_PATH, sb.sing_box_version_text, pk._run_decode_link = saved
            pk.PODKOP_XHTTP.clear()
            pk.DECODED_LINKS.clear()


def test_decode_link_runner():
    """Разбор ответа decode-link: код возврата, JSON, error: и warning:."""
    print('Запуск sing-box decode-link')
    import stat
    import tempfile

    script = '''#!/bin/sh
[ "$1 $2 $3" = "tools decode-link --compact" ] || exit 1
case "$4" in
*flow=*) echo "error: flow xtls-rprx-vision is not supported over the xhttp transport" >&2; exit 2 ;;
*broken*) echo "not json"; exit 0 ;;
*ech=*) echo "warning: ech is not supported and is ignored" >&2 ;;
esac
echo '{"type":"vless","tag":"x","server":"node.example.net","transport":{"type":"xhttp"}}'
'''
    old_path = os.environ.get('PATH', '')
    with tempfile.TemporaryDirectory() as tmp:
        binary = os.path.join(tmp, 'sing-box')
        with open(binary, 'w') as f:
            f.write(script)
        os.chmod(binary, stat.S_IRWXU)
        os.environ['PATH'] = tmp + os.pathsep + old_path
        try:
            link = 'vless://00000000-0000-4000-8000-000000000001@node.example.net:443?type=xhttp'
            outbound, reason, _ = pk._run_decode_link(link + '#ok')
            check('outbound разобран', reason == '' and outbound.get('transport') == {'type': 'xhttp'},
                  str((outbound, reason)))
            outbound, reason, _ = pk._run_decode_link(link + '&ech=AEX#warn')
            check('warning ключ не отбраковывает', reason == '' and outbound.get('type') == 'vless')
            outbound, reason, detail = pk._run_decode_link(link + '&flow=xtls-rprx-vision#bad')
            check('код 2 отбраковывает ключ с текстом ошибки',
                  outbound is None and reason == 'decode_link_failed'
                  and detail == 'flow xtls-rprx-vision is not supported over the xhttp transport', detail)
            outbound, reason, _ = pk._run_decode_link(link + '#broken')
            check('мусор вместо JSON отбраковывает ключ', outbound is None and reason == 'decode_link_failed')
        finally:
            os.environ['PATH'] = old_path


def test_xray_xhttp_extra():
    """Настройки xhttp из конфига Xray доезжают до ссылки в параметре extra."""
    print('extra у xhttp из конфига Xray')
    import json

    def link_for(xhttp_settings):
        config = {'remarks': 'Узел', 'outbounds': [{
            'tag': 'proxy', 'protocol': 'vless',
            'settings': {'vnext': [{'address': 'node.example.net', 'port': 443,
                                    'users': [{'id': '00000000-0000-4000-8000-000000000001'}]}]},
            'streamSettings': {'network': 'xhttp', 'security': 'tls', 'xhttpSettings': xhttp_settings}}]}
        return u.xray_outbound_to_uri(config, config['outbounds'][0])

    def extra_of(link):
        _key, params, _name = parse_link(link)
        return json.loads(u.unquote_percent(params['extra'])) if 'extra' in params else None

    nested = {'path': '/x', 'mode': 'packet-up', 'xPaddingBytes': '1-2',
              'extra': {'xPaddingBytes': '100-1000', 'downloadSettings': {'address': 'down.example.net', 'port': 443}}}
    check('extra из конфига идёт как есть',
          extra_of(link_for(nested)) == nested['extra'], str(extra_of(link_for(nested))))
    flat = {'path': '/x', 'host': 'cdn.example.net', 'mode': 'auto', 'xPaddingBytes': '100-1000',
            'noGRPCHeader': False, 'headers': {}}
    check('без extra идут поля верхнего уровня, кроме host, path, mode и пустых',
          extra_of(link_for(flat)) == {'xPaddingBytes': '100-1000', 'noGRPCHeader': False},
          str(extra_of(link_for(flat))))
    check('без лишних настроек extra нет', extra_of(link_for({'path': '/x', 'mode': 'auto', 'host': ''})) is None)


def test_singbox_check_position():
    print('Поиск битого ключа по ответу sing-box check')
    decode = 'FATAL[0000] decode config at /tmp/x.json: outbounds[3].transport: unknown transport type: bogus'
    init = 'FATAL[0000] initialize outbound[1]: unknown method: rc4-nonsense'
    dup = 'FATAL[0000] decode config at /tmp/x.json: duplicate outbound/endpoint tag: podkop-sub-test-2'

    check('позиция из decode', sb.parse_singbox_check_position(decode, 5) == 3)
    check('позиция из initialize', sb.parse_singbox_check_position(init, 5) == 1)
    check('позиция из тега', sb.parse_singbox_check_position(dup, 5) == 1)
    check('позиция вне списка отбрасывается', sb.parse_singbox_check_position(decode, 2) is None)
    check('без позиции None', sb.parse_singbox_check_position('FATAL: something went wrong', 5) is None)
    check('пустой ответ', sb.parse_singbox_check_position('', 5) is None)


def test_singbox_hard_validation():
    print('Отбраковка ключей через sing-box check')
    links = ['vless://00000000-0000-4000-8000-00000000000%d@node%d.example.net:443?type=tcp#Узел-%d' % (i, i, i)
             for i in range(1, 9)]
    bad = {links[2], links[6]}
    original = sb.run_singbox_check_for_links

    def fake_check(batch, timeout=None):
        """sing-box падает на первом неподходящем ключе и называет его позицию."""
        for idx, link in enumerate(batch):
            if link in bad:
                return False, 'singbox_check_failed', idx
        return True, '', None

    def blind_check(batch, timeout=None):
        """Тот же ответ, но без позиции: остаётся деление пополам."""
        ok, reason, _position = fake_check(batch)
        return ok, reason, None

    try:
        sb.run_singbox_check_for_links = fake_check
        good, stats = sb.hard_validate_links_with_singbox('тест', links)
        check('битые ключи отброшены', good == [l for l in links if l not in bad], str(len(good)))
        check('порядок сохранён', good == sorted(good, key=links.index))
        check('запусков на один больше, чем битых ключей', stats['runs'] == len(bad) + 1, str(stats['runs']))
        check('счётчик отброшенных верен', stats['rejected'] == len(bad))
        check('проверка признана успешной', stats['ok'] is True)

        sb.run_singbox_check_for_links = blind_check
        good_blind, stats_blind = sb.hard_validate_links_with_singbox('тест', links)
        check('без позиции результат тот же', good_blind == good, str(len(good_blind)))
        check('без позиции запусков больше', stats_blind['runs'] > stats['runs'],
              f"{stats_blind['runs']} vs {stats['runs']}")

        # Потолок запусков: битые ключи кончаются позже, чем разрешённые запуски.
        sb.run_singbox_check_for_links = lambda batch, timeout=None: (False, 'singbox_check_failed', 0)
        good_limit, stats_limit = sb.hard_validate_links_with_singbox('тест', links, max_runs=2)
        check('при исчерпании лимита секция не меняется', good_limit == [])
        check('причина названа', 'singbox_check_limit' in stats_limit['rejected_by_reason'])
        check('лимит не превышен', stats_limit['runs'] <= 2, str(stats_limit['runs']))

        sb.run_singbox_check_for_links = lambda batch, timeout=None: (False, 'singbox_not_found', None)
        good_missing, stats_missing = sb.hard_validate_links_with_singbox('тест', links)
        check('без sing-box список не принимается', good_missing == [])
        check('отсутствие sing-box названо причиной',
              'singbox_not_found' in stats_missing['rejected_by_reason'])

        sb.run_singbox_check_for_links = lambda batch, timeout=None: (True, '', None)
        good_all, stats_all = sb.hard_validate_links_with_singbox('тест', links)
        check('чистый список проходит за один запуск', good_all == links and stats_all['runs'] == 1,
              str(stats_all['runs']))
        check('пустой список не запускает sing-box',
              sb.hard_validate_links_with_singbox('тест', [])[1]['runs'] == 0)
    finally:
        sb.run_singbox_check_for_links = original


def test_uci_values():
    print('Значения UCI')
    check('одинарные кавычки', u.parse_uci_value("'abc'") == 'abc')
    check('апостроф в записи uci', u.parse_uci_value("'a'\\''b'") == "a'b",
          u.parse_uci_value("'a'\\''b'"))
    check('двойные кавычки с экранированием', u.parse_uci_value('"a\\"b"') == 'a"b')
    check('без кавычек', u.parse_uci_value('abc') == 'abc')
    check('пробел внутри кавычек', u.parse_uci_value("'a b'") == 'a b')
    check('решётка внутри кавычек', u.parse_uci_value("'x|#.*(YT)'") == 'x|#.*(YT)')
    check('пустое значение', u.parse_uci_value("''") == '')
    for value in ("Joe's #1", "a'b'c", "''", 'plain', "vless://x@h:1?a=b#На'звание"):
        check(f'туда и обратно: {value}', u.parse_uci_value(u.uci_quote(value)) == value,
              u.parse_uci_value(u.uci_quote(value)))


def test_uci_open_quote():
    print('Незакрытая кавычка в строке UCI')
    cases = [
        ("list x 'abc'", None, None),
        ("list x 'a'\\''b'", None, None),
        ("list x 'Tom'\\''s VPN'", None, None),
        ("option proxy_string 'vless://a", None, "'"),
        ("vless://b'", "'", None),
        ("still inside", "'", "'"),
        ("x'\\''y'", "'", None),
        ('option x "a\\"b"', None, None),
        ('option x "a', None, '"'),
        ("option x 'a' # it's", None, None),
        ("option x 'a#b'", None, None),
        ("option x a\\'b", None, None),
    ]
    for text, start, expected in cases:
        got = u.uci_open_quote(text, start)
        check(f'{text!r} (с {start!r})', got == expected, repr(got))


def test_tachyon_modular():
    """Tachyon 1.7: ключи в своём outbound_source, первым в ссылках секции."""
    import tempfile
    print('Tachyon 1.7: источники и балансировщики')
    a = 'vless://00000000-0000-4000-8000-00000000000a@a.example.net:443?type=tcp#A'
    b = 'vless://00000000-0000-4000-8000-00000000000b@b.example.net:443?type=tcp#B'
    c = "vless://00000000-0000-4000-8000-00000000000c@c.example.net:443?type=tcp#Joe's"
    z = 'vless://00000000-0000-4000-8000-00000000000f@z.example.net:443?type=tcp#Z'
    user = 'vless://00000000-0000-4000-8000-0000000000aa@u.example.net:443?type=tcp#U'
    # Как после миграции 1.6 -> 1.7: ключи и в секции, и в копии src_links_main.
    config = (
        "config settings 'settings'\n"
        "\tlist applied_migrations 'interface_sections'\n"
        "\tlist applied_migrations 'modular_entities_v1'\n"
        "\n"
        "config section 'main'\n"
        "\toption action 'connection'\n"
        f"\tlist selector_proxy_links '{a}'\n"
        f"\tlist selector_proxy_links '{b}'\n"
        "\tlist condition_refs 'cond_main'\n"
        "\tlist outbound_source_refs 'src_links_main'\n"
        "\tlist balancer_refs 'bal_main_Fastest'\n"
        "\n"
        "config section 'sec2'\n"
        "\toption action 'connection'\n"
        "\tlist outbound_source_refs 'src_user'\n"
        "\tlist balancer_refs 'bal_user'\n"
        "\n"
        "config outbound_source 'src_links_main'\n"
        "\toption enabled '1'\n"
        "\toption name 'main (Links)'\n"
        "\toption source_type 'proxy_links'\n"
        f"\tlist proxy_links '{a}'\n"
        f"\tlist proxy_links '{z}'\n"
        "\n"
        "config outbound_source 'src_user'\n"
        "\toption source_type 'proxy_links'\n"
        f"\tlist proxy_links '{user}'\n"
        "\n"
        "config balancer 'bal_main_Fastest'\n"
        "\toption enabled '1'\n"
        "\toption balancer_type 'urltest'\n"
        "\toption name 'Fastest'\n"
        "\n"
        "config balancer 'bal_user'\n"
        "\toption balancer_type 'priority'\n"
        "\tlist source_refs 'src_user'\n"
    )
    logged = []
    saved_log = u.log
    try:
        u.log = lambda level, message: logged.append((level, message))
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'tachyon')
            with open(path, 'w', encoding='utf-8') as f:
                f.write(config)
            sections = ty.load_sections(path)
            check('схема 1.7 распознана', ty.modular_schema(u.parse_uci_sections(path)))
            check('ключи из секции считаются нашими до переезда', sections['main']['links'] == [a, b],
                  str(sections['main']['links']))
            check('секция просит переписать её под 1.7', sections['main']['relayout'] and sections['sec2']['relayout'])
            check('URLTest узнаётся по балансировщику', sections['main']['ptype'] == 'urltest')

            updates = {'main': {'ptype': 'urltest', 'links': [a, c], 'name': 'main'},
                       'sec2': {'ptype': 'urltest', 'links': [b], 'name': 'sec2'}}
            old, new = ty.rewrite_config(path, updates)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new)
            parsed = {(s['type'], s['name']): s for s in u.parse_uci_sections(path)}
            main, sec2 = parsed[('section', 'main')], parsed[('section', 'sec2')]
            check('в секции больше нет своих ключей', 'selector_proxy_links' not in main['lists'], str(main['lists']))
            check('наш источник первым, копия миграции после',
                  main['lists'].get('outbound_source_refs') == ['src_psub_main', 'src_links_main'],
                  str(main['lists'].get('outbound_source_refs')))
            check('чужие ссылки секции сохранены', main['lists'].get('condition_refs') == ['cond_main']
                  and main['lists'].get('balancer_refs') == ['bal_main_Fastest'])
            src = parsed.get(('outbound_source', 'src_psub_main'))
            check('источник создан с ключами и именем', src is not None
                  and src['lists'].get('proxy_links') == [a, c] and src['options'].get('source_type') == 'proxy_links'
                  and src['options'].get('name') == 'main (podkop-subscriptions)', str(src))
            check('копия миграции не тронута', parsed[('outbound_source', 'src_links_main')]['lists']['proxy_links'] == [a, z])
            check('предупреждение про ключ вне подписки в копии',
                  any(level == 'WARN' and 'вне подписки: 1' in msg for level, msg in logged), str(logged))
            check('для sec2 создан URLTest-балансировщик',
                  parsed.get(('balancer', 'bal_psub_sec2'), {}).get('options', {}).get('balancer_type') == 'urltest'
                  and sec2['lists'].get('balancer_refs') == ['bal_user', 'bal_psub_sec2'], str(sec2['lists']))
            check('ограниченный балансировщик видит и наш источник',
                  parsed[('balancer', 'bal_user')]['lists'].get('source_refs') == ['src_user', 'src_psub_sec2'])
            check('ссылки sec2: наш источник первым',
                  sec2['lists'].get('outbound_source_refs') == ['src_psub_sec2', 'src_user'])

            again = ty.load_sections(path)
            check('после записи ключи читаются из источника', again['main']['links'] == [a, c], str(again['main']['links']))
            check('переписывать больше не нужно', not again['main']['relayout'] and not again['sec2']['relayout'])
            check('наши ключи первые, смещения нет', again['main']['tag_offset'] == 0)
            _, repeat = ty.rewrite_config(path, updates)
            check('повторная запись ничего не меняет', repeat == new)

            # Пользователь переставил наш источник ниже в панели: номера сдвигаются.
            moved = new.replace("\tlist outbound_source_refs 'src_psub_sec2'\n\tlist outbound_source_refs 'src_user'\n",
                                "\tlist outbound_source_refs 'src_user'\n\tlist outbound_source_refs 'src_psub_sec2'\n")
            with open(path, 'w', encoding='utf-8') as f:
                f.write(moved)
            shifted = ty.load_sections(path)['sec2']
            check('смещение тегов на ключи источника перед нашим', shifted['tag_offset'] == 1 and shifted['relayout'],
                  str(shifted.get('tag_offset')))
            snap = u.proxy_snapshot_for_links('sec2', [b], {'sec2-2-out': {'history': [{'delay': 120}]}},
                                              shifted['tag_offset'])
            check('health-check берёт тег со смещением', snap[u.stable_id(b)]['tag'] == 'sec2-2-out'
                  and snap[u.stable_id(b)]['status'] == 'ok', str(snap))

            with open(path, 'w', encoding='utf-8') as f:
                f.write(new)
            commands = ty._uci_commands_modular(ty._index(u.parse_uci_sections(path)))('main', updates['main'])
            check('uci повторяет ссылки секции', ['add_list', 'tachyon.main.outbound_source_refs=src_psub_main'] in commands
                  and ['-q', 'delete', 'tachyon.main.selector_proxy_links'] in commands)
            check('uci повторяет источник', ['set', 'tachyon.src_psub_main=outbound_source'] in commands
                  and ['add_list', f'tachyon.src_psub_main.proxy_links={c}'] in commands, str(commands[-3:]))
    finally:
        u.log = saved_log


def test_rewrite_keeps_neighbours():
    """Вырезая старые ключи секции, нельзя задеть её остальные опции."""
    import tempfile
    print('Перезапись ключей секции')
    old_a = 'vless://00000000-0000-4000-8000-00000000000a@a.example.net:443#Tom%27s'
    upd = {'main': {'ptype': 'urltest', 'name': 'main', 'links': [
        "vless://00000000-0000-4000-8000-00000000000c@c.example.net:443#Joe's",
        'vless://00000000-0000-4000-8000-00000000000d@d.example.net:443#D']}}
    podkop = (
        "config section 'main'\n"
        "\toption connection_type 'proxy'\n"
        "\toption proxy_config_type 'urltest'\n"
        f"\tlist urltest_proxy_links '{old_a}'\n"
        "\tlist urltest_proxy_links 'vless://00000000-0000-4000-8000-00000000000b@b.example.net:443#Tom'\\''s'\n"
        "\toption domain_list 'ru'\n"
        "\toption proxy_string 'vless://x@x.example.net:443#first\n"
        "vless://y@y.example.net:443#Ann'\\''s\n"
        "vless://z@z.example.net:443#last'\n"
        "\tlist community_lists 'youtube'\n"
        "\n"
        "config settings 'settings'\n"
        "\toption dns_server '1.1.1.1'\n"
    )
    tachyon = (
        "config section 'main'\n"
        "\toption action 'connection'\n"
        "\tlist selector_proxy_links 'vless://00000000-0000-4000-8000-00000000000b@b.example.net:443#Tom'\\''s'\n"
        "\toption domain_list 'ru'\n"
        "\n"
        "config urltest\n"
        "\toption section 'main'\n"
    )
    for name, list_key, text, kept in (
            ('podkop', 'urltest_proxy_links', podkop, ("option domain_list 'ru'", "list community_lists 'youtube'",
                                "config settings 'settings'", "option dns_server '1.1.1.1'")),
            ('tachyon', 'selector_proxy_links', tachyon, ("option domain_list 'ru'", "config urltest", "option section 'main'"))):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, name)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(text)
            _, new = u.load_backend(name).rewrite_config(path, upd)
            for line in kept:
                check(f'{name}: осталось «{line}»', line in new, new)
            check(f'{name}: старые ключи и proxy_string убраны',
                  'b.example.net' not in new and 'a.example.net' not in new
                  and 'proxy_string' not in new and 'z.example.net' not in new, new)
            check(f'{name}: новые ключи записаны с экранированным апострофом',
                  "c.example.net:443#Joe'\\''s'" in new and 'd.example.net' in new, new)
            sections = {s['name']: s for s in u.parse_uci_sections(path)}
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new)
            after = {s['name']: s for s in u.parse_uci_sections(path)}
            check(f'{name}: разбор после перезаписи видит те же ключи',
                  after['main']['lists'].get(list_key) == upd['main']['links'],
                  str(after['main']['lists']))
            check(f'{name}: опции секции не потерялись',
                  after['main']['options'].get('domain_list') == 'ru'
                  and set(sections) == set(after), str(after['main']['options']))


def test_regex_filter():
    import subprocess
    import tempfile
    print('Regex-фильтр')
    links = ['vless://a@a.example.net:443#RU%20Moscow', 'vless://b@b.example.net:443#NL']
    rx, err = u.compile_filter_regex('ru')
    check('корректный regex собран', rx is not None and err == '', err)
    check('совпадение без учёта регистра и по раскодированному имени',
          u.filter_links(links, rx, 'ifmatch', 'skip', 'main', 'источник') == links[:1])
    check('ifnotmatch', u.filter_links(links, rx, 'ifnotmatch', 'skip', 'main', 'источник') == links[1:])
    check('пустой фильтр пропускает всё',
          u.compile_filter_regex('') == (None, '')
          and u.filter_links(links, None, 'ifmatch', 'skip', 'main', 'источник') == links)
    rx, err = u.compile_filter_regex('(RU|NL')
    check('битый regex распознан', rx is None and err, err)

    config = (
        "config subscription_group 'bad'\n"
        "\toption target_section 'main'\n"
        "\toption regex '(RU|NL'\n"
        "\toption match_mode 'ifmatch'\n"
        "\tlist source 'https://sub.example.net/a'\n"
        "\n"
        "config subscription_group 'good'\n"
        "\toption target_section 'other'\n"
        "\toption regex 'nl'\n"
        "\tlist source 'https://sub.example.net/b'\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'podkop_subscriptions')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(config)
        jobs = u.load_jobs_from_uci_podkop(path)
    bad = jobs['main']['entries'][0]
    good = jobs['other']['entries'][0]
    check('группа с битым regex не выброшена, ошибка запомнена',
          bad['regex'] is None and bad['regex_error'], str(bad))
    check('соседняя группа собрана как обычно',
          good['regex'] is not None and good['regex_error'] == '', str(good))

    fetched = []
    original = u.fetch_source_trying_fingerprints
    try:
        def fake_fetch(source, *args, **kwargs):
            fetched.append(source)
            raise subprocess.TimeoutExpired(source, 1)
        u.fetch_source_trying_fingerprints = fake_fetch
        only_bad = {'main': jobs['main']}
        u.fetch_links(only_bad, 'hwid', 'model', 'kernel', config_path='/nonexistent/podkop')
    finally:
        u.fetch_source_trying_fingerprints = original
    check('источник группы с битым regex не скачивается', fetched == [], str(fetched))
    check('статус источника invalid_regex',
          [st['status'] for st in jobs['main']['source_stats']] == ['invalid_regex'],
          str(jobs['main']['source_stats']))
    check('общий статус invalid_regex', u.classify_empty_status(only_bad) == 'invalid_regex',
          u.classify_empty_status(only_bad))
    mixed = {'main': jobs['main'], 'other': {'source_stats': [{'status': 'download_failed'}],
                                             'source_errors': 1}}
    check('упавшая загрузка соседа не прячется за invalid_regex',
          u.classify_empty_status(mixed) == 'download_failed', u.classify_empty_status(mixed))


def test_write_config_atomic():
    import stat
    import tempfile
    print('Запись конфига')
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'tachyon')
        with open(path, 'w', encoding='utf-8') as f:
            f.write('old\n')
        os.chmod(path, 0o600)
        u.write_config_atomic(path, 'new\n')
        with open(path, encoding='utf-8') as f:
            check('содержимое заменено', f.read() == 'new\n')
        check('права 0600 сохранены', stat.S_IMODE(os.stat(path).st_mode) == 0o600,
              oct(os.stat(path).st_mode))
        check('временного файла не осталось', os.listdir(tmp) == ['tachyon'], str(os.listdir(tmp)))

        original = u.os.replace
        try:
            def broken_replace(src, dst):
                raise OSError(28, 'No space left on device')
            u.os.replace = broken_replace
            try:
                u.write_config_atomic(path, 'newer\n')
                raised = False
            except OSError:
                raised = True
        finally:
            u.os.replace = original
        check('ошибка записи пробрасывается', raised)
        with open(path, encoding='utf-8') as f:
            check('при ошибке конфиг не тронут', f.read() == 'new\n')
        check('при ошибке временный файл удалён', os.listdir(tmp) == ['tachyon'], str(os.listdir(tmp)))


def test_tags_follow_podkop():
    """Теги Podkop: имя секции как есть и номер по списку с дублями."""
    import json
    import tempfile
    print('Теги outbound и чистка state')
    a = 'vless://00000000-0000-4000-8000-00000000000a@a.example.net:443?type=tcp#A'
    b = 'vless://00000000-0000-4000-8000-00000000000b@b.example.net:443?type=tcp#B'
    c = 'vless://00000000-0000-4000-8000-00000000000c@c.example.net:443?type=tcp#C'
    config = (
        "config section 'YouTube'\n"
        "\toption connection_type 'proxy'\n"
        "\toption proxy_config_type 'urltest'\n"
        f"\tlist urltest_proxy_links '{a}'\n"
        f"\tlist urltest_proxy_links '{b}'\n"
        f"\tlist urltest_proxy_links '{a}'\n"
        f"\tlist urltest_proxy_links '{c}'\n"
    )
    proxies = {
        'YouTube-1-out': {'history': [{'delay': 100}]},
        'YouTube-2-out': {'history': [{'delay': 0}]},
        'YouTube-4-out': {'history': [{'delay': 150}]},
    }
    stale = {'url': 'vless://gone', 'name': 'gone', 'fail_count': 5}
    original = u.load_podkop_proxies
    try:
        u.load_podkop_proxies = lambda path: proxies
        with tempfile.TemporaryDirectory() as tmp:
            config_path = os.path.join(tmp, 'podkop')
            state_path = os.path.join(tmp, 'state.json')
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(config)
            with open(state_path, 'w', encoding='utf-8') as f:
                json.dump({'sections': {'youtube': {'links': {'deadbeef': stale}},
                                        'removed_section': {'links': {'x': stale}}}}, f)

            u.select_backend(config_path, 'podkop')
            sections = u.BACKEND.load_sections(config_path)
            check('имя секции сохранено как в конфиге', sections['youtube']['name'] == 'YouTube')

            u.observe_only(config_path, state_path)
            with open(state_path, encoding='utf-8') as f:
                state = json.load(f)
            links = state['sections']['youtube']['links']
            by_name = {item['name']: item for item in links.values()}
            check('рабочий ключ найден по тегу с регистром', by_name['A']['last_status'] == 'ok',
                  by_name['A'].get('last_tag'))
            check('нерабочий ключ отмечен', by_name['B']['fail_count'] == 1)
            check('ключ после дубля взял свой номер', by_name['C']['last_tag'] == 'YouTube-4-out'
                  and by_name['C']['last_status'] == 'ok', by_name['C'].get('last_tag'))
            check('ключ, которого нет в конфиге, убран из state', 'deadbeef' not in links)
            check('секция, которой нет в конфиге, убрана из state',
                  'removed_section' not in state['sections'])

            snap = u.proxy_snapshot_for_links('YouTube', sections['youtube']['links'], proxies)
            check('снимок для отсеивателя по тем же тегам',
                  snap[u.stable_id(c)]['tag'] == 'YouTube-4-out', snap[u.stable_id(c)]['tag'])
    finally:
        u.load_podkop_proxies = original

    state = {'sections': {'main': {'links': {'x': stale}}}}
    check('пустой разбор конфига ничего не удаляет',
          u.prune_state_to_config(state, {}) == 0 and 'x' in state['sections']['main']['links'])


def test_expand_budget():
    print('Лимит времени на DNS')
    original_resolve, original_budget = u.resolve_ipv4, u.EXPAND_RESOLVE_BUDGET_SECONDS
    try:
        calls = []
        u.resolve_ipv4 = lambda host, timeout=4: calls.append(host) or ['198.51.100.7', '198.51.100.9']
        u.EXPAND_RESOLVE_BUDGET_SECONDS = -1
        link = 'vless://00000000-0000-4000-8000-000000000001@node01.example.net:443?type=tcp#Узел'
        out = u.expand_domain_ips([link], 'тест')
        check('после лимита DNS не опрашивается', calls == [] and out == [link], str(calls))
    finally:
        u.resolve_ipv4, u.EXPAND_RESOLVE_BUDGET_SECONDS = original_resolve, original_budget


def test_podkop_check_links():
    """Podkop 0.8 (бинарник) проверяет ключи сам; у 0.7 (скрипт) всё по-старому."""
    print('Podkop 0.8: podkop check_links')
    import stat
    import tempfile

    # Отвечает как podkop-exp 0.8.4: JSON-массив по ключу на аргумент.
    script = '''#!/bin/sh
[ "$1" = check_links ] || exit 1
shift
printf '['
sep=
for link; do
  case "$link" in
  *type=foo*) printf '%s{"ok":false,"error":"unsupported transport: foo"}' "$sep" ;;
  *) printf '%s{"ok":true}' "$sep" ;;
  esac
  sep=,
done
printf ']\\n'
'''
    uuid = '00000000-0000-4000-8000-000000000001'
    xhttp = f'vless://{uuid}@node.example.net:443?type=xhttp&security=tls#x'
    upgrade = f'vless://{uuid}@node.example.net:443?type=httpupgrade&security=tls#hu'
    bad = f'vless://{uuid}@node.example.net:443?type=foo#bad'
    saved = (u.PODKOP_BINARY_PATH, pkx.CHECK_LINKS_BATCH_BYTES)
    with tempfile.TemporaryDirectory() as tmp:
        binary = os.path.join(tmp, 'podkop')
        try:
            u.PODKOP_BINARY_PATH = binary
            check('без /usr/bin/podkop check_links не включается', not u.podkop_is_backend_binary())
            with open(binary, 'w') as f:
                f.write(script)
            os.chmod(binary, stat.S_IRWXU)
            check('скрипт Podkop 0.7 не считается бинарником', not u.podkop_is_backend_binary())
            check('Podkop 0.7 определяется как podkop', u.detect_backend('/etc/config/podkop') == 'podkop')

            # Скрипт «#!» нужен только для подмены ответа; признак 0.8 — его отсутствие.
            u.podkop_is_backend_binary = lambda: True
            u.select_backend('/etc/config/podkop')
            check('Podkop 0.8 определяется как podkop_exp', u.BACKEND is pkx, u.BACKEND.NAME)
            pkx.CHECK_LINKS_BATCH_BYTES = 120
            valid, stats = u.BACKEND.validate_links('main', [xhttp, upgrade, bad, xhttp])
            check('xhttp и httpupgrade приняты, битый отброшен, дубль убран',
                  valid == [xhttp, upgrade], str(valid))
            check('статистика', stats['rejected'] == 1 and stats['hard_check_ok']
                  and stats['rejected_by_reason'] == {'check_links_rejected': 1}, str(stats))
            check('длинный список идёт пачками', stats['singbox_runs'] >= 2, str(stats['singbox_runs']))

            with open(binary, 'w') as f:
                f.write('#!/bin/sh\necho "unknown command" >&2\nexit 1\n')
            check('без check_links ответа нет', pkx.run_podkop_check_links([xhttp]) is None)
        finally:
            u.PODKOP_BINARY_PATH, pkx.CHECK_LINKS_BATCH_BYTES = saved[0], saved[1]
            del u.podkop_is_backend_binary
            u.select_backend('/etc/config/podkop', 'podkop')


def _vl(host, port=443, uid='u', name='', **q):
    query = '&'.join(f'{k}={v}' for k, v in q.items())
    return f'vless://{uid}@{host}:{port}?{query or "security=none"}#{name or host}'


def _run_section(groups, current, incoming, link_group, delays=None, fails=None, state=None,
                 protected=None, recent=None, min_keep=0, sec='main', protection_known=True):
    """Один прогон build_final_links_for_section на синтетических данных.

    delays — {ссылка: ping или None (N/A)}, fails — {ссылка: fail_count}.
    """
    job = {'ptype': 'urltest', 'groups': groups, 'links': list(incoming),
           'link_group': {u.stable_id(x): g for x, g in link_group.items()},
           'external_valid_count': max(1, len(incoming)), 'source_errors': 0}
    state = state if state is not None else {'sections': {}}
    st = u.ensure_state_section(state, sec, 'urltest')
    for link, fc in (fails or {}).items():
        st['links'].setdefault(u.stable_id(link), {'url': link})['fail_count'] = fc
    proxies = {}
    for idx, link in enumerate(current, 1):
        d = (delays or {}).get(link, 100)
        proxies[f'{sec}-{idx}-out'] = {'history': [{'delay': d if d is not None else 0}]}
    cur = {sec: {'ptype': 'urltest', 'links': list(current)}}
    final, info = u.build_final_links_for_section(sec, job, cur, state, 72, min_keep,
                                                  set(protected or ()), proxies, set(recent or ()),
                                                  protection_known=protection_known)
    return final, info, state


def _grp(key, max_links=0, ping=0, force=False, sni=False, ep=False):
    return {'key': key, 'max_links': max_links, 'max_latency_ms': ping, 'force_cleanup': force,
            'dedupe_sni_rotation': sni, 'dedupe_endpoint_host': ep}


def test_group_policies():
    import tempfile
    print('Правила групп в одной секции')
    pub = _grp('pub', max_links=3, ping=300)
    own = _grp('own')
    p_old = [_vl(f'p{i}.example.net') for i in range(3)]
    o_old = [_vl(f'o{i}.example.net') for i in range(2)]
    p_new = [_vl(f'pn{i}.example.net') for i in range(5)]
    o_new = [_vl('o9.example.net')]
    current = p_old + o_old
    # Публичные медленные, свои ещё медленнее и с плохой историей.
    delays = {p_old[0]: 400, p_old[1]: 450, p_old[2]: 100, o_old[0]: 900, o_old[1]: None}
    fails = {o_old[0]: 1, o_old[1]: 5, p_old[1]: 1}
    incoming = p_old + p_new + o_old + o_new
    lg = {x: 'pub' for x in p_old + p_new}
    lg.update({x: 'own' for x in o_old + o_new})
    final, info, state = _run_section([pub, own], current, incoming, lg, delays, fails)
    ids = {u.stable_id(x) for x in final}
    check('свои ключи не удалены ни по ping, ни по fail_count, ни по лимиту чужой группы',
          all(u.stable_id(x) in ids for x in o_old), str(info['removed_by_reason']))
    check('новый свой ключ добавлен, хотя лимит публичной группы занят',
          u.stable_id(o_new[0]) in ids)
    pub_left = [x for x in final if u.stable_id(x) in {u.stable_id(y) for y in p_old + p_new}]
    check('публичная группа ужата до своего лимита 3', len(pub_left) == 3, str(len(pub_left)))
    check('медленные публичные ключи удалены',
          u.stable_id(p_old[0]) not in ids and u.stable_id(p_old[1]) not in ids, str(info['removed_by_reason']))
    groups_in_state = {v.get('group') for v in state['sections']['main']['links'].values()
                       if v.get('url') and u.stable_id(v['url']) in ids}
    check('хозяин записан в state', groups_in_state == {'pub', 'own'}, str(groups_in_state))

    # Второй прогон: подписка своих серверов не ответила, хозяина берём из state.
    final2, info2, _ = _run_section([pub, own], final, [x for x in incoming if lg[x] == 'pub'],
                                    {x: g for x, g in lg.items() if g == 'pub'},
                                    {x: 2000 for x in final}, {x: 3 for x in final}, state=state)
    ids2 = {u.stable_id(x) for x in final2}
    check('своя подписка не ответила — свои ключи всё равно не тронуты',
          all(u.stable_id(x) in ids2 for x in o_old + o_new), str(info2['removed_by_reason']))

    # Старый state без хозяев: ключ из подписки своих узнаётся по этому запуску.
    final3, info3, _ = _run_section([pub, own], current, incoming, lg, delays, fails)
    check('state без group: свои ключи узнаны по подписке',
          all(u.stable_id(x) in {u.stable_id(y) for y in final3} for x in o_old))

    # Ключ без хозяина (нет ни в подписке, ни в state) идёт под строгую группу.
    stray = _vl('stray.example.net')
    final4, info4, _ = _run_section([pub, own], [stray] + o_old, o_old + p_new,
                                    {**{x: 'own' for x in o_old}, **{x: 'pub' for x in p_new}},
                                    {stray: 900})
    check('бесхозный медленный ключ чистится по правилам строгой группы',
          u.stable_id(stray) not in {u.stable_id(x) for x in final4}, str(info4['removed_by_reason']))

    # Один узел в обеих группах достаётся мягкой.
    both = _vl('both.example.net')
    lg5 = {both: 'own'}
    final5, _, _ = _run_section([pub, own], [both] + p_old, p_old + p_new + [both],
                                {**{x: 'pub' for x in p_old + p_new}, **lg5}, {both: 900, **delays})
    check('общий узел под мягкой группой не удалён', u.stable_id(both) in {u.stable_id(x) for x in final5})

    # Схлопывание IP:порт у публичной группы не задевает свои ключи на одном IP:443.
    pub_ep = _grp('pub', max_links=10, ep=True, sni=True)
    own_same = [_vl('1.2.3.4', uid=f'id{i}', sni=f's{i}.ru') for i in range(3)]
    pub_same = [_vl('5.6.7.8', uid=f'pid{i}') for i in range(3)]
    final6, info6, _ = _run_section([pub_ep, own], [], own_same + pub_same,
                                    {**{x: 'own' for x in own_same}, **{x: 'pub' for x in pub_same}})
    ids6 = {u.stable_id(x) for x in final6}
    check('свои ключи на одном IP:порт не схлопнуты', all(u.stable_id(x) in ids6 for x in own_same))
    check('публичные на одном IP:порт схлопнуты до одного',
          sum(1 for x in pub_same if u.stable_id(x) in ids6) == 1, str(info6))

    # SNI-ротация работает внутри своей группы.
    pub_sni = _grp('pub', sni=True)
    old_r = _vl('9.9.9.9', sni='a.ru')
    new_r = _vl('9.9.9.9', sni='b.ru')
    final7, info7, _ = _run_section([pub_sni, own], [old_r], [new_r], {new_r: 'pub'},
                                    state={'sections': {'main': {'links': {u.stable_id(old_r): {'url': old_r, 'group': 'pub'}}}}})
    check('SNI-ротация внутри группы заменяет ключ',
          [u.stable_id(x) for x in final7] == [u.stable_id(new_r)], str(info7['removed_by_reason']))
    final8, _, _ = _run_section([pub_sni, own], [old_r], [new_r], {new_r: 'pub'},
                                state={'sections': {'main': {'links': {u.stable_id(old_r): {'url': old_r, 'group': 'own'}}}}})
    check('SNI-ротация публичной группы не вытесняет свой ключ',
          u.stable_id(old_r) in {u.stable_id(x) for x in final8})

    # force_cleanup одной группы не действует на другую.
    pub_force = _grp('pub', ping=300, force=True)
    final9, _, _ = _run_section([pub_force, own], p_old + o_old, p_old + o_old,
                                {**{x: 'pub' for x in p_old}, **{x: 'own' for x in o_old}},
                                {x: 900 for x in p_old + o_old}, {x: 3 for x in p_old + o_old})
    ids9 = {u.stable_id(x) for x in final9}
    check('force_cleanup чистит только свою группу',
          all(u.stable_id(x) in ids9 for x in o_old) and not any(u.stable_id(x) in ids9 for x in p_old))

    # Мягкая чистка «долго не работает» общая для всех групп.
    final10, info10, _ = _run_section([pub, own], o_old, o_old, {x: 'own' for x in o_old},
                                      fails={o_old[0]: 72})
    check('fail_count>=72 удаляет и свой ключ', u.stable_id(o_old[0]) not in {u.stable_id(x) for x in final10},
          str(info10['removed_by_reason']))

    # min_keep действует на секцию целиком.
    final11, _, _ = _run_section([pub_force, own], p_old, p_old, {x: 'pub' for x in p_old},
                                 {x: 900 for x in p_old}, {x: 3 for x in p_old}, min_keep=2)
    check('min_keep сохраняет ключи секции', len(final11) == 2, str(len(final11)))

    # Защищённые локальные ключи не удаляются и занимают место своей группы.
    loc = [_vl(f'l{i}.example.net') for i in range(3)]
    final12, info12, _ = _run_section([pub, own], loc, loc + p_new, {**{x: 'pub' for x in loc + p_new}},
                                      {x: 900 for x in loc}, protected={u.stable_id(x) for x in loc})
    ids12 = {u.stable_id(x) for x in final12}
    check('локальные ключи не удалены и заняли лимит группы',
          all(u.stable_id(x) in ids12 for x in loc) and len(final12) == 3, str(info12))

    # Одна группа: поведение прежнее (ping чистит только при переполнении).
    single = [_grp('pub', max_links=10, ping=300)]
    final13, _, _ = _run_section(single, p_old, p_old, {x: 'pub' for x in p_old}, {x: 900 for x in p_old})
    check('одна группа без переполнения: по ping не чистится', len(final13) == 3)
    single_tight = [_grp('pub', max_links=2, ping=300)]
    final14, info14, _ = _run_section(single_tight, p_old, p_old, {x: 'pub' for x in p_old},
                                      {p_old[0]: 100, p_old[1]: 200, p_old[2]: 250})
    check('одна группа сверх лимита: убран самый медленный',
          len(final14) == 2 and u.stable_id(p_old[2]) not in {u.stable_id(x) for x in final14}, str(info14))

    # Старый плоский файл без групп.
    flat = {'ptype': 'urltest', 'links': p_new, 'max_links': 2, 'external_valid_count': 5}
    cur = {'main': {'ptype': 'urltest', 'links': []}}
    final15, _ = u.build_final_links_for_section('main', flat, cur, {'sections': {}}, 72, 0, set(), {}, set())
    check('плоский файл: лимит секции как раньше', len(final15) == 2)

    # Недавно удалённые пропускаются один раз в любой группе.
    final16, info16, _ = _run_section([pub, own], [], o_new, {o_new[0]: 'own'},
                                      recent={u.stable_id(o_new[0])})
    check('недавно удалённый свой ключ пропущен один раз', final16 == [] and info16['skipped_recent'] == 1)

    # Загрузка групп из UCI: у каждой своя политика, у источников пометка группы.
    config = (
        "config subscription_group\n"
        "\toption target_section 'main'\n"
        "\toption max_links '150'\n"
        "\toption max_latency_ms '300'\n"
        "\toption dedupe_endpoint_host '1'\n"
        "\tlist source 'https://pub.example.net/a'\n"
        "\n"
        "config subscription_group\n"
        "\toption enabled '0'\n"
        "\toption target_section 'main'\n"
        "\toption max_links '23'\n"
        "\tlist source 'https://pub.example.net/xhttp'\n"
        "\n"
        "config subscription_group 'mine'\n"
        "\toption target_section 'main'\n"
        "\tlist source 'https://own.example.net/a'\n"
        "\tlist source 'https://own.example.net/b'\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'podkop_subscriptions')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(config)
        jobs = u.load_jobs_from_uci_podkop(path)
    job = jobs['main']
    keys = [g['key'] for g in job['groups']]
    check('отключённая группа не попала в правила', keys == ['группа 1', 'mine'], str(keys))
    check('политики групп раздельные',
          job['groups'][0]['max_links'] == 150 and job['groups'][0]['max_latency_ms'] == 300
          and job['groups'][0]['dedupe_endpoint_host']
          and job['groups'][1]['max_links'] == 0 and job['groups'][1]['max_latency_ms'] == 0
          and not job['groups'][1]['dedupe_endpoint_host'], str(job['groups']))
    check('источники помечены группой',
          [e['group'] for e in job['entries']] == ['группа 1', 'mine', 'mine'])

    # fetch_links раскладывает ключи по группам, общий узел отдаёт мягкой.
    shared = _vl('shared.example.net')
    payloads = {
        'https://pub.example.net/a': [_vl('a.example.net'), shared],
        'https://own.example.net/a': [shared, _vl('mine.example.net')],
        'https://own.example.net/b': [_vl('mine2.example.net')],
    }
    original = u.fetch_source_trying_fingerprints
    try:
        def fake_fetch(source, *args, **kwargs):
            return '', list(payloads[source]), 'plain', ''
        u.fetch_source_trying_fingerprints = fake_fetch
        for e in job['entries']:
            e['expand_ips'] = False
        u.fetch_links({'main': job}, 'hwid', 'model', 'kernel', config_path='/nonexistent/podkop')
    finally:
        u.fetch_source_trying_fingerprints = original
    lgm = job['link_group']
    check('ключ публичной подписки — её группе', lgm.get(u.stable_id(_vl('a.example.net'))) == 'группа 1')
    check('свои ключи — своей группе',
          lgm.get(u.stable_id(_vl('mine.example.net'))) == 'mine'
          and lgm.get(u.stable_id(_vl('mine2.example.net'))) == 'mine')
    check('общий узел — мягкой группе', lgm.get(u.stable_id(shared)) == 'mine', str(lgm))


def test_local_protection_follows_list():
    print('Защита локальных ключей повторяет локальный список')
    own = _grp('own')
    loc = _vl('loc.example.net')
    sub = [_vl(f's{i}.example.net') for i in range(2)]
    sid = u.stable_id(loc)

    def marked_state():
        st = {'sections': {}}
        u.ensure_state_section(st, 'main', 'urltest')['links'][sid] = {'url': loc, 'protected_local': True}
        return st

    # Ключ в списке: защищён, fail_count его не трогает.
    final1, _, st1 = _run_section([own], [loc] + sub, sub, {x: 'own' for x in sub},
                                  fails={loc: 72}, state=marked_state(), protected={sid})
    check('ключ из списка защищён', loc in final1 and st1['sections']['main']['links'][sid].get('protected_local'))

    # Список выключили или ключ из него убрали: пометка снята, мёртвый ключ удалён.
    final2, info2, st2 = _run_section([own], [loc] + sub, sub, {x: 'own' for x in sub},
                                      fails={loc: 72}, state=marked_state())
    check('пометка снята', not st2['sections']['main']['links'].get(sid, {}).get('protected_local'))
    check('бывший локальный ключ чистится по fail_count', loc not in final2, str(info2.get('removed_by_reason')))

    # Список не прочитался: пометку не снимаем, ключ остаётся.
    final3, _, st3 = _run_section([own], [loc] + sub, sub, {x: 'own' for x in sub},
                                  fails={loc: 72}, state=marked_state(), protection_known=False)
    check('при сбое чтения защита сохранена',
          loc in final3 and st3['sections']['main']['links'][sid].get('protected_local'))

    # Защита действует только там, где группа берёт локальный список.
    path = u.LOCAL_LINKS_PATH_DEFAULT
    check('секция без локального списка', not u.job_uses_local_links({'entries': [{'source': 'https://x/sub'}]}))
    check('секция с локальным списком', u.job_uses_local_links({'entries': [{'source': 'file://' + path}]}))


def main():
    for test in (test_clash_matches_reference, test_xray_configs, test_xray_hysteria, test_trojan_and_ss,
                 test_plain_and_base64,
                 test_refusals, test_domain_expansion, test_fingerprint_headers,
                 test_podkop_conversion_limits, test_tachyon_conversion_limits,
                 test_backend_modules, test_tachyon_core_engine, test_podkop_xhttp, test_decode_link_runner, test_xray_xhttp_extra,
                 test_singbox_check_position, test_singbox_hard_validation,
                 test_uci_values, test_uci_open_quote, test_tachyon_modular, test_rewrite_keeps_neighbours,
                 test_regex_filter, test_write_config_atomic,
                 test_tags_follow_podkop, test_expand_budget,
                 test_podkop_check_links, test_group_policies, test_local_protection_follows_list):
        test()
        print()
    if failures:
        print(f'ПРОВАЛЕНО проверок: {len(failures)}')
        for name in failures:
            print(f'  - {name}')
        return 1
    print('Все проверки пройдены')
    return 0


if __name__ == '__main__':
    sys.exit(main())
