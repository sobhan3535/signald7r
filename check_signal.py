"""
Signal Watcher — چک کردن کراس میانگین متحرک و ارسال ایمیل هشدار
این اسکریپت طوری طراحی شده که هر بار اجرا می‌شود، فقط آخرین کندل بسته‌شده را
بررسی می‌کند. اگر با GitHub Actions هر ۱۵ دقیقه (هم‌زمان با تایم‌فریم چارت)
اجرا شود، هر کندل دقیقاً یک‌بار بررسی می‌شود و نیازی به ذخیره وضعیت قبلی نیست.
"""

import os
from datetime import datetime, timezone
import urllib.request
import json

# ---------- تنظیمات (از GitHub Secrets خوانده می‌شود) ----------
SYMBOL = os.environ.get("SYMBOL", "TAOUSDT")
INTERVAL = os.environ.get("INTERVAL", "15m")   # باید با زمان‌بندی cron هماهنگ باشد
FAST_LEN = int(os.environ.get("FAST_LEN", "9"))
SLOW_LEN = int(os.environ.get("SLOW_LEN", "21"))

# ایمیل از طریق Resend API ارسال می‌شود — ساده‌ترین گزینه، بدون نیاز به دامنه یا پسورد گوگل
RESEND_API_KEY = os.environ["RESEND_API_KEY"]   # از dashboard Resend -> API Keys
ALERT_TO       = os.environ["ALERT_TO"]         # همون ایمیلی که با آن در Resend ثبت‌نام کردی


def fetch_klines(symbol, interval, limit=100):
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    with urllib.request.urlopen(url, timeout=15) as res:
        data = json.loads(res.read().decode())
    return [
        {
            "time": row[0],
            "close": float(row[4]),
        }
        for row in data
    ]


def sma(values, length):
    out = [None] * len(values)
    running = 0.0
    for i, v in enumerate(values):
        running += v
        if i >= length:
            running -= values[i - length]
        if i >= length - 1:
            out[i] = running / length
    return out


def send_email(subject, body):
    """ارسال ایمیل از طریق Resend API (بدون نیاز به دامنه یا پسورد گوگل)."""
    url = "https://api.resend.com/emails"
    payload = {
        "from": "onboarding@resend.dev",
        "to": [ALERT_TO],
        "subject": subject,
        "text": body,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        if res.status not in (200, 201):
            raise RuntimeError(f"Resend API error: {res.status} {res.read()}")


def main():
    candles = fetch_klines(SYMBOL, INTERVAL, limit=max(100, SLOW_LEN + 5))
    closes = [c["close"] for c in candles]

    fast = sma(closes, FAST_LEN)
    slow = sma(closes, SLOW_LEN)

    # فقط دو کندل آخرِ *بسته‌شده* را بررسی می‌کنیم (کندل آخر لیست ممکن است هنوز در حال شکل‌گیری باشد،
    # پس از ایندکس -2 و -3 استفاده می‌کنیم تا مطمئن باشیم کندل کامل است)
    i, j = -2, -3
    if fast[i] is None or fast[j] is None or slow[i] is None or slow[j] is None:
        print("داده کافی برای محاسبه میانگین‌ها وجود ندارد.")
        return

    prev_diff = fast[j] - slow[j]
    curr_diff = fast[i] - slow[i]
    price = closes[i]
    candle_time = datetime.fromtimestamp(candles[i]["time"] / 1000, tz=timezone.utc)

    if prev_diff <= 0 and curr_diff > 0:
        subject = f"🔺 {SYMBOL} — کراس صعودی"
        body = f"نماد: {SYMBOL}\nقیمت: {price}\nزمان کندل (UTC): {candle_time}\nتایم‌فریم: {INTERVAL}"
        send_email(subject, body)
        print("ایمیل کراس صعودی ارسال شد.")
    elif prev_diff >= 0 and curr_diff < 0:
        subject = f"🔻 {SYMBOL} — کراس نزولی"
        body = f"نماد: {SYMBOL}\nقیمت: {price}\nزمان کندل (UTC): {candle_time}\nتایم‌فریم: {INTERVAL}"
        send_email(subject, body)
        print("ایمیل کراس نزولی ارسال شد.")
    else:
        print(f"سیگنالی نیست. fast={fast[i]:.4f} slow={slow[i]:.4f}")


if __name__ == "__main__":
    main()
