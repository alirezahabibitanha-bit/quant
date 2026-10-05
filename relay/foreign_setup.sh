#!/bin/bash
# foreign_setup.sh - آماده کردن سرور خارج به‌عنوان واسطه جیمینای
#
# اجرا (فقط یک بار، روی سرور خارج، با کاربر root):
#   bash foreign_setup.sh "ssh-ed25519 AAAA... gemini-relay"
#   (متن داخل گیومه = کلیدی که iran_setup.sh روی سرور ایران چاپ کرد)
#
# کارها به ترتیب:
#   1. nginx را نصب می‌کند تا درخواست‌ها را به گوگل (جیمینای) برساند.
#      nginx فقط روی 127.0.0.1 گوش می‌دهد، یعنی از اینترنت در دسترس نیست؛ فقط از داخل تونل.
#   2. کاربر محدود "relay" را می‌سازد که فقط می‌تواند تونل بزند (نه دستور اجرا کند، نه فایل ببیند).
#   3. دیوار آتش (ufw) را روشن می‌کند و فقط درگاه SSH را باز می‌گذارد.
# اجرای دوباره بی‌خطر است (کلید قبلی با کلید جدید جایگزین می‌شود).
#
# به‌روز کردن تنظیمات واسطه بدون عوض کردن کلید (مثلاً برای اضافه شدن قیمت جهانی طلا):
#   bash foreign_setup.sh update
#
# علاوه بر جیمینای، دو مسیر دیگر هم هست (برای ربات طلا و دلار، global_gold.py):
#   /yahoo/    ← query1.finance.yahoo.com (قیمت روزانه طلا)
#   /swissquote/ ← forex-data-feed.swissquote.com (قیمت لحظه‌ای طلا)
#   /goldapi/  ← api.gold-api.com (قیمت لحظه‌ای طلا، پشتیبان)

set -euo pipefail

PUBKEY="${1:-}"
RELAY_PORT=8080

if [ "$(id -u)" -ne 0 ]; then
    echo "!! Run this as root."
    exit 1
fi
UPDATE_ONLY=0
if [ "$PUBKEY" = "update" ]; then
    if [ ! -s /home/relay/.ssh/authorized_keys ]; then
        echo "!! No tunnel key on this server yet. Run the full setup first (with the key from iran_setup.sh)."
        exit 1
    fi
    UPDATE_ONLY=1
elif [[ ! "$PUBKEY" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+(\ [^\"]*)?$ ]]; then
    echo "!! Give the key printed by iran_setup.sh, in quotes:"
    echo '     bash foreign_setup.sh "ssh-ed25519 AAAA... gemini-relay"'
    exit 1
fi

echo "== Installing nginx and firewall..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -q >/dev/null
apt-get install -y -q nginx ufw ca-certificates curl >/dev/null

echo "== Writing the relay config..."
cat > /etc/nginx/sites-available/gemini-relay <<EOF
# Gemini relay: only reachable from this machine (and so only through the SSH tunnel)
server {
    listen 127.0.0.1:${RELAY_PORT};
    # gold prices for the gold/USD bot (global_gold.py); read-only public data
    location /yahoo/ {
        proxy_pass https://query1.finance.yahoo.com/;
        proxy_ssl_server_name on;
        proxy_ssl_name query1.finance.yahoo.com;
        proxy_ssl_verify on;
        proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
        proxy_set_header Host query1.finance.yahoo.com;
        proxy_set_header Cookie "";
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_read_timeout 30s;
        access_log off;
    }
    location /swissquote/ {
        proxy_pass https://forex-data-feed.swissquote.com/;
        proxy_ssl_server_name on;
        proxy_ssl_name forex-data-feed.swissquote.com;
        proxy_ssl_verify on;
        proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
        proxy_set_header Host forex-data-feed.swissquote.com;
        proxy_set_header Cookie "";
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_read_timeout 30s;
        access_log off;
    }
    location /goldapi/ {
        proxy_pass https://api.gold-api.com/;
        proxy_ssl_server_name on;
        proxy_ssl_name api.gold-api.com;
        proxy_ssl_verify on;
        proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
        proxy_set_header Host api.gold-api.com;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_read_timeout 30s;
        access_log off;
    }
    location / {
        proxy_pass https://generativelanguage.googleapis.com;
        proxy_ssl_server_name on;
        proxy_ssl_name generativelanguage.googleapis.com;
        proxy_ssl_verify on;
        proxy_ssl_trusted_certificate /etc/ssl/certs/ca-certificates.crt;
        proxy_set_header Host generativelanguage.googleapis.com;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_read_timeout 180s;
        proxy_send_timeout 180s;
        client_max_body_size 10m;
        access_log off;
    }
}
EOF
ln -sf /etc/nginx/sites-available/gemini-relay /etc/nginx/sites-enabled/gemini-relay
rm -f /etc/nginx/sites-enabled/default
nginx -t -q
systemctl enable --quiet nginx
systemctl restart nginx

if [ "$UPDATE_ONLY" = "1" ]; then
    echo "== Testing gold price routes..."
    for path in "/yahoo/v8/finance/chart/GC=F?interval=1d&range=5d" "/swissquote/public-quotes/bboquotes/instrument/XAU/USD" "/goldapi/price/XAU"; do
        CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "http://127.0.0.1:${RELAY_PORT}${path}" || true)
        echo "   ${path%%\?*}: code $CODE (200 = OK)"
    done
    echo "== Done. Relay config updated; tunnel key and firewall unchanged."
    exit 0
fi

echo "== Creating the tunnel-only user 'relay'..."
if ! id relay >/dev/null 2>&1; then
    useradd --create-home --shell /usr/sbin/nologin relay
fi
usermod -p '*' relay   # no password at all, but not "locked" (locked accounts can't use SSH keys on some servers)
install -d -m 700 -o relay -g relay /home/relay/.ssh
echo "restrict,port-forwarding,permitopen=\"127.0.0.1:${RELAY_PORT}\" ${PUBKEY}" > /home/relay/.ssh/authorized_keys
chown relay:relay /home/relay/.ssh/authorized_keys
chmod 600 /home/relay/.ssh/authorized_keys

echo "== Turning on the firewall (SSH only)..."
SSH_PORTS=$(sshd -T 2>/dev/null | awk '$1 == "port" {print $2}')
SSH_PORTS=${SSH_PORTS:-22}
for p in $SSH_PORTS; do
    ufw allow "${p}/tcp" >/dev/null
done
ufw --force enable >/dev/null

echo "== Testing the connection to Google..."
CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 20 "http://127.0.0.1:${RELAY_PORT}/v1beta/models" || true)
if [ "$CODE" = "000" ] || [ "$CODE" = "502" ] || [ "$CODE" = "504" ]; then
    echo "!! This server could not reach Google (code $CODE). Check its internet connection."
    exit 1
fi

echo
echo "== Done. Google answered (code $CODE; an error code here is normal, there is no API key on this server)."
echo "== SSH port(s) of this server: $SSH_PORTS"
echo "== Now go back to the Iran server and run:"
echo "     bash ~/quant/relay/iran_setup.sh connect THIS_SERVER_IP"
