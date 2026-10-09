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
spec.loader.exec_module(u)

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
            u.proxy_link_to_singbox_outbound(link, 'тест')
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
            return None, u.proxy_link_to_singbox_outbound(link, 'тест')
        except u.LinkValidationError as e:
            return e.reason, None

    uuid = '00000000-0000-4000-8000-000000000001'
    xhttp = f'vless://{uuid}@node.example.net:443?type=xhttp&security=tls&sni=example.net&path=%2Fx&mode=packet-up#Узел'
    upgrade = f'vless://{uuid}@node.example.net:443?type=httpupgrade&security=tls&sni=example.net&path=%2Fu#Узел'
    saved = dict(u.TARGET)
    original = u.sing_box_supports_xhttp
    try:
        u.sing_box_supports_xhttp = lambda: False
        u.set_link_target('/etc/config/tachyon')
        check('без extended xhttp отбракован с понятной причиной',
              reject_reason(xhttp)[0] == 'xhttp_unsupported_singbox', str(reject_reason(xhttp)[0]))
        check('причина называет sing-box, а не Podkop',
              'sing-box' in u.validation_reason_text('xhttp_unsupported_singbox'))
        check('httpupgrade проходит и без extended', reject_reason(upgrade)[0] is None)
        check('сообщения называют Tachyon',
              'Tachyon' in u.validation_reason_text('unsupported_transport'))

        u.sing_box_supports_xhttp = lambda: True
        u.set_link_target('/etc/config/tachyon')
        reason, outbound = reject_reason(xhttp)
        transport = (outbound or {}).get('transport') or {}
        check('с extended xhttp проходит', reason is None, str(reason))
        check('xhttp собран как у Tachyon',
              transport.get('type') == 'xhttp' and transport.get('mode') == 'packet-up'
              and transport.get('path') == '/x' and transport.get('host') == 'example.net', str(transport))
    finally:
        u.sing_box_supports_xhttp = original
        u.TARGET.clear()
        u.TARGET.update(saved)


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
            return None, u.proxy_link_to_singbox_outbound(link, 'тест')
        except u.LinkValidationError as e:
            return (e.reason, e.detail), None

    def setup(facade_text, version_text):
        with open(facade, 'w', encoding='utf-8') as f:
            f.write(facade_text)
        u.sing_box_version_text = lambda: version_text
        u.PODKOP_XHTTP.clear()
        u.DECODED_LINKS.clear()
        del calls[:]

    old_facade = 'case "$transport" in\nws) ;;\ngrpc) ;;\nesac\n'
    new_facade = ('xhttp | splithttp)\n    _add_decoded_proxy_outbound "$config" "$section" "$url"\n'
                  'outbound=$(sing-box tools decode-link --compact "$url" 2> "$messages")\n')
    stock = 'sing-box version 1.12.22\n\nEnvironment: go1.24\nTags: with_quic,with_utls\n'
    engine = ('sing-box version 1.13.21-pdk-r12\n\nTags: with_quic,with_utls,podkop_slim\n'
              'Features: urltest.fallbacks,urltest.download_url,transport.xhttp,tools.decode-link\n')

    saved = (u.PODKOP_FACADE_PATH, u.sing_box_version_text, u._run_decode_link)
    with tempfile.TemporaryDirectory() as tmp:
        facade = os.path.join(tmp, 'sing_box_config_facade.sh')
        try:
            u.PODKOP_FACADE_PATH = facade
            u._run_decode_link = fake_decode

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

            u.TARGET['title'] = 'Tachyon'
            del calls[:]
            outcome(xhttp)
            check('для Tachyon decode-link не используется', not calls, str(calls))
        finally:
            u.TARGET['title'] = 'Podkop'
            u.PODKOP_FACADE_PATH, u.sing_box_version_text, u._run_decode_link = saved
            u.PODKOP_XHTTP.clear()
            u.DECODED_LINKS.clear()


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
            outbound, reason, _ = u._run_decode_link(link + '#ok')
            check('outbound разобран', reason == '' and outbound.get('transport') == {'type': 'xhttp'},
                  str((outbound, reason)))
            outbound, reason, _ = u._run_decode_link(link + '&ech=AEX#warn')
            check('warning ключ не отбраковывает', reason == '' and outbound.get('type') == 'vless')
            outbound, reason, detail = u._run_decode_link(link + '&flow=xtls-rprx-vision#bad')
            check('код 2 отбраковывает ключ с текстом ошибки',
                  outbound is None and reason == 'decode_link_failed'
                  and detail == 'flow xtls-rprx-vision is not supported over the xhttp transport', detail)
            outbound, reason, _ = u._run_decode_link(link + '#broken')
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

    check('позиция из decode', u.parse_singbox_check_position(decode, 5) == 3)
    check('позиция из initialize', u.parse_singbox_check_position(init, 5) == 1)
    check('позиция из тега', u.parse_singbox_check_position(dup, 5) == 1)
    check('позиция вне списка отбрасывается', u.parse_singbox_check_position(decode, 2) is None)
    check('без позиции None', u.parse_singbox_check_position('FATAL: something went wrong', 5) is None)
    check('пустой ответ', u.parse_singbox_check_position('', 5) is None)


def test_singbox_hard_validation():
    print('Отбраковка ключей через sing-box check')
    links = ['vless://00000000-0000-4000-8000-00000000000%d@node%d.example.net:443?type=tcp#Узел-%d' % (i, i, i)
             for i in range(1, 9)]
    bad = {links[2], links[6]}
    original = u.run_singbox_check_for_links

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
        u.run_singbox_check_for_links = fake_check
        good, stats = u.hard_validate_links_with_singbox('тест', links)
        check('битые ключи отброшены', good == [l for l in links if l not in bad], str(len(good)))
        check('порядок сохранён', good == sorted(good, key=links.index))
        check('запусков на один больше, чем битых ключей', stats['runs'] == len(bad) + 1, str(stats['runs']))
        check('счётчик отброшенных верен', stats['rejected'] == len(bad))
        check('проверка признана успешной', stats['ok'] is True)

        u.run_singbox_check_for_links = blind_check
        good_blind, stats_blind = u.hard_validate_links_with_singbox('тест', links)
        check('без позиции результат тот же', good_blind == good, str(len(good_blind)))
        check('без позиции запусков больше', stats_blind['runs'] > stats['runs'],
              f"{stats_blind['runs']} vs {stats['runs']}")

        # Потолок запусков: битые ключи кончаются позже, чем разрешённые запуски.
        u.run_singbox_check_for_links = lambda batch, timeout=None: (False, 'singbox_check_failed', 0)
        good_limit, stats_limit = u.hard_validate_links_with_singbox('тест', links, max_runs=2)
        check('при исчерпании лимита секция не меняется', good_limit == [])
        check('причина названа', 'singbox_check_limit' in stats_limit['rejected_by_reason'])
        check('лимит не превышен', stats_limit['runs'] <= 2, str(stats_limit['runs']))

        u.run_singbox_check_for_links = lambda batch, timeout=None: (False, 'singbox_not_found', None)
        good_missing, stats_missing = u.hard_validate_links_with_singbox('тест', links)
        check('без sing-box список не принимается', good_missing == [])
        check('отсутствие sing-box названо причиной',
              'singbox_not_found' in stats_missing['rejected_by_reason'])

        u.run_singbox_check_for_links = lambda batch, timeout=None: (True, '', None)
        good_all, stats_all = u.hard_validate_links_with_singbox('тест', links)
        check('чистый список проходит за один запуск', good_all == links and stats_all['runs'] == 1,
              str(stats_all['runs']))
        check('пустой список не запускает sing-box',
              u.hard_validate_links_with_singbox('тест', [])[1]['runs'] == 0)
    finally:
        u.run_singbox_check_for_links = original


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

            sections = u.load_current_podkop_sections(config_path)
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


def main():
    for test in (test_clash_matches_reference, test_xray_configs, test_xray_hysteria, test_trojan_and_ss,
                 test_plain_and_base64,
                 test_refusals, test_domain_expansion, test_fingerprint_headers,
                 test_podkop_conversion_limits, test_tachyon_conversion_limits,
                 test_podkop_xhttp, test_decode_link_runner, test_xray_xhttp_extra,
                 test_singbox_check_position, test_singbox_hard_validation,
                 test_uci_values, test_tags_follow_podkop, test_expand_budget):
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
