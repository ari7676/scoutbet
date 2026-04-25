"""
MatchIQ — Backend Flask v5
- Login
- SQLite para guardar predicciones
- Verificacion automatica de aciertos
"""
from flask import Flask, jsonify, render_template, request, redirect, url_for, session
from datetime import datetime, timedelta
from functools import wraps
import requests, time, math, os, sqlite3, json

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "matchiq-secret-key-2026-xyz")

APP_USER = os.environ.get("APP_USER", "matchiq")
APP_PASS = os.environ.get("APP_PASS", "futbol2026")

# Path DB - en Railway se monta volumen en /data
DB_PATH = os.environ.get("DB_PATH", "matchiq.db")

FD_KEY = "e28d6269df9441fea1b6a19548e982c6"
FD_URL = "https://api.football-data.org/v4"
FD_HEADERS = {"X-Auth-Token": FD_KEY}

AS_KEY = "fb49b7a70ea23977f8e7711c5ed027b1"
AS_URL = "https://v3.football.api-sports.io"
AS_HEADERS = {"x-apisports-key": AS_KEY}

LIGAS = {
    "PL":  {"nombre": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League",  "as_id": 39,  "season": 2025, "source": "fd"},
    "PD":  {"nombre": "🇪🇸 La Liga",           "as_id": 140, "season": 2025, "source": "fd"},
    "SA":  {"nombre": "🇮🇹 Serie A",            "as_id": 135, "season": 2025, "source": "as"},
    "BL1": {"nombre": "🇩🇪 Bundesliga",         "as_id": 78,  "season": 2025, "source": "fd"},
    "FL1": {"nombre": "🇫🇷 Ligue 1",            "as_id": 61,  "season": 2025, "source": "fd"},
    "CL":  {"nombre": "🏆 Champions League",    "as_id": 2,   "season": 2025, "source": "fd"},
    "BSA": {"nombre": "🇧🇷 Brasileirão",        "as_id": 71,  "season": 2026, "source": "fd"},
}

_cache = {}
CACHE_TTL = 300
CACHE_TTL_AS = 43200
CACHE_TTL_FX_STATS = 604800  # 7 dias para stats por partido


# ── DATABASE ──────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
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


def save_prediction(match_id, liga, fecha, home, away, veredicto):
    """Guarda la prediccion cuando se analiza un partido."""
    mp_text = veredicto.get("mercado_principal", "")
    comb_text = veredicto.get("combinable", "")

    # Extraer prob y cuota del mercado principal
    mp_prob, mp_cuota = _extract_prob_cuota(mp_text)
    comb_prob, comb_cuota = _extract_prob_cuota(comb_text)

    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""INSERT OR REPLACE INTO predicciones
            (match_id, liga, fecha, home, away, mercado_principal, mp_prob, mp_cuota,
             combinable, comb_prob, comb_cuota, creado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (match_id, liga, fecha, home, away, mp_text, mp_prob, mp_cuota,
             comb_text, comb_prob, comb_cuota, datetime.utcnow().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Error saving prediction: {e}")


def _extract_prob_cuota(text):
    """De 'Goles Over 2.5 (72% · cuota @1.27)' extrae (72, 1.27)."""
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
    """Verifica si el mercado principal y combinable acertaron."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT mercado_principal, combinable, home, away FROM predicciones WHERE match_id=?", (match_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return

    mp, comb, home, away = row
    mp_ok = _check_mercado(mp, home_goals, away_goals, home, away)
    comb_ok = _check_mercado(comb, home_goals, away_goals, home, away) if comb else None

    c.execute("""UPDATE predicciones SET resultado_home=?, resultado_away=?,
        mp_acertado=?, comb_acertado=?, verificado=1 WHERE match_id=?""",
        (home_goals, away_goals,
         1 if mp_ok else (0 if mp_ok is False else None),
         1 if comb_ok else (0 if comb_ok is False else None),
         match_id))
    conn.commit()
    conn.close()


def _check_mercado(texto, hg, ag, home, away):
    """Verifica si el mercado acerto segun el resultado."""
    if not texto: return None
    t = texto.lower()
    total = hg + ag

    # 1X2
    if "resultado final" in t:
        if home.lower() in t:
            return hg > ag
        elif away.lower() in t:
            return ag > hg
        elif "empate" in t:
            return hg == ag

    # Doble Chance
    if "doble oportunidad" in t or "1x" in t or "x2" in t:
        if "1x" in t or home.lower() in t:
            return hg >= ag
        elif "x2" in t or away.lower() in t:
            return ag >= hg

    # Over/Under
    if "over 2.5" in t: return total > 2.5
    if "under 2.5" in t: return total < 2.5
    if "over 1.5" in t: return total > 1.5
    if "under 1.5" in t: return total < 1.5
    if "over 3.5" in t: return total > 3.5

    # BTTS
    if "ambos anotan" in t or "btts" in t:
        return hg > 0 and ag > 0

    # Goles equipo Over 0.5
    if "over 0.5" in t:
        if home.lower() in t: return hg > 0
        if away.lower() in t: return ag > 0

    # No 0-0
    if "no termina 0-0" in t:
        return total > 0

    return None


def fd_get(ep, params=None):
    ck="fd:"+ep+str(params or ""); now=time.time()
    if ck in _cache and now-_cache[ck][1]<CACHE_TTL: return _cache[ck][0]
    try:
        r=requests.get(f"{FD_URL}{ep}",headers=FD_HEADERS,params=params,timeout=15)
        if r.status_code==429: time.sleep(6); r=requests.get(f"{FD_URL}{ep}",headers=FD_HEADERS,params=params,timeout=15)
        d=r.json(); _cache[ck]=(d,now); return d
    except Exception as e: return {"error":str(e)}

def as_get(ep, params=None):
    ck="as:"+ep+str(params or ""); now=time.time()
    # Cache largo para fixtures/statistics
    ttl = CACHE_TTL_FX_STATS if "fixtures/statistics" in ep else CACHE_TTL_AS
    if ck in _cache and now-_cache[ck][1]<ttl: return _cache[ck][0]
    try:
        r=requests.get(f"{AS_URL}{ep}",headers=AS_HEADERS,params=params,timeout=15)
        if r.status_code==429: return {"error":"rate_limit"}
        d=r.json()
        if d.get("response") is not None: _cache[ck]=(d,now)
        return d
    except Exception as e: return {"error":str(e)}


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("auth"): return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper

def api_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("auth"): return jsonify({"error":"unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper

@app.route("/login", methods=["GET","POST"])
def login():
    error=None
    if request.method=="POST":
        u=request.form.get("user","").strip();p=request.form.get("pass","").strip()
        if u==APP_USER and p==APP_PASS:
            session["auth"]=True;session.permanent=True
            return redirect(url_for("index"))
        error="Credenciales incorrectas"
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.clear();return redirect(url_for("login"))

@app.route("/")
@login_required
def index():
    return render_template("index.html", ligas={k:v["nombre"] for k,v in LIGAS.items()})


@app.route("/analisis_avanzado/<codigo>/<int:match_id>")
@api_login_required
def analisis_avanzado(codigo, match_id):
    """Trae stats reales (corners, tarjetas, posesion, faltas, remates) de los ultimos 5 partidos de cada equipo."""
    liga = LIGAS.get(codigo, {})
    as_id = liga.get("as_id")
    season = liga.get("season")
    if not as_id:
        return jsonify({"error": "Liga sin api-sports id"})

    # Obtener nombres de equipos del partido
    if liga.get("source", "fd") == "fd":
        md = fd_get(f"/matches/{match_id}")
        if "error" in md or "id" not in md:
            return jsonify({"error": "Partido no encontrado"})
        hn = md["homeTeam"]["name"]
        an = md["awayTeam"]["name"]
    else:
        fx_data = as_get("/fixtures", {"id": match_id})
        if "error" in fx_data or not fx_data.get("response"):
            return jsonify({"error": "Partido no encontrado"})
        teams = fx_data["response"][0].get("teams", {})
        hn = teams.get("home", {}).get("name", "")
        an = teams.get("away", {}).get("name", "")

    # Buscar IDs api-sports
    hid = _search_as(hn, as_id, season)
    aid = _search_as(an, as_id, season)
    if not hid or not aid:
        return jsonify({"error": "No se encontraron equipos en api-sports"})

    # Stats avanzadas de ultimos 5 partidos
    home_avg = _avg_fixture_stats(hid, as_id, season)
    away_avg = _avg_fixture_stats(aid, as_id, season)

    # Mercados adicionales basados en stats avanzadas
    mercados_extra = _mercados_avanzados(home_avg, away_avg, hn, an)

    return jsonify({
        "home_team": hn,
        "away_team": an,
        "home_stats": home_avg,
        "away_stats": away_avg,
        "mercados": mercados_extra,
    })


def _avg_fixture_stats(team_id, league_id, season):
    """Promedia stats de los ultimos 5 partidos del equipo."""
    fx_data = as_get("/fixtures", {"team": team_id, "season": season, "league": league_id, "last": 5})
    if "error" in fx_data or not fx_data.get("response"):
        return None

    fixtures = fx_data["response"]
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
        time.sleep(0.3)  # Rate limit

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


def _mercados_avanzados(home, away, hn, an):
    """Genera mercados a partir de stats avanzadas."""
    if not home or not away: return []
    mercados = []

    # Corners totales
    if home.get("corners_pj") != "—" and away.get("corners_pj") != "—":
        total_corners = home["corners_pj"] + away["corners_pj"]
        # Over 9.5 corners
        if total_corners >= 10:
            p = min(85, round(50 + (total_corners - 9.5) * 12))
            mercados.append({
                "mercado": "Corners Totales Over 9.5",
                "prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "CORNERS",
                "aprobado": p >= 65,
                "sintesis": f"Promedio combinado de corners: {round(total_corners,1)}/partido. {hn} {home['corners_pj']} y {an} {away['corners_pj']}."
            })
        if total_corners <= 9:
            p = min(85, round(50 + (9.5 - total_corners) * 12))
            mercados.append({
                "mercado": "Corners Totales Under 9.5",
                "prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "CORNERS",
                "aprobado": p >= 65,
                "sintesis": f"Promedio combinado bajo: {round(total_corners,1)} corners/partido."
            })

    # Tarjetas amarillas totales
    if home.get("tarjetas_amarillas_pj") != "—" and away.get("tarjetas_amarillas_pj") != "—":
        total_y = home["tarjetas_amarillas_pj"] + away["tarjetas_amarillas_pj"]
        if total_y >= 4.5:
            p = min(80, round(50 + (total_y - 4.5) * 15))
            mercados.append({
                "mercado": "Tarjetas Amarillas Over 4.5",
                "prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "CARDS",
                "aprobado": p >= 65,
                "sintesis": f"Promedio combinado: {round(total_y,1)} amarillas/partido. {hn} {home['tarjetas_amarillas_pj']} y {an} {away['tarjetas_amarillas_pj']}."
            })
        if total_y <= 4:
            p = min(80, round(50 + (4.5 - total_y) * 15))
            mercados.append({
                "mercado": "Tarjetas Amarillas Under 4.5",
                "prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "CARDS",
                "aprobado": p >= 65,
                "sintesis": f"Promedio combinado bajo: {round(total_y,1)} amarillas/partido."
            })

    # Posesion - Local domina
    if home.get("posesion_avg") != "—" and away.get("posesion_avg") != "—":
        if home["posesion_avg"] >= 55 and away["posesion_avg"] < home["posesion_avg"]:
            p = min(85, round(home["posesion_avg"] + 10))
            mercados.append({
                "mercado": f"Mayor posesión — {hn}",
                "prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "POSS",
                "aprobado": p >= 65,
                "sintesis": f"{hn} promedia {home['posesion_avg']}% de posesión vs {away['posesion_avg']}% de {an}."
            })
        elif away["posesion_avg"] >= 55:
            p = min(85, round(away["posesion_avg"]))
            mercados.append({
                "mercado": f"Mayor posesión — {an}",
                "prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "POSS",
                "aprobado": p >= 65,
                "sintesis": f"{an} promedia {away['posesion_avg']}% de posesión vs {home['posesion_avg']}% de {hn}."
            })

    # Equipo con mas remates
    if home.get("remates_pj") != "—" and away.get("remates_pj") != "—":
        diff = abs(home["remates_pj"] - away["remates_pj"])
        if diff >= 3:
            mas = hn if home["remates_pj"] > away["remates_pj"] else an
            mas_v = max(home["remates_pj"], away["remates_pj"])
            men_v = min(home["remates_pj"], away["remates_pj"])
            p = min(80, round(55 + diff * 3))
            mercados.append({
                "mercado": f"Más remates — {mas}",
                "prob": p, "riesgo": 100-p, "cuota": _cuota(p), "tipo": "SHOTS",
                "aprobado": p >= 65,
                "sintesis": f"Diferencia clara de remates: {mas_v} vs {men_v}/partido."
            })

    mercados.sort(key=lambda x: x["prob"], reverse=True)
    return mercados


@app.route("/partidos/<codigo>")
@api_login_required
def partidos(codigo):
    liga = LIGAS.get(codigo, {})
    source = liga.get("source", "fd")

    hoy = datetime.utcnow()

    # Obtener predicciones ya guardadas
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT match_id, mp_acertado, comb_acertado, verificado FROM predicciones")
    preds = {row[0]: {"mp_ok":row[1], "comb_ok":row[2], "verif":row[3]} for row in c.fetchall()}
    conn.close()

    matches = []

    if source == "as":
        # Usar api-sports
        as_id = liga.get("as_id")
        season = liga.get("season")
        # Trae fixtures proximos y recientes
        desde = (hoy - timedelta(days=2)).strftime("%Y-%m-%d")
        hasta = (hoy + timedelta(days=30)).strftime("%Y-%m-%d")
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
        # Football-data (default)
        desde = (hoy - timedelta(days=2)).strftime("%Y-%m-%d")
        hasta = (hoy + timedelta(days=30)).strftime("%Y-%m-%d")
        data = fd_get(f"/competitions/{codigo}/matches", {"limit": 80})
        if "error" in data:
            return jsonify({"response": [], "error": data["error"]})
        for m in data.get("matches", []):
            refs = m.get("referees", [])
            score = m.get("score", {}).get("fullTime", {})
            estado = m["status"]
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

    return jsonify({"response": matches, "total": len(matches)})


@app.route("/estadisticas")
@login_required
def estadisticas():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT COUNT(*), SUM(mp_acertado), SUM(comb_acertado),
                 COUNT(CASE WHEN verificado=1 AND mp_acertado IS NOT NULL THEN 1 END),
                 COUNT(CASE WHEN verificado=1 AND comb_acertado IS NOT NULL THEN 1 END)
                 FROM predicciones WHERE verificado=1""")
    total, mp_ok, comb_ok, mp_total, comb_total = c.fetchone()

    # Detalle de cada partido verificado
    c.execute("""SELECT match_id, fecha, home, away, resultado_home, resultado_away,
                 mercado_principal, mp_acertado, combinable, comb_acertado, liga
                 FROM predicciones WHERE verificado=1 ORDER BY fecha DESC""")
    detalle = []
    for row in c.fetchall():
        detalle.append({
            "match_id": row[0], "fecha": row[1], "home": row[2], "away": row[3],
            "score": f"{row[4]}-{row[5]}",
            "mercado_principal": row[6], "mp_acertado": row[7],
            "combinable": row[8], "comb_acertado": row[9],
            "liga": row[10]
        })

    conn.close()
    return jsonify({
        "total_verificados": total or 0,
        "mp_aciertos": mp_ok or 0,
        "mp_total": mp_total or 0,
        "mp_pct": round((mp_ok or 0)/(mp_total or 1)*100, 1) if mp_total else 0,
        "comb_aciertos": comb_ok or 0,
        "comb_total": comb_total or 0,
        "comb_pct": round((comb_ok or 0)/(comb_total or 1)*100, 1) if comb_total else 0,
        "detalle": detalle,
    })


def _do_analyze(codigo, match_id):
    """Logica de analisis reutilizable. Detecta source de la liga."""
    liga = LIGAS.get(codigo, {})
    source = liga.get("source", "fd")
    if source == "as":
        return _do_analyze_as(codigo, match_id, liga)
    return _do_analyze_fd(codigo, match_id)


def _do_analyze_as(codigo, match_id, liga):
    """Analisis usando api-sports (para Serie A)."""
    as_id = liga.get("as_id")
    season = liga.get("season")

    # Detalle del partido
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

    # Standings
    sd = as_get("/standings", {"league": as_id, "season": season})
    standings = []
    if not "error" in sd and sd.get("response"):
        try:
            standings = sd["response"][0]["league"]["standings"][0]
        except: pass

    hp = _find_as(standings, hid)
    ap = _find_as(standings, aid)

    # Stats por equipo
    hs = as_get("/teams/statistics", {"league": as_id, "season": season, "team": hid}).get("response")
    aws = as_get("/teams/statistics", {"league": as_id, "season": season, "team": aid}).get("response")

    # Forma reciente (ultimos 10)
    hfx = as_get("/fixtures", {"team": hid, "season": season, "league": as_id, "last": 10})
    afx = as_get("/fixtures", {"team": aid, "season": season, "league": as_id, "last": 10})
    hf = _forma_as(hfx.get("response", []), hid)
    af = _forma_as(afx.get("response", []), aid)
    hl3 = _u3_as(hfx.get("response", []), hid)
    al3 = _u3_as(afx.get("response", []), aid)

    # H2H
    h2h_data = as_get("/fixtures/headtohead", {"h2h": f"{hid}-{aid}", "last": 5})
    h2h = _h2h_as(h2h_data.get("response", []), hid, aid)

    # Goleadores
    sc_data = as_get("/players/topscorers", {"league": as_id, "season": season})
    jh = _enrich(_jugadores_as(sc_data.get("response", []), hid), hn, hf)
    ja = _enrich(_jugadores_as(sc_data.get("response", []), aid), an, af)

    # Convertir hp/ap a estructura compatible con _analisis
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

    estado = md_compat.get("status", "")
    if estado == "NS":
        save_prediction(match_id, codigo, fixture.get("date", ""), hn, an, resultado["veredicto"])
    elif estado in ("FT", "AET", "PEN"):
        save_prediction(match_id, codigo, fixture.get("date", ""), hn, an, resultado["veredicto"])
        goals = fx.get("goals", {})
        if goals.get("home") is not None:
            verify_prediction(match_id, goals["home"], goals["away"])
    return resultado


def _find_as(standings, tid):
    for s in standings:
        if s.get("team", {}).get("id") == tid: return s
    return None


def _conv_pos_as(s):
    """Convierte standing api-sports a formato compatible."""
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
    """Convierte stats home/away."""
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
    """Forma reciente a partir de fixtures de api-sports."""
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
        # Determinar quien gano segun el equipo "home" actual del partido
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
    """Logica original con football-data."""
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

    estado = md.get("status", "")
    if estado in ("SCHEDULED", "TIMED"):
        save_prediction(match_id, codigo, md.get("utcDate",""), hn, an, resultado["veredicto"])
    elif estado == "FINISHED":
        save_prediction(match_id, codigo, md.get("utcDate",""), hn, an, resultado["veredicto"])
        score = md.get("score",{}).get("fullTime",{})
        if score.get("home") is not None:
            verify_prediction(match_id, score["home"], score["away"])
    return resultado


@app.route("/analizar_pendientes/<codigo>")
@api_login_required
def analizar_pendientes(codigo):
    """Analiza todos los partidos jugados de la liga que aun no tengan prediccion."""
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

    conn = sqlite3.connect(DB_PATH)
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
@api_login_required
def analizar(codigo, match_id):
    r = _do_analyze(codigo, match_id)
    return jsonify(r)


def _team_stats(as_stats, pos, forma):
    result={"remates_pj":"—","al_arco_pj":"—","goles_pj":"—","recibidos_pj":"—","posicion":"—","puntos":0}
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
    # Fallback: si no hay as_stats pero si forma, estimar igual con goles
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


def _search_as(name, lid, season):
    if not name or not lid: return None

    # Limpiar nombre: quitar sufijos comunes
    clean = name.replace(" FC", "").replace(" CF", "").replace(" AFC", "").replace(" AC", "").strip()

    # Estrategia 1: buscar dentro de los equipos de la liga (mas confiable)
    d = as_get("/teams", {"league": lid, "season": season})
    if "error" not in d and d.get("response"):
        candidates = d["response"]
        nl = name.lower()
        cl = clean.lower()
        # Match exacto
        for t in candidates:
            tn = t["team"]["name"].lower()
            if tn == nl or tn == cl: return t["team"]["id"]
        # Match parcial bidireccional
        for t in candidates:
            tn = t["team"]["name"].lower()
            if cl in tn or tn in cl: return t["team"]["id"]
        for t in candidates:
            tn = t["team"]["name"].lower()
            if nl in tn or tn in nl: return t["team"]["id"]
        # Match por primera palabra
        first_word = clean.split(" ")[0].lower()
        if len(first_word) >= 3:
            for t in candidates:
                if first_word in t["team"]["name"].lower(): return t["team"]["id"]

    # Estrategia 2: search global
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
        mercados.append({"mercado":f"Resultado Final — {hn}","prob":ph,"riesgo":100-ph,"cuota":_cuota(ph),"tipo":"1X2","aprobado":ph>=55,"sintesis":s})
    if pa>=40:
        s=_s1x2(an,hn,pa,af,ap,aa,localia_a_score,"away",ge)
        mercados.append({"mercado":f"Resultado Final — {an}","prob":pa,"riesgo":100-pa,"cuota":_cuota(pa),"tipo":"1X2","aprobado":pa>=55,"sintesis":s})
    if pd>=22:
        s=f"Equipos separados por {abs((pos_h or 10)-(pos_a or 10))} posiciones. "
        if hf["matches"]>0 and af["matches"]>0: s+=f"PPG similar: {hn} {hf['ppg']} vs {an} {af['ppg']}. "
        s+="Empate es resultado logico."
        mercados.append({"mercado":"Resultado Final — Empate","prob":pd,"riesgo":100-pd,"cuota":_cuota(pd),"tipo":"1X2","aprobado":pd>=30,"sintesis":s})

    dc1x=ph+pd;dcx2=pa+pd
    if dc1x>=55:
        s=f"{hn} o Empate cubre el escenario mas probable. Solo pierde si gana {an} ({pa}%)."
        mercados.append({"mercado":f"Doble Oportunidad 1X — {hn}","prob":dc1x,"riesgo":100-dc1x,"cuota":_cuota(dc1x),"tipo":"DC","aprobado":dc1x>=65,"sintesis":s})
    if dcx2>=55:
        mercados.append({"mercado":f"Doble Oportunidad X2 — {an}","prob":dcx2,"riesgo":100-dcx2,"cuota":_cuota(dcx2),"tipo":"DC","aprobado":dcx2>=65,"sintesis":f"Cubre victoria de {an} o empate."})

    if ge>=2.5:
        p=min(85,round(50+(ge-2.5)*20))
        s=f"Promedio combinado supera {ge} goles/partido."
        mercados.append({"mercado":"Goles Totales Over 2.5","prob":p,"riesgo":100-p,"cuota":_cuota(p),"tipo":"O/U","aprobado":p>=60,"sintesis":s})
    if ge<=2.5:
        p=min(85,round(50+(2.5-ge)*20))
        s=f"Goles esperados: {ge}. Perfil defensivo favorece Under."
        mercados.append({"mercado":"Goles Totales Under 2.5","prob":p,"riesgo":100-p,"cuota":_cuota(p),"tipo":"O/U","aprobado":p>=60,"sintesis":s})
    if ge>=1.8:
        p=min(92,round(60+(ge-1.5)*15))
        mercados.append({"mercado":"Goles Totales Over 1.5","prob":p,"riesgo":100-p,"cuota":_cuota(p),"tipo":"O/U","aprobado":p>=75,"sintesis":f"Con {ge} goles esperados."})

    btts=min(82,max(20,round(hsc*50+asc*50)))
    if btts>=45:
        mercados.append({"mercado":"BTTS — Ambos Anotan","prob":btts,"riesgo":100-btts,"cuota":_cuota(btts),"tipo":"BTTS","aprobado":btts>=60,"sintesis":f"{hn} marca en {round(hsc*100)}%, {an} en {round(asc*100)}%."})

    ho=min(95,max(30,round(hsc*100)))
    if ho>=70:
        mercados.append({"mercado":f"Goles Equipo — {hn} Over 0.5","prob":ho,"riesgo":100-ho,"cuota":_cuota(ho),"tipo":"GE","aprobado":ho>=85,"sintesis":f"{hn} marco en {round(hsc*100)}% de sus ultimos partidos."})
    ao=min(95,max(30,round(asc*100)))
    if ao>=70:
        mercados.append({"mercado":f"Goles Equipo — {an} Over 0.5","prob":ao,"riesgo":100-ao,"cuota":_cuota(ao),"tipo":"GE","aprobado":ao>=85,"sintesis":f"{an} marco en {round(asc*100)}% de sus ultimos partidos."})

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
    comb=""
    if len(aprobados)>=2:
        c2=[m for m in aprobados if m["tipo"]!=aprobados[0]["tipo"]]
        if c2: comb=f"{c2[0]['mercado']} ({c2[0]['prob']}% · cuota @{c2[0]['cuota']}) como alternativa de mayor retorno."
    ta=" · ".join(f"{m['mercado']} ({m['prob']}%)"for m in aprobados[:4])if aprobados else"Ninguno"

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
        "veredicto":{"texto":texto,"favorito":fav,"mercados_aprobados":ta,"total_aprobados":len(aprobados),"mercado_principal":mp,"combinable":comb},
    }

def _s1x2(team,rival,prob,form,pos,ha,localia,side,ge):
    s=""
    if form["matches"]>0:
        r=_racha(form["form"])
        if r[0]=="W"and r[1]>=2: s+=f"{team} en racha de {r[1]} victorias. "
        elif r[0]=="L"and r[1]>=2: s+=f"Atencion: {team} viene de {r[1]} derrotas. "
        s+=f"PPG: {form['ppg']}. "
    if pos: s+=f"#{pos.get('position','?')} con {pos.get('points',0)} pts. "
    s+=f"Probabilidad {'supera' if prob>=55 else 'no alcanza'} umbral (>=55%). "
    return s

if __name__=="__main__":
    app.run(debug=True)