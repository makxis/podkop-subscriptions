"use strict";
"require form";
"require fs";
"require ui";
"require baseclass";

const LOCAL_LINKS = "/etc/config/podkop-local-links";
const PODKOP_SUBSCRIPTIONS_VERSION = "3.6";
const STATUS_STYLE_PLAIN_CARD_V36 = true;

function hideDuplicatedSubscriptionsTitle() {
  if (document.getElementById("podkop-subscriptions-title-hide-style"))
    return;

  const style = document.createElement("style");
  style.id = "podkop-subscriptions-title-hide-style";
  style.textContent = `
    /* Hide duplicated in-page title of the subscriptions tab.
       The tab label itself remains visible in the Podkop tab bar. */
    [data-tab="subscriptions_ui"] h2:first-child,
    [data-tab="subscriptions_ui"] h3:first-child,
    [data-tab="subscriptions_ui"] .cbi-section > h2:first-child,
    [data-tab="subscriptions_ui"] .cbi-section > h3:first-child,
    [data-tab="subscriptions_ui"] .cbi-section-title:first-child {
      display: none !important;
    }
  `;
  document.head.appendChild(style);
}


function execOutputText(res) {
  let out = "";

  if (res && res.stdout)
    out += res.stdout;

  if (res && res.stderr)
    out += "\n" + res.stderr;

  return out;
}

function makeLogBox(text) {
  const box = E("pre", {
    style: "white-space: pre-wrap; max-height: 620px; overflow: auto"
  }, text || "");
  ui.addNotification(null, box);
  return box;
}

function setLogBox(box, text) {
  if (!box)
    return;

  box.textContent = text || _("Нет вывода updater");
  box.scrollTop = box.scrollHeight;
}

function notifyOutput(title, res) {
  const out = execOutputText(res);
  return makeLogBox(out || title);
}

function delayMs(ms) {
  return new Promise(function(resolve) {
    window.setTimeout(resolve, ms);
  });
}

function pollUpdaterResult(attempt, box) {
  return fs.exec("/usr/bin/podkop-sub-run-now", ["--status"]).then(function(res) {
    const out = execOutputText(res);
    setLogBox(box, out);

    if (out.indexOf("STATE=running") !== -1 && attempt < 240)
      return delayMs(3000).then(function() {
        return pollUpdaterResult(attempt + 1, box);
      });

    return out;
  }).catch(function(err) {
    const msg = String(err);
    if (box)
      setLogBox(box, msg);
    else
      ui.addNotification(null, E("pre", { style: "white-space: pre-wrap" }, msg), "danger");
  });
}

return baseclass.extend({
  createSubscriptionsContent: function(section, podkopSections) {
    section.uciconfig = "podkop_subscriptions";
    section.anonymous = true;
    section.addremove = false;
    section.cfgsections = function() {
      return ["subscriptions_ui"];
    };

    let o, ss;

    o = section.option(
      form.DummyValue,
      "_status_summary",
      _("Состояние")
    );
    o.rawhtml = true;
    o.cfgvalue = function() {
      return fs.exec("/usr/bin/podkop-sub-updater.py", ["--status-summary"]).then(function(res) {
        const text = execOutputText(res) || "Состояние: нет данных.";
        const isBad = text.indexOf("авария") !== -1 || text.indexOf("ошибка") !== -1;
        const isFirst = text.indexOf("первичная настройка") !== -1;
        const label = isBad ? "АВАРИЯ" : (isFirst ? "ПЕРВИЧНАЯ НАСТРОЙКА" : "OK");
        const labelBg = isBad ? "#b00020" : (isFirst ? "#2059b3" : "#0b6b0b");
        const safeText = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        return '<div style="white-space:pre-wrap;padding:11px 13px;border:1px solid #222;background:#ffffff;color:#000000;font-weight:600;line-height:1.45;border-radius:4px;box-shadow:none">' +
          '<span style="display:inline-block;margin-bottom:6px;padding:2px 7px;border-radius:3px;background:' + labelBg + ';color:#ffffff;font-weight:700;font-size:11px;letter-spacing:.02em">' + label + '</span>\n' +
          safeText +
          '</div>';
      }).catch(function(err) {
        return '<div style="white-space:pre-wrap;padding:10px;border-left:4px solid #999;background:#f7f7f7">Состояние: нет данных. ' +
          String(err).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") +
          '</div>';
      });
    };

    o = section.option(
      form.SectionValue,
      "_subscription_groups",
      form.TypedSection,
      "subscription_group",
      "",
      _("Добавьте одну или несколько групп подписок. Секция Podkop выбирается из списка существующих секций.")
    );

    o.uciconfig = "podkop_subscriptions";

    ss = o.subsection;
    ss.uciconfig = "podkop_subscriptions";
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
      _("Ссылки HTTP/HTTPS-подписок. Локальный список включается отдельной галкой ниже.")
    );
    o.placeholder = "";
    o.rmempty = true;

    o = ss.option(
      form.Flag,
      "use_local_links",
      _("Использовать локальный список ключей"),
      _("Добавляет ключи из /etc/config/podkop-local-links. Эти ключи защищены от автоудаления.")
    );
    o.default = "0";
    o.rmempty = false;

    o = ss.option(
      form.TextValue,
      "regex",
      _("Фильтр Regex"),
      _("Фильтр применяется ко всей proxy-ссылке. Регистр не важен. Пример: xhttp|#.*(YouTube|youtube|Ютуб|ютуб|YT|без рекламы|Messengers|MultiIP|Белый|список|Россия|Финляндия|🇦🇺|🇫🇮|\\bAI\\b). xhttp сработает по всему ключу, остальное ищется только после решётки # в названии.")
    );
    o.rows = 2;
    o.placeholder = "";
    o.rmempty = true;
    o.inputstyle = "width: 100%; min-height: 4.2em; resize: vertical; font-family: monospace;";
    o.write = function(section_id, value) {
      value = String(value || "").replace(/^\s+|\s+$/g, "");
      return form.TextValue.prototype.write.apply(this, [section_id, value]);
    };


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

    o = ss.option(
      form.Value,
      "max_links",
      _("Максимум ключей в секции"),
      _("Необязательный лимит количества ключей в целевой секции. 0 или пусто — без ограничения. Если при добавлении новых ключей лимит будет превышен, updater удалит худшие незащищённые ключи и добавит новые только в освободившиеся места.")
    );
    o.datatype = "uinteger";
    o.placeholder = "50";
    o.rmempty = true;

    o = ss.option(
      form.Value,
      "max_latency_ms",
      _("Максимальный ping, мс"),
      _("Необязательный предел задержки по текущему URLTest Podkop. 0 или пусто — без фильтра по ping. Ключи с задержкой выше этого значения могут быть удалены отсеивателем.")
    );
    o.datatype = "uinteger";
    o.placeholder = "500";
    o.rmempty = true;

    o = ss.option(
      form.Flag,
      "force_cleanup",
      _("Принудительная чистка"),
      _("Экстремальный режим. Если подписки успешно загрузились, updater может удалять ключи с fail_count >= 2 и ключи с ping выше лимита даже без превышения максимального количества. Используйте осторожно.")
    );
    o.default = "0";
    o.rmempty = false;

    o = ss.option(
      form.Flag,
      "dedupe_sni_rotation",
      _("Схлопывать SNI-дубликаты"),
      _("Если новый ключ из подписки отличается от существующего только параметром sni, старый вариант будет заменён новым. Название ключа не используется для сравнения. Локальные ключи из /etc/config/podkop-local-links не заменяются.")
    );
    o.default = "0";
    o.rmempty = false;

    o = ss.option(
      form.Flag,
      "dedupe_endpoint_host",
      _("Схлопывать IP/домен:порт-дубликаты"),
      _("Если несколько ключей ведут на один и тот же IP/домен и один порт, будет оставлен последний вариант из подписки. Один IP/домен с разными портами не схлопывается. Transport, sni и другие параметры при сравнении не учитываются. Локальные ключи из /etc/config/podkop-local-links не заменяются.")
    );
    o.default = "0";
    o.rmempty = false;

    o = section.option(
      form.SectionValue,
      "_subscription_schedules",
      form.TypedSection,
      "subscription_schedule",
      _("Расписание обновлений"),
      _("Добавьте одно или несколько расписаний обновления. Например, отдельные записи для 03:00, 04:00 и 05:00.")
    );

    o.uciconfig = "podkop_subscriptions";

    ss = o.subsection;
    ss.uciconfig = "podkop_subscriptions";
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
      _("Сначала нажмите Save & Apply. Кнопка запускает updater в фоне, затем LuCI покажет результат последнего запуска.")
    );
    o.inputtitle = _("Запустить updater");
    o.inputstyle = "reload";
    o.onclick = function() {
      return fs.exec("/usr/bin/podkop-sub-run-now", []).then(function(res) {
        const box = notifyOutput("Updater started", res);
        return delayMs(1000).then(function() {
          return pollUpdaterResult(0, box);
        });
      }).catch(function(err) {
        ui.addNotification(null, E("pre", { style: "white-space: pre-wrap" }, String(err)), "danger");
      });
    };

    o = section.option(
      form.DummyValue,
      "_podkop_subscriptions_version",
      ""
    );
    o.rawhtml = true;
    o.cfgvalue = function() {
      return '<div style="margin-top:8px;color:#888;font-size:11px;line-height:1.3">Podkop Subscriptions v' + PODKOP_SUBSCRIPTIONS_VERSION + '</div>';
    };
  }
});
