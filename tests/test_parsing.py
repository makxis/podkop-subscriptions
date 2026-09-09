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


def main():
    for test in (test_clash_matches_reference, test_xray_configs, test_trojan_and_ss,
                 test_plain_and_base64,
                 test_refusals, test_domain_expansion, test_fingerprint_headers):
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
