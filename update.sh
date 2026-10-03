#!/bin/bash
# update.sh - گرفتن آخرین نسخه کد از GitHub و روشن کردن دوباره ربات بله
#
# اجرا (روی سرور):   ~/quant/update.sh
#
# کارها به ترتیب:
#   1. نشان می‌دهد در GitHub چه چیزی جدید است
#   2. آن را می‌گیرد (فقط اگر روی سرور فایلی دستی عوض نشده باشد)
#   3. بررسی می‌کند کدها سالم باشند؛ اگر نبودند به نسخه قبلی برمی‌گردد
#   4. ربات را دوباره روشن می‌کند
# این اسکریپت خودکار اجرا نمی‌شود؛ فقط وقتی خودتان بزنید.

set -u
cd "$(dirname "$0")" || exit 1

echo "== Current version: $(git log --oneline -1)"

if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "!! Some project files were changed by hand on this server:"
    git status --short --untracked-files=no
    echo "!! Update stopped so nothing is lost. To throw away those changes, run:"
    echo "     git checkout -- . && ~/quant/update.sh"
    exit 1
fi

echo "== Checking GitHub..."
if ! git fetch --quiet origin; then
    echo "!! Could not reach GitHub. Check the internet connection or the GitHub key."
    exit 1
fi

OLD=$(git rev-parse HEAD)
NEW=$(git rev-parse '@{u}')
if [ "$OLD" = "$NEW" ]; then
    echo "== Already up to date. Nothing to do."
    exit 0
fi

echo "== New changes:"
git log --oneline "$OLD..$NEW"

if ! git merge --quiet --ff-only '@{u}'; then
    echo "!! Could not apply the update. Nothing was changed."
    exit 1
fi

echo "== Checking the code..."
if ! python3 -m py_compile advisor.py bale_bot.py; then
    echo "!! The new code has an error. Going back to the previous version."
    git reset --quiet --hard "$OLD"
    exit 1
fi

if ! systemctl is-enabled --quiet bale-bot 2>/dev/null; then
    echo "== Updated. The bale-bot service is not installed, so restart the bot yourself."
    exit 0
fi

if git diff --name-only "$OLD" HEAD | grep -qx "bale-bot.service"; then
    echo "== Service file changed; installing the new one..."
    cp bale-bot.service /etc/systemd/system/
    systemctl daemon-reload
fi

echo "== Restarting the bot..."
systemctl restart bale-bot
sleep 5
if systemctl is-active --quiet bale-bot; then
    echo "== Done. Bot is running: $(git log --oneline -1)"
else
    echo "!! The bot did not start. Last messages:"
    journalctl -u bale-bot -n 20 --no-pager
    exit 1
fi
