#!/bin/bash
# update.sh - گرفتن آخرین نسخه کد از GitHub و روشن کردن دوباره ربات (بله یا تلگرام) و گیرنده متاتریدر
#
# اجرا (روی سرور):   ~/quant/update.sh
#
# کارها به ترتیب:
#   1. نشان می‌دهد در GitHub چه چیزی جدید است
#   2. آن را می‌گیرد (فقط اگر روی سرور فایلی دستی عوض نشده باشد)
#   3. بررسی می‌کند کدها سالم باشند؛ اگر نبودند به نسخه قبلی برمی‌گردد
#   4. سرویس‌های نصب‌شده روی همین سرور را دوباره روشن می‌کند (bale-bot / telegram-bot / mt-bridge)
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
if ! python3 -m py_compile advisor.py bale_bot.py news_ai.py mt_bridge.py; then
    echo "!! The new code has an error. Going back to the previous version."
    git reset --quiet --hard "$OLD"
    exit 1
fi

# سرور ایران: bale-bot | سرور آلمان: telegram-bot و mt-bridge. فقط سرویس‌هایی که نصب شده‌اند.
FAILED=0
FOUND=0
for SVC in bale-bot telegram-bot mt-bridge; do
    systemctl is-enabled --quiet "$SVC" 2>/dev/null || continue
    FOUND=1
    if git diff --name-only "$OLD" HEAD | grep -qx "$SVC.service"; then
        echo "== $SVC.service changed; installing the new one..."
        cp "$SVC.service" /etc/systemd/system/
        systemctl daemon-reload
    fi
    echo "== Restarting $SVC..."
    systemctl restart "$SVC"
done

if [ "$FOUND" = 0 ]; then
    echo "== Updated. No service is installed, so restart the bot yourself."
    exit 0
fi

sleep 5
for SVC in bale-bot telegram-bot mt-bridge; do
    systemctl is-enabled --quiet "$SVC" 2>/dev/null || continue
    if systemctl is-active --quiet "$SVC"; then
        echo "== $SVC is running."
    else
        echo "!! $SVC did not start. Last messages:"
        journalctl -u "$SVC" -n 20 --no-pager
        FAILED=1
    fi
done
[ "$FAILED" = 0 ] && echo "== Done: $(git log --oneline -1)"
exit "$FAILED"
