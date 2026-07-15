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
