from fastapi import FastAPI, Request
import json

app = FastAPI()

@app.get("/")
def home():
    return {"status": "ok"}

@app.post("/webhook/tv")
async def webhook_tv(request: Request):
    # Log de diagnóstico
    headers = dict(request.headers)
    raw_bytes = await request.body()
    raw_text  = raw_bytes.decode("utf-8", errors="ignore").strip()

    # Intentar parsear en este orden: JSON nativo -> form-encoded -> JSON en texto -> texto plano
    data = None
    try:
        data = await request.json()
    except Exception:
        pass

    if data is None:
        # ¿Formato form-encoded? (ej. payload=<json>)
        if raw_text.startswith("payload="):
            # decode básico urlencoded
            cooked = (raw_text
                      .replace("payload=", "")
                      .replace("%7B","{").replace("%7D","}")
                      .replace("%22",'"').replace("%20"," ")
                      .replace("%3A",":").replace("%2C",","))
            try:
                data = json.loads(cooked)
            except Exception:
                data = {"signal": cooked}

        # ¿JSON en texto?
        elif raw_text.startswith("{") and raw_text.endswith("}"):
            try:
                data = json.loads(raw_text)
            except Exception:
                data = {"signal": raw_text}
        else:
            # Texto plano ('buy', 'sell', etc.)
            data = {"signal": raw_text}

    # Normalización
    signal = str(data.get("signal") or "").lower()
    symbol = str(data.get("symbol") or "UNKNOWN")
    price  = data.get("price", None)

    # Respuesta y eco de depuración (para que veas exactamente qué llegó)
    print("[TV DEBUG] content-type=", headers.get("content-type"),
          "| raw_text=", raw_text,
          "| parsed=", data)

    return {
        "ok": True,
        "normalized": {"signal": signal, "symbol": symbol, "price": price},
        "debug": {"content_type": headers.get("content-type"), "raw": raw_text}
    }
