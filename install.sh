#!/bin/sh
set -eu

APP_VERSION="3.6"
REPO="${REPO:-makxis/podkop-subscriptions}"
BRANCH="${BRANCH:-main}"
RAW_BASE="${RAW_BASE:-https://raw.githubusercontent.com/${REPO}/${BRANCH}}"
PANEL_MODE="ask"
SOURCE_MODE="auto"
CONFIG_MODE="ask"

SUB_CFG="/etc/config/podkop_subscriptions"
PODKOP_CFG="/etc/config/podkop"
LOCAL_LINKS="/etc/config/podkop-local-links"

SCRIPT_PATH="$0"
case "$SCRIPT_PATH" in
  */*) SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)" ;;
  *) SCRIPT_DIR="$(pwd)" ;;
esac

for arg in "$@"; do
  case "$arg" in
    --with-panel) PANEL_MODE="yes" ;;
    --no-panel|--core-only) PANEL_MODE="no" ;;
    --configure) CONFIG_MODE="yes" ;;
    --no-config) CONFIG_MODE="no" ;;
    --local) SOURCE_MODE="local" ;;
    --remote) SOURCE_MODE="remote" ;;
    --repo=*) REPO="${arg#--repo=}"; RAW_BASE="https://raw.githubusercontent.com/${REPO}/${BRANCH}" ;;
    --branch=*) BRANCH="${arg#--branch=}"; RAW_BASE="https://raw.githubusercontent.com/${REPO}/${BRANCH}" ;;
    --raw-base=*) RAW_BASE="${arg#--raw-base=}" ;;
    -h|--help)
      echo "Usage: sh install.sh [--with-panel|--no-panel] [--configure|--no-config] [--local|--remote] [--repo=owner/repo] [--branch=main]"
      exit 0
      ;;
    *) echo "Unknown argument: $arg" >&2; exit 1 ;;
  esac
done

say() { printf '%s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

ask_yn() {
  prompt="$1"
  default="$2"
  if [ "$default" = "yes" ]; then suffix="[Y/n]"; else suffix="[y/N]"; fi
  # Prompt goes to stderr, so ask_yn can be used safely in command substitutions.
  printf '%s %s: ' "$prompt" "$suffix" >&2
  read ans || ans=""
  case "$ans" in
    y|Y|yes|YES|Yes) return 0 ;;
    n|N|no|NO|No) return 1 ;;
    '') [ "$default" = "yes" ] ;;
    *) [ "$default" = "yes" ] ;;
  esac
}

ask_value() {
  prompt="$1"
  default="$2"
  # Prompt goes to stderr, returned value goes to stdout.
  # This is important for code like value="$(ask_value ...)".
  printf '%s' "$prompt" >&2
  [ -n "$default" ] && printf ' [%s]' "$default" >&2
  printf ': ' >&2
  read ans || ans=""
  [ -n "$ans" ] && printf '%s' "$ans" || printf '%s' "$default"
}

uci_escape() {
  printf '%s' "$1" | sed "s/'/'\\\\''/g"
}

need_root() {
  [ "$(id -u)" = "0" ] || fail "run as root"
}

backup_file() {
  file="$1"
  [ -e "$file" ] || return 0
  ts="$(date +%Y%m%d-%H%M%S 2>/dev/null || echo now)"
  cp -fp "$file" "${file}.bak.${ts}"
}

copy_local() {
  src="$1"
  dest="$2"
  [ -f "$src" ] || fail "local file not found: $src"
  mkdir -p "$(dirname "$dest")"
  cp -f "$src" "$dest"
}

fetch_remote() {
  url="$1"
  dest="$2"
  tmp="${dest}.tmp.$$"
  mkdir -p "$(dirname "$dest")"
  if command -v wget >/dev/null 2>&1; then
    wget -qO "$tmp" "$url" || { rm -f "$tmp"; fail "download failed: $url"; }
  elif command -v curl >/dev/null 2>&1; then
    curl -fsSL "$url" -o "$tmp" || { rm -f "$tmp"; fail "download failed: $url"; }
  else
    fail "wget or curl is required"
  fi
  mv "$tmp" "$dest"
}

install_file() {
  rel="$1"
  dest="$2"
  local_src="$SCRIPT_DIR/$rel"
  if [ "$SOURCE_MODE" = "local" ] || { [ "$SOURCE_MODE" = "auto" ] && [ -f "$local_src" ]; }; then
    copy_local "$local_src" "$dest"
  else
    fetch_remote "$RAW_BASE/$rel" "$dest"
  fi
}

ensure_python() {
  if command -v python3 >/dev/null 2>&1; then return 0; fi
  say "python3 not found, trying to install python3-light..."
  if command -v opkg >/dev/null 2>&1; then
    opkg update
    opkg install python3-light ca-bundle || opkg install python3 ca-bundle || fail "cannot install python3"
  elif command -v apk >/dev/null 2>&1; then
    apk update
    apk add python3-light ca-bundle || apk add python3 ca-bundle || fail "cannot install python3"
  else
    fail "python3 is required, but neither opkg nor apk was found"
  fi
}

list_podkop_sections() {
  [ -f "$PODKOP_CFG" ] || return 0
  awk '
    /^[ \t]*config[ \t]+section[ \t]+/ {
      name=$3
      gsub(/^\047|\047$/, "", name)
      gsub(/^\"|\"$/, "", name)
      if (name != "") print name
    }
  ' "$PODKOP_CFG"
}

choose_target_section() {
  sections="$(list_podkop_sections || true)"
  if [ -n "$sections" ]; then
    printf '\n' >&2
    printf '%s\n' "Найдены секции Podkop:" >&2
    i=1
    echo "$sections" | while IFS= read -r s; do
      [ -n "$s" ] && printf '  %s) %s\n' "$i" "$s" >&2
      i=$((i + 1))
    done
    choice="$(ask_value "Выберите целевую секцию" "1")"
    selected="$(echo "$sections" | sed -n "${choice}p" 2>/dev/null || true)"
    if [ -n "$selected" ]; then printf '%s' "$selected"; return 0; fi
  fi
  ask_value "Введите имя целевой секции Podkop" "main"
}


create_upgrade_backup() {
  ts="$(date +%Y%m%d-%H%M%S 2>/dev/null || echo now)"
  backup="/root/podkop-subscriptions-upgrade-backup-${ts}.tar.gz"

  tar -czf "$backup" \
    /etc/config/podkop \
    /etc/config/podkop_subscriptions \
    /etc/config/podkop-local-links \
    /etc/podkop-subscriptions \
    /etc/crontabs/root \
    /usr/bin/podkop-sub-updater.py \
    /usr/bin/podkop-sub-cron-sync \
    /usr/bin/podkop-sub-run-now \
    /usr/share/podkop-subscriptions \
    /www/luci-static/resources/view/podkop/subscriptions.js \
    /www/luci-static/resources/view/podkop_subscriptions \
    /usr/share/luci/menu.d/luci-app-podkop-subscriptions.json \
    /usr/share/rpcd/acl.d/luci-app-podkop-subscriptions.json \
    2>/tmp/podkop-sub-upgrade-backup-errors.log || true

  if [ -s "$backup" ]; then
    say "Backup created: $backup"
  else
    warn "backup file was not created or is empty: $backup"
  fi
}

migrate_legacy_config_from_podkop() {
  # v3.0 stored subscription_group/subscription_schedule directly in /etc/config/podkop.
  # v3.1+ uses /etc/config/podkop_subscriptions.
  [ -f "$SUB_CFG" ] && return 0
  [ -f "$PODKOP_CFG" ] || return 0

  tmp="${SUB_CFG}.tmp.$$"

  awk '
    function is_legacy_type(t) {
      return t == "subscription_group" || t == "subscription_schedule"
    }

    /^[ \t]*config[ \t]+/ {
      if (copy) print ""
      copy = 0

      t = $2
      if (is_legacy_type(t)) {
        copy = 1
        found = 1
        print
      }
      next
    }

    {
      if (copy) print
    }

    END {
      if (!found) exit 2
    }
  ' "$PODKOP_CFG" > "$tmp" || {
    rm -f "$tmp"
    return 0
  }

  if [ -s "$tmp" ]; then
    mkdir -p "$(dirname "$SUB_CFG")"
    {
      echo "# Podkop Subscriptions config"
      echo "# Migrated automatically from /etc/config/podkop legacy sections."
      echo "# Main settings file for Podkop Subscriptions v${APP_VERSION}."
      echo ""
      cat "$tmp"
    } > "$SUB_CFG"
    rm -f "$tmp"
    say "Migrated legacy subscription settings to: $SUB_CFG"
  else
    rm -f "$tmp"
  fi
}

remove_legacy_sections_from_podkop() {
  [ -f "$PODKOP_CFG" ] || return 0
  command -v uci >/dev/null 2>&1 || return 0

  changed=0
  for typ in subscription_group subscription_schedule subscriptions_ui local_links; do
    for s in $(uci show podkop 2>/dev/null | sed -n "s/^podkop\.\([^=]*\)=${typ}$/\1/p"); do
      say "Removing legacy Podkop section: $typ $s"
      uci delete "podkop.$s" 2>/dev/null || true
      changed=1
    done
  done

  [ "$changed" = "1" ] && uci commit podkop 2>/dev/null || true
}

cleanup_old_luci_leftovers() {
  rm -f /www/luci-static/resources/view/podkop/subscriptions.js 2>/dev/null || true
  rm -rf /www/luci-static/resources/view/podkop_subscriptions 2>/dev/null || true
  rm -f /usr/share/luci/menu.d/luci-app-podkop-subscriptions.json 2>/dev/null || true
  rm -f /usr/share/rpcd/acl.d/luci-app-podkop-subscriptions.json 2>/dev/null || true
}

cleanup_old_cron_lines() {
  cron_file="/etc/crontabs/root"
  [ -f "$cron_file" ] || return 0
  tmp="${cron_file}.tmp.$$"

  grep -v 'podkop-sub-updater' "$cron_file" \
    | grep -v 'podkop-sub-health' \
    | grep -v 'podkop-updater-cron' \
    | grep -v 'podkop-sub-catchup' \
    | grep -v 'podkop-sub-run-now' > "$tmp" || true

  mv "$tmp" "$cron_file"
}

prepare_upgrade_from_legacy() {
  create_upgrade_backup
  migrate_legacy_config_from_podkop
  remove_legacy_sections_from_podkop
  cleanup_old_cron_lines
  cleanup_old_luci_leftovers

  # Preserve user local links and state:
  #   /etc/config/podkop-local-links
  #   /etc/podkop-subscriptions/state.json
  mkdir -p /etc/podkop-subscriptions
}


write_default_config() {
  mkdir -p /etc/config
  if [ -f "$SUB_CFG" ]; then
    say "Конфиг уже существует, не перезаписываю: $SUB_CFG"
    return 0
  fi
  cat > "$SUB_CFG" <<'CFG'
# Podkop Subscriptions config
# Основной файл настройки дополнения.
# После изменения выполните: /usr/bin/podkop-sub-cron-sync

config subscription_group 'main'
    option enabled '0'

    # Целевая секция Podkop, куда будут записываться ключи.
    # Секцию сначала надо создать в Podkop.
    option target_section 'main'

    # Источники подписок. Раскомментируйте и замените на свои URL.
    # list source 'https://example.com/subscription-1'
    # list source 'https://example.com/subscription-2'

    # Локальный список ручных ключей: /etc/config/podkop-local-links
    # 1 — использовать, 0 — не использовать.
    option use_local_links '0'

    # Фильтр. Пусто — без фильтра.
    option regex ''
    option match_mode 'ifnotmatch'
    option on_empty 'skip'

    # Тип группы в Podkop: urltest или selector.
    option proxy_type 'urltest'

    # Ограничения. 0 — без ограничения.
    option max_links '0'
    option max_latency_ms '0'

    # Жёсткая чистка и схлопывание похожих ключей.
    option force_cleanup '0'
    option dedupe_sni_rotation '0'
    option dedupe_endpoint_host '0'


# Пример расписания. Раскомментируйте enabled или настройте через интерактивный мастер/LuCI.
config subscription_schedule 'main_0300'
    option enabled '0'
    option hour '3'
    option minute '0'
    option jitter '1800'
    option force '0'
CFG
  chmod 0600 "$SUB_CFG" || true
  say "Создан пример конфига: $SUB_CFG"
}

interactive_config() {
  say ""
  say "Интерактивная настройка Podkop Subscriptions"
  say "Мастер создаст основной конфиг: $SUB_CFG"
  say "Родной конфиг Podkop останется здесь: $PODKOP_CFG"
  say "Если не уверены в ответе, можно нажимать Enter и оставлять значение по умолчанию."
  say ""
  target="$(choose_target_section)"
  say ""
  say ""
  say "Теперь укажите HTTP/HTTPS URL подписки."
  say "Если сейчас не хотите указывать подписку, просто нажмите Enter."
  sources=""
  while :; do
    src="$(ask_value "Введите URL подписки, пусто — закончить" "")"
    [ -z "$src" ] && break
    sources="${sources}
$src"
    ask_yn "Добавить ещё один URL подписки?" "no" || break
  done
  use_local="0"
  if ask_yn "Использовать локальный список ключей $LOCAL_LINKS?" "yes"; then use_local="1"; fi
  regex="$(ask_value "Regex-фильтр, пусто — без фильтра" "")"
  say "Режим фильтрации: 1) ifmatch — оставить совпавшие; 2) ifnotmatch — исключить совпавшие"
  mm_choice="$(ask_value "Выберите режим" "2")"
  [ "$mm_choice" = "1" ] && match_mode="ifmatch" || match_mode="ifnotmatch"
  say "Тип группы: 1) urltest; 2) selector"
  pt_choice="$(ask_value "Выберите тип" "1")"
  [ "$pt_choice" = "2" ] && ptype="selector" || ptype="urltest"
  max_links="$(ask_value "Максимум ключей в секции, 0 — без лимита" "50")"
  max_ping="$(ask_value "Максимальный ping, мс, 0 — без фильтра" "500")"
  force_cleanup="0"
  if ask_yn "Включить принудительную чистку?" "no"; then force_cleanup="1"; fi
  dedupe_sni="1"
  if ! ask_yn "Схлопывать SNI-дубликаты?" "yes"; then dedupe_sni="0"; fi
  schedule="0"
  hour="3"; minute="0"; jitter="1800"
  if ask_yn "Настроить cron-расписание?" "yes"; then
    schedule="1"
    hour="$(ask_value "Час запуска" "3")"
    minute="$(ask_value "Минута запуска" "0")"
    jitter="$(ask_value "Случайная задержка, секунд" "1800")"
  fi

  say ""
  say "Итог настройки:"
  say "  целевая секция Podkop: $target"
  if [ -n "$sources" ]; then
    count_sources="$(printf '%s\n' "$sources" | sed '/^$/d' | wc -l | tr -d ' ')"
  else
    count_sources="0"
  fi
  say "  источников подписки: $count_sources"
  say "  локальный список: $use_local"
  say "  тип группы: $ptype"
  say "  максимум ключей: $max_links"
  say "  максимум ping: $max_ping"
  say "  принудительная чистка: $force_cleanup"
  say "  SNI-схлопывание: $dedupe_sni"
  say "  cron enabled: $schedule"
  say ""
  backup_file "$SUB_CFG"
  tmp="${SUB_CFG}.tmp.$$"
  {
    echo "# Podkop Subscriptions config"
    echo "# Основной файл настройки дополнения."
    echo ""
    echo "config subscription_group 'main'"
    echo "    option enabled '1'"
    echo "    option target_section '$(uci_escape "$target")'"
    echo "$sources" | while IFS= read -r src; do
      [ -n "$src" ] && echo "    list source '$(uci_escape "$src")'"
    done
    echo "    option use_local_links '$use_local'"
    echo "    option regex '$(uci_escape "$regex")'"
    echo "    option match_mode '$match_mode'"
    echo "    option on_empty 'skip'"
    echo "    option proxy_type '$ptype'"
    echo "    option max_links '$(uci_escape "$max_links")'"
    echo "    option max_latency_ms '$(uci_escape "$max_ping")'"
    echo "    option force_cleanup '$force_cleanup'"
    echo "    option dedupe_sni_rotation '$dedupe_sni'"
    echo ""
    echo "# Дополнительный источник можно добавить так:"
    echo "# list source 'https://example.com/second-subscription'"
    echo "# Локальные ключи добавляйте в $LOCAL_LINKS"
    echo ""
    echo "config subscription_schedule 'main_0300'"
    echo "    option enabled '$schedule'"
    echo "    option hour '$(uci_escape "$hour")'"
    echo "    option minute '$(uci_escape "$minute")'"
    echo "    option jitter '$(uci_escape "$jitter")'"
    echo "    option force '0'"
  } > "$tmp"
  mv "$tmp" "$SUB_CFG"
  chmod 0600 "$SUB_CFG" || true
  say "Конфиг записан: $SUB_CFG"
}

install_core() {
  if [ "$SOURCE_MODE" = "remote" ]; then
    say "Installing Podkop subscription updater from: $RAW_BASE"
  else
    say "Installing Podkop subscription updater from local package: $SCRIPT_DIR"
  fi
  ensure_python
  backup_file /usr/bin/podkop-sub-updater.py
  backup_file /usr/bin/podkop-sub-cron-sync
  backup_file /usr/bin/podkop-sub-run-now
  install_file "podkop-sub-updater.py" /usr/bin/podkop-sub-updater.py
  install_file "podkop-sub-cron-sync" /usr/bin/podkop-sub-cron-sync
  install_file "podkop-sub-run-now" /usr/bin/podkop-sub-run-now
  mkdir -p /usr/share/podkop-subscriptions
  if [ -f "$SCRIPT_DIR/VERSION" ]; then
    install_file "VERSION" /usr/share/podkop-subscriptions/VERSION
  else
    printf '%s\n' "$APP_VERSION" > /usr/share/podkop-subscriptions/VERSION
  fi
  chmod 0755 /usr/bin/podkop-sub-updater.py /usr/bin/podkop-sub-cron-sync /usr/bin/podkop-sub-run-now
  touch "$LOCAL_LINKS"
  chmod 0600 "$LOCAL_LINKS" || true
  prepare_upgrade_from_legacy
  write_default_config
  say "Core installed. Version: $APP_VERSION"
}

detach_old_embedded_panel() {
  file="/www/luci-static/resources/view/podkop/podkop.js"
  [ -f "$file" ] || return 0

  # Remove old Podkop Subscriptions tab injected by v3.1/v3.2.
  # This is a cleanup of our previous integration, not a new Podkop patch.
  if grep -q 'view.podkop.subscriptions as subscriptions\|subscriptions.createSubscriptionsContent' "$file"; then
    python3 - "$file" <<'PY'
import re
import sys

path = sys.argv[1]
with open(path, 'r', encoding='utf-8', errors='replace') as f:
    text = f.read()

text = re.sub(r'\n// Subscriptions content\n"require view\.podkop\.subscriptions as subscriptions";\n', '\n', text)
text = re.sub(r'\n"require view\.podkop\.subscriptions as subscriptions";\n', '\n', text)

text = re.sub(
    r'\nfunction podkopSubParseUciValue\(raw\) \{.*?\n\}\n\nfunction podkopSubParsePodkopSections\(text\) \{.*?\n\}\n\n',
    '\n',
    text,
    flags=re.S,
)

text = re.sub(
    r'\n    const podkopText = await fs\.read\("/etc/config/podkop"\)\.catch\(function\(\) \{\n      return "";\n    \}\);\n\n    const podkopSections = podkopSubParsePodkopSections\(podkopText\);\n',
    '\n',
    text,
)

text = re.sub(
    r'\n    if \(podkopMap\.chain\)\n      podkopMap\.chain\("podkop_subscriptions"\);\n\n    const podkopSubOriginalAfterCommit = podkopMap\.on_after_commit;\n    podkopMap\.on_after_commit = function\(\) \{\n      const previous = podkopSubOriginalAfterCommit \? podkopSubOriginalAfterCommit\.apply\(this, arguments\) : Promise\.resolve\(\);\n      return Promise\.resolve\(previous\)\.then\(function\(\) \{\n        return fs\.exec\("/usr/bin/podkop-sub-cron-sync", \[\]\)\.catch\(function\(\) \{\}\);\n      \}\);\n    \};\n',
    '\n',
    text,
)

text = re.sub(
    r'\n    // Subscriptions tab\n    const subscriptionsSection = podkopMap\.section\(\n      form\.TypedSection,\n      "subscriptions_ui",\n      _\("Подписки"\),\n    \);\n    subscriptions\.createSubscriptionsContent\(subscriptionsSection, podkopSections\);\n',
    '\n',
    text,
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(text)
PY
  fi

  rm -f /www/luci-static/resources/view/podkop/subscriptions.js 2>/dev/null || true
}

install_panel() {
  if [ ! -d /www/luci-static/resources/view ]; then
    warn "LuCI static view directory was not found. Install LuCI first, then rerun with --with-panel."
    return 0
  fi

  say "Installing standalone LuCI app: Services -> Подписки Podkop"
  say "Podkop native LuCI files are not patched or replaced."

  backup_file /www/luci-static/resources/view/podkop/subscriptions.js
  backup_file /usr/share/luci/menu.d/luci-app-podkop-subscriptions.json
  backup_file /usr/share/rpcd/acl.d/luci-app-podkop-subscriptions.json

  detach_old_embedded_panel

  install_file "luci/www/luci-static/resources/view/podkop_subscriptions/content.js" /www/luci-static/resources/view/podkop_subscriptions/content.js
  install_file "luci/www/luci-static/resources/view/podkop_subscriptions/subscriptions.js" /www/luci-static/resources/view/podkop_subscriptions/subscriptions.js
  install_file "luci/usr/share/luci/menu.d/luci-app-podkop-subscriptions.json" /usr/share/luci/menu.d/luci-app-podkop-subscriptions.json
  install_file "luci/usr/share/rpcd/acl.d/luci-app-podkop-subscriptions.json" /usr/share/rpcd/acl.d/luci-app-podkop-subscriptions.json

  rm -f /tmp/luci-indexcache 2>/dev/null || true
  rm -rf /tmp/luci-modulecache 2>/dev/null || true
  /etc/init.d/rpcd restart >/dev/null 2>&1 || true
  /etc/init.d/uhttpd restart >/dev/null 2>&1 || true
  say "LuCI app installed. Open LuCI: Services -> Подписки Podkop."
}

ask_panel() {
  [ "$PANEL_MODE" = "yes" ] && return 0
  [ "$PANEL_MODE" = "no" ] && return 1
  ask_yn "Install optional LuCI panel for subscriptions?" "yes"
}

ask_configure() {
  [ "$CONFIG_MODE" = "yes" ] && return 0
  [ "$CONFIG_MODE" = "no" ] && return 1
  ask_yn "Создать/настроить конфиг подписок сейчас?" "yes"
}

need_root
install_core
if ask_configure; then
  interactive_config
fi
/usr/bin/podkop-sub-cron-sync || true
if ask_panel; then
  install_panel
else
  say "Panel installation skipped. You can install it later: sh install.sh --with-panel --no-config"
fi

say ""
say "Done."
say "Настройки: $SUB_CFG"
say "Локальные ключи: $LOCAL_LINKS"
say "Состояние: /etc/podkop-subscriptions/state.json"
say ""
say "Проверка:"
say "  /usr/bin/podkop-sub-updater.py --subs $SUB_CFG --config $PODKOP_CFG --force"
say "  /usr/bin/podkop-sub-updater.py --observe-only --config $PODKOP_CFG"
say "  /usr/bin/podkop-sub-run-now"
