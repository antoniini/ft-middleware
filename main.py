from fastapi import FastAPI, Request
import os, json, datetime as dt
from zoneinfo import ZoneInfo

app = FastAPI()

# --- Config ---
WHITELIST = set((os.getenv("WHITELIST","MES,ETHUSDT")).split(","))
TZ = ZoneInfo(os.getenv("TZ","US/Eastern"))
RTH_START = os.getenv("RTH_START","09:30")
RTH_END   = os.getenv("RTH_END","15:45")
DAILY_STOP = float(os.getenv("DAILY_STOP","-500"))
DAILY_TAKE = float(os.getenv("DAILY_TAKE","250"))
PAPER_MODE = os.getenv("PAPER_MODE","true").lower() == "true"

# Estado simple en memoria
state = {
    "date": None,
    "daily_pnl": 0.0,
    "position": 0,      # +1 long, -1 short, 0 flat
    "last_keys": set(), # dedupe
}

def in_rth(now_et: dt.datetime):
    s_h, s_m = map(int, RTH_START.split(":"))
    e_h, e_m = map(int, RTH_END.split(":"))
    t = now_et.time()
    return (t >= dt.time(s_h,s_m)) and (t <= dt.time(e_h,e_m))

def reset_if_new_day(now_et: dt.datetime):
    d = now_et.date()
    if state["date"] != d:
        state["date"] = d
        state["daily_pnl"] = 0.0
        state["position"] = 0
        state["last_keys"].clear()

def price_to_float(x):
    try: return float(x)
    except: return None

# --- stub Tradovate (actívalo cuando quites PAPER_MODE) ---
def place_tradovate_order(symbol: str, side: str, qty: int, price: float|None=None):
    # side: "buy"|"sell"
    print(f"[TRADOVATE] {side.upper()} {qty} {symbol} @ {price or 'MKT'}")
    return {"ok": True, "orderId": "sim-123"}

@app.post("/webhook/tv")
async def webhook_tv(request: Request):
    # Parse robusto (json / text / form-encoded)
    raw = (await request.body()).decode("utf-8","ignore").strip()
    try:
        data = await request.json()
    except:
        if raw.startswith("payload="):
            cooked = (raw.replace("payload=","")
                        .replace("%7B","{").replace("%7D","}")
                        .replace("%22",'"').replace("%20"," ")
                        .replace("%3A",":").replace("%2C",","))
            try: data = json.loads(cooked)
            except: data = {"signal": raw}
        elif raw.startswith("{") and raw.endswith("}"):
            try: data = json.loads(raw)
            except: data = {"signal": raw}
        else:
            data = {"signal": raw}

    symbol = str(data.get("symbol") or "").upper()
    signal = str(data.get("signal") or "").lower()
    price  = price_to_float(data.get("price"))
    tstr   = str(data.get("time") or "")
    now_et = dt.datetime.now(TZ)

    # 1) whitelisting
    if symbol not in WHITELIST:
        return {"ok": False, "reason": "symbol_not_allowed", "symbol": symbol}

    # 2) RTH (solo MES se restringe a RTH; crypto 24/7)
    if symbol == "MES":
        reset_if_new_day(now_et)
        if not in_rth(now_et):
            return {"ok": False, "reason": "outside_rth"}

        # 3) caps diarios
        if state["daily_pnl"] <= DAILY_STOP:
            return {"ok": False, "reason": "daily_stop_reached"}
        if state["daily_pnl"] >= DAILY_TAKE:
            return {"ok": False, "reason": "daily_take_reached"}

    # 4) dedupe por barra+señal
    key = f"{symbol}|{signal}|{tstr}"
    if key in state["last_keys"]:
        return {"ok": False, "reason": "duplicate"}
    state["last_keys"].add(key)

    # 5) Lógica simple de posición (1 contrato)
    exec_info = {"mode": "paper" if PAPER_MODE else "live"}
    if signal == "buy":
        if state["position"] < 1:
            # cerrar short si había
            if state["position"] == -1 and price:
                pnl = (state.get("entry_price", price) - price) * 5.0  # MES $5/pt aprox.
                state["daily_pnl"] += pnl
            state["position"] = 1
            state["entry_price"] = price
            if not PAPER_MODE:
                exec_info = place_tradovate_order("MES", "buy", 1, price)
    elif signal == "sell":
        if state["position"] > -1:
            if state["position"] == 1 and price:
                pnl = (price - state.get("entry_price", price)) * 5.0
                state["daily_pnl"] += pnl
            state["position"] = -1
            state["entry_price"] = price
            if not PAPER_MODE:
                exec_info = place_tradovate_order("MES", "sell", 1, price)
    else:
        return {"ok": False, "reason": "invalid_signal", "got": signal}

    return {
        "ok": True,
        "symbol": symbol,
        "signal": signal,
        "price": price,
        "state": {
            "position": state["position"],
            "daily_pnl": round(state["daily_pnl"],2),
            "date": str(state["date"]),
        },
        "exec": exec_info
    }
