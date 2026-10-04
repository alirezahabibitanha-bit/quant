#!/bin/bash
# iran_setup.sh - وصل کردن سرور ایران به سرور خارج با تونل SSH (برای رسیدن به جیمینای)
#
# دو مرحله دارد (هر دو روی سرور ایران، با کاربر root):
#   مرحله ۱:  bash ~/quant/relay/iran_setup.sh key
#             یک کلید مخصوص تونل می‌سازد و چاپ می‌کند. آن را برای foreign_setup.sh لازم دارید.
#   مرحله ۲:  bash ~/quant/relay/iran_setup.sh connect IP_SERVER_KHAREJ [PORT_SSH]
#             (PORT_SSH اگر ۲۲ نیست؛ foreign_setup.sh آن را چاپ می‌کند)
#             سرویس gemini-tunnel را می‌سازد که تونل را همیشه روشن نگه می‌دارد
#             (با روشن شدن سرور روشن می‌شود و اگر قطع شد بعد از ۱۰ ثانیه دوباره وصل می‌شود).
#             آدرس واسطه را در ~/.gemini_base_url می‌نویسد و با کلید ~/.gemini_key تست می‌کند.
#
# دستورهای روزمره:
#   وضعیت تونل:     systemctl status gemini-tunnel
#   پیام‌های تونل:   journalctl -u gemini-tunnel -n 30
#   وصل کردن دوباره: systemctl restart gemini-tunnel

set -euo pipefail

KEY="$HOME/.ssh/gemini_relay"
KNOWN="$HOME/.ssh/gemini_relay_known_hosts"
LOCAL_PORT=18080
REMOTE_PORT=8080
SERVICE=/etc/systemd/system/gemini-tunnel.service

if [ "$(id -u)" -ne 0 ]; then
    echo "!! Run this as root."
    exit 1
fi

case "${1:-}" in
key)
    install -d -m 700 "$HOME/.ssh"
    if [ ! -f "$KEY" ]; then
        ssh-keygen -q -t ed25519 -N "" -C "gemini-relay" -f "$KEY"
        echo "== New tunnel key created."
    else
        echo "== Tunnel key already exists; showing it again."
    fi
    echo
    echo "== On the FOREIGN server run (copy the whole line):"
    echo
    echo "bash foreign_setup.sh \"$(cat "$KEY.pub")\""
    echo
    ;;

connect)
    HOST="${2:-}"
    SSH_PORT="${3:-22}"
    if [[ ! "$HOST" =~ ^[A-Za-z0-9.:-]+$ ]] || [[ ! "$SSH_PORT" =~ ^[0-9]+$ ]]; then
        echo "!! Usage: bash iran_setup.sh connect FOREIGN_SERVER_IP [SSH_PORT]"
        exit 1
    fi
    if [ ! -f "$KEY" ]; then
        echo "!! No tunnel key yet. First run: bash iran_setup.sh key"
        exit 1
    fi

    echo "== Writing the tunnel service..."
    cat > "$SERVICE" <<EOF
# gemini-tunnel: encrypted SSH tunnel from this server to the foreign relay (made by relay/iran_setup.sh)
[Unit]
Description=SSH tunnel to the foreign Gemini relay
After=network-online.target
Wants=network-online.target

[Service]
User=root
ExecStart=/usr/bin/ssh -N -i $KEY -p $SSH_PORT \\
    -o BatchMode=yes -o IdentitiesOnly=yes \\
    -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$KNOWN \\
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \\
    -L 127.0.0.1:$LOCAL_PORT:127.0.0.1:$REMOTE_PORT relay@$HOST
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable --quiet gemini-tunnel
    systemctl restart gemini-tunnel

    echo "http://127.0.0.1:$LOCAL_PORT" > "$HOME/.gemini_base_url"
    echo "== Relay address saved in ~/.gemini_base_url"

    echo "== Waiting for the tunnel..."
    for _ in $(seq 1 15); do
        if curl -s -o /dev/null --max-time 3 "http://127.0.0.1:$LOCAL_PORT/"; then
            break
        fi
        sleep 2
    done
    if ! systemctl is-active --quiet gemini-tunnel || \
       ! curl -s -o /dev/null --max-time 5 "http://127.0.0.1:$LOCAL_PORT/"; then
        echo "!! The tunnel did not come up. Last messages:"
        journalctl -u gemini-tunnel -n 15 --no-pager
        exit 1
    fi
    echo "== Tunnel is up."

    if [ ! -f "$HOME/.gemini_key" ]; then
        echo "== No ~/.gemini_key yet, so Gemini itself was not tested. Put the key there and run:"
        echo "     bash ~/quant/relay/iran_setup.sh test"
        exit 0
    fi
    exec bash "$0" test
    ;;

test)
    BASE=$(cat "$HOME/.gemini_base_url" 2>/dev/null || echo "http://127.0.0.1:$LOCAL_PORT")
    REPLY=$(curl -s --max-time 30 -H "x-goog-api-key: $(cat "$HOME/.gemini_key")" "$BASE/v1beta/models" || true)
    if echo "$REPLY" | grep -q '"models"'; then
        echo "== Gemini works through the relay."
    elif echo "$REPLY" | grep -qi "location"; then
        echo "!! Google says the location is not supported. The foreign server's country is blocked too;"
        echo "!! a server in Germany, the Netherlands, Finland or France should work."
        exit 1
    elif echo "$REPLY" | grep -qi "api key"; then
        echo "!! Google rejected the API key. Check ~/.gemini_key (only the key, nothing else)."
        exit 1
    else
        echo "!! Unexpected answer:"
        echo "$REPLY" | head -c 400; echo
        exit 1
    fi
    ;;

*)
    echo "Usage:"
    echo "  bash iran_setup.sh key                         (step 1: make the tunnel key)"
    echo "  bash iran_setup.sh connect FOREIGN_IP [PORT]   (step 2: start the tunnel)"
    echo "  bash iran_setup.sh test                        (check Gemini through the tunnel)"
    exit 1
    ;;
esac
