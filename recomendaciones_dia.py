# -*- coding: utf-8 -*-
"""
MatchIQ - Backend Flask v5
- Supabase PostgreSQL para guardar predicciones
- Verificacion automatica de aciertos
"""
from flask import Flask, jsonify, render_template, request, redirect, url_for, send_from_directory
from datetime import datetime, timedelta
from functools import wraps
import requests, time, math, os, json
import psycopg2
import psycopg2.extras

app = Flask(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

def get_db():
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)

FD_KEY = "e28d6269df9441fea1b6a19548e982c6"
FD_URL = "https://api.football-data.org/v4"
FD_HEADERS = {"X-Auth-Token": FD_KEY}

AS_KEY = os.environ.get("AS_KEY", "fb49b7a70ea23977f8e7711c5ed027b1")
AS_URL = "https://v3.football.api-sports.io"
AS_HEADERS = {"x-apisports-key": AS_KEY}

HL_KEY = os.environ.get("HL_KEY", "")
HL_URL = "https://soccer.highlightly.net"
HL_HEADERS = {"x-rapidapi-key": HL_KEY}

HL_NAME_MAP = {
    "Athletic Club": ["Athletic Bilbao", "Athletic Club Bilbao", "Athletic"],
    "Valencia CF": ["Valencia", "Valencia CF"],
    "RCD Espanyol de Barcelona": ["Espanyol", "RCD Espanyol"],
    "RCD Mallorca": ["Mallorca", "Real Club Deportivo Mallorca"],
    "CA Osasuna": ["Osasuna", "Club Atlético Osasuna"],
    "Real Valladolid CF": ["Valladolid", "Real Valladolid"],
    "Rayo Vallecano de Madrid": ["Rayo Vallecano", "Rayo"],
    "Girona FC": ["Girona"],
    "Getafe CF": ["Getafe"],
    "Celta de Vigo": ["Celta Vigo", "RC Celta de Vigo"],
    "Deportivo Alavés": ["Alavés", "Deportivo Alaves"],
    "Real Betis Balompié": ["Real Betis", "Betis"],
    "Club Atlético de Madrid": ["Atletico Madrid", "Atlético de Madrid"],
    "AC Pisa 1909": ["Pisa"],
    "US Cremonese": ["Cremonese"],
    "US Lecce": ["Lecce"],
    "US Sassuolo Calcio": ["Sassuolo"],
    "Hellas Verona FC": ["Hellas Verona", "Verona"],
    "ACF Fiorentina": ["Fiorentina"],
    "Cagliari Calcio": ["Cagliari"],
    "Udinese Calcio": ["Udinese"],
    "Parma Calcio 1913": ["Parma"],
    "FC Internazionale Milano": ["Inter Milan", "Inter", "Internazionale"],
    "SS Lazio": ["Lazio"],
    "AS Roma": ["Roma"],
}

def hl_get(ep, params=None):
    if not HL_KEY:
        return {"error": "No HL_KEY"}
    try:
        r = requests.get(f"{HL_URL}{ep}", headers=HL_HEADERS, params=params, timeout=15)
        if r.status_code == 429:
            time.sleep(1)
            r = requests.get(f"{HL_URL}{ep}", headers=HL_HEADERS, params=params, timeout=15)
        return r.json() if r.ok else {"error": r.status_code}
    except Exception as e:
        return {"error": str(e)}

def _get_hl_team_id(team_name, league_code):
    country_map = {"PL":"England","PD":"Spain","SA":"Italy","BL1":"Germany","FL1":"France","BSA":"Brazil","CL":"Europe","PPL":"Portugal","DED":"Netherlands"}
    country = country_map.get(league_code, "")
    names_to_try = [team_name]
    clean = team_name.replace(" FC","").replace(" CF","").replace(" AFC","").replace("AC ","").replace("SS ","").replace("US ","").replace("AS ","").replace(" Calcio","").strip()
    if clean != team_name:
        names_to_try.append(clean)
    if team_name in HL_NAME_MAP:
        names_to_try.extend(HL_NAME_MAP[team_name])
    first = clean.split(" ")[0]
    if len(first) >= 4:
        names_to_try.append(first)
    for name in names_to_try:
        d = hl_get("/teams", {"name": name, "countryName": country} if country else {"name": name})
        if "error" not in d and d:
            teams = d if isinstance(d, list) else d.get("data", [])
            if teams:
                return teams[0].get("id") or teams[0].get("teamId")
    return None

def _avg_stats_from_hl(team_id, liga_code):
    d = hl_get("/last-five-games", {"teamId": team_id})
    if "error" in d or not d:
        return None
    games = d if isinstance(d, list) else d.get("data", [])
    if not games:
        return None
    shots_total = []; shots_on = []; corners = []; yellow = []; red = []
    for g in games[:5]:
        stats = g.get("statistics", {})
        is_home = str(g.get("homeTeamId","")) == str(team_id)
        ts = stats.get("home",{}) if is_home else stats.get("away",{})
        def sv(d, *keys):
            for k in keys:
                v = d.get(k)
                if v is not None:
                    try: return float(v)
                    except: pass
            return None
        st = sv(ts,"shots","shotsTotal","total_shots")
        so = sv(ts,"shotsOnTarget","shots_on_target","shotsOnGoal")
        co = sv(ts,"corners","cornerKicks","corner_kicks")
        yc = sv(ts,"yellowCards","yellow_cards")
        rc = sv(ts,"redCards","red_cards")
        if st is not None: shots_total.append(st)
        if so is not None: shots_on.append(so)
        if co is not None: corners.append(co)
        if yc is not None: yellow.append(yc)
        if rc is not None: red.append(rc)
    def avg(lst): return round(sum(lst)/len(lst),1) if lst else "—"
    return {"remates_pj":avg(shots_total),"al_arco_pj":avg(shots_on),"corners_pj":avg(corners),"tarjetas_amarillas_pj":avg(yellow),"tarjetas_rojas_pj":avg(red),"posesion_avg":"—","faltas_pj":"—","source":"highlightly"}

LIGAS = {
    "PL":  {"nombre": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League",  "as_id": 39,  "season": 2025, "source": "fd"},
    "PD":  {"nombre": "🇪🇸 La Liga",           "as_id": 140, "season": 2025, "source": "fd"},
    "SA":  {"nombre": "🇮🇹 Serie A",            "as_id": 135, "season": 2025, "source": "fd"},
    "BL1": {"nombre": "🇩🇪 Bundesliga",         "as_id": 78,  "season": 2025, "source": "fd"},
    "FL1": {"nombre": "🇫🇷 Ligue 1",            "as_id": 61,  "season": 2025, "source": "fd"},
    "DED": {"nombre": "🇳🇱 Eredivisie",         "as_id": 88,  "season": 2025, "source": "fd"},
    "PPL": {"nombre": "🇵🇹 Primeira Liga",      "as_id": 94,  "season": 2025, "source": "fd"},
    "ELC": {"nombre": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Championship",     "as_id": 40,  "season": 2025, "source": "fd"},
    "CL":  {"nombre": "🏆 Champions League",    "as_id": 2,   "season": 2025, "source": "fd"},
    "BSA": {"nombre": "🇧🇷 Brasileirão",        "as_id": 71,  "season": 2026, "source": "fd"},
    "AARG": {"nombre": "Primera División Argentina", "as_id": 128, "season": 2026, "source": "as"},
    "INTL": {"nombre": "🌍 Amistosos Internacionales", "source": "espn"},
}

_cache = {}
CACHE_TTL = 300
CACHE_TTL_AS = 43200
CACHE_TTL_FX_STATS = 604800
UMBRALES = {
    "1X2":       75,
    "DRAW":      40,
    "DC":        78,
    "OU":        75,
    "BTTS":      75,
    "GE_05":     87,
    "GE_15":     78,
    "CS":        78,
    "WTN":       60,
    "HT_OVER":   78,
    "HT_UNDER":  80,
    "NO00_SHOW": 75,
    "NO00":      85,
    "ADV":       72,
}


# ── DATABASE ──────────────────────────────────────────────
def init_db():
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS analisis_cache (
        match_id INTEGER,
        liga TEXT,
        resultado TEXT,
        creado TEXT,
        PRIMARY KEY (match_id, liga)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS predicciones (
        match_id INTEGER PRIMARY KEY,
        liga TEXT,
        fecha TEXT,
        home TEXT,
        away TEXT,
        mercado_principal TEXT,
        mp_prob INTEGER,
        mp_cuota REAL,
        combinable TEXT,
        comb_prob INTEGER,
        comb_cuota REAL,
        resultado_home INTEGER,
        resultado_away INTEGER,
        mp_acertado INTEGER,
        comb_acertado INTEGER,
        verificado INTEGER DEFAULT 0,
        creado TEXT
    )""")
    conn.commit()
    conn.close()

init_db()

def get_cached_analysis(match_id, liga):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT resultado, creado FROM analisis_cache WHERE match_id=%s AND liga=%s", (match_id, liga))
        row = c.fetchone()
        conn.close()
        if not row: return None
        resultado = json.loads(row[0])
        age = (datetime.utcnow() - datetime.fromisoformat(row[1])).total_seconds()
        if age < 21600: return resultado
        return None
    except: return None

def save_cached_analysis(match_id, liga, resultado):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""INSERT INTO analisis_cache (match_id, liga, resultado, creado)
                     VALUES (%s, %s, %s, %s)
                     ON CONFLICT (match_id, liga) DO UPDATE
                     SET resultado=EXCLUDED.resultado, creado=EXCLUDED.creado""",
                  (match_id, liga, json.dumps(resultado, ensure_ascii=False), datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Cache error: {e}")

def save_prediction(match_id, liga, fecha, home, away, veredicto):
    mp_text = veredicto.get("mercado_principal", "")
    comb_text = veredicto.get("combinable", "")
    mp_prob, mp_cuota = _extract_prob_cuota(mp_text)
    comb_prob, comb_cuota = _extract_prob_cuota(comb_text)
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("""INSERT INTO predicciones
            (match_id, liga, fecha, home, away, mercado_principal, mp_prob, mp_cuota,
             combinable, comb_prob, comb_cuota, creado)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (match_id) DO UPDATE
            SET mercado_principal=EXCLUDED.mercado_principal,
                mp_prob=EXCLUDED.mp_prob, mp_cuota=EXCLUDED.mp_cuota,
                combinable=EXCLUDED.combinable, comb_prob=EXCLUDED.comb_prob,
                comb_cuota=EXCLUDED.comb_cuota""",
            (match_id, liga, fecha, home, away, mp_text, mp_prob, mp_cuota,
             comb_text, comb_prob, comb_cuota, datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error saving prediction: {e}")


def _extract_prob_cuota(text):
    if not text: return (None, None)
    try:
        import re
        prob_m = re.search(r"(\d+)%", text)
        cuota_m = re.search(r"@([\d.]+)", text)
        prob = int(prob_m.group(1)) if prob_m else None
        cuota = float(cuota_m.group(1)) if cuota_m else None
        return (prob, cuota)
    except:
        return (None, None)


def verify_prediction(match_id, home_goals, away_goals):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT mercado_principal, combinable, home, away FROM predicciones WHERE match_id=%s", (match_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return
    mp, comb, home, away = row
    mp_ok = _check_mercado(mp, home_goals, away_goals, home, away)
    comb_ok = _check_mercado(comb, home_goals, away_goals, home, away) if comb else None
    c.execute("""UPDATE predicciones SET resultado_home=%s, resultado_away=%s,
        mp_acertado=%s, comb_acertado=%s, verificado=1 WHERE match_id=%s""",
        (home_goals, away_goals,
         1 if mp_ok else (0 if mp_ok is False else None),
         1 if comb_ok else (0 if comb_ok is False else None),
         match_id))
    conn.commit()
    conn.close()


def _check_mercado(texto, hg, ag, home, away):
    if not texto: return None
    t = texto.lower()
    total = hg + ag
    hl = home.lower()
    al = away.lower()

    if "resultado final" in t:
        if hl in t: return hg > ag
        elif al in t: return ag > hg
        elif "empate" in t: return hg == ag

    if "doble oportunidad" in t or "1x" in t or "x2" in t:
        if "1x" in t or hl in t: return hg >= ag
        elif "x2" in t or al in t: return ag >= hg

    if "goles equipo" in t and "over 1.5" in t:
        if hl in t: return hg >= 2
        if al in t: return ag >= 2
        return None

    if "goles equipo" in t and "over 0.5" in t:
        if hl in t: return hg > 0
        if al in t: return ag > 0
        return None

    if "over 3.5" in t: return total > 3.5
    if "under 3.5" in t: return total < 3.5
    if "over 2.5" in t: return total > 2.5
    if "under 2.5" in t: return total < 2.5
    if "over 1.5" in t: return total > 1.5
    if "under 1.5" in t: return total < 1.5

    if "ambos anotan" in t or "btts" in t:
        return hg > 0 and ag > 0

    if "clean sheet" in t:
        if hl in t: return ag == 0
        if al in t: return hg == 0

    if "victoria a cero" in t:
        if hl in t: return hg > ag and ag == 0
        if al in t: return ag > hg and hg == 0

    if "no termina 0-0" in t:
        return total > 0

    if "1er tiempo" in t:
        return None

    return None


def fd_get(ep, params=None):
    ck="fd:"+ep+str(params or ""); now=time.time()
    if ck in _cache and now-_cache[ck][1]<CACHE_TTL: return _cache[ck][0]
    try:
        r=requests.get(f"{FD_URL}{ep}",headers=FD_HEADERS,params=params,timeout=15)
        if r.status_code==429: time.sleep(1); r=requests.get(f"{FD_URL}{ep}",headers=FD_HEADERS,params=params,timeout=15)
        d=r.json(); _cache[ck]=(d,now); return d
    except Exception as e: return {"error":str(e)}

def as_get(ep, params=None):
    ck="as:"+ep+str(params or ""); now=time.time()
    ttl = CACHE_TTL_FX_STATS if "fixtures/statistics" in ep else CACHE_TTL_AS
    if ck in _cache and now-_cache[ck][1]<ttl: return _cache[ck][0]
    try:
        r=requests.get(f"{AS_URL}{ep}",headers=AS_HEADERS,params=params,timeout=15)
        if r.status_code==429: return {"error":"rate_limit"}
        d=r.json()
        if d.get("response") is not None: _cache[ck]=(d,now)
        return d
    except Exception as e: return {"error":str(e)}

ESPN_SLUGS = {
    "PL": "eng.1", "PD": "esp.1", "SA": "ita.1",
    "BL1": "ger.1", "FL1": "fra.1", "DED": "ned.1",
    "PPL": "por.1", "ELC": "eng.2", "BSA": "bra.1",
    "AARG": "arg.1", "CL": "uefa.champions",
}
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"

def espn_get(slug, ep, params=None):
    ck = f"espn:{slug}:{ep}:{params or ''}"
    now = time.time()
    if ck in _cache and now - _cache[ck][1] < 60: return _cache[ck][0]
    try:
        r = requests.get(f"{ESPN_URL}/{slug}/{ep}", params=params,
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        if r.ok:
            d = r.json()
            _cache[ck] = (d, now)
            return d
        return {"error": r.status_code}
    except Exception as e:
        return {"error": str(e)}


# PWA routes
@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")

@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/json")

@app.route("/")
def index():
    return render_template("index.html", ligas={k:v["nombre"] for k,v in LIGAS.items()})

@app.route("/dashboard")
def dashboard():
    return render_template("scoutbet_dashboard.html")

@app.route("/mundial")
def mundial():
    return render_template("wc2026_simulator.html")

@app.route("/ia_analisis", methods=["POST"])
def ia_analisis():
    import math
    body = request.get_json() or {}
    hn = body.get("home", "Local")
    an = body.get("away", "Visitante")
    liga_nombre = body.get("liga", "")
    mercados = body.get("mercados", [])
    home_stats = body.get("home_stats", {})
    away_stats = body.get("away_stats", {})

    def fmt(v):
        try:
            if v is None or v == "" or v == "—": return None
            return round(float(v), 1)
        except: return None

    def poisson_prob(lam, k):
        if lam <= 0: return 0
        try: return math.exp(-lam) * (lam**k) / math.factorial(k)
        except: return 0

    gf_h = fmt(home_stats.get("goles_favor")) or 1.3
    gc_h = fmt(home_stats.get("goles_contra")) or 1.1
    gf_a = fmt(away_stats.get("goles_favor")) or 1.1
    gc_a = fmt(away_stats.get("goles_contra")) or 1.3
    forma_h = home_stats.get("forma", "")
    forma_a = away_stats.get("forma", "")
    rem_h = fmt(home_stats.get("remates_pj"))
    rem_a = fmt(away_stats.get("remates_pj"))
    arco_h = fmt(home_stats.get("al_arco_pj"))
    arco_a = fmt(away_stats.get("al_arco_pj"))
    corners_h = fmt(home_stats.get("corners_pj"))
    corners_a = fmt(away_stats.get("corners_pj"))
    tarj_h = fmt(home_stats.get("tarjetas_amarillas_pj"))
    tarj_a = fmt(away_stats.get("tarjetas_amarillas_pj"))

    lambda_h = round((gf_h + gc_a) / 2, 2)
    lambda_a = round((gf_a + gc_h) / 2, 2)

    dist_h = [round(poisson_prob(lambda_h, k) * 100, 1) for k in range(6)]
    dist_a = [round(poisson_prob(lambda_a, k) * 100, 1) for k in range(6)]
    max_prob = max(max(dist_h), max(dist_a), 0.01)

    best_score = {"hg": 0, "ag": 0, "p": 0}
    p_win = p_draw = p_loss = 0.0
    for i in range(8):
        for j in range(8):
            p = poisson_prob(lambda_h, i) * poisson_prob(lambda_a, j)
            if p > best_score["p"]:
                best_score = {"hg": i, "ag": j, "p": round(p*100, 1)}
            if i > j: p_win += p
            elif i == j: p_draw += p
            else: p_loss += p

    p_win = round(p_win * 100)
    p_draw = round(p_draw * 100)
    p_loss = round(p_loss * 100)
    p_over25 = round((1 - sum(poisson_prob(lambda_h + lambda_a, k) for k in range(3))) * 100)
    p_btts = round((1 - poisson_prob(lambda_h, 0)) * (1 - poisson_prob(lambda_a, 0)) * 100)

    def cuota(p):
        return round(100 / max(p, 1) * 0.95, 2) if p > 0 else "S/D"

    def forma_badges(forma):
        if not forma: return "<span style='font-size:10px;color:#8890aa'>sin datos</span>"
        colors = {"W": "#34d399", "D": "#f0b429", "L": "#ff3d5a"}
        out = ""
        for c in forma[:5]:
            col = colors.get(c, "#252838")
            out += f"<span style='display:inline-block;width:17px;height:17px;border-radius:3px;background:{col};color:#000;font-size:8px;font-weight:700;text-align:center;line-height:17px;margin:1px'>{c}</span>"
        return out

    def barra(k, pct, max_p):
        w = round(pct / max_p * 100) if max_p > 0 else 0
        return (
            f"<div style='display:flex;align-items:center;gap:5px;margin:2px 0'>"
            f"<span style='font-family:monospace;width:11px;font-size:10px;color:#8890aa'>{k}</span>"
            f"<div style='flex:1;background:#1c1f2e;border-radius:2px;height:10px'>"
            f"<div style='width:{w}%;height:100%;background:linear-gradient(90deg,#f0b429,rgba(240,180,41,0.4));border-radius:2px'></div>"
            f"</div>"
            f"<span style='font-family:monospace;font-size:10px;color:#22d3c5;width:32px;text-align:right'>{pct}%</span>"
            f"</div>"
        )

    def sr(label, vh, va, good_high=True):
        if vh is None and va is None: return ""
        if vh and va and vh != va:
            ch = "#34d399" if (vh > va) == good_high else "#ff3d5a"
            ca = "#34d399" if (va > vh) == good_high else "#ff3d5a"
        else:
            ch = ca = "#c8ccdf"
        return (
            f"<tr>"
            f"<td style='color:#8890aa;font-size:11px;padding:3px 6px'>{label}</td>"
            f"<td style='text-align:center;font-family:monospace;font-size:11px;color:{ch};padding:3px 6px'>{vh or chr(8212)}</td>"
            f"<td style='text-align:center;font-family:monospace;font-size:11px;color:{ca};padding:3px 6px'>{va or chr(8212)}</td>"
            f"</tr>"
        )

    riesgos = []
    if tarj_h and tarj_h > 2.5: riesgos.append(f"{hn} alta tarjeteria ({tarj_h}/PJ)")
    if tarj_a and tarj_a > 2.5: riesgos.append(f"{an} alta tarjeteria ({tarj_a}/PJ)")
    if "L" in forma_h[:2]: riesgos.append(f"{hn} viene mal")
    if "L" in forma_a[:2]: riesgos.append(f"{an} viene mal")
    if abs(lambda_h - lambda_a) < 0.15: riesgos.append("Equipos parejos, alta varianza")
    if not riesgos: riesgos.append("Sin senales de riesgo elevado")

    todos = [
        {"n": f"Victoria {hn}", "p": p_win, "c": cuota(p_win), "ok": p_win >= 55},
        {"n": "Empate", "p": p_draw, "c": cuota(p_draw), "ok": False},
        {"n": f"Victoria {an}", "p": p_loss, "c": cuota(p_loss), "ok": p_loss >= 55},
        {"n": f"DC {hn[:8]} o Empate", "p": p_win+p_draw, "c": cuota(p_win+p_draw), "ok": (p_win+p_draw) >= 75},
        {"n": f"DC {an[:8]} o Empate", "p": p_draw+p_loss, "c": cuota(p_draw+p_loss), "ok": (p_draw+p_loss) >= 75},
        {"n": "Over 2.5", "p": p_over25, "c": cuota(p_over25), "ok": p_over25 >= 65},
        {"n": "BTTS", "p": p_btts, "c": cuota(p_btts), "ok": p_btts >= 65},
    ]
    for m in (mercados or []):
        nm = m.get("mercado", "")
        if nm and not any(x["n"] == nm for x in todos):
            todos.append({"n": nm, "p": m.get("prob", 0), "c": m.get("cuota", "S/D"), "ok": m.get("aprobado", False)})
    todos.sort(key=lambda x: -x["p"])

    mhtml = ""
    for m in todos:
        bc = "#f0b429" if m["ok"] else "#252838"
        badge = "<span style='font-size:9px;color:#f0b429;margin-left:5px'>&#10003;</span>" if m["ok"] else ""
        mhtml += (
            f"<div style='display:flex;justify-content:space-between;align-items:center;"
            f"padding:6px 10px;border-left:2px solid {bc};background:#151720;border-radius:3px;margin-bottom:3px'>"
            f"<span style='font-size:12px;color:#f0f0f8'>{m['n']}{badge}</span>"
            f"<div style='display:flex;gap:8px;align-items:center'>"
            f"<span style='font-family:monospace;font-size:12px;color:#f0b429;font-weight:700'>{m['p']}%</span>"
            f"<span style='font-family:monospace;font-size:10px;color:#22d3c5'>@{m['c']}</span>"
            f"</div></div>"
        )

    stats_rows = (
        sr("Goles/PJ", gf_h, gf_a) +
        sr("G.contra/PJ", gc_h, gc_a, False) +
        sr("Remates/PJ", rem_h, rem_a) +
        sr("Al arco/PJ", arco_h, arco_a) +
        sr("Corners/PJ", corners_h, corners_a) +
        sr("Tarjetas/PJ", tarj_h, tarj_a, False)
    )

    goles_h_html = "".join([barra(k, dist_h[k], max_prob) for k in range(6)])
    goles_a_html = "".join([barra(k, dist_a[k], max_prob) for k in range(6)])
    over_col = "#34d399" if p_over25 >= 60 else "#8890aa"
    btts_col = "#34d399" if p_btts >= 60 else "#8890aa"

    html = (
        f"<div style='font-family:IBM Plex Sans,sans-serif;color:#f0f0f8;font-size:13px'>"
        f"<div style='font-family:monospace;font-size:9px;color:#22d3c5;letter-spacing:3px;margin-bottom:10px'>ANALISIS {liga_nombre.upper()}</div>"

        # VS block
        f"<div style='background:#0e1018;border:1px solid #252838;border-radius:6px;padding:10px;margin-bottom:8px'>"
        f"<div style='display:grid;grid-template-columns:1fr auto 1fr;gap:4px;align-items:center;margin-bottom:8px'>"
        f"<div style='text-align:center'><div style='font-weight:700;font-size:13px'>{hn}</div>"
        f"<div style='font-family:monospace;font-size:10px;color:#f0b429'>lambda {lambda_h}</div>"
        f"<div style='margin-top:3px'>{forma_badges(forma_h)}</div></div>"
        f"<div style='font-size:12px;color:#252838;padding:0 4px'>VS</div>"
        f"<div style='text-align:center'><div style='font-weight:700;font-size:13px'>{an}</div>"
        f"<div style='font-family:monospace;font-size:10px;color:#f0b429'>lambda {lambda_a}</div>"
        f"<div style='margin-top:3px'>{forma_badges(forma_a)}</div></div>"
        f"</div>"

        # 1X2
        f"<div style='display:grid;grid-template-columns:1fr 1fr 1fr;gap:4px'>"
        f"<div style='background:rgba(79,142,247,0.12);border-radius:4px;padding:7px;text-align:center'>"
        f"<div style='font-family:monospace;font-size:20px;font-weight:700;color:#4f8ef7'>{p_win}%</div>"
        f"<div style='font-size:9px;color:#8890aa'>LOCAL</div>"
        f"<div style='font-family:monospace;font-size:10px;color:#22d3c5'>@{cuota(p_win)}</div></div>"
        f"<div style='background:rgba(90,94,122,0.12);border-radius:4px;padding:7px;text-align:center'>"
        f"<div style='font-family:monospace;font-size:20px;font-weight:700;color:#c8ccdf'>{p_draw}%</div>"
        f"<div style='font-size:9px;color:#8890aa'>EMPATE</div>"
        f"<div style='font-family:monospace;font-size:10px;color:#22d3c5'>@{cuota(p_draw)}</div></div>"
        f"<div style='background:rgba(255,61,90,0.1);border-radius:4px;padding:7px;text-align:center'>"
        f"<div style='font-family:monospace;font-size:20px;font-weight:700;color:#ff3d5a'>{p_loss}%</div>"
        f"<div style='font-size:9px;color:#8890aa'>VISITANTE</div>"
        f"<div style='font-family:monospace;font-size:10px;color:#22d3c5'>@{cuota(p_loss)}</div></div>"
        f"</div>"
        f"<div style='font-family:monospace;font-size:10px;color:#c8ccdf;text-align:center;margin-top:7px'>"
        f"Probable: <b style='color:#f0b429'>{hn} {best_score['hg']}-{best_score['ag']} {an} ({best_score['p']}%)</b></div>"
        f"</div>"

        # Goles
        f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-bottom:8px'>"
        f"<div style='background:#0e1018;border:1px solid #252838;border-radius:6px;padding:9px'>"
        f"<div style='font-family:monospace;font-size:8px;color:#22d3c5;letter-spacing:2px;margin-bottom:5px'>GOLES {hn[:12].upper()}</div>"
        f"{goles_h_html}</div>"
        f"<div style='background:#0e1018;border:1px solid #252838;border-radius:6px;padding:9px'>"
        f"<div style='font-family:monospace;font-size:8px;color:#22d3c5;letter-spacing:2px;margin-bottom:5px'>GOLES {an[:12].upper()}</div>"
        f"{goles_a_html}</div></div>"

        # Stats
        f"<div style='background:#0e1018;border:1px solid #252838;border-radius:6px;padding:9px;margin-bottom:8px'>"
        f"<div style='font-family:monospace;font-size:8px;color:#22d3c5;letter-spacing:2px;margin-bottom:6px'>STATS COMPARADAS</div>"
        f"<table style='width:100%;border-collapse:collapse'>"
        f"<tr style='border-bottom:1px solid #1c1f2e'>"
        f"<th style='font-family:monospace;font-size:9px;color:#8890aa;padding:2px 6px;text-align:left;font-weight:400'>STAT</th>"
        f"<th style='font-family:monospace;font-size:9px;color:#8890aa;padding:2px 6px;text-align:center;font-weight:400'>{hn[:10]}</th>"
        f"<th style='font-family:monospace;font-size:9px;color:#8890aa;padding:2px 6px;text-align:center;font-weight:400'>{an[:10]}</th>"
        f"</tr>{stats_rows}"
        f"<tr><td colspan='3' style='font-family:monospace;font-size:10px;color:#8890aa;padding:4px 6px'>"
        f"Over 2.5: <b style='color:{over_col}'>{p_over25}%</b> &nbsp;&#183;&nbsp; BTTS: <b style='color:{btts_col}'>{p_btts}%</b>"
        f"</td></tr></table></div>"

        # Mercados
        f"<div style='font-family:monospace;font-size:8px;color:#22d3c5;letter-spacing:2px;margin-bottom:5px'>MERCADOS</div>"
        f"{mhtml}"

        # Riesgos
        f"<div style='background:rgba(255,61,90,0.05);border:1px solid rgba(255,61,90,0.15);border-radius:5px;padding:7px;margin-top:7px'>"
        f"<div style='font-family:monospace;font-size:8px;color:#ff3d5a;letter-spacing:2px;margin-bottom:3px'>RIESGOS</div>"
        f"<div style='font-size:11px;color:#c8ccdf'>{'<br>'.join(riesgos)}</div>"
        f"</div></div>"
    )

    return jsonify({"analisis": html})


@app.route("/diag_as/<codigo>/<int:match_id>")
def diag_as(codigo, match_id):
    liga = LIGAS.get(codigo, {})
    as_id = liga.get("as_id")
    season = liga.get("season")
    md = fd_get(f"/matches/{match_id}")
    if "error" in md or "id" not in md:
        return jsonify({"error": "fd match not found", "raw": md})
    hn = md["homeTeam"]["name"]
    an = md["awayTeam"]["name"]
    teams_data = as_get("/teams", {"league": as_id, "season": season})
    teams_prev = as_get("/teams", {"league": as_id, "season": season-1})
    hid = _search_as(hn, as_id, season)
    aid = _search_as(an, as_id, season)
    return jsonify({
        "fd_home": hn, "fd_away": an,
        "as_league": as_id, "season": season,
        "hid_found": hid, "aid_found": aid,
        "teams_count_current": len(teams_data.get("response",[])),
        "teams_count_prev": len(teams_prev.get("response",[])),
        "sample_teams": [t["team"]["name"] for t in teams_data.get("response",[])[:10]],
    })

@app.route("/analisis_avanzado/<codigo>/<int:match_id>")
def analisis_avanzado(codigo, match_id):
    liga = LIGAS.get(codigo, {})
    as_id = liga.get("as_id")
    season = liga.get("season")

    md = fd_get(f"/matches/{match_id}")
    if "error" in md or "id" not in md:
        return jsonify({"error": "Partido no encontrado"})
    hn = md["homeTeam"]["name"]
    an = md["awayTeam"]["name"]
    home_id_fd = md["homeTeam"]["id"]
    away_id_fd = md["awayTeam"]["id"]

    home_avg = away_avg = None
    if as_id:
        hid = _search_as(hn, as_id, season)
        aid = _search_as(an, as_id, season)
        if hid and aid:
            home_avg = _avg_fixture_stats(hid, as_id, season)
            away_avg = _avg_fixture_stats(aid, as_id, season)

    if (not home_avg or not away_avg) and HL_KEY:
        h_hl_id = _get_hl_team_id(hn, codigo)
        a_hl_id = _get_hl_team_id(an, codigo)
        if h_hl_id and not home_avg:
            home_avg = _avg_stats_from_hl(h_hl_id, codigo)
        if a_hl_id and not away_avg:
            away_avg = _avg_stats_from_hl(a_hl_id, codigo)

    if not home_avg:
        home_avg = _avg_stats_from_espn(hn, codigo)
    if not away_avg:
        away_avg = _avg_stats_from_espn(an, codigo)
    if not home_avg:
        home_avg = _avg_stats_from_fd(home_id_fd, codigo, season)
    if not away_avg:
        away_avg = _avg_stats_from_fd(away_id_fd, codigo, season)

    DEFAULT_STATS = {
            "remates_pj": 12.0, "al_arco_pj": 4.5, "corners_pj": 5.0,
            "tarjetas_amarillas_pj": 2.0, "tarjetas_rojas_pj": 0.1,
            "posesion_avg": "—", "faltas_pj": "—", "source": "default"
    }
    if not home_avg:
        home_avg = DEFAULT_STATS.copy()
    if not away_avg:
        away_avg = DEFAULT_STATS.copy()

    mercados_extra = _mercados_avanzados(home_avg, away_avg, hn, an)

    return jsonify({
        "home_team": hn, "away_team": an,
        "home_stats": home_avg, "away_stats": away_avg,
        "mercados": mercados_extra,
    })


def _avg_stats_from_fd(team_id, liga_code, season):
    try:
        data = fd_get(f"/competitions/{liga_code}/matches", {
            "status": "FINISHED", "limit": 20
        })
        all_matches = data.get("matches", [])
        matches = [m for m in all_matches if
                   m.get("homeTeam", {}).get("id") == team_id or
                   m.get("awayTeam", {}).get("id") == team_id]
        matches = matches[-10:]

        if not matches:
            return None

            shots_total = []; shots_on = []; corners = []; yellow = []; red = []

        for m in matches:
            sc = m.get("score", {}).get("fullTime", {})
            hg = sc.get("home") or 0
            ag = sc.get("away") or 0
            is_home = m.get("homeTeam", {}).get("id") == team_id
            team_g = hg if is_home else ag

            shots_total.append(max(8, round(team_g * 5.5 + 7)))
            shots_on.append(max(2, round(team_g * 2.5 + 2)))
            corners.append(max(2, round(team_g * 1.8 + 3.5)))
            yellow.append(round(1.8 + (0.3 if not is_home else 0)))
            red.append(0.05)

        def avg(lst): return round(sum(lst)/len(lst), 1) if lst else "—"

        return {
            "remates_pj": avg(shots_total),
            "al_arco_pj": avg(shots_on),
            "corners_pj": avg(corners),
            "tarjetas_amarillas_pj": avg(yellow),
            "tarjetas_rojas_pj": avg(red),
            "posesion_avg": "—",
            "faltas_pj": "—",
            "source": "estimated"
        }
    except Exception as e:
        return None
        
def _avg_stats_from_espn(team_name, liga_code):
    slug = ESPN_SLUGS.get(liga_code)
    if not slug: 
        return None
    try:
        teams_data = espn_get(slug, "teams")
        if "error" in teams_data: 
            return None
        teams = teams_data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
        team_id = None
        clean = team_name.lower().replace(" fc","").replace(" cf","").strip()
        for t in teams:
            tn = t.get("team", {}).get("displayName", "").lower()
            if clean in tn or tn in clean:
                team_id = t.get("team", {}).get("id")
                break
        if not team_id: 
            return None
        stats_data = espn_get(slug, f"teams/{team_id}/statistics")
        if "error" in stats_data: 
            return None
        splits = stats_data.get("results", {}).get("splits", {}).get("categories", [])
        result = {}
        for cat in splits:
            for stat in cat.get("stats", []):
                name = stat.get("name", "")
                val = stat.get("value")
                if name == "avgShotsPerGame": 
                    result["remates_pj"] = round(float(val), 1) if val else "—"
                elif name == "avgShotsOnTargetPerGame": 
                    result["al_arco_pj"] = round(float(val), 1) if val else "—"
                elif name == "avgCornersPerGame": 
                    result["corners_pj"] = round(float(val), 1) if val else "—"
                elif name == "avgYellowCardsPerGame": 
                    result["tarjetas_amarillas_pj"] = round(float(val), 1) if val else "—"
                elif name == "avgFoulsPerGame": 
                    result["faltas_pj"] = round(float(val), 1) if val else "—"
                elif name == "avgPossessionPct": 
                    result["posesion_avg"] = round(float(val), 1) if val else "—"
        if result:
            result.setdefault("tarjetas_rojas_pj", "—")
            result["source"] = "espn"
            return result
        return None
    except Exception as e:
        print(f"ESPN stats error: {e}")
        return None

        shots_total = []; shots_on = []; corners = []; yellow = []; red = []

        for m in matches:
            sc = m.get("score", {}).get("fullTime", {})
            hg = sc.get("home") or 0
            ag = sc.get("away") or 0
            is_home = m.get("homeTeam", {}).get("id") == team_id
            team_g = hg if is_home else ag

            shots_total.append(max(8, round(team_g * 5.5 + 7)))
            shots_on.append(max(2, round(team_g * 2.5 + 2)))
            corners.append(max(2, round(team_g * 1.8 + 3.5)))
            yellow.append(round(1.8 + (0.3 if not is_home else 0)))
            red.append(0.05)

        def avg(lst): return round(sum(lst)/len(lst), 1) if lst else "—"

        return {
            "remates_pj": avg(shots_total),
            "al_arco_pj": avg(shots_on),
            "corners_pj": avg(corners),
            "tarjetas_amarillas_pj": avg(yellow),
            "tarjetas_rojas_pj": avg(red),
            "posesion_avg": "—",
            "faltas_pj": "—",
            "source": "estimated"
        }
    except Exception as e:
        return None

def _avg_fixture_stats(team_id, league_id, season):
    desde = (datetime.utcnow() - timedelta(days=120)).strftime("%Y-%m-%d")
    hasta = datetime.utcnow().strftime("%Y-%m-%d")
    fx_data = as_get("/fixtures", {"team": team_id, "season": season, "league": league_id, "from": desde, "to": hasta, "status": "FT"})
    if "error" in fx_data or not fx_data.get("response"):
        return None

    fixtures = fx_data["response"][-5:]
    totals = {
        "shots_total": [], "shots_on": [], "corners": [],
        "yellow": [], "red": [], "fouls": [], "possession": [],
    }

    for fx in fixtures:
        fid = fx.get("fixture", {}).get("id")
        if not fid: continue
        stats_data = as_get("/fixtures/statistics", {"fixture": fid, "team": team_id})
        if "error" in stats_data or not stats_data.get("response"):
            continue
        for team_stats in stats_data["response"]:
            for s in team_stats.get("statistics", []):
                t = s.get("type", "")
                v = s.get("value")
                if v is None: continue
                if isinstance(v, str) and v.endswith("%"):
                    try: v = float(v.replace("%", ""))
                    except: v = 0
                if not isinstance(v, (int, float)): continue
                if t == "Total Shots": totals["shots_total"].append(v)
                elif t == "Shots on Goal": totals["shots_on"].append(v)
                elif t == "Corner Kicks": totals["corners"].append(v)
                elif t == "Yellow Cards": totals["yellow"].append(v)
                elif t == "Red Cards": totals["red"].append(v)
                elif t == "Fouls": totals["fouls"].append(v)
                elif t == "Ball Possession": totals["possession"].append(v)
        time.sleep(0.3)

    def avg(lst):
        return round(sum(lst)/len(lst), 1) if lst else "—"

    return {
        "remates_pj": avg(totals["shots_total"]),
        "al_arco_pj": avg(totals["shots_on"]),
        "corners_pj": avg(totals["corners"]),
        "tarjetas_amarillas_pj": avg(totals["yellow"]),
        "tarjetas_rojas_pj": avg(totals["red"]),
        "faltas_pj": avg(totals["fouls"]),
        "posesion_avg": avg(totals["possession"]),
        "partidos_analizados": len(fixtures),
    }


def _mercados_avanzados(home, away, hn, an, ge=None):
    if ge is None:
        h_sot = home.get("al_arco_pj", 0) if isinstance(home.get("al_arco_pj"), (int, float)) else 0
        a_sot = away.get("al_arco_pj", 0) if isinstance(away.get("al_arco_pj"), (int, float)) else 0
        ge = round((h_sot + a_sot) * 0.33, 1) if (h_sot + a_sot) > 0 else 2.5
    if not home or not away: return []
    mercados = []
    home = {**{"corners_pj":"—","tarjetas_amarillas_pj":"—","remates_pj":"—","al_arco_pj":"—","posesion_avg":"—"}, **home}
    away = {**{"corners_pj":"—","tarjetas_amarillas_pj":"—","remates_pj":"—","al_arco_pj":"—","posesion_avg":"—"}, **away}

    if home.get("corners_pj") != "—" and away.get("corners_pj") != "—":
        total_corners = home["corners_pj"] + away["corners_pj"]
        if total_corners >= 10:
            p = min(85, round(50 + (total_corners - 9.5) * 12))
            mercados.append({"mercado": "Corners Totales Over 9.5","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "CORNERS","aprobado": p >= 75,"sintesis": f"Promedio combinado de corners: {round(total_corners,1)}/partido. {hn} {home['corners_pj']} y {an} {away['corners_pj']}."})
        if total_corners <= 9:
            p = min(85, round(50 + (9.5 - total_corners) * 12))
            mercados.append({"mercado": "Corners Totales Under 9.5","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "CORNERS","aprobado": p >= 75,"sintesis": f"Promedio combinado bajo: {round(total_corners,1)} corners/partido."})

    if home.get("remates_pj") != "—" and away.get("remates_pj") != "—":
        total_shots = home["remates_pj"] + away["remates_pj"]
        if total_shots >= 23:
            p = min(82, round(50 + (total_shots - 22.5) * 8))
            mercados.append({"mercado": "Remates Totales Over 22.5","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "SHOTS","aprobado": p >= 75,"sintesis": f"Promedio combinado: {round(total_shots,1)} remates/partido."})
        if total_shots <= 22:
            p = min(82, round(50 + (22.5 - total_shots) * 8))
            mercados.append({"mercado": "Remates Totales Under 22.5","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "SHOTS","aprobado": p >= 75,"sintesis": f"Promedio combinado bajo: {round(total_shots,1)} remates/partido."})
        if total_shots >= 26:
            p = min(80, round(45 + (total_shots - 25.5) * 8))
            mercados.append({"mercado": "Remates Totales Over 25.5","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "SHOTS","aprobado": p >= 75,"sintesis": f"Equipos ofensivos: {round(total_shots,1)} remates combinados/partido."})

    if home.get("al_arco_pj") != "—" and away.get("al_arco_pj") != "—":
        total_sot = home["al_arco_pj"] + away["al_arco_pj"]
        if total_sot >= 9:
            p = min(82, round(50 + (total_sot - 8.5) * 10))
            mercados.append({"mercado": "Remates al Arco Over 8.5","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "SOT","aprobado": p >= 75,"sintesis": f"Promedio combinado al arco: {round(total_sot,1)}/partido."})
        if total_sot <= 8:
            p = min(82, round(50 + (8.5 - total_sot) * 10))
            mercados.append({"mercado": "Remates al Arco Under 8.5","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "SOT","aprobado": p >= 75,"sintesis": f"Equipos poco precisos: {round(total_sot,1)} al arco/partido."})
        if total_sot >= 11:
            p = min(80, round(45 + (total_sot - 10.5) * 10))
            mercados.append({"mercado": "Remates al Arco Over 10.5","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "SOT","aprobado": p >= 75,"sintesis": f"Alta precisión combinada: {round(total_sot,1)} al arco/partido."})

    if home.get("tarjetas_amarillas_pj") != "—" and away.get("tarjetas_amarillas_pj") != "—":
        total_y = home["tarjetas_amarillas_pj"] + away["tarjetas_amarillas_pj"]
        if total_y >= 4:
            p = min(85, round(50 + (total_y - 3.5) * 12))
            mercados.append({"mercado": "Tarjetas Over 3.5","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "CARDS","aprobado": p >= 75,"sintesis": f"Promedio combinado: {round(total_y,1)} amarillas/partido."})
        if total_y <= 3:
            p = min(85, round(50 + (3.5 - total_y) * 12))
            mercados.append({"mercado": "Tarjetas Under 3.5","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "CARDS","aprobado": p >= 75,"sintesis": f"Partido limpio esperado: {round(total_y,1)} amarillas/partido."})
        if total_y >= 5:
            p = min(82, round(50 + (total_y - 4.5) * 12))
            mercados.append({"mercado": "Tarjetas Over 4.5","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "CARDS","aprobado": p >= 75,"sintesis": f"Promedio alto: {round(total_y,1)} amarillas/partido."})
        if total_y <= 4:
            p = min(82, round(50 + (4.5 - total_y) * 12))
            mercados.append({"mercado": "Tarjetas Under 4.5","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "CARDS","aprobado": p >= 75,"sintesis": f"Pocas amarillas esperadas: {round(total_y,1)}/partido."})
        if total_y >= 6:
            p = min(80, round(45 + (total_y - 5.5) * 12))
            mercados.append({"mercado": "Tarjetas Over 5.5","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "CARDS","aprobado": p >= 75,"sintesis": f"Partido caliente esperado: {round(total_y,1)} amarillas/partido."})

    if ge > 0:
        import math as _m
        def _poisson_range(lam, lo, hi):
            p = 0
            for k in range(lo, hi+1):
                p += (lam**k * _m.exp(-lam)) / _m.factorial(k)
            return min(95, round(p * 100))

        p_01 = _poisson_range(ge, 0, 1)
        p_23 = _poisson_range(ge, 2, 3)
        p_4plus = max(0, 100 - p_01 - p_23)

        if p_01 >= 30:
            mercados.append({"mercado": "Rango de Goles: 0-1","prob": p_01, "riesgo": 100-p_01, "cuota": _cuota(p_01), "tipo": "RANGE","aprobado": p_01 >= 65,"sintesis": f"Con {ge} goles esperados, hay {p_01}% de probabilidad de 0 o 1 gol."})
        if p_23 >= 30:
            mercados.append({"mercado": "Rango de Goles: 2-3","prob": p_23, "riesgo": 100-p_23, "cuota": _cuota(p_23), "tipo": "RANGE","aprobado": p_23 >= 65,"sintesis": f"Con {ge} goles esperados, el rango 2-3 es el más probable ({p_23}%)."})
        if p_4plus >= 20:
            mercados.append({"mercado": "Rango de Goles: 4+","prob": p_4plus, "riesgo": 100-p_4plus, "cuota": _cuota(p_4plus), "tipo": "RANGE","aprobado": p_4plus >= 60,"sintesis": f"Partido abierto: {p_4plus}% de probabilidad de 4 o más goles."})

    if home.get("posesion_avg") != "—" and away.get("posesion_avg") != "—":
        if home["posesion_avg"] >= 55 and away["posesion_avg"] < home["posesion_avg"]:
            p = min(85, round(home["posesion_avg"] + 10))
            mercados.append({"mercado": f"Mayor posesión — {hn}","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "POSS","aprobado": p >= 65,"sintesis": f"{hn} promedia {home['posesion_avg']}% de posesión."})
        elif away["posesion_avg"] >= 55:
            p = min(85, round(away["posesion_avg"]))
            mercados.append({"mercado": f"Mayor posesión — {an}","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "POSS","aprobado": p >= 65,"sintesis": f"{an} promedia {away['posesion_avg']}% de posesión."})

    if home.get("remates_pj") != "—" and away.get("remates_pj") != "—":
        diff = abs(home["remates_pj"] - away["remates_pj"])
        if diff >= 3:
            mas = hn if home["remates_pj"] > away["remates_pj"] else an
            mas_v = max(home["remates_pj"], away["remates_pj"])
            men_v = min(home["remates_pj"], away["remates_pj"])
            p = min(80, round(55 + diff * 3))
            mercados.append({"mercado": f"Más remates — {mas}","prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "SHOTS","aprobado": p >= 65,"sintesis": f"Diferencia clara: {mas_v} vs {men_v}/partido."})

    mercados.sort(key=lambda x: x["prob"], reverse=True)
    return mercados


@app.route("/diag/<codigo>")
def diag(codigo):
    liga = LIGAS.get(codigo, {})
    if liga.get("source") == "as":
        as_id = liga.get("as_id")
        season = liga.get("season")
        d = as_get("/fixtures", {"league": as_id, "season": season, "next": 5})
        return jsonify({"source": "api-sports", "league_id": as_id, "season": season, "raw": d})
    else:
        d = fd_get(f"/competitions/{codigo}/matches", {"limit": 100})
        return jsonify({"source": "football-data", "raw": d})


@app.route("/partidos/<codigo>")
def partidos(codigo):
    liga = LIGAS.get(codigo, {})
    source = liga.get("source", "fd")

    hoy = datetime.utcnow()

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT match_id, mp_acertado, comb_acertado, verificado FROM predicciones")
    preds = {row[0]: {"mp_ok":row[1], "comb_ok":row[2], "verif":row[3]} for row in c.fetchall()}
    conn.close()

    matches = []

    if source == "espn":
        # Amistosos internacionales via ESPN API - proximos 7 dias + ultimos 2
        # Limitado para evitar timeout de gunicorn (max ~25s total)
        espn_slugs_intl = ["fifa.friendly", "fifa.worldq.conmebol", "fifa.worldq.uefa",
                           "uefa.nations", "concacaf.nations.league"]
        seen_ids = set()
        fechas = [(hoy + timedelta(days=i)).strftime("%Y%m%d") for i in range(-2, 8)]
        t_start = time.time()
        for slug in espn_slugs_intl:
            if time.time() - t_start > 20:
                break
            for fecha_str in fechas:
                try:
                    ck = f"espn_intl:{slug}:{fecha_str}"
                    now_t = time.time()
                    if ck in _cache and now_t - _cache[ck][1] < 3600:
                        d = _cache[ck][0]
                    else:
                        r = requests.get(f"{ESPN_URL}/{slug}/scoreboard",
                                    params={"dates": fecha_str}, timeout=10)
                        d = r.json() if r.ok else {}
                        if d.get("events"):
                            _cache[ck] = (d, now_t)
                    for ev in d.get("events", []):
                        ev_id = ev.get("id", "")
                        if ev_id in seen_ids:
                            continue
                        seen_ids.add(ev_id)
                        comp_data = ev.get("competitions", [{}])[0]
                        competitors = comp_data.get("competitors", [])
                        home_c = next((c for c in competitors if c.get("homeAway") == "home"), {})
                        away_c = next((c for c in competitors if c.get("homeAway") == "away"), {})
                        status_obj = ev.get("status", {})
                        state = status_obj.get("type", {}).get("state", "pre")
                        estado = "FINISHED" if state == "post" else ("IN_PLAY" if state == "in" else "SCHEDULED")
                        resultado = None
                        if estado == "FINISHED":
                            resultado = f"{home_c.get('score','0')}-{away_c.get('score','0')}"
                        matches.append({
                            "id": f"espn_{slug}_{fecha_str}_{ev_id}",
                            "fecha": ev.get("date", ""),
                            "home": home_c.get("team", {}).get("displayName", ""),
                            "home_id": home_c.get("team", {}).get("id", ""),
                            "away": away_c.get("team", {}).get("displayName", ""),
                            "away_id": away_c.get("team", {}).get("id", ""),
                            "jornada": None,
                            "competicion": ev.get("name", slug),
                            "estado": estado,
                            "arbitro": None,
                            "resultado": resultado,
                            "mp_acertado": None,
                            "comb_acertado": None,
                            "tiene_prediccion": False,
                            "live_state": state,
                            "live_clock": status_obj.get("displayClock", ""),
                            "live_score": f"{home_c.get('score','0')}-{away_c.get('score','0')}" if state == "in" else None,
                        })
                except Exception as e:
                    print(f"ESPN intl error {slug} {fecha_str}: {e}")
        # Ordenar por fecha
        matches.sort(key=lambda x: x.get("fecha", ""))
        return jsonify({"response": matches, "total": len(matches)})

    elif source == "as":
        as_id = liga.get("as_id")
        season = liga.get("season")
        desde = (hoy - timedelta(days=2)).strftime("%Y-%m-%d")
        hasta = (hoy + timedelta(days=120)).strftime("%Y-%m-%d")
        data = as_get("/fixtures", {"league": as_id, "season": season, "from": desde, "to": hasta})
        if "error" in data:
            return jsonify({"response": [], "error": data["error"]})
        for fx in data.get("response", []):
            fixture = fx.get("fixture", {})
            teams = fx.get("tx", {}) or fx.get("teams", {})
            home_t = teams.get("home", {})
            away_t = teams.get("away", {})
            goals = fx.get("goals", {})
            status = fixture.get("status", {}).get("short", "NS")
            estado = "FINISHED" if status in ("FT", "AET", "PEN") else ("SCHEDULED" if status == "NS" else status)
            resultado = f"{goals.get('home', 0)}-{goals.get('away', 0)}" if estado == "FINISHED" else None
            mid = fixture.get("id")
            pred = preds.get(mid)
            if estado == "FINISHED" and pred and not pred["verif"]:
                verify_prediction(mid, goals.get('home', 0), goals.get('away', 0))
            matches.append({
                "id": mid,
                "fecha": fixture.get("date", ""),
                "home": home_t.get("name", ""),
                "home_id": home_t.get("id"),
                "away": away_t.get("name", ""),
                "away_id": away_t.get("id"),
                "jornada": fx.get("league", {}).get("round", "").replace("Regular Season - ", "J"),
                "competicion": liga.get("nombre", ""),
                "estado": estado,
                "arbitro": fixture.get("referee"),
                "resultado": resultado,
                "mp_acertado": pred["mp_ok"] if pred else None,
                "comb_acertado": pred["comb_ok"] if pred else None,
                "tiene_prediccion": pred is not None,
            })
    else:
        desde = (hoy - timedelta(days=2)).strftime("%Y-%m-%d")
        hasta = (hoy + timedelta(days=120)).strftime("%Y-%m-%d")
        is_cup = codigo in ("CL", "WC", "EC")
        if is_cup:
            d1 = fd_get(f"/competitions/{codigo}/matches", {"dateFrom": hoy.strftime('%Y-%m-%d'), "dateTo": (hoy + timedelta(days=90)).strftime('%Y-%m-%d'), "limit": 20})
            d2 = fd_get(f"/competitions/{codigo}/matches", {"dateFrom": (hoy - timedelta(days=7)).strftime('%Y-%m-%d'), "dateTo": hoy.strftime('%Y-%m-%d'), "status": "FINISHED", "limit": 20})
            all_m = (d1.get("matches", []) if "error" not in d1 else []) + (d2.get("matches", []) if "error" not in d2 else [])
            seen = set()
            deduped = []
            for mm in all_m:
                if mm["id"] not in seen:
                    seen.add(mm["id"])
                    deduped.append(mm)
            data = {"matches": deduped}
        else:
            data = fd_get(f"/competitions/{codigo}/matches", {"dateFrom": desde, "dateTo": hasta, "limit": 80})
        if "error" in data:
            return jsonify({"response": [], "error": data["error"]})
        for m in data.get("matches", []):
            refs = m.get("referees", [])
            score = m.get("score", {}).get("fullTime", {})
            estado = m["status"]
            if not m["homeTeam"].get("name") or not m["awayTeam"].get("name"):
                continue
            resultado = f"{score.get('home',0)}-{score.get('away',0)}" if estado == "FINISHED" else None
            pred = preds.get(m["id"])
            if estado == "FINISHED" and pred and not pred["verif"]:
                verify_prediction(m["id"], score.get('home', 0), score.get('away', 0))
            matches.append({
                "id": m["id"], "fecha": m["utcDate"],
                "home": m["homeTeam"]["name"], "home_id": m["homeTeam"]["id"],
                "away": m["awayTeam"]["name"], "away_id": m["awayTeam"]["id"],
                "jornada": m.get("matchday"),
                "competicion": data.get("competition", {}).get("name", ""),
                "estado": estado,
                "arbitro": refs[0]["name"] if refs else None,
                "resultado": resultado,
                "mp_acertado": pred["mp_ok"] if pred else None,
                "comb_acertado": pred["comb_ok"] if pred else None,
                "tiene_prediccion": pred is not None,
            })

    # Enriquecer con scores en vivo de ESPN
    slug = ESPN_SLUGS.get(codigo)
    if slug:
        try:
            espn_data = espn_get(slug, "scoreboard")
            espn_scores = {}
            for ev in espn_data.get("events", []):
                comp = ev.get("competitions", [{}])[0]
                competitors = comp.get("competitors", [])
                state = ev.get("status", {}).get("type", {}).get("state", "pre")
                clock = ev.get("status", {}).get("displayClock", "")
                home_c = next((c for c in competitors if c.get("homeAway") == "home"), {})
                away_c = next((c for c in competitors if c.get("homeAway") == "away"), {})
                hn_e = home_c.get("team", {}).get("displayName", "").lower()
                an_e = away_c.get("team", {}).get("displayName", "").lower()
                espn_scores[(hn_e, an_e)] = {
                    "live_state": state,
                    "live_clock": clock,
                    "live_score": f"{home_c.get('score','0')}-{away_c.get('score','0')}" if state == "in" else None,
                }
            for m in matches:
                key = (m["home"].lower(), m["away"].lower())
                if key in espn_scores:
                    m.update(espn_scores[key])
        except: pass

    return jsonify({"response": matches, "total": len(matches)})


def _auto_verify_pending():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT match_id, liga FROM predicciones WHERE verificado=0")
    pending = c.fetchall()
    conn.close()
    if not pending:
        return
    for match_id, liga in pending[:15]:
        try:
            liga_cfg = LIGAS.get(liga, {})
            source = liga_cfg.get("source", "fd")
            if source == "as":
                fx = as_get("/fixtures", {"id": match_id})
                if fx.get("response"):
                    f = fx["response"][0]
                    st = f.get("fixture", {}).get("status", {}).get("short", "")
                    if st in ("FT", "AET", "PEN"):
                        g = f.get("goals", {})
                        verify_prediction(match_id, g.get("home", 0), g.get("away", 0))
            else:
                d = fd_get(f"/matches/{match_id}")
                if d.get("status") == "FINISHED":
                    sc = d.get("score", {}).get("fullTime", {})
                    verify_prediction(match_id, sc.get("home", 0), sc.get("away", 0))
            time.sleep(0.5)
        except Exception as e:
            print(f"Auto-verify error {match_id}: {e}")


@app.route("/estadisticas")
def estadisticas():
    return render_template("estadisticas.html")


def _do_analyze(codigo, match_id):
    cached = get_cached_analysis(match_id, codigo)
    if cached:
        return cached
    liga = LIGAS.get(codigo, {})
    source = liga.get("source", "fd")
    resultado = _do_analyze_as(codigo, match_id, liga) if source == "as" else _do_analyze_fd(codigo, match_id)
    if "error" not in resultado:
        save_cached_analysis(match_id, codigo, resultado)
    return resultado

def _do_analyze_as(codigo, match_id, liga):
    as_id = liga.get("as_id")
    season = liga.get("season")

    fx_data = as_get("/fixtures", {"id": match_id})
    if "error" in fx_data or not fx_data.get("response"):
        return {"error": "Partido no encontrado"}
    fx = fx_data["response"][0]

    fixture = fx.get("fixture", {})
    teams = fx.get("teams", {})
    home_t = teams.get("home", {})
    away_t = teams.get("away", {})
    hid = home_t.get("id")
    aid = away_t.get("id")
    hn = home_t.get("name", "")
    an = away_t.get("name", "")
    arbitro_name = fixture.get("referee")

    sd = as_get("/standings", {"league": as_id, "season": season})
    standings = []
    if not "error" in sd and sd.get("response"):
        try:
            standings = sd["response"][0]["league"]["standings"][0]
        except: pass

    hp = _find_as(standings, hid)
    ap = _find_as(standings, aid)

    hs = as_get("/teams/statistics", {"league": as_id, "season": season, "team": hid}).get("response")
    aws = as_get("/teams/statistics", {"league": as_id, "season": season, "team": aid}).get("response")

    hfx = as_get("/fixtures", {"team": hid, "season": season, "league": as_id, "last": 10})
    afx = as_get("/fixtures", {"team": aid, "season": season, "league": as_id, "last": 10})
    hf = _forma_as(hfx.get("response", []), hid)
    af = _forma_as(afx.get("response", []), aid)
    hl3 = _u3_as(hfx.get("response", []), hid)
    al3 = _u3_as(afx.get("response", []), aid)

    h2h_data = as_get("/fixtures/headtohead", {"h2h": f"{hid}-{aid}", "last": 5})
    h2h = _h2h_as(h2h_data.get("response", []), hid, aid)

    sc_data = as_get("/players/topscorers", {"league": as_id, "season": season})
    jh = _enrich(_jugadores_as(sc_data.get("response", []), hid), hn, hf)
    ja = _enrich(_jugadores_as(sc_data.get("response", []), aid), an, af)

    hp_compat = _conv_pos_as(hp) if hp else None
    ap_compat = _conv_pos_as(ap) if ap else None
    hh_compat = _conv_local_as(hp, "home") if hp else None
    aa_compat = _conv_local_as(ap, "away") if ap else None

    arb_perfil = _arbitro_perfil(arbitro_name)
    md_compat = {
        "id": match_id,
        "utcDate": fixture.get("date", ""),
        "matchday": fx.get("league", {}).get("round", "").replace("Regular Season - ", ""),
        "competition": {"name": liga.get("nombre", "")},
        "homeTeam": {"id": hid, "name": hn},
        "awayTeam": {"id": aid, "name": an},
        "status": fixture.get("status", {}).get("short", "NS"),
        "score": {"fullTime": fx.get("goals", {})},
        "head2head": h2h,
        "referees": [{"name": arbitro_name}] if arbitro_name else [],
    }

    resultado = _analisis(md_compat, hp_compat, ap_compat, hh_compat, aa_compat, hf, af, h2h, hn, an, standings)
    resultado["arbitro"] = arbitro_name
    resultado["arbitro_perfil"] = arb_perfil
    resultado["ultimos3"] = {"home": hl3, "away": al3}
    resultado["jugadores"] = {"home": jh, "away": ja}
    resultado["stats_avanzadas"] = _adv(hs, aws, hn, an)
    resultado["stats_equipo"] = {"home": _team_stats(hs, hp_compat, hf), "away": _team_stats(aws, ap_compat, af)}
    resultado["resumen"] = _resumen(hn, an, hf, af, hp_compat, ap_compat, hh_compat, aa_compat, h2h, arbitro_name, arb_perfil, resultado, md_compat)
    # Bajas confirmadas via Claude web search
    try:
        bajas = _buscar_bajas(hn, an, fixture.get("date", ""))
        resultado["bajas"] = bajas
        resultado["resumen"] = _agregar_bajas_resumen(resultado["resumen"], bajas, hn, an)
    except Exception as e:
        print(f"Bajas error: {e}")
        resultado["bajas"] = {"home": [], "away": []}

    estado = md_compat.get("status", "")
    if estado == "NS":
        save_prediction(match_id, codigo, fixture.get("date", ""), hn, an, resultado["veredicto"])
    elif estado in ("FT", "AET", "PEN"):
        save_prediction(match_id, codigo, fixture.get("date", ""), hn, an, resultado["veredicto"])
        goals = fx.get("goals", {})
        if goals.get("home") is not None:
            verify_prediction(match_id, goals["home"], goals["away"])
    try:
        home_adv = resultado.get("stats_equipo", {}).get("home")
        away_adv = resultado.get("stats_equipo", {}).get("away")
        if home_adv and away_adv and home_adv.get("remates_pj") != "—":
            adv_markets = _mercados_avanzados(home_adv, away_adv, hn, an, resultado.get("goles_esperados"))
            resultado["mercados"].extend(adv_markets)
            resultado["mercados"].sort(key=lambda x: x["prob"], reverse=True)
            aprobados_all = [m for m in resultado["mercados"] if m.get("aprobado")]
            if aprobados_all:
                mp = f"{aprobados_all[0]['mercado']} ({aprobados_all[0]['prob']}% · cuota @{aprobados_all[0]['cuota']})"
                resultado["veredicto"]["mercado_principal"] = mp
                resultado["veredicto"]["mp_prob"] = aprobados_all[0]["prob"]
                resultado["veredicto"]["total_aprobados"] = len(aprobados_all)
        aprobados_final = [m for m in resultado["mercados"] if m.get("aprobado")]
        resultado["match_score"] = _calcular_score(
            aprobados_final,
            resultado["probabilidades"]["home"],
            resultado["probabilidades"]["away"],
            resultado["veredicto"].get("favorito") and "alta" or "baja"
        )
    except Exception as e:
        print(f"Mercados avanzados error: {e}")
    return resultado


def _find_as(standings, tid):
    for s in standings:
        if s.get("team", {}).get("id") == tid: return s
    return None


def _conv_pos_as(s):
    if not s: return None
    return {
        "team": {"id": s.get("team", {}).get("id"), "name": s.get("team", {}).get("name")},
        "position": s.get("rank"),
        "points": s.get("points", 0),
        "playedGames": s.get("all", {}).get("played", 0),
        "won": s.get("all", {}).get("win", 0),
        "draw": s.get("all", {}).get("draw", 0),
        "lost": s.get("all", {}).get("lose", 0),
        "goalsFor": s.get("all", {}).get("goals", {}).get("for", 0),
        "goalsAgainst": s.get("all", {}).get("goals", {}).get("against", 0),
    }


def _conv_local_as(s, side):
    if not s: return None
    key = "home" if side == "home" else "away"
    d = s.get(key, {})
    return {
        "team": {"id": s.get("team", {}).get("id")},
        "playedGames": d.get("played", 0),
        "won": d.get("win", 0),
        "draw": d.get("draw", 0),
        "lost": d.get("lose", 0),
    }


def _forma_as(fixtures, tid):
    if not fixtures:
        return {"form":"","w":0,"d":0,"l":0,"gf":0,"gc":0,"matches":0,"ppg":0,"gf_avg":0,"gc_avg":0,"clean_sheets":0,"failed_to_score":0}
    fixtures = sorted(fixtures, key=lambda x: x.get("fixture", {}).get("date", ""), reverse=True)[:10]
    w=d=l=gf=gc=cs=fts=0; fs=""
    for fx in fixtures:
        teams = fx.get("teams", {})
        goals = fx.get("goals", {})
        hg, ag = goals.get("home"), goals.get("away")
        if hg is None: continue
        ih = teams.get("home", {}).get("id") == tid
        my, th = (hg, ag) if ih else (ag, hg)
        gf += my; gc += th
        if th == 0: cs += 1
        if my == 0: fts += 1
        if my > th: w += 1; fs += "W"
        elif my == th: d += 1; fs += "D"
        else: l += 1; fs += "L"
    t = w+d+l
    return {"form":fs[:5],"w":w,"d":d,"l":l,"gf":gf,"gc":gc,"matches":t,
        "ppg":round((w*3+d)/t,2) if t>0 else 0,
        "gf_avg":round(gf/t,2) if t>0 else 0,
        "gc_avg":round(gc/t,2) if t>0 else 0,
        "clean_sheets":cs,"failed_to_score":fts}


def _u3_as(fixtures, tid):
    if not fixtures: return []
    fixtures = sorted(fixtures, key=lambda x: x.get("fixture", {}).get("date", ""), reverse=True)[:3]
    r = []
    for fx in fixtures:
        teams = fx.get("teams", {})
        goals = fx.get("goals", {})
        hg, ag = goals.get("home"), goals.get("away")
        if hg is None: continue
        ih = teams.get("home", {}).get("id") == tid
        my, th = (hg, ag) if ih else (ag, hg)
        res = "W" if my>th else ("D" if my==th else "L")
        r.append({
            "fecha": fx.get("fixture", {}).get("date", "")[:10],
            "rival": teams.get("away", {}).get("name") if ih else teams.get("home", {}).get("name"),
            "competicion": "SA",
            "marcador": f"{hg}-{ag}",
            "local": ih,
            "resultado": res,
        })
    return r


def _h2h_as(fixtures, hid, aid):
    if not fixtures: return {"numberOfMatches": 0, "totalGoals": 0, "homeTeam": {"wins": 0, "draws": 0}, "awayTeam": {"wins": 0, "draws": 0}}
    hw = aw = d = tg = 0
    for fx in fixtures:
        goals = fx.get("goals", {})
        teams = fx.get("teams", {})
        hg, ag = goals.get("home"), goals.get("away")
        if hg is None: continue
        tg += hg + ag
        local_id = teams.get("home", {}).get("id")
        if hg == ag: d += 1
        elif (local_id == hid and hg > ag) or (local_id == aid and ag > hg):
            hw += 1
        else:
            aw += 1
    return {
        "numberOfMatches": len(fixtures),
        "totalGoals": tg,
        "homeTeam": {"wins": hw, "draws": d},
        "awayTeam": {"wins": aw, "draws": d},
    }


def _jugadores_as(scorers, tid):
    j = []
    for s in scorers:
        stats = s.get("statistics", [{}])[0]
        team = stats.get("team", {})
        if team.get("id") != tid: continue
        goals = stats.get("goals", {})
        games = stats.get("games", {})
        g = goals.get("total", 0) or 0
        a = goals.get("assists", 0) or 0
        p = games.get("appearences", 0) or games.get("appearances", 0) or 0
        j.append({
            "nombre": s.get("player", {}).get("name", ""),
            "goles": g, "asistencias": a, "partidos": p,
            "promedio": round(g/max(p, 1), 2),
        })
    return j[:3]


def _do_analyze_fd(codigo, match_id):
    liga=LIGAS.get(codigo,{})
    md=fd_get(f"/matches/{match_id}")
    if "error" in md or "id" not in md: return {"error":"Partido no encontrado"}
    hid,aid=md["homeTeam"]["id"],md["awayTeam"]["id"]
    hn,an=md["homeTeam"]["name"],md["awayTeam"]["name"]
    sd=fd_get(f"/competitions/{codigo}/standings")
    tt,th,ta=[],[],[]
    for s in sd.get("standings",[]):
        if s["type"]=="TOTAL":tt=s["table"]
        elif s["type"]=="HOME":th=s["table"]
        elif s["type"]=="AWAY":ta=s["table"]
    hp,ap=_find(tt,hid),_find(tt,aid)
    hh,aa=_find(th,hid),_find(ta,aid)
    fhr=fd_get(f"/teams/{hid}/matches",{"status":"FINISHED","limit":10})
    far=fd_get(f"/teams/{aid}/matches",{"status":"FINISHED","limit":10})
    hf=_forma(fhr.get("matches",[]),hid)
    af=_forma(far.get("matches",[]),aid)
    hl3=_u3(fhr.get("matches",[]),hid)
    al3=_u3(far.get("matches",[]),aid)
    h2h=md.get("head2head",{})
    refs=md.get("referees",[])
    arbitro_name=refs[0]["name"] if refs else None
    sc=fd_get(f"/competitions/{codigo}/scorers",{"limit":15})
    jh=_enrich(_jugadores(sc.get("scorers",[]),hid),hn,hf)
    ja=_enrich(_jugadores(sc.get("scorers",[]),aid),an,af)
    hs=_get_as(hn,liga.get("as_id"),liga.get("season"))
    aws=_get_as(an,liga.get("as_id"),liga.get("season"))
    arb_perfil=_arbitro_perfil(arbitro_name)

    resultado=_analisis(md,hp,ap,hh,aa,hf,af,h2h,hn,an,tt)
    resultado["arbitro"]=arbitro_name
    resultado["arbitro_perfil"]=arb_perfil
    resultado["ultimos3"]={"home":hl3,"away":al3}
    resultado["jugadores"]={"home":jh,"away":ja}
    resultado["stats_avanzadas"]=_adv(hs,aws,hn,an)
    resultado["stats_equipo"]={"home":_team_stats(hs,hp,hf),"away":_team_stats(aws,ap,af)}
    resultado["resumen"]=_resumen(hn,an,hf,af,hp,ap,hh,aa,h2h,arbitro_name,arb_perfil,resultado,md)
    # Bajas confirmadas via Claude web search
    try:
        bajas = _buscar_bajas(hn, an, md.get("utcDate", ""))
        resultado["bajas"] = bajas
        resultado["resumen"] = _agregar_bajas_resumen(resultado["resumen"], bajas, hn, an)
    except Exception as e:
        print(f"Bajas error: {e}")
        resultado["bajas"] = {"home": [], "away": []}

    estado = md.get("status", "")
    if estado in ("SCHEDULED", "TIMED"):
        save_prediction(match_id, codigo, md.get("utcDate",""), hn, an, resultado["veredicto"])
    elif estado == "FINISHED":
        save_prediction(match_id, codigo, md.get("utcDate",""), hn, an, resultado["veredicto"])
        score = md.get("score",{}).get("fullTime",{})
        if score.get("home") is not None:
            verify_prediction(match_id, score["home"], score["away"])
    try:
        home_adv = resultado.get("stats_equipo", {}).get("home")
        away_adv = resultado.get("stats_equipo", {}).get("away")
        if home_adv and away_adv and home_adv.get("remates_pj") != "—":
            adv_markets = _mercados_avanzados(home_adv, away_adv, hn, an, resultado.get("goles_esperados"))
            resultado["mercados"].extend(adv_markets)
            resultado["mercados"].sort(key=lambda x: x["prob"], reverse=True)
            aprobados_all = [m for m in resultado["mercados"] if m.get("aprobado")]
            if aprobados_all:
                mp = f"{aprobados_all[0]['mercado']} ({aprobados_all[0]['prob']}% · cuota @{aprobados_all[0]['cuota']})"
                resultado["veredicto"]["mercado_principal"] = mp
                resultado["veredicto"]["mp_prob"] = aprobados_all[0]["prob"]
                resultado["veredicto"]["total_aprobados"] = len(aprobados_all)
        aprobados_final = [m for m in resultado["mercados"] if m.get("aprobado")]
        resultado["match_score"] = _calcular_score(
            aprobados_final,
            resultado["probabilidades"]["home"],
            resultado["probabilidades"]["away"],
            resultado["veredicto"].get("favorito") and "alta" or "baja"
        )
    except Exception as e:
        print(f"Mercados avanzados error: {e}")
    return resultado


@app.route("/analizar_pendientes/<codigo>")
def analizar_pendientes(codigo):
    liga = LIGAS.get(codigo, {})
    source = liga.get("source", "fd")
    hoy = datetime.utcnow()
    desde = (hoy - timedelta(days=14)).strftime("%Y-%m-%d")
    hasta = hoy.strftime("%Y-%m-%d")

    fixtures_ids = []
    if source == "as":
        data = as_get("/fixtures", {"league": liga.get("as_id"), "season": liga.get("season"), "from": desde, "to": hasta, "status": "FT"})
        if "error" in data:
            return jsonify({"error": data["error"], "procesados": 0})
        fixtures_ids = [fx.get("fixture", {}).get("id") for fx in data.get("response", []) if fx.get("fixture", {}).get("id")]
    else:
        data = fd_get(f"/competitions/{codigo}/matches", {"dateFrom": desde, "dateTo": hasta, "status": "FINISHED", "limit": 100})
        if "error" in data:
            return jsonify({"error": data["error"], "procesados": 0})
        fixtures_ids = [m["id"] for m in data.get("matches", [])]

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT match_id FROM predicciones WHERE verificado=1")
    ya_verif = {row[0] for row in c.fetchall()}
    conn.close()

    procesados = 0
    errores = 0
    pendientes = [mid for mid in fixtures_ids if mid not in ya_verif]
    for mid in pendientes:
        try:
            r = _do_analyze(codigo, mid)
            if "error" not in r:
                procesados += 1
            else:
                errores += 1
            time.sleep(0.6)
        except Exception as e:
            errores += 1
            print(f"Error analizando {mid}: {e}")

    return jsonify({"procesados": procesados, "errores": errores, "total": len(pendientes)})


@app.route("/analizar/<codigo>/<int:match_id>")
def analizar(codigo, match_id):
    r = _do_analyze(codigo, match_id)
    return jsonify(r)


@app.route("/analizar/INTL/<espn_id>")
def analizar_intl(espn_id):
    """Analisis de amistosos internacionales usando ELO de eloratings.net"""
    # ID formato: espn_{slug}_{fecha}_{ev_id}  ej: espn_fifa.friendly_20260611_401874104
    partido = None
    parts = espn_id.split("_", 3)  # ["espn", slug, fecha, ev_id]
    if len(parts) == 4:
        _, slug, fecha_str, real_id = parts
        try:
            ck = f"espn_intl:{slug}:{fecha_str}"
            now_t = time.time()
            if ck in _cache and now_t - _cache[ck][1] < 3600:
                d = _cache[ck][0]
            else:
                r = requests.get(f"{ESPN_URL}/{slug}/scoreboard",
                            params={"dates": fecha_str}, timeout=10)
                d = r.json() if r.ok else {}
                if d.get("events"):
                    _cache[ck] = (d, now_t)
            for ev in d.get("events", []):
                if str(ev.get("id", "")) == str(real_id):
                    partido = ev
                    break
        except Exception as e:
            print(f"analizar_intl error: {e}")

    if not partido:
        return jsonify({"error": "Partido no encontrado"})

    comp_data = partido.get("competitions", [{}])[0]
    competitors = comp_data.get("competitors", [])
    home_c = next((c for c in competitors if c.get("homeAway") == "home"), {})
    away_c = next((c for c in competitors if c.get("homeAway") == "away"), {})
    hn = home_c.get("team", {}).get("displayName", "")
    an = away_c.get("team", {}).get("displayName", "")
    fecha = partido.get("date", "")

    # ELO en tiempo real de eloratings.net (mismo que wc_data)
    elo_data = {}
    try:
        r = requests.get("https://www.eloratings.net/World.tsv",
                 headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                 timeout=15)
        if r.ok:
            lines = r.content.decode('utf-8', errors='replace').strip().split("\n")
            for line in lines[1:]:
                parts = line.strip().split("\t")
                if len(parts) >= 3:
                    try:
                        nombre = parts[1].strip()
                        elo_data[nombre.lower()] = {"rank": int(parts[0]), "elo": int(float(parts[2])), "nombre": nombre}
                    except: pass
    except Exception as e:
        print(f"ELO fetch error: {e}")
    # Fallback estatico si falla el fetch
    if not elo_data:
        elo_data = {
        "spain": {"rank": 1, "elo": 2088}, "france": {"rank": 2, "elo": 2005},
        "england": {"rank": 3, "elo": 1994}, "brazil": {"rank": 4, "elo": 1983},
        "argentina": {"rank": 5, "elo": 1975}, "portugal": {"rank": 6, "elo": 1960},
        "belgium": {"rank": 7, "elo": 1942}, "netherlands": {"rank": 8, "elo": 1938},
        "germany": {"rank": 9, "elo": 1930}, "italy": {"rank": 10, "elo": 1922},
        "croatia": {"rank": 11, "elo": 1905}, "colombia": {"rank": 12, "elo": 1898},
        "uruguay": {"rank": 13, "elo": 1890}, "denmark": {"rank": 14, "elo": 1882},
        "switzerland": {"rank": 15, "elo": 1875}, "usa": {"rank": 16, "elo": 1868},
        "united states": {"rank": 16, "elo": 1868}, "mexico": {"rank": 17, "elo": 1860},
        "morocco": {"rank": 18, "elo": 1855}, "japan": {"rank": 19, "elo": 1848},
        "senegal": {"rank": 20, "elo": 1840}, "australia": {"rank": 21, "elo": 1832},
        "austria": {"rank": 22, "elo": 1825}, "ukraine": {"rank": 23, "elo": 1818},
        "turkey": {"rank": 24, "elo": 1812}, "ecuador": {"rank": 25, "elo": 1805},
        "chile": {"rank": 26, "elo": 1798}, "iran": {"rank": 27, "elo": 1792},
        "sweden": {"rank": 28, "elo": 1788}, "poland": {"rank": 29, "elo": 1782},
        "hungary": {"rank": 30, "elo": 1778}, "norway": {"rank": 31, "elo": 1772},
        "korea republic": {"rank": 32, "elo": 1765}, "south korea": {"rank": 32, "elo": 1765},
        "czech republic": {"rank": 33, "elo": 1758}, "czechia": {"rank": 33, "elo": 1758},
        "serbia": {"rank": 34, "elo": 1752}, "nigeria": {"rank": 35, "elo": 1748},
        "egypt": {"rank": 36, "elo": 1742}, "cameroon": {"rank": 37, "elo": 1738},
        "peru": {"rank": 38, "elo": 1732}, "russia": {"rank": 39, "elo": 1728},
        "scotland": {"rank": 40, "elo": 1722}, "romania": {"rank": 41, "elo": 1718},
        "algeria": {"rank": 42, "elo": 1712}, "venezuela": {"rank": 43, "elo": 1705},
        "costa rica": {"rank": 44, "elo": 1698}, "ivory coast": {"rank": 45, "elo": 1695},
        "cote d'ivoire": {"rank": 45, "elo": 1695}, "slovakia": {"rank": 46, "elo": 1690},
        "ghana": {"rank": 47, "elo": 1685}, "greece": {"rank": 48, "elo": 1680},
        "south africa": {"rank": 49, "elo": 1675}, "bolivia": {"rank": 50, "elo": 1668},
        "paraguay": {"rank": 51, "elo": 1662}, "canada": {"rank": 52, "elo": 1658},
        "qatar": {"rank": 53, "elo": 1652}, "saudi arabia": {"rank": 54, "elo": 1648},
        "iraq": {"rank": 55, "elo": 1642}, "tunisia": {"rank": 56, "elo": 1638},
        "mali": {"rank": 57, "elo": 1632}, "wales": {"rank": 58, "elo": 1628},
        "republic of ireland": {"rank": 59, "elo": 1622}, "ireland": {"rank": 59, "elo": 1622},
        "jamaica": {"rank": 60, "elo": 1618}, "haiti": {"rank": 61, "elo": 1610},
        "panama": {"rank": 62, "elo": 1605}, "honduras": {"rank": 63, "elo": 1598},
        "bosnia-herzegovina": {"rank": 64, "elo": 1592}, "north macedonia": {"rank": 65, "elo": 1585},
        "albania": {"rank": 66, "elo": 1578}, "zimbabwe": {"rank": 67, "elo": 1572},
        "trinidad and tobago": {"rank": 68, "elo": 1565}, "curacao": {"rank": 69, "elo": 1558},
        "india": {"rank": 70, "elo": 1552}, "uzbekistan": {"rank": 71, "elo": 1548},
        "el salvador": {"rank": 72, "elo": 1542}, "nicaragua": {"rank": 73, "elo": 1480},
        "sudan": {"rank": 74, "elo": 1475}, "gambia": {"rank": 75, "elo": 1520},
        "andorra": {"rank": 76, "elo": 1200}, "lebanon": {"rank": 77, "elo": 1490},
        "kosovo": {"rank": 78, "elo": 1545}, "finland": {"rank": 79, "elo": 1662},
        "bulgaria": {"rank": 80, "elo": 1598}, "israel": {"rank": 81, "elo": 1642},
        "new zealand": {"rank": 82, "elo": 1620},
    }  # fin fallback

    # Buscar ELO de cada selección (fuzzy match)
    # Alias ESPN -> eloratings
    ALIAS_MAP = {
        "curacao": "curacao", "curazao": "curacao",
        "south korea": "korea republic", "korea": "korea republic",
        "north korea": "korea dpr",
        "usa": "usa", "united states": "usa", "us": "usa",
        "iran": "ir iran", "ir iran": "ir iran",
        "ivory coast": "cote d'ivoire", "cote d ivoire": "cote d'ivoire",
        "cape verde": "cape verde islands",
        "czech republic": "czechia", "czechia": "czechia",
        "trinidad and tobago": "trinidad and tobago",
        "bosnia-herzegovina": "bosnia-herzegovina",
        "bosnia and herzegovina": "bosnia-herzegovina",
        "north macedonia": "north macedonia",
        "republic of ireland": "ireland",
        "congo dr": "dr congo", "democratic republic of congo": "dr congo",
        "congo": "congo", "republic of congo": "congo",
        "guinea bissau": "guinea-bissau",
        "st. kitts and nevis": "saint kitts and nevis",
        "antigua and barbuda": "antigua and barbuda",
        "u.s. virgin islands": "us virgin islands",
    }

    def normalize(s):
        import unicodedata
        s = unicodedata.normalize('NFD', s.lower())
        return ''.join(c for c in s if unicodedata.category(c) != 'Mn')

    def find_elo(nombre):
        if nombre in elo_data:
            return elo_data[nombre]
        # Busqueda exacta normalizada
        norm = normalize(nombre)
        alias = ALIAS_MAP.get(norm, norm)
        for k, v in elo_data.items():
            if normalize(k) == alias or normalize(k) == norm:
                return v
        # Busqueda parcial normalizada
        for k, v in elo_data.items():
            kn = normalize(k)
            if kn in norm or norm in kn:
                return v
        return {"rank": 80, "elo": 1600}

    home_elo = find_elo(hn)
    away_elo = find_elo(an)
    elo_h = home_elo["elo"]
    elo_a = away_elo["elo"]

    # Mismo modelo que wc2026_simulator.html
    import math

    # eloWinProb identico al simulador del Mundial
    def elo_win_prob(elo_a, elo_b, home_bonus=0):
        dr = elo_a - elo_b + home_bonus
        return 1 / (math.pow(10, -dr / 400) + 1)

    # poissonLambda identico al simulador del Mundial
    def poisson_lambda(elo_team, elo_opp, home_bonus=0):
        gap = (elo_team - elo_opp + home_bonus) / 100
        return min(6.0, max(0.15, 1.30 + 0.18 * gap))

    # Amistosos: sin ventaja local (home_bonus=0)
    p_home_raw = elo_win_prob(elo_h, elo_a, 0)
    draw_factor = 0.22
    p_home = round(p_home_raw * (1 - draw_factor) * 100)
    p_away = round((1 - p_home_raw) * (1 - draw_factor) * 100)
    p_draw = 100 - p_home - p_away

    xg_home = round(poisson_lambda(elo_h, elo_a, 0), 2)
    xg_away = round(poisson_lambda(elo_a, elo_h, 0), 2)
    xg_total = xg_home + xg_away

    # Poisson: P(X <= k) = sum e^-lambda * lambda^i / i!
    def poisson_prob(lam, k):
        return sum(math.exp(-lam) * (lam**i) / math.factorial(i) for i in range(k+1))

    # Over/Under 2.5 via Poisson en cada equipo
    def p_total_over(threshold, xg_h, xg_a):
        p_under = 0
        for h in range(int(threshold) + 1):
            for a in range(int(threshold) + 1 - h):
                p_under += (math.exp(-xg_h) * xg_h**h / math.factorial(h)) *                            (math.exp(-xg_a) * xg_a**a / math.factorial(a))
        return round((1 - p_under) * 100)

    # Mercados
    mercados = []

    def add_mercado(nombre, prob, umbral_key):
        umbral = UMBRALES.get(umbral_key, 75)
        cuota = round(100 / max(prob, 1), 2)
        aprobado = prob >= umbral
        mercados.append({
            "mercado": nombre, "prob": prob, "cuota": cuota,
            "aprobado": aprobado, "umbral": umbral,
            "estado": "APROBADO ✓" if aprobado else ("MARGINAL" if prob >= umbral - 7 else "RECHAZADO")
        })

    # 1X2
    if p_home >= UMBRALES["1X2"] or p_away >= UMBRALES["1X2"]:
        fav = hn if p_home > p_away else an
        prob_fav = max(p_home, p_away)
        add_mercado(f"Victoria {fav}", prob_fav, "1X2")
    # DC
    dc_h = p_home + p_draw
    dc_a = p_away + p_draw
    if dc_h >= dc_a:
        add_mercado(f"DC {hn} (1X)", dc_h, "DC")
    else:
        add_mercado(f"DC {an} (X2)", dc_a, "DC")
    # Over/Under 2.5 via Poisson
    p_over25 = p_total_over(2, xg_home, xg_away)
    p_under25 = 100 - p_over25
    add_mercado("Over 2.5 goles", p_over25, "OU")
    add_mercado("Under 2.5 goles", p_under25, "OU")
    # BTTS via Poisson
    p_h_scores = round((1 - math.exp(-xg_home)) * 100)
    p_a_scores = round((1 - math.exp(-xg_away)) * 100)
    p_btts = round(p_h_scores * p_a_scores / 100)
    add_mercado("Ambos anotan (BTTS)", p_btts, "BTTS")

    # Veredicto
    aprobados = [m for m in mercados if m.get("aprobado")]
    aprobados.sort(key=lambda x: x["prob"], reverse=True)
    mercado_principal = aprobados[0]["mercado"] + f" ({aprobados[0]['prob']}% · @{aprobados[0]['cuota']})" if aprobados else "Sin mercados aprobados"

    return jsonify({
        "home": hn,
        "away": an,
        "fecha": fecha,
        "elo": {"home": elo_h, "away": elo_a, "rank_home": home_elo["rank"], "rank_away": away_elo["rank"]},
        "probabilidades": {"home": p_home, "draw": p_draw, "away": p_away},
        "goles_esperados": {"home": xg_home, "away": xg_away, "total": round(xg_total, 2)},
        "mercados": mercados,
        "veredicto": {
            "favorito": hn if p_home > p_away else (an if p_away > p_home else "Equilibrado"),
            "confianza": "alta" if abs(p_home - p_away) > 20 else ("media" if abs(p_home - p_away) > 10 else "baja"),
            "mercado_principal": mercado_principal,
            "total_aprobados": len(aprobados),
        },
        "resumen": f"{hn} (ELO {elo_h}, #{home_elo['rank']}) vs {an} (ELO {elo_a}, #{away_elo['rank']}). "
                   f"Diferencia ELO: {elo_h - elo_a:+d} puntos. "
                   f"Probabilidades: {hn} {p_home}% · Empate {p_draw}% · {an} {p_away}%. "
                   f"xG esperado: {xg_home}-{xg_away}.",
    })


def _team_stats(as_stats, pos, forma):
    result={"remates_pj":"—","al_arco_pj":"—","corners_pj":"—","tarjetas_amarillas_pj":"—","tarjetas_rojas_pj":"—","goles_pj":"—","recibidos_pj":"—","posicion":"—","puntos":0}
    if pos:
        result["posicion"]=f"#{pos.get('position','—')}"
        result["puntos"]=pos.get("points",0)
    if forma and forma.get("matches",0)>0:
        result["goles_pj"]=forma["gf_avg"]
        result["recibidos_pj"]=forma["gc_avg"]
    if as_stats:
        played=as_stats.get("fixtures",{}).get("played",{}).get("total",0) or 0
        if played>0:
            gf=as_stats.get("goals",{}).get("for",{}).get("total",{}).get("total",0) or 0
            gc=as_stats.get("goals",{}).get("against",{}).get("total",{}).get("total",0) or 0
            result["goles_pj"]=round(gf/played,2)
            result["recibidos_pj"]=round(gc/played,2)
            avg_total=(gf+gc)/played
            result["remates_pj"]=round(avg_total*8+5,1)
            result["al_arco_pj"]=round(avg_total*3+1.5,1)
    if result["remates_pj"] == "—" and forma and forma.get("matches",0) > 0:
        gf_avg = forma.get("gf_avg", 0)
        gc_avg = forma.get("gc_avg", 0)
        avg_total = gf_avg + gc_avg
        if avg_total > 0:
            result["remates_pj"] = round(avg_total * 7 + 6, 1)
            result["al_arco_pj"] = round(avg_total * 2.5 + 2, 1)
    return result


def _resumen(hn,an,hf,af,hp,ap,hh,aa,h2h,arb,arb_perfil,resultado,md):
    partes=[]
    if hp and ap:
        hpos=hp.get("position",10); apos=ap.get("position",10)
        hpts=hp.get("points",0); apts=ap.get("points",0)
        diff_pts=abs(hpts-apts)
        h_obj=_objetivo(hpos); a_obj=_objetivo(apos)
        if diff_pts>=10:
            leader=hn if hpts>apts else an
            follower=an if hpts>apts else hn
            partes.append(f"Diferencia marcada en la tabla: {leader} aventaja por {diff_pts} puntos a {follower}.")
        elif diff_pts<=3:
            partes.append(f"Equipos igualados en puntos ({hn} {hpts} pts, {an} {apts} pts).")
        partes.append(f"{hn} llega ({h_obj}) y {an} ({a_obj}).")
    if hf["matches"]>0:
        rh=_racha(hf["form"])
        if rh[0]=="W" and rh[1]>=3: partes.append(f"{hn} atraviesa un gran momento con {rh[1]} victorias consecutivas, promediando {hf['gf_avg']} goles por partido.")
        elif rh[0]=="L" and rh[1]>=2: partes.append(f"{hn} viene de {rh[1]} derrotas al hilo.")
        elif rh[0]=="D" and rh[1]>=2: partes.append(f"{hn} acumula {rh[1]} empates consecutivos.")
        else: partes.append(f"{hn} tiene un rendimiento reciente de {hf['ppg']} PPG ({hf['w']}V {hf['d']}E {hf['l']}D).")
    if af["matches"]>0:
        ra=_racha(af["form"])
        if ra[0]=="W" and ra[1]>=3: partes.append(f"{an} llega encendido con {ra[1]} triunfos seguidos.")
        elif ra[0]=="L" and ra[1]>=2: partes.append(f"{an} llega debilitado con {ra[1]} derrotas consecutivas.")
        else: partes.append(f"{an} registra {af['ppg']} PPG ({af['w']}V {af['d']}E {af['l']}D).")
    if hh:
        pg=hh.get("playedGames",0); hw=hh.get("won",0)
        if pg>0:
            pct=round(hw/pg*100)
            if pct>=60: partes.append(f"{hn} domina de local: {hw} victorias en {pg} partidos.")
            elif pct<=30: partes.append(f"Llamativa debilidad de {hn} como local: solo {hw} victorias en {pg} partidos.")
    h2t=h2h.get("numberOfMatches",0)
    if h2t>=2:
        hw2=h2h.get("homeTeam",{}).get("wins",0); aw2=h2h.get("awayTeam",{}).get("wins",0)
        d2=h2h.get("homeTeam",{}).get("draws",0) or h2h.get("awayTeam",{}).get("draws",0)
        tg=h2h.get("totalGoals",0); avg_tg=round(tg/h2t,1) if h2t>0 else 0
        if hw2>aw2: partes.append(f"El historial favorece a {hn} con {hw2} victorias en {h2t} enfrentamientos ({avg_tg} goles/choque).")
        elif aw2>hw2: partes.append(f"Historial visitante favorable a {an} ({aw2} triunfos en {h2t} duelos).")
        else: partes.append(f"H2H equilibrado: {hw2}-{hw2} con {d2} empates.")
    ge=resultado.get("goles_esperados",2.5)
    if ge>=3.0: partes.append(f"Partido ofensivo ({ge} goles esperados).")
    elif ge<=1.8: partes.append(f"Perfil defensivo ({ge} goles esperados).")
    if arb: partes.append(f"Arbitra {arb}.")
    if arb_perfil and "Sin perfil" not in arb_perfil.get("descripcion",""): partes.append(arb_perfil["descripcion"])
    return " ".join(partes)


def _objetivo(pos):
    if pos<=4: return f"peleando por el titulo, {pos}°"
    elif pos<=6: return f"buscando clasificacion europea, {pos}°"
    elif pos<=10: return f"en zona media, {pos}°"
    elif pos<=16: return f"intentando alejarse del descenso, {pos}°"
    else: return f"peleando no descender, {pos}°"


def _racha(form):
    if not form: return ("",0)
    f=form[0]; c=0
    for x in form:
        if x==f: c+=1
        else: break
    return (f,c)


def _arbitro_perfil(name):
    if not name: return None
    return {"nombre":name,"descripcion":_ref_description(name)}

def _ref_description(name):
    refs_db={
        "michael oliver":{"estilo":"Estricto","tarjetas":"Alto","desc":"Árbitro FIFA de alto perfil. Tendencia a mostrar tarjetas."},
        "anthony taylor":{"estilo":"Equilibrado","tarjetas":"Medio","desc":"Experimentado árbitro internacional."},
        "paul tierney":{"estilo":"Permisivo","tarjetas":"Bajo","desc":"Tiende a dejar jugar."},
        "simon hooper":{"estilo":"Moderado","tarjetas":"Medio","desc":"Árbitro de perfil medio."},
        "robert jones":{"estilo":"Estricto","tarjetas":"Alto","desc":"Perfil riguroso."},
        "stuart attwell":{"estilo":"Moderado","tarjetas":"Medio","desc":"Árbitro equilibrado."},
        "chris kavanagh":{"estilo":"Estricto","tarjetas":"Alto","desc":"Árbitro FIFA con tendencia a tarjetas."},
        "john brooks":{"estilo":"Permisivo","tarjetas":"Bajo","desc":"Permite contacto físico."},
        "darren england":{"estilo":"Moderado","tarjetas":"Medio","desc":"Perfil equilibrado."},
        "tim robinson":{"estilo":"Moderado","tarjetas":"Medio","desc":"Perfil neutral."},
        "david coote":{"estilo":"Estricto","tarjetas":"Alto","desc":"Alto promedio de tarjetas."},
        "peter bankes":{"estilo":"Moderado","tarjetas":"Medio","desc":"Árbitro consistente."},
        "andy madley":{"estilo":"Permisivo","tarjetas":"Bajo","desc":"Deja fluir el juego."},
        "jarred gillett":{"estilo":"Moderado","tarjetas":"Medio","desc":"Árbitro australiano."},
        "tony harrington":{"estilo":"Moderado","tarjetas":"Medio","desc":"Perfil moderado."},
        "samuel barrott":{"estilo":"Moderado","tarjetas":"Medio","desc":"Árbitro joven en ascenso."},
    }
    key=name.lower().strip()
    if key in refs_db:
        r=refs_db[key]
        return f"Estilo: {r['estilo']} · Tarjetas: {r['tarjetas']}. {r['desc']}"
    return f"Sin perfil detallado disponible para {name}."




def _agregar_bajas_resumen(resumen, bajas, hn, an):
    """Agrega info de bajas al resumen narrativo."""
    partes = []
    home_bajas = bajas.get("home", [])
    away_bajas = bajas.get("away", [])
    altos_home = [b for b in home_bajas if b.get("impacto") == "alto"]
    altos_away = [b for b in away_bajas if b.get("impacto") == "alto"]
    if altos_home:
        nombres = ", ".join(b["nombre"] for b in altos_home)
        partes.append(f"⚠ Bajas importantes en {hn}: {nombres}.")
    if altos_away:
        nombres = ", ".join(b["nombre"] for b in altos_away)
        partes.append(f"⚠ Bajas importantes en {an}: {nombres}.")
    if partes:
        return resumen + " " + " ".join(partes)
    return resumen

def _buscar_bajas(hn, an, fecha=""):
    """Busca bajas confirmadas usando Claude con web search."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"home": [], "away": [], "fuente": "sin_api_key"}
    
    ck = f"bajas:{hn}:{an}:{fecha[:10] if fecha else ''}"
    now_t = time.time()
    if ck in _cache and now_t - _cache[ck][1] < 21600:
        return _cache[ck][0]
    
    prompt = f"""Buscá las bajas confirmadas para el partido {hn} vs {an}{' del ' + fecha[:10] if fecha else ''}.
Respondé SOLO con JSON, sin texto adicional, sin markdown:
{{
  "home": [
    {{"nombre": "Nombre Jugador", "posicion": "Posición", "motivo": "lesion/suspension/otro", "impacto": "alto/medio/bajo"}}
  ],
  "away": [],
  "fuente": "nombre del sitio consultado"
}}
Solo incluí jugadores con baja CONFIRMADA. Si no hay bajas confirmadas, devolvé listas vacías. Máximo 4 por equipo."""

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 600,
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=30
        )
        if r.ok:
            data = r.json()
            texto = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    texto += block.get("text", "")
            # Limpiar y parsear JSON
            texto = texto.strip()
            if "```" in texto:
                texto = texto.split("```")[1].replace("json", "").strip()
            result = json.loads(texto)
            _cache[ck] = (result, now_t)
            return result
    except Exception as e:
        print(f"Bajas fetch error: {e}")
    
    return {"home": [], "away": [], "fuente": "error"}

def _search_as(name, lid, season):
    if not name or not lid: return None
    clean = name.replace(" FC", "").replace(" CF", "").replace(" AFC", "").replace(" AC", "")\
                .replace("AC ", "").replace("SS ", "").replace("US ", "").replace("AS ", "")\
                .replace(" Calcio", "").strip()

    def _find_in(candidates, name, clean):
        nl = name.lower(); cl = clean.lower()
        for t in candidates:
            tn = t["team"]["name"].lower()
            if tn == nl or tn == cl: return t["team"]["id"]
        for t in candidates:
            tn = t["team"]["name"].lower()
            if cl in tn or tn in cl: return t["team"]["id"]
        for t in candidates:
            tn = t["team"]["name"].lower()
            if nl in tn or tn in nl: return t["team"]["id"]
        first_word = clean.split(" ")[0].lower()
        if len(first_word) >= 3:
            for t in candidates:
                if first_word in t["team"]["name"].lower(): return t["team"]["id"]
        return None

    for s in [season, season-1]:
        d = as_get("/teams", {"league": lid, "season": s})
        if "error" not in d and d.get("response"):
            result = _find_in(d["response"], name, clean)
            if result: return result

    search_term = clean.split(" ")[0] if len(clean.split(" ")[0]) >= 3 else clean
    d = as_get("/teams", {"search": search_term})
    if "error" not in d and d.get("response"):
        for t in d["response"]:
            if name.lower() in t["team"]["name"].lower() or t["team"]["name"].lower() in name.lower():
                return t["team"]["id"]
    return None

def _get_as(name,lid,season):
    if not lid: return None
    tid=_search_as(name,lid,season)
    if not tid: return None
    d=as_get("/teams/statistics",{"league":lid,"season":season,"team":tid})
    return d.get("response") if "error" not in d else None

def _adv(hs,aws,hn,an):
    if not hs and not aws: return None
    r={}
    if hs and aws:
        hgm=hs.get("goals",{}).get("for",{}).get("minute",{})
        agm=aws.get("goals",{}).get("for",{}).get("minute",{})
        r["goles_por_tiempo"]={"home":_pm(hgm),"away":_pm(agm)}
        hc,ac=hs.get("cards",{}),aws.get("cards",{})
        hy,ay=_cc(hc.get("yellow",{})),_cc(ac.get("yellow",{}))
        hr,ar=_cc(hc.get("red",{})),_cc(ac.get("red",{}))
        hpp,app_=_pl(hs),_pl(aws)
        r["tarjetas"]={"home_yellow":hy,"away_yellow":ay,"home_red":hr,"away_red":ar,
            "home_yellow_avg":round(hy/max(hpp,1),2),"away_yellow_avg":round(ay/max(app_,1),2)}
        hgf=hs.get("goals",{}).get("for",{}).get("total",{}).get("total",0) or 0
        hgc=hs.get("goals",{}).get("against",{}).get("total",{}).get("total",0) or 0
        agf=aws.get("goals",{}).get("for",{}).get("total",{}).get("total",0) or 0
        agc=aws.get("goals",{}).get("against",{}).get("total",{}).get("total",0) or 0
        hcs=hs.get("clean_sheet",{}).get("total",0) or 0
        acs=aws.get("clean_sheet",{}).get("total",0) or 0
        comps=[]
        tgf=hgf+agf
        if tgf>0: comps.append({"label":"Poder ofensivo","home":round(hgf/tgf*100),"away":round(agf/tgf*100)})
        tgc=hgc+agc
        if tgc>0: comps.append({"label":"Solidez defensiva","home":round((1-hgc/tgc)*100),"away":round((1-agc/tgc)*100)})
        tc=hy+ay
        if tc>0: comps.append({"label":"Mayor tarjetas","home":round(hy/tc*100),"away":round(ay/tc*100)})
        tcs=hcs+acs
        if tcs>0: comps.append({"label":"Valla invicta","home":round(hcs/tcs*100),"away":round(acs/tcs*100)})
        r["comparativas"]=comps
        h1=_gh(hgm,"1st");h2=_gh(hgm,"2nd");a1=_gh(agm,"1st");a2=_gh(agm,"2nd")
        ht,at=h1+h2,a1+a2
        r["prob_gol_tiempo"]={"home_1st":round(h1/max(ht,1)*100),"home_2nd":round(h2/max(ht,1)*100),
            "away_1st":round(a1/max(at,1)*100),"away_2nd":round(a2/max(at,1)*100)}
    return r

def _pm(md):
    return[{"intervalo":iv,"goles":(md.get(iv,{}).get("total")or 0),"pct":int((md.get(iv,{}).get("percentage")or"0%").replace("%",""))} for iv in["0-15","16-30","31-45","46-60","61-75","76-90"]]
def _cc(cd): return sum(v.get("total",0)or 0 for v in cd.values() if isinstance(v,dict))
def _pl(s): return(s.get("fixtures",{}).get("played",{}).get("total")or 0)
def _gh(md,half):
    keys=["0-15","16-30","31-45"] if half=="1st" else ["46-60","61-75","76-90"]
    return sum(md.get(k,{}).get("total",0)or 0 for k in keys)

def _find(t,tid):
    for x in t:
        if x["team"]["id"]==tid: return x
    return None

def _forma(matches,tid):
    if not matches: return{"form":"","w":0,"d":0,"l":0,"gf":0,"gc":0,"matches":0,"ppg":0,"gf_avg":0,"gc_avg":0,"clean_sheets":0,"failed_to_score":0}
    matches=sorted(matches,key=lambda x:x.get("utcDate",""),reverse=True)[:10]
    w=d=l=gf=gc=cs=fts=0;fs=""
    for m in matches:
        ft=m.get("score",{}).get("fullTime",{});hg,ag=ft.get("home"),ft.get("away")
        if hg is None:continue
        ih=m["homeTeam"]["id"]==tid;my,th=(hg,ag) if ih else(ag,hg)
        gf+=my;gc+=th
        if th==0:cs+=1
        if my==0:fts+=1
        if my>th:w+=1;fs+="W"
        elif my==th:d+=1;fs+="D"
        else:l+=1;fs+="L"
    t=w+d+l
    return{"form":fs[:5],"w":w,"d":d,"l":l,"gf":gf,"gc":gc,"matches":t,
        "ppg":round((w*3+d)/t,2)if t>0 else 0,"gf_avg":round(gf/t,2)if t>0 else 0,
        "gc_avg":round(gc/t,2)if t>0 else 0,"clean_sheets":cs,"failed_to_score":fts}

def _u3(matches,tid):
    if not matches:return[]
    matches=sorted(matches,key=lambda x:x.get("utcDate",""),reverse=True)[:3]
    r=[]
    for m in matches:
        ft=m.get("score",{}).get("fullTime",{});hg,ag=ft.get("home"),ft.get("away")
        if hg is None:continue
        ih=m["homeTeam"]["id"]==tid;my,th=(hg,ag)if ih else(ag,hg)
        res="W"if my>th else("D"if my==th else"L")
        r.append({"fecha":m.get("utcDate","")[:10],"rival":m["awayTeam"]["name"]if ih else m["homeTeam"]["name"],
            "competicion":m.get("competition",{}).get("code",""),"marcador":f"{hg}-{ag}","local":ih,"resultado":res})
    return r

def _jugadores(scorers,tid):
    j=[]
    for s in scorers:
        if s["team"]["id"]==tid:
            j.append({"nombre":s["player"]["name"],"goles":s.get("goals",0),"asistencias":s.get("assists",0),
                "partidos":s.get("playedMatches",0),"promedio":round(s.get("goals",0)/max(s.get("playedMatches",1),1),2)})
    return j[:3]

def _enrich(players,team,form):
    for p in players:
        desc=[]
        if p["goles"]>=10: desc.append(f"Goleador principal de {team} con {p['goles']} goles en {p['partidos']} partidos.")
        elif p["goles"]>=5: desc.append(f"Amenaza ofensiva con {p['goles']} goles.")
        else: desc.append(f"Aporta {p['goles']} goles y {p['asistencias']or 0} asistencias.")
        if p["promedio"]>=0.5: desc.append(f"Promedio de {p['promedio']} goles/partido."); p["mercado_sugerido"]="Anotador en cualquier momento"
        elif p["asistencias"] and p["asistencias"]>=5: desc.append(f"Generador clave con {p['asistencias']} asistencias."); p["mercado_sugerido"]="Asistencia"
        else: p["mercado_sugerido"]="Anotador Over 0.5"
        p["descripcion"]=" ".join(desc)
    return players


def _cuota(prob_pct):
    if prob_pct<=0: return "—"
    return round(100/prob_pct*0.95, 2)

UMBRAL = 82

_CORR_GROUPS = {
    "1X2":"RES","DC":"RES","WTN":"RES",
    "O/U":"GOLES","BTTS":"GOLES","GE":"GOLES","ESP":"GOLES","RANGE":"GOLES",
    "CS":"DEF","HT":"TIEMPO",
    "CORNERS":"STATS","CARDS":"STATS","SHOTS":"STATS","SOT":"STATS","POSS":"STATS",
}
_INCOMPATIBLE = {
    frozenset(["BTTS","CS"]),frozenset(["O/U","ESP"]),
}
def _analisis(md,hp,ap,hh,aa,hf,af,h2h,hn,an,tt):
    te=len(tt)if tt else 20
    forma_h_val=hf["ppg"] if hf["matches"]>0 else 0
    forma_a_val=af["ppg"] if af["matches"]>0 else 0
    forma_h_score=round(forma_h_val/3*100)if hf["matches"]>0 else 50
    forma_a_score=round(forma_a_val/3*100)if af["matches"]>0 else 50
    h2h_home_wins=h2h.get("homeTeam",{}).get("wins",0)
    h2h_away_wins=h2h.get("awayTeam",{}).get("wins",0)
    h2h_draws=h2h.get("homeTeam",{}).get("draws",h2h.get("awayTeam",{}).get("draws",0))
    h2t=h2h.get("numberOfMatches",0)
    h2h_score=50
    if h2t>0: h2h_score=round((h2h_home_wins*100+h2h_draws*50)/h2t)
    localia_h_val=0;localia_a_val=0
    localia_h_score=localia_a_score=50
    if hh:
        pg=hh.get("playedGames",0)
        if pg>0:
            localia_h_val=round(hh.get("won",0)/pg*100)
            localia_h_score=round((hh.get("won",0)*3+hh.get("draw",0))/(pg*3)*100)
    if aa:
        pg=aa.get("playedGames",0)
        if pg>0:
            localia_a_val=round(aa.get("won",0)/pg*100)
            localia_a_score=round((aa.get("won",0)*3+aa.get("draw",0))/(pg*3)*100)
    pos_h=hp.get("position") if hp else None
    pos_a=ap.get("position") if ap else None
    pos_h_score=round((1-(pos_h-1)/max(te-1,1))*100) if pos_h else 50
    pos_a_score=round((1-(pos_a-1)/max(te-1,1))*100) if pos_a else 50

    ph=round(forma_h_score*.25+h2h_score*.10+localia_h_score*.35+pos_h_score*.30)
    pa=round(forma_a_score*.25+(100-h2h_score)*.10+localia_a_score*.35+pos_a_score*.30)
    ph=min(95,ph+8)
    pd=max(0,100-ph-pa)
    tp=ph+pa+pd
    if tp>0: ph=round(ph/tp*100);pa=round(pa/tp*100);pd=100-ph-pa

    egh=hf["gf_avg"]if hf["matches"]>0 else 1.3
    ech=hf["gc_avg"]if hf["matches"]>0 else 1.0
    ega=af["gf_avg"]if af["matches"]>0 else 1.0
    eca=af["gc_avg"]if af["matches"]>0 else 1.3
    ge=round((egh+ega+ech+eca)/2,2)
    hfts=hf["failed_to_score"]/max(hf["matches"],1);afts=af["failed_to_score"]/max(af["matches"],1)
    hcs_r=hf["clean_sheets"]/max(hf["matches"],1);acs_r=af["clean_sheets"]/max(af["matches"],1)
    hsc=1-hfts;asc=1-afts

    mercados=[]
    if ph>=40:
        s=_s1x2(hn,an,ph,hf,hp,hh,localia_h_score,"home",ge)
        mercados.append({"mercado":f"Resultado Final — {hn}","prob":ph,"riesgo":100-ph,"cuota":_cuota(ph),"tipo":"1X2","aprobado":ph>=75,"sintesis":s})
    if pa>=40:
        s=_s1x2(an,hn,pa,af,ap,aa,localia_a_score,"away",ge)
        mercados.append({"mercado":f"Resultado Final — {an}","prob":pa,"riesgo":100-pa,"cuota":_cuota(pa),"tipo":"1X2","aprobado":pa>=70,"sintesis":s})

    dc1x=ph+pd;dcx2=pa+pd
    if dc1x>=55:
        s=f"{hn} o Empate cubre el escenario mas probable. Solo pierde si gana {an} ({pa}%)."
        mercados.append({"mercado":f"Doble Oportunidad 1X — {hn}","prob":dc1x,"riesgo":100-dc1x,"cuota":_cuota(dc1x),"tipo":"DC","aprobado":dc1x>=75,"sintesis":s})
    if dcx2>=55:
        mercados.append({"mercado":f"Doble Oportunidad X2 — {an}","prob":dcx2,"riesgo":100-dcx2,"cuota":_cuota(dcx2),"tipo":"DC","aprobado":dcx2>=75,"sintesis":f"Cubre victoria de {an} o empate."})

    if ge>=2.5:
        p=min(85,round(50+(ge-2.5)*20))
        mercados.append({"mercado":"Goles Totales Over 2.5","prob":p,"riesgo":100-p,"cuota":_cuota(p),"tipo":"O/U","aprobado":p>=75,"sintesis":f"Promedio combinado supera {ge} goles/partido."})
    if ge<=2.5:
        p=min(85,round(50+(2.5-ge)*20))
        mercados.append({"mercado":"Goles Totales Under 2.5","prob":p,"riesgo":100-p,"cuota":_cuota(p),"tipo":"O/U","aprobado":p>=80,"sintesis":f"Goles esperados: {ge}."})
    if ge>=1.8:
        p=min(92,round(60+(ge-1.5)*15))
        mercados.append({"mercado":"Goles Totales Over 1.5","prob":p,"riesgo":100-p,"cuota":_cuota(p),"tipo":"O/U","aprobado":p>=80,"sintesis":f"Con {ge} goles esperados."})
    if ge>=3.0:
        p=min(80,round(40+(ge-3.0)*25))
        mercados.append({"mercado":"Goles Totales Over 3.5","prob":p,"riesgo":100-p,"cuota":_cuota(p),"tipo":"O/U","aprobado":p>=85,"sintesis":f"Promedio combinado alto: {ge} goles."})
    if ge<=3.0:
        p=min(80,round(40+(3.0-ge)*25))
        mercados.append({"mercado":"Goles Totales Under 3.5","prob":p,"riesgo":100-p,"cuota":_cuota(p),"tipo":"O/U","aprobado":p>=70,"sintesis":f"Goles esperados: {ge}."})

    btts=min(82,max(20,round(hsc*50+asc*50)))
    if btts>=45:
        mercados.append({"mercado":"BTTS — Ambos Anotan","prob":btts,"riesgo":100-btts,"cuota":_cuota(btts),"tipo":"BTTS","aprobado":btts>=82,"sintesis":f"{hn} marca en {round(hsc*100)}%, {an} en {round(asc*100)}%."})

    ho=min(95,max(30,round(hsc*100)))
    if ho>=70:
        mercados.append({"mercado":f"Goles Equipo — {hn} Over 0.5","prob":ho,"riesgo":100-ho,"cuota":_cuota(ho),"tipo":"GE","aprobado":ho>=85,"sintesis":f"{hn} marco en {round(hsc*100)}% de sus ultimos partidos."})
    ao=min(95,max(30,round(asc*100)))
    if ao>=70:
        mercados.append({"mercado":f"Goles Equipo — {an} Over 0.5","prob":ao,"riesgo":100-ao,"cuota":_cuota(ao),"tipo":"GE","aprobado":ao>=90,"sintesis":f"{an} marco en {round(asc*100)}% de sus ultimos partidos."})

    h15=min(85,max(20,round(egh/(egh+0.8)*100))) if egh>=1.3 else 0
    if h15>=50:
        mercados.append({"mercado":f"Goles Equipo — {hn} Over 1.5","prob":h15,"riesgo":100-h15,"cuota":_cuota(h15),"tipo":"GE","aprobado":h15>=78,"sintesis":f"{hn} promedia {egh} goles/partido."})
    a15=min(85,max(20,round(ega/(ega+0.8)*100))) if ega>=1.3 else 0
    if a15>=50:
        mercados.append({"mercado":f"Goles Equipo — {an} Over 1.5","prob":a15,"riesgo":100-a15,"cuota":_cuota(a15),"tipo":"GE","aprobado":a15>=82,"sintesis":f"{an} promedia {ega} goles/partido."})

    hcs_p=min(85,round(hcs_r*100*(1-asc+0.3)))
    if hcs_p>=30:
        mercados.append({"mercado":f"Clean Sheet — {hn}","prob":hcs_p,"riesgo":100-hcs_p,"cuota":_cuota(hcs_p),"tipo":"CS","aprobado":hcs_p>=80,"sintesis":f"{hn} dejo valla invicta en {round(hcs_r*100)}% de partidos."})
    acs_p=min(85,round(acs_r*100*(1-hsc+0.3)))
    if acs_p>=30:
        mercados.append({"mercado":f"Clean Sheet — {an}","prob":acs_p,"riesgo":100-acs_p,"cuota":_cuota(acs_p),"tipo":"CS","aprobado":acs_p>=80,"sintesis":f"{an} dejo valla invicta en {round(acs_r*100)}% de partidos."})

    wtn_h=min(80,round(ph*hcs_r)) if ph>=55 and hcs_r>=0.4 else 0
    if wtn_h>=25:
        mercados.append({"mercado":f"Victoria a Cero — {hn}","prob":wtn_h,"riesgo":100-wtn_h,"cuota":_cuota(wtn_h),"tipo":"WTN","aprobado":wtn_h>=55,"sintesis":f"{hn} gana ({ph}%) y mantiene valla invicta ({round(hcs_r*100)}%)."})
    wtn_a=min(80,round(pa*acs_r)) if pa>=55 and acs_r>=0.4 else 0
    if wtn_a>=25:
        mercados.append({"mercado":f"Victoria a Cero — {an}","prob":wtn_a,"riesgo":100-wtn_a,"cuota":_cuota(wtn_a),"tipo":"WTN","aprobado":wtn_a>=55,"sintesis":f"{an} gana ({pa}%) y mantiene valla invicta ({round(acs_r*100)}%)."})

    ght=ge*0.45
    p1o=min(88,round(50+(ght-0.5)*40)) if ght>=0.5 else max(20,round(ght/0.5*40))
    p1u=100-p1o
    if p1o>=50:
        mercados.append({"mercado":"1er Tiempo — Over 0.5 Goles","prob":p1o,"riesgo":100-p1o,"cuota":_cuota(p1o),"tipo":"HT","aprobado":p1o>=78,"sintesis":f"Goles esperados 1T: {round(ght,1)}."})
    if p1u>=40:
        mercados.append({"mercado":"1er Tiempo — Under 0.5 Goles","prob":p1u,"riesgo":100-p1u,"cuota":_cuota(p1u),"tipo":"HT","aprobado":p1u>=80,"sintesis":f"Goles esperados 1T: {round(ght,1)}."})

    p00=round(hfts*acs_r*100);pn00=min(95,100-p00)
    if pn00>=70:
        mercados.append({"mercado":"El Partido No Termina 0-0","prob":pn00,"riesgo":100-pn00,"cuota":_cuota(pn00),"tipo":"ESP","aprobado":pn00>=82,"sintesis":f"Promedio combinado: {ge} goles."})

    mercados.sort(key=lambda x:x["prob"],reverse=True)

    aprobados=[m for m in mercados if m["aprobado"]]
    if ph>=pa+15: fav,conf=hn,("alta"if ph>=55 else"moderada")
    elif pa>=ph+15: fav,conf=an,("alta"if pa>=55 else"moderada")
    else: fav,conf=None,"baja"
    if fav: texto=f"Victoria de {fav} en tiempo reglamentario. "
    else: texto=f"Partido equilibrado entre {hn} ({ph}%) y {an} ({pa}%). Sin favorito claro. "
    if hf["matches"]>0: texto+=f"{hn} llega con {hf['ppg']} PPG vs {af['ppg']} PPG. "
    if hp and ap: texto+=f"Posiciones: {hn} #{hp.get('position','?')} vs {an} #{ap.get('position','?')}. "
    texto+=f"Goles esperados: {ge}."
    mp=f"{aprobados[0]['mercado']} ({aprobados[0]['prob']}% · cuota @{aprobados[0]['cuota']})" if aprobados else "Ninguno supera el umbral"
    mp_prob=aprobados[0]['prob'] if aprobados else 0
    comb=""
    comb_prob=0
    if len(aprobados)>=2:
        c2=[m for m in aprobados if m["tipo"]!=aprobados[0]["tipo"]]
        if c2:
            comb=f"{c2[0]['mercado']} ({c2[0]['prob']}% · cuota @{c2[0]['cuota']}) como alternativa de mayor retorno."
            comb_prob=c2[0]['prob']
    ta=" · ".join(f"{m['mercado']} ({m['prob']}%)"for m in aprobados[:4])if aprobados else"Ninguno"

    combinadas = _generar_combinadas(aprobados)
    match_score = _calcular_score(aprobados, ph, pa, conf)

    return{
        "match":{"home":hn,"away":an,"fecha":md.get("utcDate",""),"jornada":md.get("matchday"),"competicion":md.get("competition",{}).get("name","")},
        "probabilidades":{"home":ph,"draw":pd,"away":pa,"cuota_home":_cuota(ph),"cuota_draw":_cuota(pd),"cuota_away":_cuota(pa)},
        "goles_esperados":ge,
        "forma":{"home":hf,"away":af},
        "h2h":{"total":h2t,"home_wins":h2h_home_wins,"away_wins":h2h_away_wins,"draws":h2h_draws,"total_goals":h2h.get("totalGoals",0)},
        "posiciones":{"home":pos_h,"away":pos_a,
            "home_pts":hp.get("points")if hp else None,"away_pts":ap.get("points")if ap else None,
            "home_gf":hp.get("goalsFor")if hp else None,"home_gc":hp.get("goalsAgainst")if hp else None,
            "away_gf":ap.get("goalsFor")if ap else None,"away_gc":ap.get("goalsAgainst")if ap else None},
        "mercados":mercados,
        "combinadas":combinadas,
        "match_score":match_score,
        "veredicto":{"texto":texto,"favorito":fav,"mercados_aprobados":ta,"total_aprobados":len(aprobados),"mercado_principal":mp,"mp_prob":mp_prob,"combinable":comb,"comb_prob":comb_prob},
    }

def _generar_combinadas(aprobados):
    if len(aprobados) < 2:
        return []
    combinadas = []
    for i in range(len(aprobados)):
        for j in range(i+1, len(aprobados)):
            a, b = aprobados[i], aprobados[j]
            ga = _CORR_GROUPS.get(a["tipo"], a["tipo"])
            gb = _CORR_GROUPS.get(b["tipo"], b["tipo"])
            if ga == gb:
                continue
            if frozenset([a["tipo"], b["tipo"]]) in _INCOMPATIBLE:
                continue
            ca = a["cuota"] if isinstance(a["cuota"], (int, float)) else 0
            cb = b["cuota"] if isinstance(b["cuota"], (int, float)) else 0
            prob_comb = round(a["prob"] * b["prob"] / 100, 1)
            cuota_comb = round(ca * cb, 2) if ca and cb else 0
            combinadas.append({"tipo":"doble","legs":[
                {"mercado":a["mercado"],"prob":a["prob"],"cuota":ca,"tipo":a["tipo"]},
                {"mercado":b["mercado"],"prob":b["prob"],"cuota":cb,"tipo":b["tipo"]},
            ],"prob_combinada":prob_comb,"cuota_combinada":cuota_comb,
              "score":prob_comb*(cuota_comb if cuota_comb>0 else 1)})
    if len(aprobados) >= 3:
        for i in range(len(aprobados)):
            for j in range(i+1, len(aprobados)):
                for k in range(j+1, len(aprobados)):
                    a, b, c = aprobados[i], aprobados[j], aprobados[k]
                    ga = _CORR_GROUPS.get(a["tipo"], a["tipo"])
                    gb = _CORR_GROUPS.get(b["tipo"], b["tipo"])
                    gc_ = _CORR_GROUPS.get(c["tipo"], c["tipo"])
                    if len(set([ga, gb, gc_])) < 2:
                        continue
                    tipos = set([a["tipo"], b["tipo"], c["tipo"]])
                    skip = any(inc.issubset(tipos) for inc in _INCOMPATIBLE)
                    if skip:
                        continue
                    ca = a["cuota"] if isinstance(a["cuota"], (int, float)) else 0
                    cb = b["cuota"] if isinstance(b["cuota"], (int, float)) else 0
                    cc = c["cuota"] if isinstance(c["cuota"], (int, float)) else 0
                    prob_comb = round(a["prob"] * b["prob"] * c["prob"] / 10000, 1)
                    cuota_comb = round(ca * cb * cc, 2) if ca and cb and cc else 0
                    combinadas.append({"tipo":"triple","legs":[
                        {"mercado":a["mercado"],"prob":a["prob"],"cuota":ca,"tipo":a["tipo"]},
                        {"mercado":b["mercado"],"prob":b["prob"],"cuota":cb,"tipo":b["tipo"]},
                        {"mercado":c["mercado"],"prob":c["prob"],"cuota":cc,"tipo":c["tipo"]},
                    ],"prob_combinada":prob_comb,"cuota_combinada":cuota_comb,
                      "score":prob_comb*(cuota_comb if cuota_comb>0 else 1)})
    combinadas.sort(key=lambda x: x["score"], reverse=True)
    return combinadas[:3]

def _calcular_score(aprobados, ph, pa, conf):
    if not aprobados:
        return 0
    n_aprob = len(aprobados)
    max_prob = aprobados[0]["prob"] if aprobados else 0
    diff = abs(ph - pa)
    score = min(100, round(n_aprob * 8 + max_prob * 0.3 + diff * 0.4 + (10 if conf == "alta" else 0)))
    return score

def _s1x2(team,rival,prob,form,pos,ha,localia,side,ge):
    s=""
    if form["matches"]>0:
        r=_racha(form["form"])
        if r[0]=="W"and r[1]>=2: s+=f"{team} en racha de {r[1]} victorias. "
        elif r[0]=="L"and r[1]>=2: s+=f"Atencion: {team} viene de {r[1]} derrotas. "
        s+=f"PPG: {form['ppg']}. "
    if pos: s+=f"#{pos.get('position','?')} con {pos.get('points',0)} pts. "
    s+=f"Probabilidad {'supera' if prob>=70 else 'no alcanza'} umbral (>=70%). "
    return s

@app.route("/estadisticas/json")
def estadisticas_json():
    _auto_verify_pending()
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT COUNT(*), SUM(mp_acertado),
                 COUNT(CASE WHEN verificado=1 AND mp_acertado IS NOT NULL THEN 1 END),
                 COUNT(CASE WHEN verificado=0 THEN 1 END)
                 FROM predicciones""")
    row = c.fetchone()
    total = row[0] or 0
    ganadas = int(row[1] or 0)
    verificadas = row[2] or 0
    pendientes = row[3] or 0
    perdidas = verificadas - ganadas
    acierto = round(ganadas / verificadas * 100, 1) if verificadas else 0
    c.execute("""SELECT fecha, home, away, mercado_principal, mp_prob,
                 verificado, mp_acertado FROM predicciones
                 ORDER BY fecha DESC LIMIT 20""")
    historial = [{"fecha":r[0][:10] if r[0] else "—","home":r[1],"away":r[2],
                  "mercado_principal":r[3],"mp_prob":r[4],
                  "verificado":r[5],"mp_acertado":r[6]} for r in c.fetchall()]
    conn.close()
    return jsonify({"total":total,"ganadas":ganadas,"perdidas":perdidas,
                    "pendientes":pendientes,"acierto":acierto,"historial":historial})

@app.route("/backtest")
def backtest():
    return render_template("backtest.html")


@app.route("/backtest/json")
def backtest_json():
    _auto_verify_pending()
    conn = get_db()
    c = conn.cursor()

    c.execute("""SELECT mercado_principal, mp_prob, mp_acertado
                 FROM predicciones
                 WHERE verificado=1 AND mp_acertado IS NOT NULL AND mercado_principal != ''
                 AND mercado_principal NOT LIKE '%Ninguno%'""")
    rows = c.fetchall()
    conn.close()

    if not rows:
        return jsonify({"resumen": {"total_predicciones": 0, "accuracy_global": 0,
                                     "mercados_evaluados": 0, "total_acertadas": 0},
                        "por_mercado": [], "calibracion": [], "umbrales_optimos": []})

    total = len(rows)
    total_ok = sum(1 for r in rows if r[2] == 1)
    accuracy_global = round(total_ok / total * 100, 1) if total else 0

    from collections import defaultdict
    mercado_data = defaultdict(lambda: {"n": 0, "ok": 0, "probs": []})
    for mercado, prob, ok in rows:
        mercado_data[mercado]["n"] += 1
        mercado_data[mercado]["ok"] += (ok or 0)
        if prob: mercado_data[mercado]["probs"].append(prob)

    por_mercado = []
    for mercado, d in sorted(mercado_data.items(), key=lambda x: -x[1]["n"]):
        if d["n"] < 3:
            continue
        acc = round(d["ok"] / d["n"] * 100, 1)
        prob_media = round(sum(d["probs"]) / len(d["probs"]), 1) if d["probs"] else 0
        umbral = UMBRALES.get(mercado, 70)
        estado = "bueno" if acc >= 80 else "marginal" if acc >= 70 else "bajo"
        por_mercado.append({"mercado": mercado, "n": d["n"], "ok": d["ok"],
                             "accuracy": acc, "prob_media": prob_media,
                             "umbral_actual": umbral, "estado": estado})

    rangos = [(50, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
    calibracion = []
    for lo, hi in rangos:
        bucket = [(p, ok) for _, p, ok in rows if p and lo <= p < hi]
        if len(bucket) < 3:
            continue
        acc_real = round(sum(1 for _, ok in bucket if ok == 1) / len(bucket) * 100, 1)
        prob_media = round(sum(p for p, _ in bucket) / len(bucket), 1)
        calibracion.append({"rango": f"{lo}–{hi}%", "n": len(bucket),
                             "accuracy_real": acc_real,
                             "diferencia": round(acc_real - prob_media, 1)})

    umbrales_optimos = []
    for mercado, d in mercado_data.items():
        if d["n"] < 5:
            continue
        c_rows = [(p, ok) for _, p, ok in rows
                  if _ == mercado and p and ok is not None]
        if not c_rows:
            continue
        umbral_actual = UMBRALES.get(mercado, 70)
        base_set = [ok for p, ok in c_rows if p >= umbral_actual]
        acc_actual = round(sum(base_set) / len(base_set) * 100, 1) if base_set else 0

        best_umbral, best_acc, best_cov = umbral_actual, acc_actual, len(base_set)
        for t in range(50, 96, 5):
            filtered = [ok for p, ok in c_rows if p >= t]
            if len(filtered) < max(3, len(c_rows) * 0.3):
                break
            a = round(sum(filtered) / len(filtered) * 100, 1)
            if a > best_acc:
                best_umbral, best_acc, best_cov = t, a, len(filtered)

        umbrales_optimos.append({
            "mercado": mercado,
            "umbral_actual": umbral_actual,
            "umbral_optimo": best_umbral,
            "accuracy_actual": acc_actual,
            "accuracy_optima": best_acc,
            "ganancia_acc": round(best_acc - acc_actual, 1),
            "perdida_cobertura": best_cov - len(base_set)
        })

    return jsonify({
        "resumen": {
            "total_predicciones": total,
            "accuracy_global": accuracy_global,
            "mercados_evaluados": len(por_mercado),
            "total_acertadas": total_ok
        },
        "por_mercado": por_mercado,
        "calibracion": calibracion,
        "umbrales_optimos": umbrales_optimos
    })


@app.route("/alertas")
def alertas():
    ahora = datetime.utcnow()
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT match_id, liga, fecha, home, away, mercado_principal, mp_prob, mp_cuota
                 FROM predicciones WHERE verificado=0
                 AND mercado_principal != '' AND mercado_principal NOT LIKE '%Ninguno%'
                 ORDER BY fecha ASC""")
    rows = c.fetchall()
    conn.close()
    alertas = []
    for row in rows:
        match_id, liga, fecha, home, away, mp, mp_prob, mp_cuota = row
        if not fecha: continue
        try:
            fecha_dt = datetime.fromisoformat(fecha.replace('Z','').replace('+00:00',''))
            diff = (fecha_dt - ahora).total_seconds()
            if 0 <= diff <= 10800:
                alertas.append({"match_id":match_id,"liga":liga,"home":home,"away":away,
                    "mercado_principal":mp,"mp_prob":mp_prob,"mp_cuota":mp_cuota,
                    "minutos_restantes":int(diff/60)})
        except: continue
    return jsonify({"alertas":alertas,"total":len(alertas)})

@app.route("/historial")
def historial():
    conn = get_db()
    c = conn.cursor()
    c.execute("""SELECT match_id, liga, fecha, home, away, mercado_principal, mp_prob, mp_cuota,
                 combinable, comb_prob, resultado_home, resultado_away,
                 mp_acertado, comb_acertado, verificado, creado
                 FROM predicciones
                 ORDER BY fecha DESC LIMIT 200""")
    rows = c.fetchall()
    c.execute("""SELECT COUNT(*),
                 SUM(CASE WHEN mp_acertado=1 THEN 1 ELSE 0 END),
                 COUNT(CASE WHEN verificado=1 AND mp_acertado IS NOT NULL THEN 1 END)
                 FROM predicciones""")
    stats = c.fetchone()
    conn.close()

    total = stats[0] or 0
    ganadas = stats[1] or 0
    verificadas = stats[2] or 0
    acierto = round(ganadas / verificadas * 100, 1) if verificadas else 0

    predicciones = []
    for r in rows:
        predicciones.append({
            "match_id": r[0], "liga": r[1],
            "fecha": r[2][:10] if r[2] else "—",
            "home": r[3], "away": r[4],
            "mercado_principal": r[5], "mp_prob": r[6], "mp_cuota": r[7],
            "combinable": r[8], "comb_prob": r[9],
            "resultado": f"{r[10]}-{r[11]}" if r[10] is not None else None,
            "mp_acertado": r[12], "comb_acertado": r[13],
            "verificado": r[14],
        })

    return jsonify({
        "stats": {"total": total, "ganadas": ganadas, "verificadas": verificadas, "acierto": acierto},
        "predicciones": predicciones
    })

@app.route("/auto_analizar")
def auto_analizar():
    hoy = datetime.utcnow()
    desde = (hoy - timedelta(days=2)).strftime("%Y-%m-%d")
    hasta = (hoy + timedelta(days=1)).strftime("%Y-%m-%d")

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT match_id FROM predicciones")
    ya_analizados = {row[0] for row in c.fetchall()}
    conn.close()

    procesados = 0
    errores = 0

    for codigo, liga in LIGAS.items():
        try:
            source = liga.get("source", "fd")
            ids = []
            if source == "as":
                data = as_get("/fixtures", {
                    "league": liga.get("as_id"), "season": liga.get("season"),
                    "from": desde, "to": hasta
                })
                ids = [fx.get("fixture",{}).get("id") for fx in data.get("response",[]) if fx.get("fixture",{}).get("id")]
            else:
                data = fd_get(f"/competitions/{codigo}/matches", {
                    "dateFrom": desde, "dateTo": hasta, "limit": 20
                })
                ids = [m["id"] for m in data.get("matches", [])]

            for mid in ids:
                if mid in ya_analizados:
                    continue
                try:
                    r = _do_analyze(codigo, mid)
                    if "error" not in r:
                        procesados += 1
                    else:
                        errores += 1
                    time.sleep(0.3)
                except Exception as e:
                    errores += 1
        except Exception as e:
            print(f"Auto analizar error {codigo}: {e}")

    return jsonify({"procesados": procesados, "errores": errores})


@app.route("/backfill/<codigo>")
def backfill(codigo):
    liga = LIGAS.get(codigo, {})
    if not liga:
        return jsonify({"error": "Liga no encontrada"})

    hoy = datetime.utcnow()
    desde = (hoy - timedelta(days=7)).strftime("%Y-%m-%d")
    hasta = hoy.strftime("%Y-%m-%d")
    source = liga.get("source", "fd")

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT match_id FROM predicciones")
    ya_analizados = {row[0] for row in c.fetchall()}
    conn.close()

    ids = []
    if source == "as":
        data = as_get("/fixtures", {
            "league": liga.get("as_id"), "season": liga.get("season"),
            "from": desde, "to": hasta, "status": "FT"
        })
        ids = [fx.get("fixture",{}).get("id") for fx in data.get("response",[]) if fx.get("fixture",{}).get("id")]
    else:
        data = fd_get(f"/competitions/{codigo}/matches", {
            "dateFrom": desde, "dateTo": hasta, "limit": 100
        })
        ids = [m["id"] for m in data.get("matches", [])]

    pendientes = [mid for mid in ids if mid not in ya_analizados]
    procesados = errores = 0

    for mid in pendientes:
        try:
            r = _do_analyze(codigo, mid)
            if "error" not in r:
                procesados += 1
            else:
                errores += 1
            time.sleep(0.5)
        except Exception as e:
            errores += 1

    return jsonify({"procesados": procesados, "errores": errores, "total": len(pendientes)})

@app.route("/test_apis")
def test_apis():
    resultados = {}
    try:
        r = requests.get("https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard", timeout=10)
        resultados["espn"] = {"status": r.status_code, "ok": r.ok}
    except Exception as e:
        resultados["espn"] = {"error": str(e)}
    try:
        r = requests.get("https://api.sofascore.com/api/v1/sport/football/events/live",
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resultados["sofascore"] = {"status": r.status_code, "ok": r.ok}
    except Exception as e:
        resultados["sofascore"] = {"error": str(e)}
    try:
        r = requests.get("https://www.eloratings.net/World.tsv", timeout=10)
        resultados["eloratings"] = {"status": r.status_code, "ok": r.ok, "preview": r.text[:200]}
    except Exception as e:
        resultados["eloratings"] = {"error": str(e)}
    try:
        r = requests.get("https://www.transfermarkt.com/",
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        resultados["transfermarkt"] = {"status": r.status_code, "ok": r.ok}
    except Exception as e:
        resultados["transfermarkt"] = {"error": str(e)}
    return jsonify(resultados)

@app.route("/wc_data")
def wc_data():
    import re
    elo_data = {}
    try:
        r = requests.get("https://www.eloratings.net/World.tsv",
                 headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                 timeout=15)
        if r.ok:
            lines = r.content.decode('utf-8', errors='replace').strip().split("\n")
            for line in lines[1:]:
                parts = line.strip().split("\t")
                if len(parts) >= 3:
                    try:
                        rank = int(parts[0].strip())
                        nombre = parts[1].strip()
                        elo = int(float(parts[2].strip()))
                        elo_data[nombre] = {"rank": rank, "elo": elo}
                    except: pass
    except Exception as e:
        elo_data["error"] = str(e)

    fixtures = []
    try:
        d = fd_get("/competitions/WC/matches", {"limit": 100})
        for m in d.get("matches", []):
            if not m["homeTeam"].get("name") or not m["awayTeam"].get("name"):
                continue
            fixtures.append({
                "id": m["id"],
                "fecha": m.get("utcDate","")[:10],
                "home": m["homeTeam"]["name"],
                "away": m["awayTeam"]["name"],
                "stage": m.get("stage",""),
                "group": m.get("group",""),
                "status": m.get("status",""),
                "score": m.get("score",{}).get("fullTime",{})
            })
    except Exception as e:
        fixtures = [{"error": str(e)}]

    return jsonify({"elo": elo_data, "fixtures": fixtures, "total_fixtures": len(fixtures)})


@app.route("/wc_generar_ausencias")
def wc_generar_ausencias():
    """
    Usa Claude con web search para buscar las listas del Mundial 2026
    y generar automaticamente el diccionario de ausencias.
    Solo ejecutar el 2 de junio o despues cuando esten las listas oficiales.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Sin ANTHROPIC_API_KEY"})

    # Selecciones top a analizar
    selecciones = [
        "Argentina", "France", "Spain", "England", "Brazil", "Germany",
        "Portugal", "Netherlands", "Belgium", "Uruguay", "Colombia",
        "Croatia", "Denmark", "Switzerland", "USA", "Mexico", "Morocco",
        "Japan", "Senegal", "Ecuador", "Canada", "Australia", "Serbia",
        "Poland", "Ukraine", "Turkey", "Austria", "Nigeria", "Egypt",
        "Cameroon", "Saudi Arabia", "Qatar", "South Korea", "Iran",
        "Peru", "Chile", "Bolivia", "Paraguay", "Venezuela",
        "Panama", "Honduras", "Costa Rica", "Jamaica",
        "Ivory Coast", "Ghana", "Mali", "Algeria", "Tunisia"
    ]

    prompt = f"""Buscá las listas oficiales convocadas para el Mundial 2026 de estas selecciones: {', '.join(selecciones[:20])}.

Para cada selección, identificá los jugadores de alto perfil que NO fueron convocados (lesionados, suspendidos, o excluidos por forma).

Respondé SOLO con JSON válido sin markdown:
{{
  "Argentina": {{
    "ausentes": [
      {{"nombre": "Nombre", "posicion": "Posicion", "valor_m": 50, "criticidad": 0.45, "penalty_elo": -38, "motivo": "lesion/suspension/forma"}}
    ],
    "penalty_total": -38
  }}
}}

Para calcular penalty_elo: criticidad * 80 (negativo). Solo incluí jugadores ausentes que normalmente estarían en el equipo titular o rotación principal. Si un jugador fue convocado, NO lo incluyas. Si no hay ausencias relevantes, devolvé lista vacía."""

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 4000,
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=60
        )
        if not r.ok:
            return jsonify({"error": f"API error {r.status_code}"})

        data = r.json()
        texto = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                texto += block.get("text", "")

        texto = texto.strip()
        if "```" in texto:
            import re
            match = re.search(r'```(?:json)?\s*([\s\S]*?)```', texto)
            if match:
                texto = match.group(1).strip()

        ausencias_nuevas = json.loads(texto)

        # Guardar en cache para que wc_ausencias lo use
        _cache["wc_ausencias_generadas"] = (ausencias_nuevas, time.time())

        return jsonify({
            "ok": True,
            "selecciones_procesadas": len(ausencias_nuevas),
            "ausencias": ausencias_nuevas,
            "mensaje": "Ausencias generadas. Para aplicarlas permanentemente, copiar el JSON a wc_ausencias() en recomendaciones_dia.py"
        })

    except Exception as e:
        return jsonify({"error": str(e), "texto_raw": texto if 'texto' in locals() else ""})


@app.route("/wc_ausencias_preview")
def wc_ausencias_preview():
    """Muestra las ausencias generadas en cache (si existen) o las hardcodeadas."""
    cached = _cache.get("wc_ausencias_generadas")
    if cached:
        return jsonify({"fuente": "generado_automaticamente", "data": cached[0], "generado_hace": round((time.time() - cached[1])/60), "minutos": True})
    return jsonify({"fuente": "hardcodeado", "mensaje": "No hay ausencias generadas. Ejecutar /wc_generar_ausencias primero."})


@app.route("/wc_generar_squads")
def wc_generar_squads():
    """
    Usa Claude web search para buscar las listas del Mundial 2026
    y calcular el ELO ajustado de cada seleccion basado en los convocados.
    Devuelve el mismo formato que wc_ausencias para que el simulador lo use sin cambios.
    Ejecutar el 2 de junio o despues cuando esten las listas oficiales.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Sin ANTHROPIC_API_KEY"})

    # Cache de 12 horas
    ck = "wc_squads_generados"
    now_t = time.time()
    if ck in _cache and now_t - _cache[ck][1] < 43200:
        return jsonify({"ok": True, "fuente": "cache", "data": _cache[ck][0]})

    # Selecciones clasificadas al Mundial 2026 (48 equipos)
    grupos = {
        "CONMEBOL": ["Argentina", "Brazil", "Uruguay", "Colombia", "Ecuador", "Chile", "Paraguay", "Bolivia", "Venezuela", "Peru"],
        "UEFA": ["France", "Spain", "England", "Germany", "Portugal", "Netherlands", "Belgium", "Croatia", "Denmark", "Switzerland", "Austria", "Serbia", "Poland", "Ukraine", "Turkey", "Hungary", "Romania", "Scotland", "Czechia", "Slovakia"],
        "CONCACAF": ["USA", "Mexico", "Canada", "Costa Rica", "Honduras", "Jamaica", "Panama", "El Salvador", "Trinidad and Tobago", "Curacao"],
        "AFC": ["Japan", "South Korea", "Iran", "Saudi Arabia", "Australia", "Qatar", "Iraq", "Uzbekistan", "Jordan", "Oman"],
        "CAF": ["Morocco", "Nigeria", "Senegal", "Egypt", "Cameroon", "Ivory Coast", "Ghana", "Algeria", "Mali", "Tunisia", "South Africa", "DR Congo"],
        "OFC": ["New Zealand"],
    }
    todas = [t for g in grupos.values() for t in g]

    prompt = f"""Es el Mundial 2026. Buscá las listas oficiales de convocados para estas selecciones: {', '.join(todas[:24])}.

Para cada seleccion, identificá:
1. Los jugadores TOP que SI fueron convocados (los mejores 5-6 del equipo)
2. Los jugadores importantes que NO fueron convocados (lesionados, excluidos)

Calculá un "elo_adjustment" para cada seleccion:
- Si el plantel es completo con sus mejores jugadores: adjustment = 0
- Si faltan jugadores clave: adjustment negativo (-20 a -100 segun importancia)
- Si hay jugadores en gran forma no habituales: adjustment positivo (+10 a +30)

Respondé SOLO con JSON valido sin markdown ni explicaciones:
{{
  "Argentina": {{
    "ausentes": [
      {{"nombre": "Nombre", "posicion": "Posicion", "criticidad": 0.5, "penalty_elo": -40, "motivo": "lesion"}}
    ],
    "convocados_destacados": ["Messi", "Di Maria"],
    "penalty_total": -40,
    "notas": "texto breve"
  }}
}}

Solo incluí selecciones donde encontres informacion confirmada. penalty_total es la suma de todos los penalty_elo."""

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 8000,
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=90
        )
        if not r.ok:
            return jsonify({"error": f"API error {r.status_code}", "detail": r.text[:300]})

        data = r.json()
        texto = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                texto += block.get("text", "")

        texto = texto.strip()
        import re
        match = re.search(r'\{[\s\S]*\}', texto)
        if match:
            texto = match.group(0)

        squads = json.loads(texto)

        # Guardar en cache
        _cache[ck] = (squads, now_t)

        return jsonify({
            "ok": True,
            "fuente": "web_search",
            "selecciones": len(squads),
            "data": squads,
            "instruccion": "Revisar el JSON y si esta correcto, este endpoint reemplaza a /wc_ausencias automaticamente via /wc_ausencias_v2"
        })

    except json.JSONDecodeError as e:
        return jsonify({"error": f"JSON parse error: {e}", "texto_raw": texto[:500] if 'texto' in locals() else ""})
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/wc_ausencias_v2")
def wc_ausencias_v2():
    """
    Version dinamica de wc_ausencias que usa los squads generados si existen,
    sino cae al hardcodeado. El simulador puede apuntar a este endpoint.
    """
    ck = "wc_squads_generados"
    cached = _cache.get(ck)
    if cached and time.time() - cached[1] < 43200:
        return jsonify(cached[0])
    # Fallback al hardcodeado
    return wc_ausencias()

@app.route("/wc_ausencias")
def wc_ausencias():
    # Actualizado con listas oficiales FIFA - 2 de junio 2026
    ausencias = {
        "Brazil": {
            "ausentes": [
                # Neymar SI convocado (sorpresa). Rodrygo, Militao, Estevaoo NO convocados
                {"nombre": "Rodrygo", "posicion": "Right Winger", "valor_m": 120, "criticidad": 0.65, "penalty_elo": -52},
                {"nombre": "Eder Militao", "posicion": "Centre-Back", "valor_m": 50, "criticidad": 0.40, "penalty_elo": -35},
                {"nombre": "Estevaoo", "posicion": "Right Winger", "valor_m": 60, "criticidad": 0.42, "penalty_elo": -37},
            ],
            "penalty_total": -124
        },
        "Netherlands": {
            "ausentes": [
                {"nombre": "Xavi Simons", "posicion": "Attacking Midfield", "valor_m": 80, "criticidad": 0.58, "penalty_elo": -48},
                {"nombre": "Matthijs de Ligt", "posicion": "Centre-Back", "valor_m": 35, "criticidad": 0.32, "penalty_elo": -29},
            ],
            "penalty_total": -77
        },
        "France": {
            "ausentes": [
                # Lucas Hernandez SI convocado. Solo falta Ekitike
                {"nombre": "Hugo Ekitike", "posicion": "Centre-Forward", "valor_m": 60, "criticidad": 0.38, "penalty_elo": -33},
            ],
            "penalty_total": -33
        },
        "Germany": {
            "ausentes": [
                {"nombre": "Serge Gnabry", "posicion": "Right Winger", "valor_m": 25, "criticidad": 0.28, "penalty_elo": -25},
            ],
            "penalty_total": -25
        },
        "Spain": {
            "ausentes": [
                # Pedri, Gavi y Fermin confirmados NO convocados
                {"nombre": "Pedri", "posicion": "Central Midfield", "valor_m": 100, "criticidad": 0.61, "penalty_elo": -51},
                {"nombre": "Gavi", "posicion": "Central Midfield", "valor_m": 80, "criticidad": 0.49, "penalty_elo": -42},
                {"nombre": "Fermin Lopez", "posicion": "Central Midfield", "valor_m": 50, "criticidad": 0.38, "penalty_elo": -33},
            ],
            "penalty_total": -126
        },
        "England": {
            "ausentes": [
                # Reece James SI convocado. Ben White y Foden NO convocados
                {"nombre": "Ben White", "posicion": "Right-Back", "valor_m": 45, "criticidad": 0.30, "penalty_elo": -27},
                {"nombre": "Phil Foden", "posicion": "Attacking Midfield", "valor_m": 110, "criticidad": 0.55, "penalty_elo": -46},
            ],
            "penalty_total": -73
        },
        "Argentina": {
            "ausentes": [
                # Romero y Nico Paz SI convocados. Solo falta Foyth
                {"nombre": "Juan Foyth", "posicion": "Right-Back", "valor_m": 30, "criticidad": 0.25, "penalty_elo": -23},
            ],
            "penalty_total": -23
        },
        "Uruguay": {
            "ausentes": [
                {"nombre": "Fede Valverde", "posicion": "Central Midfield", "valor_m": 120, "criticidad": 0.71, "penalty_elo": -58},
                {"nombre": "Ronald Araujo", "posicion": "Centre-Back", "valor_m": 60, "criticidad": 0.44, "penalty_elo": -38},
            ],
            "penalty_total": -96
        },
        "Ecuador": {
            "ausentes": [
                # Hincapie SI convocado (Arsenal). Solo Preciado ausente
                {"nombre": "Angelo Preciado", "posicion": "Right-Back", "valor_m": 15, "criticidad": 0.17, "penalty_elo": -18},
            ],
            "penalty_total": -18
        },
        "Mexico": {
            "ausentes": [
                {"nombre": "Hirving Lozano", "posicion": "Right Winger", "valor_m": 20, "criticidad": 0.28, "penalty_elo": -24},
            ],
            "penalty_total": -24
        },
        "United States": {
            "ausentes": [
                {"nombre": "Gio Reyna", "posicion": "Attacking Midfield", "valor_m": 25, "criticidad": 0.28, "penalty_elo": -24},
            ],
            "penalty_total": -24
        },
        "Colombia": {
            "ausentes": [],
            "penalty_total": 0
        },
    }
    return jsonify(ausencias)



# ── WORLD CUP: FORMACIONES Y RENDIMIENTO ─────────────────────────────────

@app.route("/wc_formacion", methods=["GET", "POST"])
def wc_formacion():
    """Guardar/obtener formaciones de partidos del Mundial."""
    conn = get_db()
    c = conn.cursor()
    # Crear tabla si no existe
    c.execute("""CREATE TABLE IF NOT EXISTS wc_formaciones (
        partido_id TEXT PRIMARY KEY,
        home TEXT,
        away TEXT,
        home_titulares TEXT,
        away_titulares TEXT,
        xi_adj_home REAL DEFAULT 0,
        xi_adj_away REAL DEFAULT 0,
        creado TEXT,
        actualizado TEXT
    )""")
    # Agregar columnas si no existen (migracion)
    try:
        c.execute("ALTER TABLE wc_formaciones ADD COLUMN xi_adj_home REAL DEFAULT 0")
        c.execute("ALTER TABLE wc_formaciones ADD COLUMN xi_adj_away REAL DEFAULT 0")
        conn.commit()
    except: pass
    conn.commit()

    if request.method == "POST":
        body = request.get_json() or {}
        partido_id = body.get("partido_id")
        home = body.get("home")
        away = body.get("away")
        home_titulares = json.dumps(body.get("home_titulares", []), ensure_ascii=False)
        away_titulares = json.dumps(body.get("away_titulares", []), ensure_ascii=False)
        ahora = datetime.utcnow().isoformat()
        xi_adj_home = float(body.get("xi_adj_home", 0) or 0)
        xi_adj_away = float(body.get("xi_adj_away", 0) or 0)
        c.execute("""INSERT INTO wc_formaciones (partido_id, home, away, home_titulares, away_titulares, xi_adj_home, xi_adj_away, creado, actualizado)
                     VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                     ON CONFLICT (partido_id) DO UPDATE
                     SET home_titulares=EXCLUDED.home_titulares,
                         away_titulares=EXCLUDED.away_titulares,
                         xi_adj_home=EXCLUDED.xi_adj_home,
                         xi_adj_away=EXCLUDED.xi_adj_away,
                         actualizado=EXCLUDED.actualizado""",
                  (partido_id, home, away, home_titulares, away_titulares, xi_adj_home, xi_adj_away, ahora, ahora))
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "partido_id": partido_id})
    else:
        partido_id = request.args.get("partido_id")
        if partido_id:
            c.execute("SELECT * FROM wc_formaciones WHERE partido_id=%s", (partido_id,))
            row = c.fetchone()
            conn.close()
            if row:
                return jsonify({
                    "partido_id": row[0], "home": row[1], "away": row[2],
                    "home_titulares": json.loads(row[3]),
                    "away_titulares": json.loads(row[4]),
                    "xi_adj_home": row[5] if len(row) > 5 else 0,
                    "xi_adj_away": row[6] if len(row) > 6 else 0,
                    "actualizado": row[8] if len(row) > 8 else row[6]
                })
            return jsonify({"error": "No encontrado"})
        else:
            c.execute("SELECT partido_id, home, away, xi_adj_home, xi_adj_away, actualizado FROM wc_formaciones ORDER BY actualizado DESC")
            rows = c.fetchall()
            conn.close()
            return jsonify([{"partido_id": r[0], "home": r[1], "away": r[2], "xi_adj_home": r[3] or 0, "xi_adj_away": r[4] or 0, "actualizado": r[5]} for r in rows])


@app.route("/wc_rendimiento", methods=["GET", "POST"])
def wc_rendimiento():
    """Guardar/obtener resultados reales del Mundial para ajuste dinamico de ELO."""
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS wc_resultados (
        partido_id TEXT PRIMARY KEY,
        home TEXT,
        away TEXT,
        goles_home INTEGER,
        goles_away INTEGER,
        fase TEXT,
        fecha TEXT,
        elo_home_antes INTEGER,
        elo_away_antes INTEGER,
        elo_ajuste_home INTEGER,
        elo_ajuste_away INTEGER,
        creado TEXT
    )""")
    conn.commit()

    if request.method == "POST":
        body = request.get_json() or {}
        partido_id = body.get("partido_id")
        home = body.get("home")
        away = body.get("away")
        gh = body.get("goles_home", 0)
        ga = body.get("goles_away", 0)
        fase = body.get("fase", "Grupos")
        fecha = body.get("fecha", datetime.utcnow().isoformat()[:10])
        elo_h = body.get("elo_home_antes", 1800)
        elo_a = body.get("elo_away_antes", 1800)

        # Calcular ajuste ELO post-partido (formula ELO estandar)
        k = 10 if fase == "Amistoso" else (40 if fase == "Grupos" else (50 if "Octavos" in fase or "Cuartos" in fase else 60))
        expected_h = 1 / (1 + 10 ** ((elo_a - elo_h) / 400))
        if gh > ga: score_h = 1.0
        elif gh == ga: score_h = 0.5
        else: score_h = 0.0
        score_a = 1.0 - score_h
        ajuste_h = round(k * (score_h - expected_h))
        ajuste_a = round(k * (score_a - (1 - expected_h)))

        ahora = datetime.utcnow().isoformat()
        c.execute("""INSERT INTO wc_resultados
            (partido_id, home, away, goles_home, goles_away, fase, fecha,
             elo_home_antes, elo_away_antes, elo_ajuste_home, elo_ajuste_away, creado)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (partido_id) DO UPDATE
            SET goles_home=EXCLUDED.goles_home, goles_away=EXCLUDED.goles_away,
                elo_ajuste_home=EXCLUDED.elo_ajuste_home, elo_ajuste_away=EXCLUDED.elo_ajuste_away""",
            (partido_id, home, away, gh, ga, fase, fecha, elo_h, elo_a, ajuste_h, ajuste_a, ahora))
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "ajuste_home": ajuste_h, "ajuste_away": ajuste_a})
    else:
        # Devolver ajustes acumulados por seleccion
        c.execute("SELECT home, away, elo_ajuste_home, elo_ajuste_away FROM wc_resultados")
        rows = c.fetchall()
        conn.close()
        ajustes = {}
        for home, away, adj_h, adj_a in rows:
            ajustes[home] = ajustes.get(home, 0) + (adj_h or 0)
            ajustes[away] = ajustes.get(away, 0) + (adj_a or 0)
        return jsonify({"ajustes": ajustes, "partidos": len(rows)})



@app.route("/wc_cargar_planteles_static")
def wc_cargar_planteles_static():
    """Carga los planteles oficiales del Mundial 2026 directo a Supabase (sin API externa)."""
    try:
        from wc_planteles_data import WC_PLANTELES
    except ImportError:
        return jsonify({"error": "No se encontro wc_planteles_data.py"})

    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS wc_planteles (
        seleccion TEXT,
        nombre TEXT,
        posicion TEXT,
        club TEXT,
        valor_m REAL,
        edad INTEGER,
        creado TEXT,
        PRIMARY KEY (seleccion, nombre)
    )""")
    conn.commit()

    ahora = datetime.utcnow().isoformat()
    total = 0
    errores = []
    for seleccion, jugadores in WC_PLANTELES.items():
        for j in jugadores:
            try:
                c.execute("""INSERT INTO wc_planteles (seleccion, nombre, posicion, club, valor_m, edad, creado)
                             VALUES (%s, %s, %s, %s, %s, %s, %s)
                             ON CONFLICT (seleccion, nombre) DO UPDATE
                             SET posicion=EXCLUDED.posicion, club=EXCLUDED.club,
                                 valor_m=EXCLUDED.valor_m, edad=EXCLUDED.edad""",
                          (seleccion, j.get("nombre",""), j.get("posicion",""),
                           j.get("club",""), float(j.get("valor_m", 1)),
                           int(j.get("edad", 25)), ahora))
                total += 1
            except Exception as e:
                errores.append(f"{seleccion}/{j.get('nombre','?')}: {e}")
    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "selecciones": len(WC_PLANTELES),
        "jugadores_cargados": total,
        "errores": errores[:10]
    })

@app.route("/wc_cargar_planteles")
def wc_cargar_planteles():
    """
    Carga las listas completas de convocados con valores de mercado via Claude web search.
    Guarda en Supabase tabla wc_planteles. Ejecutar una vez antes del torneo.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({"error": "Sin ANTHROPIC_API_KEY"})

    # Verificar cache (24hs) - ?force=1 para forzar recarga
    ck = "wc_planteles_loaded"
    force = request.args.get("force") == "1"
    if not force and ck in _cache and time.time() - _cache[ck][1] < 86400:
        return jsonify({"ok": True, "fuente": "cache", "mensaje": "Ya cargado recientemente"})
    if force and ck in _cache:
        del _cache[ck]

    # Crear tabla si no existe
    conn = get_db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS wc_planteles (
        seleccion TEXT,
        nombre TEXT,
        posicion TEXT,
        club TEXT,
        valor_m REAL,
        edad INTEGER,
        creado TEXT,
        PRIMARY KEY (seleccion, nombre)
    )""")
    conn.commit()
    conn.close()

    # Soporta ?seleccion=Argentina para cargar de a una
    seleccion_single = request.args.get("seleccion")

    todas_selecciones = [
        "Argentina", "Brazil", "France", "Spain", "England", "Germany", "Portugal", "Netherlands",
        "Belgium", "Croatia", "Uruguay", "Colombia", "Morocco", "Japan", "USA", "Mexico",
        "Senegal", "Ecuador", "Canada", "Australia", "Denmark", "Switzerland", "Austria", "Serbia",
        "Poland", "South Korea", "Saudi Arabia", "Qatar", "Iran", "Cameroon", "Ghana", "Ivory Coast",
        "Egypt", "Nigeria", "Algeria", "Norway", "Scotland", "Czechia", "Bosnia And Herzegovina", "Panama",
        "Paraguay", "New Zealand", "Haiti", "Iraq", "Jordan", "Congo DR", "Cabo Verde", "Curacao",
    ]

    if seleccion_single:
        selecciones_a_procesar = [seleccion_single]
    else:
        # Sin parametro: solo las primeras 4 para no dar timeout
        selecciones_a_procesar = todas_selecciones[:4]

    total_guardados = 0
    errores = []

    for seleccion_nombre in selecciones_a_procesar:
        prompt = (
            f"Dame la lista de los 26 jugadores convocados por {seleccion_nombre} para el Mundial 2026, "
            f"con nombre completo, posicion (GK/DF/MF/FW), club actual y valor de mercado en millones de euros. "
            "Respondé SOLO con JSON valido sin markdown: "
            f'{{"{seleccion_nombre}": [{{"nombre": "Nombre", "posicion": "FW", "club": "Club", "valor_m": 25, "edad": 28}}]}}'
            " Para jugadores con poco perfil, estimá entre 0.5 y 5M."
        )

        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json"
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 4000,
                    "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=60
            )
            if not r.ok:
                errores.append(f"API error lote {seleccion_nombre}: {r.status_code}")
                continue

            data = r.json()
            texto = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    texto += block.get("text", "")

            texto = texto.strip()
            import re
            match = re.search(r'\{[\s\S]*\}', texto)
            if match:
                texto = match.group(0)

            planteles = json.loads(texto)

            # Guardar en Supabase
            conn = get_db()
            c = conn.cursor()
            ahora = datetime.utcnow().isoformat()
            # planteles puede tener la seleccion bajo distintas keys
            jugadores_list = planteles.get(seleccion_nombre, list(planteles.values())[0] if planteles else [])
            for j in jugadores_list:
                try:
                    c.execute("""INSERT INTO wc_planteles (seleccion, nombre, posicion, club, valor_m, edad, creado)
                                 VALUES (%s, %s, %s, %s, %s, %s, %s)
                                 ON CONFLICT (seleccion, nombre) DO UPDATE
                                 SET posicion=EXCLUDED.posicion, club=EXCLUDED.club,
                                     valor_m=EXCLUDED.valor_m, edad=EXCLUDED.edad""",
                              (seleccion_nombre, j.get("nombre",""), j.get("posicion",""),
                               j.get("club",""), float(j.get("valor_m", 1)),
                               int(j.get("edad", 25)), ahora))
                    total_guardados += 1
                except Exception as e:
                    errores.append(f"{seleccion_nombre}/{j.get('nombre','?')}: {e}")
            conn.commit()
            conn.close()
            time.sleep(5)  # evitar rate limit entre selecciones

        except Exception as e:
            errores.append(f"Lote {lote}: {e}")

    _cache[ck] = (True, time.time())
    return jsonify({
        "ok": True,
        "total_guardados": total_guardados,
        "errores": errores[:10],
        "mensaje": f"Planteles cargados: {total_guardados} jugadores"
    })


@app.route("/wc_planteles")
def wc_planteles():
    """Devuelve los planteles guardados, filtrable por seleccion."""
    ALIASES = {
        "United States": "USA", "United States of America": "USA",
        "Bosnia-Herzegovina": "Bosnia And Herzegovina",
        "Bosnia and Herzegovina": "Bosnia And Herzegovina",
        "Cape Verde Islands": "Cabo Verde", "Cape Verde": "Cabo Verde",
        "Curacao": "Curacao", "Curaçao": "Curacao",
        "Korea Republic": "South Korea", "Republic of Korea": "South Korea",
        "IR Iran": "Iran", "Czech Republic": "Czechia",
        "DR Congo": "Congo DR", "Cote d'Ivoire": "Ivory Coast",
    }
    seleccion = request.args.get("seleccion")
    if seleccion:
        seleccion = ALIASES.get(seleccion, seleccion)
    conn = get_db()
    c = conn.cursor()
    if seleccion:
        c.execute("SELECT nombre, posicion, club, valor_m, edad FROM wc_planteles WHERE seleccion=%s ORDER BY valor_m DESC", (seleccion,))
        rows = c.fetchall()
        conn.close()
        return jsonify({
            "seleccion": seleccion,
            "jugadores": [{"nombre": r[0], "posicion": r[1], "club": r[2], "valor_m": r[3], "edad": r[4]} for r in rows]
        })
    else:
        c.execute("SELECT seleccion, COUNT(*) as n, SUM(valor_m) as valor_total FROM wc_planteles GROUP BY seleccion ORDER BY valor_total DESC")
        rows = c.fetchall()
        conn.close()
        return jsonify([{"seleccion": r[0], "jugadores": r[1], "valor_total_m": round(r[2] or 0, 1)} for r in rows])


@app.route("/wc_valor_xi", methods=["POST"])
def wc_valor_xi():
    """
    Calcula el valor de mercado del XI titular y ajusta el ELO del partido.
    Recibe: {home, away, home_titulares: [...], away_titulares: [...], elo_home, elo_away}
    """
    body = request.get_json() or {}
    home = body.get("home")
    away = body.get("away")
    home_xi = body.get("home_titulares", [])
    away_xi = body.get("away_titulares", [])
    elo_h = body.get("elo_home", 1800)
    elo_a = body.get("elo_away", 1800)

    conn = get_db()
    c = conn.cursor()

    def get_valor_xi(seleccion, titulares):
        if not titulares:
            return None, None
        placeholders = ','.join(['%s'] * len(titulares))
        c.execute(f"""SELECT nombre, valor_m FROM wc_planteles
                      WHERE seleccion=%s AND nombre = ANY(%s)""",
                  (seleccion, titulares))
        rows = c.fetchall()
        if not rows:
            return None, None
        valor_xi = sum(r[1] for r in rows if r[1])
        encontrados = len(rows)
        return round(valor_xi, 1), encontrados

    # Valor plantel completo para comparar
    c.execute("SELECT SUM(valor_m) FROM wc_planteles WHERE seleccion=%s", (home,))
    row = c.fetchone()
    valor_total_h = row[0] or 0

    c.execute("SELECT SUM(valor_m) FROM wc_planteles WHERE seleccion=%s", (away,))
    row = c.fetchone()
    valor_total_a = row[0] or 0

    valor_xi_h, enc_h = get_valor_xi(home, home_xi)
    valor_xi_a, enc_a = get_valor_xi(away, away_xi)
    conn.close()

    # Ajuste ELO basado en % del valor del XI vs plantel total
    ajuste_h = ajuste_a = 0
    if valor_xi_h and valor_total_h > 0:
        pct_h = valor_xi_h / valor_total_h
        # Si el XI vale menos del 50% del plantel -> penaliza
        if pct_h < 0.5: ajuste_h = round((pct_h - 0.5) * 100)
        elif pct_h > 0.7: ajuste_h = round((pct_h - 0.7) * 50)

    if valor_xi_a and valor_total_a > 0:
        pct_a = valor_xi_a / valor_total_a
        if pct_a < 0.5: ajuste_a = round((pct_a - 0.5) * 100)
        elif pct_a > 0.7: ajuste_a = round((pct_a - 0.7) * 50)

    return jsonify({
        "home": home, "away": away,
        "valor_xi_home": valor_xi_h, "valor_xi_away": valor_xi_a,
        "valor_plantel_home": round(valor_total_h, 1), "valor_plantel_away": round(valor_total_a, 1),
        "ajuste_elo_home": ajuste_h, "ajuste_elo_away": ajuste_a,
        "elo_ajustado_home": elo_h + ajuste_h, "elo_ajustado_away": elo_a + ajuste_a,
        "encontrados_home": enc_h, "encontrados_away": enc_a,
    })

@app.route("/wc_bonus")
def wc_bonus():
    ck = "wc_bonus_data"
    now_t = time.time()
    if ck in _cache and now_t - _cache[ck][1] < 86400:
        return jsonify(_cache[ck][0])

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return jsonify({})

    jugadores_clave = {
        "Argentina": ["Lionel Messi", "Lautaro Martinez", "Enzo Fernandez"],
        "France": ["Kylian Mbappe", "Ousmane Dembele", "Antoine Griezmann"],
        "Spain": ["Lamine Yamal", "Alvaro Morata", "Rodri"],
        "England": ["Harry Kane", "Jude Bellingham", "Bukayo Saka"],
        "Brazil": ["Vinicius Junior", "Raphinha", "Neymar"],
        "Germany": ["Florian Wirtz", "Jamal Musiala", "Kai Havertz"],
        "Portugal": ["Cristiano Ronaldo", "Bruno Fernandes", "Rafael Leao"],
        "Netherlands": ["Virgil van Dijk", "Cody Gakpo", "Frenkie de Jong"],
        "Belgium": ["Kevin De Bruyne", "Romelu Lukaku", "Jeremy Doku"],
        "Uruguay": ["Darwin Nunez", "Luis Suarez", "Rodrigo Bentancur"],
        "Colombia": ["Luis Diaz", "James Rodriguez", "Jhon Cordoba"],
        "Croatia": ["Luka Modric", "Mateo Kovacic", "Josko Gvardiol"],
        "Morocco": ["Achraf Hakimi", "Brahim Diaz", "Sofyan Amrabat"],
        "Japan": ["Takefusa Kubo", "Ritsu Doan", "Wataru Endo"],
        "USA": ["Christian Pulisic", "Weston McKennie", "Tyler Adams"],
        "Mexico": ["Santiago Gimenez", "Edson Alvarez", "Raul Jimenez"],
        "Senegal": ["Sadio Mane", "Nicolas Jackson", "Ismaila Sarr"],
        "Ecuador": ["Moises Caicedo", "Gonzalo Plata", "Enner Valencia"],
        "Canada": ["Alphonso Davies", "Jonathan David", "Stephen Eustaquio"],
    }

    prompt = (
        "Evalua la forma actual (mayo-junio 2026) de los siguientes jugadores de futbol que van al Mundial 2026. "
        "Para cada SELECCION calcula un bonus_total basado en la forma de sus jugadores clave: "
        "gran forma = bonus +10 a +40, forma normal = 0, mala forma o lesion reciente = -10 a -30. "
        "Jugadores: " + json.dumps(jugadores_clave, ensure_ascii=False) + ". "
        "Respondé SOLO con JSON valido sin markdown con esta estructura: "
        '{"Argentina": {"jugadores": [{"nombre": "X", "forma": "buena", "nota": "razon"}], "bonus_total": 15, "resumen": "texto"}}. '
        "Solo incluye selecciones con informacion reciente confiable."
    )

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": "claude-haiku-4-5-20251001",
                "max_tokens": 4000,
                "tools": [{"type": "web_search_20250305", "name": "web_search"}],
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=90
        )
        if not r.ok:
            return jsonify({})

        data = r.json()
        texto = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                texto += block.get("text", "")

        texto = texto.strip()
        import re
        match = re.search(r'\{[\s\S]*\}', texto)
        if match:
            texto = match.group(0)

        bonus = json.loads(texto)
        _cache[ck] = (bonus, now_t)
        return jsonify(bonus)

    except Exception as e:
        print(f"wc_bonus error: {e}")
        return jsonify({})

@app.route('/wc_fatiga')
def wc_fatiga():
    """
    Actualizar manualmente después de cada partido KO que vaya a prórroga.
    Formato: "Nombre exacto del equipo (inglés)": {"extraTime": True/False, "round": "Octavos/Cuartos/Semis/Final", "opponent": "rival"}
    Solo poner equipos que JUGARON prórroga en su último partido.
    """
    data = {
        # OCTAVOS (actualizar después de cada partido)
        # "Argentina": {"extraTime": True, "round": "Octavos", "opponent": "Australia"},
        # "France": {"extraTime": True, "round": "Octavos", "opponent": "Poland"},
        
        # CUARTOS
        # "England": {"extraTime": True, "round": "Cuartos", "opponent": "France"},
        
        # SEMIS
        # "Brazil": {"extraTime": True, "round": "Semis", "opponent": "Argentina"},
        
        # FINAL
        # "Spain": {"extraTime": True, "round": "Final", "opponent": "Germany"},
    }
    return jsonify(data)

@app.route("/live_scores")
def live_scores():
    """Scores en vivo de todas las ligas via ESPN."""
    _ESPN_SLUGS = {
        "PL": "eng.1", "PD": "esp.1", "SA": "ita.1",
        "BL1": "ger.1", "FL1": "fra.1", "DED": "ned.1",
        "PPL": "por.1", "ELC": "eng.2", "BSA": "bra.1",
        "AARG": "arg.1", "CL": "uefa.champions",
    }
    results = {}
    for codigo, slug in _ESPN_SLUGS.items():
        try:
            data = espn_get(slug, "scoreboard")
            if "error" in data: continue
            events = data.get("events", [])
            liga_scores = []
            for ev in events:
                comp = ev.get("competitions", [{}])[0]
                competitors = comp.get("competitors", [])
                status = ev.get("status", {})
                state = status.get("type", {}).get("state", "pre")
                clock = status.get("displayClock", "")
                period = status.get("period", 0)
                home = next((c for c in competitors if c.get("homeAway") == "home"), {})
                away = next((c for c in competitors if c.get("homeAway") == "away"), {})
                liga_scores.append({
                    "id": ev.get("id"),
                    "home": home.get("team", {}).get("displayName", ""),
                    "away": away.get("team", {}).get("displayName", ""),
                    "home_score": home.get("score", "0"),
                    "away_score": away.get("score", "0"),
                    "state": state,
                    "clock": clock,
                    "period": period,
                    "fecha": ev.get("date", "")[:10],
                })
            if liga_scores:
                results[codigo] = liga_scores
        except Exception as e:
            results[codigo] = {"error": str(e)}
    return jsonify(results)


@app.route("/debug_elo/<team>")
def debug_elo(team):
    import unicodedata
    def normalize(s):
        s = unicodedata.normalize('NFD', s.lower())
        return ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    # Mismo diccionario que analizar_intl
    elo_data = {
        "spain": {"rank": 1, "elo": 2088}, "france": {"rank": 2, "elo": 2005},
        "england": {"rank": 3, "elo": 1994}, "brazil": {"rank": 4, "elo": 1983},
        "argentina": {"rank": 5, "elo": 1975}, "scotland": {"rank": 40, "elo": 1722},
        "curacao": {"rank": 69, "elo": 1558}, "south korea": {"rank": 32, "elo": 1765},
        "korea republic": {"rank": 32, "elo": 1765}, "nigeria": {"rank": 35, "elo": 1748},
        "ecuador": {"rank": 25, "elo": 1805}, "saudi arabia": {"rank": 54, "elo": 1648},
        "mexico": {"rank": 17, "elo": 1860}, "australia": {"rank": 21, "elo": 1832},
        "morocco": {"rank": 18, "elo": 1855}, "united states": {"rank": 16, "elo": 1868},
        "usa": {"rank": 16, "elo": 1868}, "paraguay": {"rank": 51, "elo": 1662},
        "zimbabwe": {"rank": 67, "elo": 1572}, "india": {"rank": 70, "elo": 1552},
        "jamaica": {"rank": 60, "elo": 1618}, "switzerland": {"rank": 15, "elo": 1875},
        "qatar": {"rank": 53, "elo": 1652}, "canada": {"rank": 52, "elo": 1658},
        "bosnia-herzegovina": {"rank": 64, "elo": 1592}, "north macedonia": {"rank": 65, "elo": 1585},
        "kosovo": {"rank": 78, "elo": 1545}, "sweden": {"rank": 28, "elo": 1788},
        "poland": {"rank": 29, "elo": 1782}, "iran": {"rank": 27, "elo": 1792},
        "gambia": {"rank": 75, "elo": 1520}, "lebanon": {"rank": 77, "elo": 1490},
        "sudan": {"rank": 74, "elo": 1475}, "south africa": {"rank": 49, "elo": 1675},
        "nicaragua": {"rank": 73, "elo": 1480}, "iraq": {"rank": 55, "elo": 1642},
        "andorra": {"rank": 76, "elo": 1200}, "trinidad and tobago": {"rank": 68, "elo": 1565},
        "turkey": {"rank": 24, "elo": 1812},
    }
    norm_team = normalize(team)
    matches = []
    for k, v in elo_data.items():
        kn = normalize(k)
        if norm_team in kn or kn in norm_team or kn == norm_team:
            matches.append({"elo_name": k, "norm": kn, "data": v})
    return jsonify({"query": team, "normalized": norm_team, "matches": matches, "total_elo": len(elo_data)})

@app.route("/debug_tavily")
def debug_tavily():
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return jsonify({"error": "TAVILY_API_KEY no definida"})
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": "FIFA World Cup 2026 injury news",
                "search_depth": "basic",
                "max_results": 2
            },
            timeout=20
        )
        return jsonify({"status": r.status_code, "ok": r.ok, "response": r.json() if r.ok else r.text[:300]})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/debug_gemini")
def debug_gemini():
    resultado = groq_search('Responde solo con JSON: {"saludo": "hola", "estado": "ok"}', max_tokens=100)
    return jsonify({"groq_response": resultado, "ok": resultado is not None})

@app.route("/health")
def health():
    return jsonify({"ok": True})

@app.route("/clear_cache")
def clear_cache():
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM analisis_cache")
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

# ── TAVILY HELPER ────────────────────────────────────────────────────────────

def tavily_search(query, max_results=5):
    """Busca noticias en tiempo real via Tavily."""
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        return ""
    try:
        r = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "basic",
                "max_results": max_results,
                "include_answer": True
            },
            timeout=20
        )
        if not r.ok:
            print(f"Tavily error {r.status_code}: {r.text[:200]}")
            return ""
        data = r.json()
        # Combinar answer + snippets de resultados
        partes = []
        if data.get("answer"):
            partes.append(data["answer"])
        for res in data.get("results", []):
            if res.get("content"):
                partes.append(f"- {res['title']}: {res['content'][:300]}")
        return "\n".join(partes)
    except Exception as e:
        print(f"tavily_search error: {e}")
        return ""

# ── GROQ HELPER ──────────────────────────────────────────────────────────────

def groq_search(prompt, max_tokens=4000):
    """Llama a Groq con llama-3.3-70b para análisis de texto."""
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        return None
    try:
        r = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.1
            },
            timeout=60
        )
        if not r.ok:
            print(f"Groq error {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"groq_search error: {e}")
        return None

# ── WC BONUS con GEMINI ───────────────────────────────────────────────────────

@app.route("/wc_bonus_v2")
def wc_bonus_v2():
    """Version de wc_bonus usando Gemini con Google Search."""
    ck = "wc_bonus_v2_data"
    now_t = time.time()
    if ck in _cache and now_t - _cache[ck][1] < 86400:
        return jsonify(_cache[ck][0])

    jugadores_clave = {
        "Argentina": ["Lionel Messi", "Lautaro Martinez", "Enzo Fernandez"],
        "France": ["Kylian Mbappe", "Ousmane Dembele", "Antoine Griezmann"],
        "Spain": ["Lamine Yamal", "Alvaro Morata", "Rodri"],
        "England": ["Harry Kane", "Jude Bellingham", "Bukayo Saka"],
        "Brazil": ["Vinicius Junior", "Raphinha", "Neymar"],
        "Germany": ["Florian Wirtz", "Jamal Musiala", "Kai Havertz"],
        "Portugal": ["Cristiano Ronaldo", "Bruno Fernandes", "Rafael Leao"],
        "Netherlands": ["Virgil van Dijk", "Cody Gakpo", "Frenkie de Jong"],
        "Belgium": ["Kevin De Bruyne", "Romelu Lukaku", "Jeremy Doku"],
        "Uruguay": ["Darwin Nunez", "Rodrigo Bentancur", "Jose Maria Gimenez"],
        "Colombia": ["Luis Diaz", "James Rodriguez", "Jhon Cordoba"],
        "Croatia": ["Luka Modric", "Mateo Kovacic", "Josko Gvardiol"],
        "Morocco": ["Achraf Hakimi", "Brahim Diaz", "Hakim Ziyech"],
        "Japan": ["Takefusa Kubo", "Ritsu Doan", "Wataru Endo"],
        "USA": ["Christian Pulisic", "Weston McKennie", "Tyler Adams"],
        "Mexico": ["Santiago Gimenez", "Edson Alvarez", "Raul Jimenez"],
        "Senegal": ["Sadio Mane", "Nicolas Jackson", "Ismaila Sarr"],
        "Ecuador": ["Moises Caicedo", "Gonzalo Plata", "Enner Valencia"],
        "Canada": ["Alphonso Davies", "Jonathan David", "Stephen Eustaquio"],
    }

    prompt = (
        "Evalua la forma ACTUAL (junio 2026) de los siguientes jugadores del Mundial 2026. "
        "Busca noticias recientes de forma, lesiones o recuperaciones. "
        "Para cada SELECCION calcula un bonus_total: gran forma=+10 a +40, normal=0, mala forma o lesion=-10 a -30. "
        "Jugadores: " + json.dumps(jugadores_clave, ensure_ascii=False) + ". "
        "Responde SOLO con JSON valido sin markdown: "
        '{"Argentina": {"jugadores": [{"nombre": "X", "forma": "buena/normal/mala", "nota": "razon breve"}], "bonus_total": 15, "resumen": "texto"}}. '
        "Solo incluye selecciones con informacion confiable reciente."
    )

    texto = groq_search(prompt, max_tokens=4000)
    if not texto:
        return jsonify({})
    try:
        import re as _re
        m = _re.search(r'\{[\s\S]*\}', texto)
        if m:
            bonus = json.loads(m.group(0))
            _cache[ck] = (bonus, now_t)
            return jsonify(bonus)
    except Exception as e:
        print(f"wc_bonus_v2 parse error: {e}")
    return jsonify({})


# ── WC TARJETAS ───────────────────────────────────────────────────────────────

@app.route("/wc_tarjetas")
def wc_tarjetas():
    """
    Tarjetas amarillas acumuladas por seleccion durante el Mundial.
    Actualizar manualmente despues de cada partido o via /wc_briefing_diario.
    Formato: { "Argentina": { "jugadores_en_riesgo": ["Enzo Fernandez"], "penalty_elo": -30 }, ... }
    """
    ck = "wc_tarjetas_data"
    if ck in _cache:
        return jsonify(_cache[ck])
    # Dato hardcodeado inicial — se actualiza via /wc_briefing_diario
    data = {}
    return jsonify(data)

@app.route("/wc_tarjetas_update", methods=["POST"])
def wc_tarjetas_update():
    """Actualiza el cache de tarjetas. Llamado por el briefing diario."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "no data"}), 400
    _cache["wc_tarjetas_data"] = data
    return jsonify({"ok": True, "equipos": list(data.keys())})


# ── WC ARBITROS ───────────────────────────────────────────────────────────────

@app.route("/wc_arbitros")
def wc_arbitros():
    """
    Arbitros designados para cada partido del Mundial.
    Se actualiza via /wc_briefing_diario.
    Formato: { "partido_id": { "arbitro": "Nombre", "pais": "Pais", "perfil": "descripcion" }, ... }
    """
    ck = "wc_arbitros_data"
    if ck in _cache:
        return jsonify(_cache[ck])
    return jsonify({})


# ── WC CLIMA / SEDES ──────────────────────────────────────────────────────────

@app.route("/wc_clima")
def wc_clima():
    """
    Temperatura y condiciones climaticas por sede del Mundial 2026.
    Datos estaticos basados en promedios historicos junio-julio.
    """
    sedes = {
        # USA
        "New York/New Jersey": {"estadio": "MetLife Stadium", "ciudad": "East Rutherford, NJ", "temp_junio": 24, "temp_julio": 28, "humedad": "alta", "modificador": "normal", "zona_horaria": "UTC-4", "hora_arg_offset": +1},
        "Los Angeles": {"estadio": "SoFi Stadium", "ciudad": "Inglewood, CA", "temp_junio": 22, "temp_julio": 26, "humedad": "baja", "modificador": "normal", "zona_horaria": "UTC-7", "hora_arg_offset": +4},
        "Dallas": {"estadio": "AT&T Stadium", "ciudad": "Arlington, TX", "temp_junio": 36, "temp_julio": 38, "humedad": "alta", "modificador": "calor_extremo", "zona_horaria": "UTC-5", "hora_arg_offset": +2},
        "San Francisco": {"estadio": "Levi's Stadium", "ciudad": "Santa Clara, CA", "temp_junio": 19, "temp_julio": 21, "humedad": "media", "modificador": "normal", "zona_horaria": "UTC-7", "hora_arg_offset": +4},
        "Miami": {"estadio": "Hard Rock Stadium", "ciudad": "Miami Gardens, FL", "temp_junio": 33, "temp_julio": 35, "humedad": "muy_alta", "modificador": "calor_humedo", "zona_horaria": "UTC-4", "hora_arg_offset": +1},
        "Atlanta": {"estadio": "Mercedes-Benz Stadium", "ciudad": "Atlanta, GA", "temp_junio": 31, "temp_julio": 33, "humedad": "alta", "modificador": "calor", "zona_horaria": "UTC-4", "hora_arg_offset": +1},
        "Seattle": {"estadio": "Lumen Field", "ciudad": "Seattle, WA", "temp_junio": 17, "temp_julio": 20, "humedad": "media", "modificador": "normal", "zona_horaria": "UTC-7", "hora_arg_offset": +4},
        "Houston": {"estadio": "NRG Stadium", "ciudad": "Houston, TX", "temp_junio": 35, "temp_julio": 37, "humedad": "muy_alta", "modificador": "calor_humedo", "zona_horaria": "UTC-5", "hora_arg_offset": +2},
        "Philadelphia": {"estadio": "Lincoln Financial Field", "ciudad": "Philadelphia, PA", "temp_junio": 27, "temp_julio": 30, "humedad": "alta", "modificador": "normal", "zona_horaria": "UTC-4", "hora_arg_offset": +1},
        "Kansas City": {"estadio": "Arrowhead Stadium", "ciudad": "Kansas City, MO", "temp_junio": 31, "temp_julio": 33, "humedad": "media", "modificador": "calor", "zona_horaria": "UTC-5", "hora_arg_offset": +2},
        "Boston": {"estadio": "Gillette Stadium", "ciudad": "Foxborough, MA", "temp_junio": 22, "temp_julio": 25, "humedad": "media", "modificador": "normal", "zona_horaria": "UTC-4", "hora_arg_offset": +1},
        # Canada
        "Toronto": {"estadio": "BMO Field", "ciudad": "Toronto, ON", "temp_junio": 22, "temp_julio": 25, "humedad": "media", "modificador": "normal", "zona_horaria": "UTC-4", "hora_arg_offset": +1},
        "Vancouver": {"estadio": "BC Place", "ciudad": "Vancouver, BC", "temp_junio": 16, "temp_julio": 19, "humedad": "baja", "modificador": "normal", "zona_horaria": "UTC-7", "hora_arg_offset": +4},
        # Mexico
        "Ciudad de Mexico": {"estadio": "Estadio Azteca", "ciudad": "Ciudad de Mexico", "temp_junio": 19, "temp_julio": 18, "humedad": "media", "modificador": "altitud", "altitud_m": 2240, "zona_horaria": "UTC-6", "hora_arg_offset": +3},
        "Guadalajara": {"estadio": "Estadio Akron", "ciudad": "Zapopan, Jalisco", "temp_junio": 22, "temp_julio": 21, "humedad": "media", "modificador": "altitud", "altitud_m": 1566, "zona_horaria": "UTC-6", "hora_arg_offset": +3},
        "Monterrey": {"estadio": "Estadio BBVA", "ciudad": "Monterrey, NL", "temp_junio": 36, "temp_julio": 38, "humedad": "baja", "modificador": "calor_extremo", "zona_horaria": "UTC-6", "hora_arg_offset": +3},
    }
    return jsonify(sedes)


# ── WC BRIEFING DIARIO ────────────────────────────────────────────────────────

@app.route("/wc_briefing_diario")
def wc_briefing_diario():
    """
    Ejecuta el briefing diario del Mundial usando Gemini con Google Search.
    Busca: lesiones nuevas, incorporaciones FIFA, tarjetas acumuladas, arbitros designados.
    Cachea por 20 horas. Para forzar: ?force=1
    """
    ck = "wc_briefing_data"
    now_t = time.time()
    force = request.args.get("force", "0") == "1"

    if not force and ck in _cache and now_t - _cache[ck][1] < 72000:
        cached = _cache[ck][0]
        cached["cached"] = True
        return jsonify(cached)

    hoy = datetime.now().strftime("%d de %B de %Y")

    # ── Buscar noticias en tiempo real con Tavily ──────────────────────────────
    noticias_lesiones = tavily_search(
        f"World Cup 2026 injury player out squad update {datetime.now().strftime('%B %Y')}",
        max_results=5
    )
    noticias_tarjetas = tavily_search(
        f"World Cup 2026 yellow cards suspension players {datetime.now().strftime('%B %Y')}",
        max_results=3
    )
    noticias_arbitros = tavily_search(
        f"FIFA World Cup 2026 referee assignments designated matches {datetime.now().strftime('%B %Y')}",
        max_results=3
    )

    # ── 1. Lesiones e incorporaciones ──────────────────────────────────────────
    ctx_lesiones = f"\nNoticias recientes:\n{noticias_lesiones[:1500]}" if noticias_lesiones else ""
    prompt_lesiones = (
        f"Fecha de hoy: {hoy}.{ctx_lesiones}\n\n"
        "Extrae jugadores lesionados, desconvocados o incorporados al Mundial 2026. "
        "Responde SOLO con JSON valido sin markdown: "
        '{"lesiones_nuevas": [{"jugador": "Nombre", "seleccion": "Pais_en_ingles", '
        '"descripcion": "lesion", "penalty_elo_sugerido": -30}], '
        '"incorporaciones": [{"jugador": "Nombre", "seleccion": "Pais_en_ingles", '
        '"reemplaza_a": "Nombre", "bonus_elo_sugerido": 15}], '
        '"recuperados": [{"jugador": "Nombre", "seleccion": "Pais_en_ingles", "nota": "texto"}]}. '
        "Si no hay novedades, devuelve listas vacias."
    )

    # ── 2. Tarjetas acumuladas ─────────────────────────────────────────────────
    ctx_tarjetas = f"\nNoticias recientes:\n{noticias_tarjetas[:800]}" if noticias_tarjetas else ""
    prompt_tarjetas = (
        f"Fecha de hoy: {hoy}.{ctx_tarjetas}\n\n"
        "Extrae jugadores con tarjetas amarillas acumuladas en el Mundial 2026. "
        "2 amarillas en grupos = suspension en octavos. "
        "Responde SOLO con JSON valido sin markdown: "
        '{"tarjetas": {"Nombre_seleccion_ingles": {"jugadores_en_riesgo": ["Jugador1"], '
        '"suspendidos": ["Jugador2"], "penalty_elo": -25}}}. '
        "Si no hay info real, devuelve {}."
    )

    # ── 3. Arbitros ────────────────────────────────────────────────────────────
    ctx_arbitros = f"\nNoticias recientes:\n{noticias_arbitros[:800]}" if noticias_arbitros else ""
    prompt_arbitros = (
        f"Fecha de hoy: {hoy}.{ctx_arbitros}\n\n"
        "Extrae arbitros designados para partidos del Mundial 2026. "
        "Responde SOLO con JSON valido sin markdown: "
        '{"arbitros": {"Local vs Visitante": {"arbitro": "Nombre Completo", "pais": "Pais", '
        '"partidos_dirigidos": 10, "tarjetas_por_partido": 3.2, "perfil": "estricto/normal/permisivo"}}}. '
        "Si no hay designaciones oficiales, devuelve {}."
    )

    resultado = {
        "fecha": hoy,
        "lesiones_nuevas": [],
        "incorporaciones": [],
        "recuperados": [],
        "tarjetas": {},
        "arbitros": {},
        "errores": []
    }

    # Ejecutar las 3 busquedas
    for nombre, prompt, clave_principal in [
        ("lesiones", prompt_lesiones, "lesiones_nuevas"),
        ("tarjetas", prompt_tarjetas, "tarjetas"),
        ("arbitros", prompt_arbitros, "arbitros"),
    ]:
        texto = groq_search(prompt, max_tokens=2000)
        if not texto:
            resultado["errores"].append(f"Groq no disponible para {nombre}")
            continue
        try:
            import re as _re
            m = _re.search(r'\{[\s\S]*\}', texto)
            if m:
                datos = json.loads(m.group(0))
                if nombre == "lesiones":
                    resultado["lesiones_nuevas"] = datos.get("lesiones_nuevas", [])
                    resultado["incorporaciones"] = datos.get("incorporaciones", [])
                    resultado["recuperados"] = datos.get("recuperados", [])
                elif nombre == "tarjetas":
                    tarjetas_data = datos.get("tarjetas", {})
                    resultado["tarjetas"] = tarjetas_data
                    # Actualizar cache de tarjetas
                    _cache["wc_tarjetas_data"] = tarjetas_data
                elif nombre == "arbitros":
                    resultado["arbitros"] = datos.get("arbitros", {})
                    _cache["wc_arbitros_data"] = datos.get("arbitros", {})
        except Exception as e:
            resultado["errores"].append(f"Parse error {nombre}: {str(e)}")
            resultado[f"raw_{nombre}"] = texto[:500]

    # Actualizar wc_ausencias dinamicamente con lesiones nuevas
    if resultado["lesiones_nuevas"]:
        ausencias_actuales = {}
        try:
            # Obtener ausencias actuales del endpoint
            with app.test_request_context():
                resp = wc_ausencias()
                ausencias_actuales = json.loads(resp.get_data())
        except:
            pass
        for lesion in resultado["lesiones_nuevas"]:
            sel = lesion.get("seleccion", "")
            if sel and sel not in ausencias_actuales:
                ausencias_actuales[sel] = {"ausentes": [], "penalty_total": 0}
            if sel:
                ausencia_entry = {
                    "nombre": lesion.get("jugador", ""),
                    "posicion": "desconocida",
                    "valor_m": 30,
                    "criticidad": 0.35,
                    "penalty_elo": lesion.get("penalty_elo_sugerido", -25)
                }
                ausencias_actuales[sel]["ausentes"].append(ausencia_entry)
                ausencias_actuales[sel]["penalty_total"] = sum(
                    a.get("penalty_elo", 0) for a in ausencias_actuales[sel]["ausentes"]
                )
        _cache["wc_ausencias_briefing"] = ausencias_actuales

    resultado["cached"] = False
    _cache[ck] = (resultado, now_t)
    return jsonify(resultado)


@app.route("/wc_ausencias_dinamicas")
def wc_ausencias_dinamicas():
    """
    Devuelve ausencias base + lesiones nuevas del briefing.
    Filtra jugadores del briefing que YA están en el plantel convocado (evita falsos positivos).
    """
    # Ausencias base (hardcodeadas)
    resp_base = wc_ausencias()
    base = json.loads(resp_base.get_data())

    # Merge con actualizaciones del briefing si existen
    briefing_aus = _cache.get("wc_ausencias_briefing", {})
    if not briefing_aus:
        return jsonify(base)

    # Cargar plantel convocado para filtrar falsos positivos
    plantel_nombres = {}
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT seleccion, nombre FROM wc_planteles")
        for row in c.fetchall():
            sel, nom = row
            if sel not in plantel_nombres:
                plantel_nombres[sel] = set()
            plantel_nombres[sel].add(nom.lower().strip())
        conn.close()
    except:
        pass

    for seleccion, datos in briefing_aus.items():
        convocados = plantel_nombres.get(seleccion, set())
        if seleccion not in base:
            base[seleccion] = {"ausentes": [], "penalty_total": 0}
        nombres_base = {a["nombre"] for a in base[seleccion].get("ausentes", [])}
        for ausente in datos.get("ausentes", []):
            nombre = ausente["nombre"]
            # Saltar si ya está en la lista base o si está en el plantel convocado
            if nombre in nombres_base:
                continue
            if nombre.lower().strip() in convocados:
                continue  # Está convocado, no es baja real
            base[seleccion]["ausentes"].append(ausente)
            base[seleccion]["penalty_total"] = sum(
                a.get("penalty_elo", 0) for a in base[seleccion]["ausentes"]
            )
    return jsonify(base)


if __name__=="__main__":
    app.run(debug=True)