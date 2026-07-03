"use strict";
"require view";
"require form";
"require fs";
"require ui";
"require view.podkop_subscriptions.content as subscriptions";

function parseUciValue(raw) {
  raw = String(raw || "").trim();

  if (raw.length >= 2 && raw.charCodeAt(0) === 39 && raw.charCodeAt(raw.length - 1) === 39)
    return raw.slice(1, -1);

  if (raw.length >= 2 && raw[0] === '"' && raw[raw.length - 1] === '"')
    return raw.slice(1, -1).replace(/\\"/g, '"').replace(/\\\\/g, "\\");

  return raw;
}

function parsePodkopSections(text) {
  const sections = [];

  String(text || "").split(/\r?\n/).forEach(function(line) {
    const raw = line.trim();
    const m = raw.match(/^config\s+section\s+(.+)$/);

    if (m) {
      const name = parseUciValue(m[1]);
      if (name)
        sections.push(name);
    }
  });

  return sections;
}

return view.extend({
  load: function() {
    return fs.read("/etc/config/podkop").catch(function() {
      return "";
    });
  },

  render: function(podkopText) {
    const podkopSections = parsePodkopSections(podkopText);

    const m = new form.Map(
      "podkop_subscriptions",
      _("Подписки Podkop"),
      _("Настройки обновления proxy-ссылок из подписок для Podkop.")
    );

    // form.js does not call the legacy Lua-CBI on_after_commit hook.
    // Wrap the real Map.save() method so Save / Save & Apply immediately
    // synchronizes the saved subscription schedules with root crontab.
    const originalSave = m.save.bind(m);
    m.save = function(cb, silent) {
      return originalSave(cb, silent).then(function(rv) {
        return fs.exec("/usr/bin/podkop-sub-cron-sync", []).catch(function() {}).then(function() {
          return rv;
        });
      });
    };

    const section = m.section(
      form.TypedSection,
      "subscriptions_ui",
      ""
    );

    subscriptions.createSubscriptionsContent(section, podkopSections);

    return m.render();
  }
});
