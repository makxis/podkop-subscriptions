"use strict";
"require view";
"require form";
"require baseclass";
"require network";
"require fs";
"require view.podkop.main as main";

// Settings content
"require view.podkop.settings as settings";

// Sections content
"require view.podkop.section as section";

// Subscriptions content
"require view.podkop.subscriptions as subscriptions";

// Dashboard content
"require view.podkop.dashboard as dashboard";

// Diagnostic content
"require view.podkop.diagnostic as diagnostic";

function parseUciValue(raw) {
  raw = String(raw || "").trim();

  if (raw.length >= 2 && raw[0] === "'" && raw[raw.length - 1] === "'")
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

const EntryPoint = {
  async render() {
    main.injectGlobalStyles();

    const podkopText = await fs.read("/etc/config/podkop").catch(function() {
      return "";
    });

    const podkopSections = parsePodkopSections(podkopText);

    const podkopMap = new form.Map(
      "podkop",
      _("Podkop Settings"),
      _("Configuration for Podkop service"),
    );

    podkopMap.tabbed = true;
    if (podkopMap.chain)
      podkopMap.chain("podkop_subscriptions");

    podkopMap.on_after_commit = function() {
      return fs.exec("/usr/bin/podkop-sub-cron-sync", []).catch(function() {});
    };

    // Sections tab
    const sectionsSection = podkopMap.section(
      form.TypedSection,
      "section",
      _("Sections"),
    );
    sectionsSection.anonymous = false;
    sectionsSection.addremove = true;
    sectionsSection.template = "cbi/simpleform";

    section.createSectionContent(sectionsSection);

    // Subscriptions tab
    const subscriptionsSection = podkopMap.section(
      form.TypedSection,
      "subscriptions_ui",
      _("Подписки"),
    );
    subscriptions.createSubscriptionsContent(subscriptionsSection, podkopSections);

    // Settings tab
    const settingsSection = podkopMap.section(
      form.TypedSection,
      "settings",
      _("Settings"),
    );
    settingsSection.anonymous = true;
    settingsSection.addremove = false;
    settingsSection.cfgsections = function () {
      return ["settings"];
    };

    settings.createSettingsContent(settingsSection);

    // Diagnostic tab
    const diagnosticSection = podkopMap.section(
      form.TypedSection,
      "diagnostic",
      _("Diagnostics"),
    );
    diagnosticSection.anonymous = true;
    diagnosticSection.addremove = false;
    diagnosticSection.cfgsections = function () {
      return ["diagnostic"];
    };

    diagnostic.createDiagnosticContent(diagnosticSection);

    // Dashboard tab
    const dashboardSection = podkopMap.section(
      form.TypedSection,
      "dashboard",
      _("Dashboard"),
    );
    dashboardSection.anonymous = true;
    dashboardSection.addremove = false;
    dashboardSection.cfgsections = function () {
      return ["dashboard"];
    };

    dashboard.createDashboardContent(dashboardSection);

    main.coreService();

    return podkopMap.render();
  },
};

return view.extend(EntryPoint);
