import yfinance as yf


def momentum_strategy(ticker: str, params: dict) -> dict:
    period = str(params.get("period", "1mo"))
    interval = str(params.get("interval", "1d"))
    lookback = int(params.get("lookback", 5))
    qty = int(params.get("qty", 1))

    hist = yf.Ticker(ticker).history(period=period, interval=interval)
    if hist.empty or len(hist) < max(lookback, 2):
        return {
            "action": "HOLD",
            "ticker": ticker.upper(),
            "qty": 0,
            "confidence": 0.0,
        }

    closes = hist["Close"].tail(lookback)
    start_price = float(closes.iloc[0])
    end_price = float(closes.iloc[-1])

    if start_price <= 0:
        return {
            "action": "HOLD",
            "ticker": ticker.upper(),
            "qty": 0,
            "confidence": 0.0,
        }

    change = (end_price - start_price) / start_price
    confidence = min(1.0, abs(change) * 5)

    if change > 0.01:
        action = "BUY"
    elif change < -0.01:
        action = "SELL"
    else:
        action = "HOLD"

    return {
        "action": action,
        "ticker": ticker.upper(),
        "qty": qty if action != "HOLD" else 0,
        "confidence": round(confidence, 4),
    }
