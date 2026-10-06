#!/bin/bash
# setup.sh - آماده کردن سرور آلمان: ربات تلگرام + جیمینای (+ متاتریدر ۵، اختیاری)
#
# روی یک سرور تازه Ubuntu 24.04 (یا 22.04)، با کاربر root:
#   bash setup.sh                 # ساده: قیمت از بازار جهانی (مثل ربات بله)، بدون متاتریدر
#   bash setup.sh --metatrader    # کامل: متاتریدر با Wine و Remote Desktop (x86_64، ۲ گیگ رم)
# حالت متاتریدر اگر یک بار نصب شود می‌ماند (اجرای بعدی بدون --metatrader آن را برنمی‌دارد).
# چند بار اجرا کردنش ضرری ندارد؛ کارهای انجام‌شده را دوباره نمی‌کند.
#
# کارها به ترتیب:
#   1. نصب کتابخانه‌های پایتون از apt (همان‌هایی که سرور ایران دارد)
#   2. Swap: با رم کمتر از ۳٫۵ گیگ، ۴ گیگ؛ وگرنه ۲ گیگ (اگر کمتر باشد)
#   3. گرفتن کد از GitHub در /root/quant (اگر مخزن خصوصی باشد، یک «کلید فقط‌خواندنی» می‌سازد)
#   (فقط با --metatrader: قدم‌های ۴ و ۵ و سرویس‌های mt-bridge و mt5)
#   4. نصب Wine (برای اجرای متاتریدر ویندوزی روی لینوکس) و یک میز کار سبک (XFCE) با «Remote Desktop»
#      - کاربر جدا به اسم trader برای متاتریدر (رمزش را یک بار می‌پرسد)
#      - Remote Desktop از اینترنت بسته است؛ فقط از راه تونل رمزشده SSH (راهنما: germany/README.md)
#   5. ساختن رمز مشترک متاتریدر و کپی اکسپرت PriceBridge برای trader
#   6. دیوار آتش: فقط SSH باز
#   7. نصب سرویس‌ها: mt-bridge (گیرنده قیمت)، telegram-bot (اگر ~/.telegram_token باشد)،
#      mt5 (متاتریدر بدون صفحه نمایش؛ وقتی متاتریدر یک بار نصب و تنظیم شد)
#   8. بررسی اتصال‌ها با check_connections.py
# کلیدها (تلگرام، جیمینای، رمز حساب متاتریدر) را این اسکریپت نمی‌سازد و نمی‌بیند.

set -euo pipefail

REPO="alirezahabibitanha-bit/quant"
DIR=/root/quant
DEPLOY_KEY="$HOME/.ssh/github_quant"
TRADER=trader
MT5_EXE="/home/$TRADER/.mt5/drive_c/Program Files/MetaTrader 5/terminal64.exe"
MT_DROPIN=/etc/systemd/system/telegram-bot.service.d/metatrader.conf

MT=0
[ "${1:-}" = "--metatrader" ] && MT=1
[ -f "$MT_DROPIN" ] && MT=1     # قبلاً با متاتریدر نصب شده

if [ "$(id -u)" -ne 0 ]; then
    echo "!! Run this as root."
    exit 1
fi
if [ "$MT" = 1 ]; then
    echo "== Mode: Telegram bot + MetaTrader"
    if [ "$(awk '/^MemTotal:/{print int($2/1024)}' /proc/meminfo)" -lt 1800 ]; then
        echo "!! Less than 2 GB RAM. MetaTrader with Wine needs at least 2 GB."
        exit 1
    fi
    if [ "$(dpkg --print-architecture)" != "amd64" ]; then
        echo "!! This server is not x86_64 (amd64). MetaTrader/Wine needs an x86_64 server, not ARM."
        exit 1
    fi
else
    echo "== Mode: Telegram bot with global market prices (no MetaTrader)"
fi
. /etc/os-release

echo "== 1/8 Installing Python packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-pandas python3-requests python3-numpy git ufw curl wget screen openssl

echo "== 2/8 Swap..."
# با ۲ گیگ رم (متاتریدر + Wine + ربات) جای اضافه کم است؛ Swap بزرگ‌تر جلوی قفل شدن سرور را می‌گیرد.
RAM_MB=$(awk '/^MemTotal:/{print int($2/1024)}' /proc/meminfo)
SWAP_MB=$(awk '/^SwapTotal:/{print int($2/1024)}' /proc/meminfo)
WANT_MB=$([ "$RAM_MB" -lt 3500 ] && echo 4096 || echo 2048)
if [ "$SWAP_MB" -ge $((WANT_MB * 9 / 10)) ]; then
    echo "   swap already ${SWAP_MB} MB (RAM ${RAM_MB} MB)."
elif [ -f /swapfile ]; then
    echo "   WARNING: /swapfile exists but total swap is only ${SWAP_MB} MB; wanted ${WANT_MB} MB. Left as is."
else
    fallocate -l "${WANT_MB}M" /swapfile 2>/dev/null || dd if=/dev/zero of=/swapfile bs=1M count="$WANT_MB" status=none
    chmod 600 /swapfile
    mkswap -q /swapfile
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "   ${WANT_MB} MB swap created (RAM ${RAM_MB} MB)."
fi
# فقط وقتی رم واقعاً پر است از Swap استفاده شود (کندی کمتر)
echo 'vm.swappiness=10' > /etc/sysctl.d/90-quant-swap.conf
sysctl -q -p /etc/sysctl.d/90-quant-swap.conf || true

echo "== 3/8 Getting the code from GitHub..."
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

for f in .gemini_base_url .proxy_url; do
    if [ -f "$HOME/$f" ]; then
        echo "   WARNING: ~/$f exists. It is only for the Iran server."
        echo "            Here Gemini and gold prices are reached directly. Remove it with:  rm ~/$f"
    fi
done

if [ "$MT" = 0 ]; then
    echo "== 4/8 and 5/8 skipped (MetaTrader not wanted; to add it later: bash setup.sh --metatrader)"
else
echo "== 4/8 Wine, light desktop and Remote Desktop (several minutes)..."
if ! command -v wine >/dev/null; then
    dpkg --add-architecture i386
    install -d -m 755 /etc/apt/keyrings
    wget -q -O /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key
    wget -q -O "/etc/apt/sources.list.d/winehq-$VERSION_CODENAME.sources" \
        "https://dl.winehq.org/wine-builds/ubuntu/dists/$VERSION_CODENAME/winehq-$VERSION_CODENAME.sources"
    apt-get update -q
    apt-get install -y -q --install-recommends winehq-stable
fi
# xorgxrdp و Xorg صریح: روی بعضی سرورها «بسته‌های پیشنهادی» نصب نمی‌شوند و بدون اینها Remote Desktop فقط صفحه آبی نشان می‌دهد
apt-get install -y -q xfce4 xfce4-terminal dbus-x11 xrdp xorgxrdp xserver-xorg-core xvfb
adduser --quiet xrdp ssl-cert 2>/dev/null || true
if ! id "$TRADER" >/dev/null 2>&1; then
    adduser --quiet --disabled-password --gecos "" "$TRADER"
    echo "   Choose a password for the Remote Desktop user '$TRADER':"
    until passwd "$TRADER"; do echo "   Try again."; done
fi
echo "xfce4-session" > "/home/$TRADER/.xsession"
chown "$TRADER:$TRADER" "/home/$TRADER/.xsession"
systemctl enable --quiet --now xrdp

echo "== 5/8 MetaTrader secret and files for $TRADER..."
if [ ! -s "$HOME/.mt_bridge_secret" ]; then
    openssl rand -hex 24 > "$HOME/.mt_bridge_secret"
    echo "   new secret created in ~/.mt_bridge_secret"
fi
chmod 600 "$HOME/.mt_bridge_secret"
install -o "$TRADER" -g "$TRADER" -m 600 "$HOME/.mt_bridge_secret" "/home/$TRADER/mt_bridge_secret.txt"
install -o "$TRADER" -g "$TRADER" -m 644 metatrader/PriceBridge.mq5 "/home/$TRADER/PriceBridge.mq5"
install -o "$TRADER" -g "$TRADER" -m 755 germany/mt5_install.sh "/home/$TRADER/mt5_install.sh"
fi

echo "== 6/8 Firewall (only SSH open)..."
# همه درگاه‌هایی که SSH رویشان گوش می‌دهد باز می‌مانند (مثلاً اگر برای ایران درگاه 443 هم اضافه شده)،
# به‌علاوه درگاهی که همین الان با آن وصل هستید؛ تا هیچ‌وقت خودتان را از سرور بیرون نیندازید.
SSH_PORTS=$( { sshd -T 2>/dev/null | awk '/^port /{print $2}'
               systemctl show -p Listen ssh.socket 2>/dev/null | grep -oE ':[0-9]+ ' | tr -d ': '
               [ -n "${SSH_CONNECTION:-}" ] && echo "$SSH_CONNECTION" | awk '{print $4}'; } | sort -un)
SSH_PORTS=${SSH_PORTS:-22}
for P in $SSH_PORTS; do ufw allow "$P/tcp" >/dev/null; done
ufw --force enable >/dev/null
SSH_PORT=$( [ -n "${SSH_CONNECTION:-}" ] && echo "$SSH_CONNECTION" | awk '{print $4}' || echo "$SSH_PORTS" | head -1)
echo "   open (SSH): $(echo $SSH_PORTS)"

echo "== 7/8 Services..."
cp telegram-bot.service /etc/systemd/system/
if [ "$MT" = 1 ]; then
    cp mt-bridge.service mt5.service /etc/systemd/system/
    # ربات تلگرام قیمت را از متاتریدر بخواند (فایل کمکی؛ با update.sh پاک نمی‌شود)
    mkdir -p "$(dirname "$MT_DROPIN")"
    printf '[Service]\nExecStart=\nExecStart=/usr/bin/python3 %s/bale_bot.py --telegram --metatrader\n' "$DIR" > "$MT_DROPIN"
fi
systemctl daemon-reload
if [ "$MT" = 1 ]; then
    systemctl enable --quiet --now mt-bridge
    systemctl restart mt-bridge
fi
if [ -s "$HOME/.telegram_token" ]; then
    systemctl enable --quiet --now telegram-bot
    systemctl restart telegram-bot
    echo "   telegram-bot started."
    [ -s "$HOME/.telegram_allowed" ] || echo "   (no ~/.telegram_allowed yet: the bot only tells people their id)"
else
    echo "   telegram-bot NOT started yet: put the token in ~/.telegram_token, then run this script again."
fi
if [ "$MT" = 0 ]; then
    :
elif [ -f "$MT5_EXE" ]; then
    if loginctl list-sessions --no-legend 2>/dev/null | grep -qw "$TRADER"; then
        echo "   mt5 NOT started: $TRADER is logged in with Remote Desktop. Close MetaTrader (File > Exit),"
        echo "   log out of Remote Desktop, then run:  systemctl enable --now mt5"
    else
        systemctl enable --quiet --now mt5
        echo "   mt5 (MetaTrader without screen) started."
    fi
else
    echo "   MetaTrader is not installed yet. Next: Remote Desktop -> run ~/mt5_install.sh (see germany/README.md)."
fi
sleep 3

echo "== 8/8 Checking connections..."
python3 check_connections.py || true

echo
echo "== Done."
if [ "$MT" = 0 ]; then
    echo "   Next steps: germany/README.md (Gemini key, Telegram token). Re-run this script after adding the token."
    exit 0
fi
IP=$(curl -4 -s --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')
echo "   Remote Desktop (from your Windows PC, keep this window open):"
echo "      ssh -L 33389:127.0.0.1:3389 -p $SSH_PORT root@$IP"
echo "   then open 'Remote Desktop Connection' and connect to:  localhost:33389   (user: $TRADER)"
