"""
Signal Watcher — سیستم سیگنال‌دهی ترکیبی (شبیه به مفهوم کلی D7R)
ترکیبی از ۴ جزء که هر کدوم یه بخش از تحلیل تکنیکال رو پوشش می‌دن:
  ۱. EMA Cross          -> تشخیص روند (Trend)
  ۲. RSI                -> تایید مومنتوم (Momentum)
  ۳. ATR                -> فیلتر نوسان/ساختار بازار (Market Structure)
  ۴. Scoring             -> سیگنال فقط وقتی صادر می‌شه که هر سه شرط تایید کنن

این اسکریپت طوری طراحی شده که هر بار اجرا می‌شود، فقط آخرین کندل بسته‌شده را
بررسی می‌کند. اگر با GitHub Actions هر ۱۵ دقیقه (هم‌زمان با تایم‌فریم چارت)
اجرا شود، هر کندل دقیقاً یک‌بار بررسی می‌شود و نیازی به ذخیره وضعیت قبلی نیست.

نکته مهم: این یه بازسازی مستقل بر اساس مفاهیم عمومی تحلیل تکنیکاله،
نه بازسازی دقیق فرمول‌های واقعی D7R (که منتشر نشدن).
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
RSI_LEN = int(os.environ.get("RSI_LEN", "14"))
ATR_LEN = int(os.environ.get("ATR_LEN", "14"))
MIN_SCORE = int(os.environ.get("MIN_SCORE", "3"))   # از ۳ امتیاز ممکن (کراس + RSI + ATR) — سخت‌گیرترین حالت: هر سه شرط باید تایید کنن

# ایمیل از طریق Resend API ارسال می‌شود — ساده‌ترین گزینه، بدون نیاز به دامنه یا پسورد گوگل
RESEND_API_KEY = os.environ["RESEND_API_KEY"]   # از dashboard Resend -> API Keys
ALERT_TO       = os.environ["ALERT_TO"]         # همون ایمیلی که با آن در Resend ثبت‌نام کردی


def fetch_klines(symbol, interval, limit=200):
    """دریافت کندل‌ها (شامل high/low/close) از Bitunix."""
    url = f"https://fapi.bitunix.com/api/v1/futures/market/kline?symbol={symbol}&interval={interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as res:
        payload = json.loads(res.read().decode())
    rows = payload.get("data", [])
    if not rows:
        raise RuntimeError(f"داده‌ای از Bitunix برنگشت: {payload}")
    candles = [
        {
            "time": int(row["time"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
        }
        for row in rows
    ]
    candles.sort(key=lambda c: c["time"])
    return candles


def ema(values, length):
    """میانگین متحرک نمایی — به قیمت‌های اخیر وزن بیشتری می‌ده و لگ کمتری داره."""
    out = [None] * len(values)
    k = 2 / (length + 1)
    prev = None
    for i, v in enumerate(values):
        if i == length - 1:
            prev = sum(values[:length]) / length
            out[i] = prev
        elif i >= length:
            prev = v * k + prev * (1 - k)
            out[i] = prev
    return out


def rsi(closes, length):
    """RSI کلاسیک با روش هموارسازی وایلدر."""
    out = [None] * len(closes)
    gains, losses = [], []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
        if i >= length:
            if i == length:
                avg_gain = sum(gains[:length]) / length
                avg_loss = sum(losses[:length]) / length
            else:
                avg_gain = (out[i - 1]["avg_gain"] * (length - 1) + gains[-1]) / length
                avg_loss = (out[i - 1]["avg_loss"] * (length - 1) + losses[-1]) / length
            rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
            value = 100 - (100 / (1 + rs))
            out[i] = {"value": value, "avg_gain": avg_gain, "avg_loss": avg_loss}
    return [o["value"] if o else None for o in out]


def atr(candles, length):
    """میانگین محدوده واقعی (ATR) — برای تشخیص کافی بودن نوسان بازار."""
    trs = [None]
    for i in range(1, len(candles)):
        high, low, prev_close = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
    out = [None] * len(candles)
    for i in range(length, len(candles)):
        window = [t for t in trs[i - length + 1:i + 1] if t is not None]
        if len(window) == length:
            out[i] = sum(window) / length
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
    candles = fetch_klines(SYMBOL, INTERVAL, limit=max(200, SLOW_LEN + RSI_LEN + ATR_LEN + 10))
    closes = [c["close"] for c in candles]

    fast = ema(closes, FAST_LEN)
    slow = ema(closes, SLOW_LEN)
    rsi_vals = rsi(closes, RSI_LEN)
    atr_vals = atr(candles, ATR_LEN)

    # فقط دو کندل آخرِ *بسته‌شده* را بررسی می‌کنیم
    i, j = -2, -3
    required = [fast[i], fast[j], slow[i], slow[j], rsi_vals[i], atr_vals[i]]
    if any(v is None for v in required):
        print("داده کافی برای محاسبه اندیکاتورها وجود ندارد.")
        return

    prev_diff = fast[j] - slow[j]
    curr_diff = fast[i] - slow[i]
    price = closes[i]
    candle_time = datetime.fromtimestamp(candles[i]["time"] / 1000, tz=timezone.utc)

    # میانگین ATR اخیر برای تشخیص «نوسان کافی» نسبت به شرایط عادی بازار
    # آستانه سخت‌گیرانه‌تر (۱.۱ برابر میانگین) تا فقط نوسان واقعاً بالاتر از حد عادی قبول شود
    recent_atr = [a for a in atr_vals[max(0, len(atr_vals) - 30):] if a is not None]
    avg_atr = sum(recent_atr) / len(recent_atr) if recent_atr else atr_vals[i]
    volatility_ok = atr_vals[i] >= avg_atr * 1.1

    direction = None
    if prev_diff <= 0 and curr_diff > 0:
        direction = "bull"
    elif prev_diff >= 0 and curr_diff < 0:
        direction = "bear"

    if direction is None:
        print(f"کراسی رخ نداده. fast={fast[i]:.4f} slow={slow[i]:.4f}")
        return

    # ---------- سیستم امتیازدهی (شبیه مفهوم D7 Scoring) ----------
    # آستانه RSI سخت‌گیرانه‌تر (۵۵/۴۵ به‌جای ۵۰) تا فقط مومنتوم واقعاً قوی قبول شود
    score = 1  # امتیاز پایه: خود کراس EMA رخ داده
    reasons = ["EMA Cross"]

    if direction == "bull" and rsi_vals[i] > 55:
        score += 1
        reasons.append(f"RSI موافق و قوی ({rsi_vals[i]:.1f} > 55)")
    elif direction == "bear" and rsi_vals[i] < 45:
        score += 1
        reasons.append(f"RSI موافق و قوی ({rsi_vals[i]:.1f} < 45)")

    if volatility_ok:
        score += 1
        reasons.append("نوسان بازار به‌وضوح بالاتر از حد عادی (ATR)")

    print(f"جهت: {direction} | امتیاز: {score}/3 | دلایل: {', '.join(reasons)}")

    if score < MIN_SCORE:
        print(f"امتیاز کافی نیست (حداقل لازم: {MIN_SCORE}) — سیگنالی ارسال نمی‌شود.")
        return

    label = "🔺 کراس صعودی" if direction == "bull" else "🔻 کراس نزولی"
    subject = f"{label} — {SYMBOL} (امتیاز {score}/3)"
    body = (
        f"نماد: {SYMBOL}\n"
        f"قیمت: {price}\n"
        f"زمان کندل (UTC): {candle_time}\n"
        f"تایم‌فریم: {INTERVAL}\n"
        f"امتیاز: {score}/3\n"
        f"دلایل: {', '.join(reasons)}"
    )
    send_email(subject, body)
    print("ایمیل سیگنال ارسال شد.")


if name == "__main__":
    main()
