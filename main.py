from fastapi import FastAPI, Request
import os, json, datetime as dt, requests
from zoneinfo import ZoneInfo

app = FastAPI()

# ===================== CONFIG =====================
WHITELIST = set((os.getenv("WHITELIST","MES,ETHUSDT")).split(","))
TZ = ZoneInfo(os.getenv("TZ","US/Eastern"))
RTH_START = os.getenv("RTH_START","09:30")
RTH_END   = os.getenv("RTH_END","15:45")
DAILY_STOP = float(os.getenv("DAILY_STOP","-500"))
DAILY_TAKE = float(os.getenv("DAILY_TAKE","250"))
PAPER_MODE = os.getenv("PAPER_MODE","true").lower() == "true"

MYFXBOOK_USER = os.getenv("MYFXBOOK_USER","")
MYFXBOOK_PASS = os.getenv("MYFXBOOK_PASS","")
NEWS_LOOKAHEAD_HOURS = int(os.getenv("NEWS_LOOKAHEAD_HOURS","12"))

# Estado persistente
state = {
    "date": None,
    "daily_pnl": 0.0,
    "position": 0,
    "last_keys": set(),
    "last_hb": None,
    "news_windows": []
}

# ===================== HELPERS =====================
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

# ---- Flatten fin de sesión ----
def maybe_flatten_eod(now_et):
    if state.get("position") != 0 and (now_et.time() >= dt.time(15,44) and now_et.time() <= dt.time(15,45)):
        state["position"] = 0
        state["entry_price"] = None
        state["last_keys"].clear()
        print("[RISK] EOD flatten ejecutado")
        return True
    return False

# ---- Heartbeat ----
def update_heartbeat(now_et):
    state["last_hb"] = now_et

def heartbeat_ok(now_et, max_minutes=10):
    hb = state.get("last_hb")
    if hb is None: 
        return True
    return (now_et - hb) <= dt.timedelta(minutes=max_minutes)

# ===================== MYFXBOOK NEWS =====================
def fetch_myfxbook_news():
    """Conecta con Myfxbook y carga noticias impacto 2 y 3 estrellas"""
    if not MYFXBOOK_USER or not MYFXBOOK_PASS:
        return []

    # Login
    login_url = "https://www.myfxbook.com/api/login.json"
    session = requests.Session()
    resp = session.get(login_url, params={"email": MYFXBOOK_USER, "password": MYFXBOOK_PASS})
    data = resp.json()
    if not data.get("error") == False:
        print("[NEWS] Error login Myfxbook:", data)
        return []
    session_id = data.get("session")

    # Obtener calendario económico
    cal_url = "https://www.myfxbook.com/api/get-economic-calendar.json"
    now = dt.datetime.utcnow()
    ahead = now + dt.timedelta(hours=NEWS_LOOKAHEAD_HOURS)
    resp = session.get(cal_url, params={
        "session": session_id,
        "start": now.strftime("%Y-%m-%d %H:%M"),
        "end": ahead.strftime("%Y-%m-%d %H:%M")
    })
    data = resp.json()
    if data.get("error"):
        print("[NEWS] Error calendario:", data)
        return []

    news_list = []
    for item in data.get("calendar", []):
        impact = item.get("impact")
        # Myfxbook: 1=low, 2=medium, 3=high
        if impact in [2,3]:
            event_time = dt.datetime.fromtimestamp(item["timestamp"]/1000, tz=TZ)
            start = event_time - dt.timedelta(minutes=30)
            end   = event_time + dt.timedelta(minutes=60)
            news_list.append((start, end, item.get("title","")))
    return news_list

def refresh_news():
    state["news_windows"] = fetch_myfxbook_news()
    print(f"[NEWS] Ventanas actualizadas: {len(state['news_windows'])} eventos")

def in_news_window(now_et):
    for (s,e,_) in state["news_windows"]:
        if s <= now_et <= e:
            return True
    return False

# ===================== TRADOVATE STUB =====================
def place_tradovate_order(symbol: str, side: str, qty: int, price: float|None=None):
    print(f"[TRADOVATE] {side.upper()} {qty} {symbol} @ {price or 'MKT'}")
    return {"ok": True, "orderId": "sim-123"}

# ===================== WEBHOOK =====================
@app.post("/webhook/tv")
async def webhook_tv(request: Request):
    raw = (await request.body()).decode("utf-8","ignore").strip()
    try:
        data = await request.json()
    except:
        data = {"signal": raw}

    symbol = str(data.get("symbol") or "").upper()
    signal = str(data.get("signal") or "").lower()
    price  = price_to_float(data.get("price"))
    tstr   = str(data.get("time") or "")
    now_et = dt.datetime.now(TZ)

    # Actualizar heartbeat
    update_heartbeat(now_et)

    # 1) Solo símbolos permitidos
    if symbol not in WHITELIST:
        return {"ok": False, "reason": "symbol_not_allowed", "symbol": symbol}

    # 2) Reset diario
    if symbol == "MES":
        reset_if_new_day(now_et)

    # 3) Flatten EOD
    if symbol == "MES":
        if maybe_flatten_eod(now_et):
            return {"ok": False, "reason": "eod_flatten"}

    # 4) Heartbeat
    if symbol == "MES" and not heartbeat_ok(now_et, max_minutes=10):
        return {"ok": False, "reason": "heartbeat_timeout"}

    # 5) Bloqueo por noticias
    if symbol == "MES" and in_news_window(now_et):
        if state["position"] != 0:
            state["position"] = 0
            state["entry_price"] = None
            print("[RISK] Flatten pre-noticia")
        return {"ok": False, "reason": "news_block"}

    # 6) Solo operar dentro de RTH
    if symbol == "MES" and not in_rth(now_et):
        return {"ok": False, "reason": "outside_rth"}

    # 7) Caps diarios
    if symbol == "MES":
        if state["daily_pnl"] <= DAILY_STOP:
            return {"ok": False, "reason": "daily_stop_reached"}
        if state["daily_pnl"] >= DAILY_TAKE:
            return {"ok": False, "reason": "daily_take_reached"}

    # 8) Deduplicación por barra
    key = f"{symbol}|{signal}|{tstr}"
    if key in state["last_keys"]:
        return {"ok": False, "reason": "duplicate"}
    state["last_keys"].add(key)

    # 9) Ejecución
    exec_info = {"mode": "paper" if PAPER_MODE else "live"}
    if signal == "buy":
        if state["position"] < 1:
            if state["position"] == -1 and price:
                pnl = (state.get("entry_price", price) - price) * 5.0
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

# ===================== STARTUP =====================
@app.on_event("startup")
async def startup_event():
    print("[INIT] Cargando noticias iniciales...")
    refresh_news()
