#!/bin/bash
# setup.sh - آماده کردن سرور آلمان (ربات تلگرام + جیمینای مستقیم + گیرنده متاتریدر)
#
# روی یک سرور تازه Ubuntu 22.04/24.04، با کاربر root:
#   bash setup.sh
# چند بار اجرا کردنش ضرری ندارد؛ کارهای انجام‌شده را دوباره نمی‌کند.
#
# کارها به ترتیب:
#   1. نصب کتابخانه‌ها از apt (همان‌هایی که سرور ایران دارد)
#   2. ساختن Swap (اگر نباشد)، تا رم پر نشود
#   3. گرفتن کد از GitHub در /root/quant (اگر مخزن خصوصی باشد، یک «کلید فقط‌خواندنی» می‌سازد
#      و می‌گوید کجای GitHub بگذارید)
#   4. ساختن رمز مشترک متاتریدر (~/.mt_bridge_secret)
#   5. دیوار آتش: فقط SSH و درگاه 8787 (گیرنده متاتریدر) باز
#   6. نصب سرویس‌ها: mt-bridge همیشه؛ telegram-bot وقتی ~/.telegram_token باشد
#   7. بررسی همه اتصال‌ها با check_connections.py
# کلیدها (تلگرام، جیمینای) را این اسکریپت نمی‌سازد و نمی‌بیند؛ خودتان با nano می‌گذارید (راهنما: germany/README.md).

set -euo pipefail

REPO="alirezahabibitanha-bit/quant"
DIR=/root/quant
DEPLOY_KEY="$HOME/.ssh/github_quant"
MT_PORT=8787

if [ "$(id -u)" -ne 0 ]; then
    echo "!! Run this as root."
    exit 1
fi

echo "== 1/7 Installing packages (a few minutes)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-pandas python3-requests python3-numpy git ufw curl screen openssl

echo "== 2/7 Swap..."
if swapon --show | grep -q .; then
    echo "   swap already on."
else
    fallocate -l 2G /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    chmod 600 /swapfile
    mkswap -q /swapfile
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "   2 GB swap created."
fi

echo "== 3/7 Getting the code from GitHub..."
if [ -d "$DIR/.git" ]; then
    echo "   $DIR already exists; to update it later use ~/quant/update.sh"
elif GIT_TERMINAL_PROMPT=0 git clone -q "https://github.com/$REPO.git" "$DIR" 2>/dev/null; then
    echo "   cloned (public repository)."
else
    install -d -m 700 "$HOME/.ssh"
    [ -f "$DEPLOY_KEY" ] || ssh-keygen -q -t ed25519 -N "" -C "germany-server-readonly" -f "$DEPLOY_KEY"
    if ! grep -q "github_quant" "$HOME/.ssh/config" 2>/dev/null; then
        printf 'Host github.com\n    IdentityFile %s\n    IdentitiesOnly yes\n' "$DEPLOY_KEY" >> "$HOME/.ssh/config"
        chmod 600 "$HOME/.ssh/config"
    fi
    ssh-keyscan -q github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null
    echo
    echo "   The repository is private. Add this READ-ONLY key on GitHub:"
    echo "   github.com/$REPO -> Settings -> Deploy keys -> Add deploy key"
    echo "   (Title: germany server; leave 'Allow write access' OFF)"
    echo
    cat "$DEPLOY_KEY.pub"
    echo
    for try in 1 2 3; do
        read -r -p "   Press Enter after adding the key... " _
        if git clone -q "git@github.com:$REPO.git" "$DIR"; then
            echo "   cloned."
            break
        fi
        [ "$try" = 3 ] && { echo "!! Clone failed 3 times. Check the deploy key and run this script again."; exit 1; }
        echo "   Not yet. Check that the whole key line was pasted."
    done
fi
cd "$DIR"

echo "== 4/7 MetaTrader secret..."
if [ ! -s "$HOME/.mt_bridge_secret" ]; then
    openssl rand -hex 24 > "$HOME/.mt_bridge_secret"
    echo "   new secret created in ~/.mt_bridge_secret"
else
    echo "   secret already exists."
fi
chmod 600 "$HOME/.mt_bridge_secret"
if [ -f "$HOME/.gemini_base_url" ]; then
    echo "   WARNING: ~/.gemini_base_url exists. It is only for the Iran server (tunnel)."
    echo "            Here Gemini is reached directly. Remove it with:  rm ~/.gemini_base_url"
fi

echo "== 5/7 Firewall (only SSH and MetaTrader port open)..."
SSH_PORT=$(sshd -T 2>/dev/null | awk '/^port /{print $2; exit}')
SSH_PORT=${SSH_PORT:-22}
ufw allow "$SSH_PORT/tcp" >/dev/null
ufw allow "$MT_PORT/tcp" >/dev/null
ufw --force enable >/dev/null
echo "   open: $SSH_PORT (SSH), $MT_PORT (MetaTrader)"

echo "== 6/7 Services..."
cp mt-bridge.service telegram-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --quiet --now mt-bridge
systemctl restart mt-bridge
if [ -s "$HOME/.telegram_token" ]; then
    systemctl enable --quiet --now telegram-bot
    systemctl restart telegram-bot
    echo "   telegram-bot started."
    [ -s "$HOME/.telegram_allowed" ] || echo "   (no ~/.telegram_allowed yet: the bot only tells people their id)"
else
    echo "   telegram-bot NOT started yet: put the token in ~/.telegram_token"
    echo "   (see germany/README.md), then run this script again."
fi
sleep 3

echo "== 7/7 Checking connections..."
python3 check_connections.py || true

IP=$(curl -4 -s --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')
echo
echo "== Done. For the PriceBridge expert in MetaTrader:"
echo "   Allowed URL : http://$IP:$MT_PORT"
echo "   ServerURL   : http://$IP:$MT_PORT/mt/push"
echo "   Secret      : $(cat "$HOME/.mt_bridge_secret")"
