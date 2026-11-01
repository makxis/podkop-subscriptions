"""Поддержка конкретного ПО для podkop-sub-updater.py.

Ядро (podkop-sub-updater.py) загружает подписки, фильтрует ключи, ведёт
state и следит за живостью и про конкретное ПО ничего не знает. Всё, чем
Podkop, Podkop 0.8 и Tachyon отличаются, лежит в модуле этого ПО. Какой
модуль нужен, ядро решает само (detect_backend), а установщик ставит только
его и то, от чего он зависит (BACKEND_MODULES в ядре).

Модуль ПО обязан дать:
    NAME, TITLE        — имя модуля и название ПО для сообщений;
    CONFIG_PATH        — настоящий конфиг ПО, UCI_PACKAGE — его пакет uci;
    CLI, INIT_SCRIPT   — «<CLI> clash_api get_proxies» и чем перезапускать;
    configure(path)    — подготовить проверку ключей под установленную сборку;
    load_sections(path) -> {секция в нижнем регистре: {name, ptype, links, section}};
    validate_links(sec, links) -> (принятые ключи, статистика);
    rewrite_config(path, updates) -> (старый текст, новый текст);
    apply_uci(path, updates).

Общие помощники модули берут у ядра через атрибут core, его ставит
load_backend() сразу после импорта.
"""
