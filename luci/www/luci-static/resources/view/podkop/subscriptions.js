"use strict";
"require form";
"require fs";
"require ui";
"require baseclass";

const LOCAL_LINKS = "/etc/config/podkop-local-links";

function notifyOutput(title, res) {
  let out = "";

  if (res && res.stdout)
    out += res.stdout;

  if (res && res.stderr)
    out += "\n" + res.stderr;

  ui.addNotification(null, E("pre", { style: "white-space: pre-wrap" }, out || title));
}

return baseclass.extend({
  createSubscriptionsContent: function(section, podkopSections) {
    section.anonymous = true;
    section.addremove = false;
    section.cfgsections = function() {
      return ["subscriptions_ui"];
    };

    let o, ss;

    o = section.option(
      form.SectionValue,
      "_subscription_groups",
      form.TypedSection,
      "subscription_group",
      _("Группы подписок"),
      _("Добавьте одну или несколько групп подписок. Секция Podkop выбирается из списка существующих секций.")
    );

    ss = o.subsection;
    ss.anonymous = true;
    ss.addremove = true;
    ss.sortable = true;
    ss.nodescriptions = false;

    o = ss.option(
      form.Flag,
      "enabled",
      _("Включено"),
      _("Отключённая группа остаётся в настройках, но updater её пропускает.")
    );
    o.default = "1";
    o.rmempty = false;

    o = ss.option(
      form.ListValue,
      "target_section",
      _("Секция Podkop"),
      _("Куда будут записаны ссылки после обновления. Сначала создайте секцию во вкладке «Секции».")
    );
    podkopSections.forEach(function(name) {
      o.value(name, name);
    });
    o.rmempty = false;

    o = ss.option(
      form.DynamicList,
      "source",
      _("Источники"),
      _("Ссылки подписок или локальные пути. Можно добавить несколько источников через плюс. Поддерживаются http://, https://, /etc/... и file:///etc/...")
    );
    o.placeholder = "https://example.org/sub?target=V2Ray";
    o.rmempty = false;

    o = ss.option(
      form.Value,
      "regex",
      _("Фильтр Regex"),
      _("Фильтр применяется ко всей proxy-ссылке. Например: russia|xhttp. Можно оставить пустым.")
    );
    o.placeholder = "russia|xhttp";
    o.rmempty = true;

    o = ss.option(
      form.ListValue,
      "match_mode",
      _("Режим фильтрации"),
      _("ifmatch — оставить только совпавшие. ifnotmatch — исключить совпавшие.")
    );
    o.value("ifmatch", "ifmatch");
    o.value("ifnotmatch", "ifnotmatch");
    o.default = "ifnotmatch";
    o.rmempty = false;

    o = ss.option(
      form.ListValue,
      "proxy_type",
      _("Тип прокси-группы"),
      _("urltest — автоматический выбор лучшего сервера. selector — ручной выбор.")
    );
    o.value("urltest", "urltest");
    o.value("selector", "selector");
    o.default = "urltest";
    o.rmempty = false;

    o = ss.option(
      form.ListValue,
      "on_empty",
      _("Если после фильтра пусто"),
      _("skip — пропустить источник. all — использовать все ссылки источника. Обычно безопаснее skip.")
    );
    o.value("skip", "skip");
    o.value("all", "all");
    o.default = "skip";
    o.rmempty = false;

    o = section.option(
      form.SectionValue,
      "_subscription_schedules",
      form.TypedSection,
      "subscription_schedule",
      _("Расписание обновлений"),
      _("Добавьте одно или несколько расписаний обновления. Например, отдельные записи для 03:00, 04:00 и 05:00.")
    );

    ss = o.subsection;
    ss.anonymous = true;
    ss.addremove = true;
    ss.sortable = true;
    ss.nodescriptions = false;

    o = ss.option(
      form.Flag,
      "enabled",
      _("Включено"),
      _("Включает или отключает это конкретное расписание.")
    );
    o.default = "1";
    o.rmempty = false;

    o = ss.option(
      form.Value,
      "hour",
      _("Час"),
      _("Час запуска по времени роутера. Значение от 0 до 23.")
    );
    o.datatype = "range(0,23)";
    o.placeholder = "3";
    o.rmempty = false;

    o = ss.option(
      form.Value,
      "minute",
      _("Минута"),
      _("Минута запуска. Значение от 0 до 59.")
    );
    o.datatype = "range(0,59)";
    o.placeholder = "0";
    o.default = "0";
    o.rmempty = false;

    o = ss.option(
      form.Value,
      "jitter",
      _("Случайная задержка, секунд"),
      _("Случайная задержка перед запуском. 1800 = до 30 минут.")
    );
    o.datatype = "uinteger";
    o.placeholder = "1800";
    o.default = "1800";
    o.rmempty = false;

    o = ss.option(
      form.Flag,
      "force",
      _("Принудительное обновление"),
      _("Запускать updater с --force. Обычно лучше выключить.")
    );
    o.default = "0";
    o.rmempty = false;

    o = section.option(
      form.SectionValue,
      "_local_links",
      form.TypedSection,
      "local_links",
      _("Локальные прокси-ссылки"),
      _("Редактирует файл /etc/config/podkop-local-links. Если файл изменён через SSH, обновите страницу.")
    );

    ss = o.subsection;
    ss.anonymous = true;
    ss.addremove = false;
    ss.cfgsections = function() {
      return ["local_links"];
    };

    o = ss.option(
      form.DynamicList,
      "_links",
      _("Ссылки"),
      _("vless://, ss://, trojan://, socks4/5://, hy2/hysteria2:// links")
    );
    o.placeholder = "vless://, ss://, trojan://, socks4/5://, hy2/hysteria2:// links";
    o.rmempty = true;

    o.cfgvalue = function() {
      return fs.read(LOCAL_LINKS).then(function(data) {
        return String(data || "")
          .split(/\r?\n/)
          .map(function(line) { return line.trim(); })
          .filter(function(line) { return line && line.charAt(0) !== "#"; });
      }).catch(function() {
        return [];
      });
    };

    o.write = function(section_id, value) {
      let lines = Array.isArray(value) ? value : (value ? [value] : []);

      lines = lines
        .map(function(line) { return String(line || "").trim(); })
        .filter(function(line) { return line; });

      return fs.write(
        LOCAL_LINKS,
        lines.length ? lines.join("\n") + "\n" : ""
      );
    };

    o.remove = function() {
      return fs.write(LOCAL_LINKS, "");
    };

    o = section.option(
      form.Button,
      "_run_now",
      _("Запустить обновление сейчас"),
      _("Сначала нажмите Save & Apply, затем используйте эту кнопку для проверки.")
    );
    o.inputtitle = _("Запустить updater");
    o.inputstyle = "reload";
    o.onclick = function() {
      return fs.exec("/usr/bin/podkop-sub-updater.py", [
        "--subs", "/etc/config/podkop",
        "--config", "/etc/config/podkop",
        "--force"
      ]).then(function(res) {
        notifyOutput("Updater finished", res);
      }).catch(function(err) {
        ui.addNotification(null, E("pre", { style: "white-space: pre-wrap" }, String(err)), "danger");
      });
    };
  }
});
