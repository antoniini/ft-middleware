# main.py
from fastapi import FastAPI, Request, Header, HTTPException
import os, datetime as dt
from zoneinfo import ZoneInfo

app = FastAPI()

# ===================== CONFIG =====================
WHITELIST   = set((os.getenv("WHITELIST", "CME_MINI:MES1!,MES,ETHUSDT")).split(","))
TZ          = ZoneInfo(os.getenv("TZ", "US/Eastern"))
RTH_START   = os.getenv("RTH_START", "09:30")
RTH_END     = os.getenv("RTH_END", "15:45")
DAILY_STOP  = float(os.getenv("DAILY_STOP", "-500"))
DAILY_TAKE  = float(os.getenv("DAILY_TAKE", "250"))
PAPER_MODE  = os.getenv("PAPER_MODE", "true").lower() == "true"

ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")  # si vacío, /enable y /disable no piden token

# ===================== STATE =====================
state = {
    "enabled": True,
    "date": None,
    "daily_pnl": 0.0,
    "position": 0,
    "entry_price": None,
    "last_keys": set(),
    "last_hb": None,
}

# ===================== HELPERS =====================
def in_rth(now_et: dt.datetime) -> bool:
    s_h, s_m = map(int, RTH_START.split(":"))
    e_h, e_m = map(int, RTH_END.split(":"))
    t = now_et.time()
    return (t >= dt.time(s_h, s_m)) and (t <= dt.time(e_h, e_m))

def reset_if_new_day(now_et: dt.datetime):
    d = now_et.date()
    if state["date"] != d:
        state["date"] = d
        state["daily_pnl"] = 0.0
        state["position"] = 0
        state["entry_price"] = None
        state["last_keys"].clear()
        print(f"[RESET] Nuevo día {d} ET. PnL=0, pos cerrada.")

def price_to_float(x):
    try:
        return float(x)
    except:
        return None

def maybe_flatten_eod(now_et: dt.datetime) -> bool:
    if state.get("position") != 0 and dt.time(15, 44) <= now_et.time() <= dt.time(15, 45):
        print("[RISK] EOD flatten ejecutado")
        state["position"] = 0
        state["entry_price"] = None
        state["last_keys"].clear()
        return True
    return False

def update_heartbeat(now_et: dt.datetime):
    state["last_hb"] = now_et

def heartbeat_ok(now_et: dt.datetime, max_minutes=10) -> bool:
    hb = state.get("last_hb")
    if hb is None:
        return True
    return (now_et - hb) <= dt.timedelta(minutes=max_minutes)

def _require_admin(x_token: str | None):
    if ADMIN_TOKEN and (x_token or "") != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="invalid admin token")

# ===================== LIVE EXEC (stub) =====================
def place_order(symbol: str, side: str, qty: int, price: float | None = None):
    print(f"[LIVE EXEC] {side.upper()} {qty} {symbol} @ {price or 'MKT'}")
    return {"ok": True, "orderId": "sim-001"}

# ===================== ENDPOINTS =====================
@app.post("/webhook/tv")
async def webhook_tv(request: Request):
    raw = (await request.body()).decode("utf-8", "ignore").strip()
    try:
        data = await request.json()
    except:
        data = {"signal": raw}

    symbol = str(data.get("symbol") or "").upper()
    signal = str(data.get("signal") or "").lower()
    price  = price_to_float(data.get("price"))
    tstr   = str(data.get("time") or "")
    now_et = dt.datetime.now(TZ)

    print(f"[WEBHOOK] symbol={symbol} signal={signal} price={price} time={tstr}")
    update_heartbeat(now_et)

    if not state["enabled"]:
        print("[BLOCK] Bot disabled")
        return {"ok": False, "reason": "disabled"}

    if symbol not in WHITELIST:
        print(f"[BLOCK] Symbol not allowed: {symbol}")
        return {"ok": False, "reason": "symbol_not_allowed", "symbol": symbol}

    if symbol in ("MES", "CME_MINI:MES1!"):
        reset_if_new_day(now_et)
        if maybe_flatten_eod(now_et):
            return {"ok": False, "reason": "eod_flatten"}
        if not heartbeat_ok(now_et, max_minutes=10):
            print("[BLOCK] Heartbeat timeout")
            return {"ok": False, "reason": "heartbeat_timeout"}
        if not in_rth(now_et):
            print("[BLOCK] Outside RTH")
            return {"ok": False, "reason": "outside_rth"}
        if state["daily_pnl"] <= DAILY_STOP:
            print("[BLOCK] Daily stop reached")
            return {"ok": False, "reason": "daily_stop_reached"}
        if state["daily_pnl"] >= DAILY_TAKE:
            print("[BLOCK] Daily take reached")
            return {"ok": False, "reason": "daily_take_reached"}

    key = f"{symbol}|{signal}|{tstr}"
    if key in state["last_keys"]:
        print("[BLOCK] Duplicate signal")
        return {"ok": False, "reason": "duplicate"}
    state["last_keys"].add(key)

    exec_info = {"mode": "paper" if PAPER_MODE else "live"}
    point_mult = 5.0  # ajustable

    if signal == "buy":
        if state["position"] < 1:
            if state["position"] == -1 and price:
                pnl = (state.get("entry_price", price) - price) * point_mult
                state["daily_pnl"] += pnl
                print(f"[PnL] Cierre SHORT PnL={pnl:.2f} | DailyPnL={state['daily_pnl']:.2f}")
            state["position"] = 1
            state["entry_price"] = price
            if PAPER_MODE:
                print(f"[PAPER EXEC] BUY 1 {symbol} @ {price or 'MKT'}")
            else:
                exec_info = place_order(symbol, "buy", 1, price)

    elif signal == "sell":
        if state["position"] > -1:
            if state["position"] == 1 and price:
                pnl = (price - state.get("entry_price", price)) * point_mult
                state["daily_pnl"] += pnl
                print(f"[PnL] Cierre LONG PnL={pnl:.2f} | DailyPnL={state['daily_pnl']:.2f}")
            state["position"] = -1
            state["entry_price"] = price
            if PAPER_MODE:
                print(f"[PAPER EXEC] SELL 1 {symbol} @ {price or 'MKT'}")
            else:
                exec_info = place_order(symbol, "sell", 1, price)
    else:
        print(f"[BLOCK] Invalid signal: {signal}")
        return {"ok": False, "reason": "invalid_signal", "got": signal}

    return {
        "ok": True,
        "symbol": symbol,
        "signal": signal,
        "price": price,
        "state": {
            "enabled": state["enabled"],
            "position": state["position"],
            "entry_price": state["entry_price"],
            "daily_pnl": round(state["daily_pnl"], 2),
            "date": str(state["date"]),
        },
        "exec": exec_info
    }

@app.post("/enable")
def enable_bot(x_admin_token: str | None = Header(default=None, convert_underscores=False)):
    _require_admin(x_admin_token)
    state["enabled"] = True
    print("[ADMIN] Bot ENABLED")
    return {"ok": True, "enabled": True}

@app.post("/disable")
def disable_bot(x_admin_token: str | None = Header(default=None, convert_underscores=False)):
    _require_admin(x_admin_token)
    state["enabled"] = False
    print("[ADMIN] Bot DISABLED")
    return {"ok": True, "enabled": False}

@app.get("/health")
def health():
    return {
        "status": "ok",
        "enabled": state["enabled"],
        "paper_mode": PAPER_MODE,
        "rth": {"start": RTH_START, "end": RTH_END, "tz": str(TZ)},
        "caps": {"daily_stop": DAILY_STOP, "daily_take": DAILY_TAKE},
        "state": {
            "date": str(state["date"]),
            "position": state["position"],
            "entry_price": state["entry_price"],
            "daily_pnl": round(state["daily_pnl"], 2),
            "last_hb": state["last_hb"].isoformat() if state["last_hb"] else None
        }
    }

@app.get("/")
def home():
    return {"status": "ok", "msg": "FT middleware running"}

# ===== EXTRA: endpoints de prueba de logs =====
@app.get("/logs/test")
def logs_test():
    now = dt.datetime.now(TZ).isoformat()
    print(f"[TEST] Ping de logs a las {now}")
    return {"ok": True, "msg": "test log written", "time": now}

@app.post("/logs/echo")
async def logs_echo(request: Request):
    raw = (await request.body()).decode("utf-8", "ignore")
    print(f"[ECHO] {raw}")
    try:
        data = await request.json()
    except:
        data = {"raw": raw}
    return {"ok": True, "echo": data}
