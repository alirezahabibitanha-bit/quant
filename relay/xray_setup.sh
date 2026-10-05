#!/bin/bash
# xray_setup.sh - پروکسی Xray روی سرور ایران (جایگزین تونل SSH) برای رسیدن به جیمینای و قیمت جهانی طلا
#
# چرا: تونل SSH از ایران قطع یا کند می‌شود. Xray با VLESS+Reality شبیه بازدید عادی یک سایت HTTPS است.
# چه می‌کند: یک پروکسی HTTP فقط روی خود سرور (127.0.0.1:10809) باز می‌کند و آدرسش را در ~/.proxy_url
#   می‌نویسد. فقط جیمینای و قیمت جهانی طلا از آن رد می‌شوند؛ بله، والکس و بقیه سرور مستقیم می‌مانند.
#   مسیر اصلی اینترنت سرور (Default Gateway) دست نمی‌خورد.
#
# قبل از اجرا: لینک vless:// را در ~/.vless_link بگذارید (یک خط). لینک مثل رمز است؛ هرگز در GitHub یا چت نه.
#   nano ~/.vless_link   (paste، ذخیره Ctrl+O، خروج Ctrl+X)
#
# دستورها (روی سرور ایران، با root):
#   bash ~/quant/relay/xray_setup.sh connect   نصب Xray (اگر نیست)، ساختن تنظیمات از لینک، روشن کردن، تست
#   bash ~/quant/relay/xray_setup.sh test      تست جیمینای و قیمت طلا از پروکسی
#   bash ~/quant/relay/xray_setup.sh off       خاموش کردن (ربات دوباره از تونل یا مستقیم می‌رود)
#
# دستورهای روزمره:
#   وضعیت:          systemctl status xray
#   پیام‌ها:          journalctl -u xray -n 30
#   روشن کردن دوباره: systemctl restart xray
#   لینک عوض شد:     لینک تازه را در ~/.vless_link بگذارید و دوباره connect بزنید

set -euo pipefail

LINK_FILE="$HOME/.vless_link"
CONFIG=/usr/local/etc/xray/config.json
PORT=10809
PROXY="http://127.0.0.1:$PORT"

if [ "$(id -u)" -ne 0 ]; then
    echo "!! Run this as root."
    exit 1
fi

install_xray() {
    if command -v xray >/dev/null 2>&1; then
        echo "== Xray already installed: $(xray version | head -1)"
        return
    fi
    echo "== Installing Xray from GitHub (official installer)..."
    if ! curl -fsSL --max-time 60 -o /tmp/xray-install.sh \
            https://github.com/XTLS/Xray-install/raw/main/install-release.sh; then
        echo "!! Could not download the installer from GitHub."
        exit 1
    fi
    bash /tmp/xray-install.sh install --without-geodata
    rm -f /tmp/xray-install.sh
    command -v xray >/dev/null 2>&1 || { echo "!! Xray install failed."; exit 1; }
}

write_config() {
    if [ ! -s "$LINK_FILE" ]; then
        echo "!! No link in $LINK_FILE. Put your vless:// link there first:"
        echo "     nano $LINK_FILE && chmod 600 $LINK_FILE"
        exit 1
    fi
    chmod 600 "$LINK_FILE"
    install -d /usr/local/etc/xray
    # لینک را پایتون می‌خواند و تنظیمات را می‌سازد؛ لینک هیچ‌جا چاپ نمی‌شود
    python3 - "$LINK_FILE" "$CONFIG" "$PORT" <<'EOF'
import json, sys
from urllib.parse import urlsplit, parse_qs, unquote

link_file, out, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
link = open(link_file, encoding="utf-8").read().split()[0]
u = urlsplit(link)
if u.scheme != "vless" or not u.username or not u.hostname:
    sys.exit("!! The link must start with vless:// and have an id and a server address.")
q = {k: unquote(v[0]) for k, v in parse_qs(u.query).items()}
net, sec = q.get("type", "tcp"), q.get("security", "none")

user = {"id": unquote(u.username), "encryption": q.get("encryption", "none")}
if q.get("flow"):
    user["flow"] = q["flow"]
stream = {"network": net, "security": sec}
if sec == "reality":
    stream["realitySettings"] = {"serverName": q.get("sni", ""), "fingerprint": q.get("fp", "chrome"),
                                 "publicKey": q.get("pbk", ""), "shortId": q.get("sid", ""),
                                 "spiderX": q.get("spx", "")}
elif sec == "tls":
    stream["tlsSettings"] = {"serverName": q.get("sni", u.hostname), "fingerprint": q.get("fp", "chrome")}
    if q.get("alpn"):
        stream["tlsSettings"]["alpn"] = q["alpn"].split(",")
elif sec != "none":
    sys.exit(f"!! security={sec} is not supported by this script.")
if net == "ws":
    stream["wsSettings"] = {"path": q.get("path", "/"), "headers": {"Host": q.get("host", "")}}
elif net == "grpc":
    stream["grpcSettings"] = {"serviceName": q.get("serviceName", "")}
elif net != "tcp":
    sys.exit(f"!! type={net} is not supported by this script (tcp, ws, grpc only).")

config = {
    "log": {"loglevel": "warning"},
    # فقط روی خود سرور؛ از اینترنت در دسترس نیست
    "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "http", "tag": "local-http"}],
    "outbounds": [{"protocol": "vless", "tag": "proxy",
                   "settings": {"vnext": [{"address": u.hostname, "port": u.port or 443, "users": [user]}]},
                   "streamSettings": stream}],
}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(config, fh, indent=2)
print(f"== Config written: {net}/{sec} to {u.hostname}:{u.port or 443} ({unquote(u.fragment) or 'no name'})")
EOF
    chmod 644 "$CONFIG"
    xray run -test -config "$CONFIG" >/dev/null || { echo "!! Xray rejected the config."; exit 1; }
}

case "${1:-}" in
connect)
    install_xray
    write_config
    systemctl enable --quiet xray
    systemctl restart xray
    echo "$PROXY" > "$HOME/.proxy_url"
    echo "== Proxy address saved in ~/.proxy_url"
    sleep 2
    exec bash "$0" test
    ;;

test)
    if ! systemctl is-active --quiet xray; then
        echo "!! Xray is not running. Last messages:"
        journalctl -u xray -n 15 --no-pager
        exit 1
    fi
    ok=1
    if curl -s -o /dev/null --max-time 20 -x "$PROXY" https://www.google.com/generate_204; then
        echo "== Internet through the proxy works."
    else
        echo "!! Nothing passes through the proxy. The link may be expired or the server blocked."
        echo "!! Last messages from Xray:"
        journalctl -u xray -n 15 --no-pager
        exit 1
    fi
    for site in https://query1.finance.yahoo.com https://api.gold-api.com https://forex-data-feed.swissquote.com; do
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -x "$PROXY" "$site/" || true)
        echo "   $site -> code $code"
    done
    if [ ! -f "$HOME/.gemini_key" ]; then
        echo "== No ~/.gemini_key yet, so Gemini was not tested. Put the key there and run:"
        echo "     bash ~/quant/relay/xray_setup.sh test"
        exit 0
    fi
    REPLY=$(curl -s --max-time 30 -x "$PROXY" -H "x-goog-api-key: $(cat "$HOME/.gemini_key")" \
                 https://generativelanguage.googleapis.com/v1beta/models || true)
    if echo "$REPLY" | grep -q '"models"'; then
        echo "== Gemini works through the proxy."
    elif echo "$REPLY" | grep -qi "location"; then
        echo "!! Google says the location is not supported: the proxy server's country is blocked too."
        ok=0
    elif echo "$REPLY" | grep -qi "api key"; then
        echo "!! Google rejected the API key. Check ~/.gemini_key (only the key, nothing else)."
        ok=0
    else
        echo "!! Unexpected answer from Gemini:"
        echo "$REPLY" | head -c 400; echo
        ok=0
    fi
    [ "$ok" = 1 ] || exit 1
    echo "== Done. Restart the bot so it uses the proxy:  systemctl restart bale-bot"
    ;;

off)
    rm -f "$HOME/.proxy_url"
    systemctl disable --now xray 2>/dev/null || true
    echo "== Proxy off. The bot will use the SSH tunnel (if any) or go direct."
    echo "   Restart the bot:  systemctl restart bale-bot"
    ;;

*)
    echo "Usage:"
    echo "  bash xray_setup.sh connect   (install Xray, build config from ~/.vless_link, start, test)"
    echo "  bash xray_setup.sh test      (check Gemini and gold prices through the proxy)"
    echo "  bash xray_setup.sh off       (stop the proxy)"
    exit 1
    ;;
esac
