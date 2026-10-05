#!/bin/bash
# mt5_install.sh - نصب متاتریدر ۵ برای کاربر trader (داخل Remote Desktop اجرا شود)
#
# setup.sh این فایل را در /home/trader گذاشته. داخل Remote Desktop یک Terminal باز کنید و بزنید:
#   ./mt5_install.sh
# کارها:
#   1. برنامه نصب رسمی متاتریدر ۵ را از سایت MetaQuotes می‌گیرد و با Wine اجرا می‌کند
#      (پنجره نصب باز می‌شود؛ Next، Next، Finish)
#   2. اکسپرت PriceBridge را در پوشه Experts متاتریدر کپی می‌کند
#   3. رمز مشترک را نشان می‌دهد تا در تنظیمات اکسپرت paste کنید

set -euo pipefail
export WINEPREFIX="$HOME/.mt5"
export WINEDEBUG=-all
MT5_DIR="$WINEPREFIX/drive_c/Program Files/MetaTrader 5"

if [ "$(id -un)" = "root" ]; then
    echo "!! Run this as the trader user inside Remote Desktop, not as root."
    exit 1
fi

if [ ! -f "$MT5_DIR/terminal64.exe" ]; then
    echo "== Preparing Wine (first time takes a minute; click Install if a window asks)..."
    wineboot -u >/dev/null 2>&1 || true
    echo "== Downloading the official MetaTrader 5 installer..."
    wget -q -O /tmp/mt5setup.exe "https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"
    echo "== Installer window is opening. Click Next ... Finish. MetaTrader opens at the end."
    wine /tmp/mt5setup.exe || true
fi

if [ ! -f "$MT5_DIR/terminal64.exe" ]; then
    echo "!! MetaTrader was not installed in: $MT5_DIR"
    exit 1
fi

mkdir -p "$MT5_DIR/MQL5/Experts"
cp "$HOME/PriceBridge.mq5" "$MT5_DIR/MQL5/Experts/"
echo "== PriceBridge copied to MQL5/Experts."
echo
echo "== In MetaTrader (see germany/README.md, step 6):"
echo "   ServerURL = http://127.0.0.1:8787/mt/push"
echo "   Secret    = $(cat "$HOME/mt_bridge_secret.txt")"
echo
echo "== To open MetaTrader again later inside Remote Desktop:"
echo "   WINEPREFIX=~/.mt5 wine \"$MT5_DIR/terminal64.exe\" &"
