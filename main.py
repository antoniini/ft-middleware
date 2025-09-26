from fastapi import FastAPI, Request
import os, json

app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok"}

@app.post("/webhook/tv")
async def webhook_tv(request: Request):
    # --- 1) intenta JSON nativo ---
    try:
        data = await request.json()
    except Exception:
        # --- 2) intenta leer como texto ---
        raw = (await request.body()).decode("utf-8", errors="ignore").strip()

        # a) si viene vacío -> error claro
        if not raw:
            return {"error": "Empty body from TradingView"}

        # b) algunos servicios mandan 'payload=<json>' (form-encoded)
        if raw.startswith("payload="):
            raw = raw[len("payload="):]
            raw = raw.replace("%7B", "{").replace("%7D", "}").replace("%22", '"')

        # c) si parece JSON, parsea; si no, trata como texto ('buy', 'sell', etc.)
        if raw.startswith("{") and raw.endswith("}"):
            try:
                data = json.loads(raw)
            except Exception:
                data = {"signal": raw}
        else:
            data = {"signal": raw}

    # ---- normaliza ----
    signal = (str(data.get("signal") or "")).lower()
    symbol = str(data.get("symbol") or "UNKNOWN")
    price  = data.get("price")

    if signal not in {"buy","sell","long","short","close","close_long","close_short"}:
        return {"error": f"Invalid signal '{signal}'", "received": data}

    print(f"[TV] signal={signal} symbol={symbol} price={price} data={data}")
    return {"ok": True, "signal": signal, "symbol": symbol, "price": price}
