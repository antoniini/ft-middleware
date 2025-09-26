from fastapi import FastAPI, Request
import os
import requests

app = FastAPI()

TRADOVATE_USERNAME = os.getenv("TRADOVATE_USERNAME")
TRADOVATE_PASSWORD = os.getenv("TRADOVATE_PASSWORD")
ACCOUNT_TYPE = os.getenv("ACCOUNT_TYPE", "demo")

@app.get("/")
def home():
    return {"status": "Middleware activo y funcionando"}

@app.post("/webhook/tv")
async def webhook_tv(request: Request):
    data = await request.json()

    # --- Validaciones básicas ---
    symbol = data.get("symbol")
    signal = data.get("signal")
    price = data.get("price")

    if not signal or signal not in ["buy", "sell"]:
        return {"error": "Señal no válida"}

    # --- Aquí conectaríamos a Tradovate ---
    tradovate_url = "https://demo-api.tradovate.com/v1"
    print(f"Ejecutando señal: {signal} {symbol} @ {price}")

    return {"message": "Orden recibida y procesada", "data": data}
