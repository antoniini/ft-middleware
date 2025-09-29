# main.py
from fastapi import FastAPI, Request, Header, HTTPException
import os, datetime as dt
from zoneinfo import ZoneInfo

app = FastAPI()

# ===================== CONFIG =====================
# Puedes sobreescribir con variables de entorno en Render → Environment
WHITELIST   = set((os.getenv("WHITELIST", "CME_MINI:MES1!,MES,ETHUSDT")).split(","))
TZ          = ZoneInfo(os.getenv("TZ", "US/Eastern"))
RTH_START   = os.getenv("RTH_START", "09:30")   # HH:MM (ET) MES RTH
RTH_END     = os.getenv("RTH_END", "15:45")     # HH:MM (ET) MES RTH
DAILY_STOP  = float(os.getenv("DAILY_STOP", "-500"))
DAILY_TAKE  = float(os.getenv("DAILY_TAKE", "250"))
PAPER_MODE  = os.getenv("PAPER_MODE", "true").lower() == "true"

# Token opcional para /enable y /disable. Si está vacío, NO se valida.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# ===================== STATE =====================
state = {
    "enabled": True,        # << switch maestro ON/OFF
    "date": None,
    "daily_pnl": 0.0,
    "position": 0,          # -1 short, 0 flat, 1 long
    "entry_price": None,
    "last_keys": set(),     # dedupe por (symbol|signal|time)
    "last_hb": None,        # último heartbeat recibido
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

def price_to_float(x):
    try:
        return float(x)
    except:
        return None

def maybe_flatten_eod(now_et: dt.datetime) -> bool:
    """Cierra toda posición entre 15:44–15:45 ET (seguridad)."""
    if state.get("position") != 0 and dt.time(15, 44) <= now_et.time() <= dt.time(15, 45):
        state["position"] = 0
        state["entry_price"] = None
        state["last_keys"].clear()
        print("[RISK] EOD flatten ejecutado")
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
    # si ADMIN_TOKEN está vacío, no exigimos header
    if ADMIN_TOKEN and (x_token or "") != ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="invalid admin token")

# ===================== EJECUCIÓN (stub) =====================
def place_order(symbol: str, side: str, qty: int, price: float | None = None):
    """Conectar aquí a Tradovate para live. Ahora solo imprime."""
    print(f"[EXEC] {side.upper()} {qty} {symbol} @ {price or 'MKT'} | mode={'paper' if PAPER_MODE else 'live'}")
    return {"ok": True, "orderId": "sim-001"}

# ===================== ENDPOINTS =====================
@app.post("/webhook/tv")
async def webhook_tv(request: Request):
    # Intentar JSON; si no, tomar body como texto (buy/sell)
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

    update_heartbeat(now_et)

    # 0) Switch maestro
    if not state["enabled"]:
        return {"ok": False, "reason": "disabled"}

    # 1) Permitir solo símbolos whitelisted
    if symbol not in WHITELIST:
        return {"ok": False, "reason": "symbol_not_allowed", "symbol": symbol}

    # 2) Reset diario y reglas específicas para MES (futuros)
    if symbol in ("MES", "CME_MINI:MES1!"):
        reset_if_new_day(now_et)

        # EOD flatten forzoso
        if maybe_flatten_eod(now_et):
            return {"ok": False, "reason": "eod_flatten"}

        # Heartbeat: si TV se “muere”, paramos
        if not heartbeat_ok(now_et, max_minutes=10):
            return {"ok": False, "reason": "heartbeat_timeout"}

        # Solo RTH
        if not in_rth(now_et):
            return {"ok": False, "reason": "outside_rth"}

        # Límites diarios (simulados)
        if state["daily_pnl"] <= DAILY_STOP:
            return {"ok": False, "reason": "daily_stop_reached"}
        if state["daily_pnl"] >= DAILY_TAKE:
            return {"ok": False, "reason": "daily_take_reached"}

    # 3) Dedupe por (symbol|signal|time)
    key = f"{symbol}|{signal}|{tstr}"
    if key in state["last_keys"]:
        return {"ok": False, "reason": "duplicate"}
    state["last_keys"].add(key)

    # 4) Ejecución + PnL simulado (multiplicador aproximado)
    exec_info = {"mode": "paper" if PAPER_MODE else "live"}
    point_mult = 5.0  # ajusta si quieres otra convención

    if signal == "buy":
        if state["position"] < 1:
            if state["position"] == -1 and price:
                pnl = (state.get("entry_price", price) - price) * point_mult
                state["daily_pnl"] += pnl
            state["position"]  = 1
            state["entry_price"] = price
            if not PAPER_MODE:
                exec_info = place_order(symbol, "buy", 1, price)

    elif signal == "sell":
        if state["position"] > -1:
            if state["position"] == 1 and price:
                pnl = (price - state.get("entry_price", price)) * point_mult
                state["daily_pnl"] += pnl
            state["position"]  = -1
            state["entry_price"] = price
            if not PAPER_MODE:
                exec_info = place_order(symbol, "sell", 1, price)

    else:
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

# ---- ON / OFF (si ADMIN_TOKEN está vacío, no se exige header) ----
@app.post("/enable")
def enable_bot(x_admin_token: str | None = Header(default=None, convert_underscores=False)):
    _require_admin(x_admin_token)
    state["enabled"] = True
    return {"ok": True, "enabled": True}

@app.post("/disable")
def disable_bot(x_admin_token: str | None = Header(default=None, convert_underscores=False)):
    _require_admin(x_admin_token)
    state["enabled"] = False
    return {"ok": True, "enabled": False}

# ---- Estado / Health ----
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
