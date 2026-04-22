"""
MatchIQ — Backend Flask
APIs: football-data.org (principal) + api-sports.io (stats avanzadas)
"""

from flask import Flask, jsonify, render_template
from datetime import datetime, timedelta
import requests, time, math

app = Flask(__name__)

# ── football-data.org ─────────────────────────────────────
FD_KEY = "e28d6269df9441fea1b6a19548e982c6"
FD_URL = "https://api.football-data.org/v4"
FD_HEADERS = {"X-Auth-Token": FD_KEY}

# ── api-sports.io (stats avanzadas) ───────────────────────
AS_KEY = "fb49b7a70ea23977f8e7711c5ed027b1"
AS_URL = "https://v3.football.api-sports.io"
AS_HEADERS = {"x-apisports-key": AS_KEY}

LIGAS = {
    "PL":  {"nombre": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League",  "id": 2021, "as_id": 39,  "season": 2025},
    "PD":  {"nombre": "🇪🇸 La Liga",           "id": 2014, "as_id": 140, "season": 2025},
    "SA":  {"nombre": "🇮🇹 Serie A",            "id": 2019, "as_id": 135, "season": 2025},
    "BL1": {"nombre": "🇩🇪 Bundesliga",         "id": 2002, "as_id": 78,  "season": 2025},
    "FL1": {"nombre": "🇫🇷 Ligue 1",            "id": 2015, "as_id": 61,  "season": 2025},
    "CL":  {"nombre": "🏆 Champions League",    "id": 2001, "as_id": 2,   "season": 2025},
    "BSA": {"nombre": "🇧🇷 Brasileirão",        "id": 2013, "as_id": 71,  "season": 2026},
}

# ── Cache ─────────────────────────────────────────────────
_cache = {}
CACHE_TTL = 300          # 5 min para football-data
CACHE_TTL_AS = 43200     # 12 horas para api-sports (stats no cambian seguido)

def fd_get(endpoint, params=None):
    cache_key = "fd:" + endpoint + str(params or "")
    now = time.time()
    if cache_key in _cache:
        data, ts = _cache[cache_key]
        if now - ts < CACHE_TTL:
            return data
    try:
        r = requests.get(f"{FD_URL}{endpoint}", headers=FD_HEADERS, params=params, timeout=15)
        if r.status_code == 429:
            time.sleep(6)
            r = requests.get(f"{FD_URL}{endpoint}", headers=FD_HEADERS, params=params, timeout=15)
        data = r.json()
        _cache[cache_key] = (data, now)
        return data
    except Exception as e:
        return {"error": str(e)}

def as_get(endpoint, params=None):
    cache_key = "as:" + endpoint + str(params or "")
    now = time.time()
    if cache_key in _cache:
        data, ts = _cache[cache_key]
        if now - ts < CACHE_TTL_AS:
            return data
    try:
        r = requests.get(f"{AS_URL}{endpoint}", headers=AS_HEADERS, params=params, timeout=15)
        if r.status_code == 429:
            return {"error": "rate_limit"}
        data = r.json()
        if data.get("response") is not None:
            _cache[cache_key] = (data, now)
        return data
    except Exception as e:
        return {"error": str(e)}


# ── RUTAS ─────────────────────────────────────────────────

@app.route("/")
def index():
    ligas_front = {k: v["nombre"] for k, v in LIGAS.items()}
    return render_template("index.html", ligas=ligas_front)


@app.route("/partidos/<codigo>")
def partidos(codigo):
    data = fd_get(f"/competitions/{codigo}/matches", {"status": "SCHEDULED", "limit": 50})
    if "error" in data:
        return jsonify({"response": [], "error": data["error"]})
    matches = []
    for m in data.get("matches", []):
        refs = m.get("referees", [])
        arbitro = refs[0]["name"] if refs else None
        matches.append({
            "id": m["id"], "fecha": m["utcDate"],
            "home": m["homeTeam"]["name"], "home_id": m["homeTeam"]["id"],
            "away": m["awayTeam"]["name"], "away_id": m["awayTeam"]["id"],
            "jornada": m.get("matchday"),
            "competicion": data.get("competition", {}).get("name", ""),
            "estado": m["status"], "arbitro": arbitro,
        })
    return jsonify({"response": matches, "total": len(matches)})


@app.route("/analizar/<codigo>/<int:match_id>")
def analizar(codigo, match_id):
    liga = LIGAS.get(codigo, {})

    # 1) Datos del partido
    match_data = fd_get(f"/matches/{match_id}")
    if "error" in match_data or "id" not in match_data:
        return jsonify({"error": "Partido no encontrado"})

    home_id = match_data["homeTeam"]["id"]
    away_id = match_data["awayTeam"]["id"]
    home_name = match_data["homeTeam"]["name"]
    away_name = match_data["awayTeam"]["name"]

    # 2) Tabla de posiciones
    standings_data = fd_get(f"/competitions/{codigo}/standings")
    tabla_total, tabla_home, tabla_away = [], [], []
    for s in standings_data.get("standings", []):
        if s["type"] == "TOTAL": tabla_total = s["table"]
        elif s["type"] == "HOME": tabla_home = s["table"]
        elif s["type"] == "AWAY": tabla_away = s["table"]

    home_pos = _find_team(tabla_total, home_id)
    away_pos = _find_team(tabla_total, away_id)
    home_home = _find_team(tabla_home, home_id)
    away_away = _find_team(tabla_away, away_id)

    # 3) Forma reciente
    forma_home_raw = fd_get(f"/teams/{home_id}/matches", {"status": "FINISHED", "limit": 10})
    forma_away_raw = fd_get(f"/teams/{away_id}/matches", {"status": "FINISHED", "limit": 10})
    home_form = _calcular_forma(forma_home_raw.get("matches", []), home_id)
    away_form = _calcular_forma(forma_away_raw.get("matches", []), away_id)
    home_last3 = _ultimos_3(forma_home_raw.get("matches", []), home_id)
    away_last3 = _ultimos_3(forma_away_raw.get("matches", []), away_id)

    # 4) H2H
    h2h = match_data.get("head2head", {})

    # 5) Arbitro
    refs = match_data.get("referees", [])
    arbitro = refs[0]["name"] if refs else None

    # 6) Goleadores
    scorers_data = fd_get(f"/competitions/{codigo}/scorers", {"limit": 15})
    goleadores_liga = scorers_data.get("scorers", [])
    jugadores_home = _jugadores_destacados(goleadores_liga, home_id)
    jugadores_away = _jugadores_destacados(goleadores_liga, away_id)

    # 7) Stats avanzadas de api-sports.io
    as_league = liga.get("as_id")
    as_season = liga.get("season")
    home_stats = _get_team_stats_as(home_name, as_league, as_season)
    away_stats = _get_team_stats_as(away_name, as_league, as_season)

    # 8) Calcular analisis
    resultado = _calcular_analisis(
        match_data, home_pos, away_pos, home_home, away_away,
        home_form, away_form, h2h, home_name, away_name, tabla_total
    )

    resultado["arbitro"] = arbitro
    resultado["ultimos3"] = {"home": home_last3, "away": away_last3}
    resultado["jugadores"] = {"home": jugadores_home, "away": jugadores_away}
    resultado["stats_avanzadas"] = _build_advanced_stats(home_stats, away_stats, home_name, away_name)

    return jsonify(resultado)


# ── api-sports helpers ────────────────────────────────────

def _search_team_as(name, league_id, season):
    """Busca equipo en api-sports por nombre dentro de una liga."""
    data = as_get("/teams", {"league": league_id, "season": season, "search": name.split(" ")[0]})
    if "error" in data or not data.get("response"):
        return None
    # Buscar mejor match
    teams = data["response"]
    for t in teams:
        tname = t["team"]["name"].lower()
        if name.lower() in tname or tname in name.lower():
            return t["team"]["id"]
    return teams[0]["team"]["id"] if teams else None


def _get_team_stats_as(team_name, league_id, season):
    """Obtiene estadisticas avanzadas de un equipo."""
    if not league_id or not season:
        return None

    # Buscar team ID
    team_id = _search_team_as(team_name, league_id, season)
    if not team_id:
        return None

    data = as_get("/teams/statistics", {"league": league_id, "season": season, "team": team_id})
    if "error" in data or not data.get("response"):
        return None

    stats = data["response"]
    return stats


def _build_advanced_stats(home_stats, away_stats, home_name, away_name):
    """Construye stats avanzadas comparativas."""
    if not home_stats and not away_stats:
        return None

    result = {}

    # Goles por intervalo de tiempo
    if home_stats and away_stats:
        home_goals_min = home_stats.get("goals", {}).get("for", {}).get("minute", {})
        away_goals_min = away_stats.get("goals", {}).get("for", {}).get("minute", {})
        result["goles_por_tiempo"] = {
            "home": _parse_minute_stats(home_goals_min),
            "away": _parse_minute_stats(away_goals_min),
        }

        # Tarjetas
        home_cards = home_stats.get("cards", {})
        away_cards = away_stats.get("cards", {})
        home_yellow = _count_cards(home_cards.get("yellow", {}))
        away_yellow = _count_cards(away_cards.get("yellow", {}))
        home_red = _count_cards(home_cards.get("red", {}))
        away_red = _count_cards(away_cards.get("red", {}))

        home_played = _get_played(home_stats)
        away_played = _get_played(away_stats)

        result["tarjetas"] = {
            "home_yellow": home_yellow, "away_yellow": away_yellow,
            "home_red": home_red, "away_red": away_red,
            "home_yellow_avg": round(home_yellow / max(home_played, 1), 2),
            "away_yellow_avg": round(away_yellow / max(away_played, 1), 2),
        }

        # Stats comparativas para barras
        home_clean = home_stats.get("clean_sheet", {}).get("total", 0) or 0
        away_clean = away_stats.get("clean_sheet", {}).get("total", 0) or 0
        home_fts = home_stats.get("failed_to_score", {}).get("total", 0) or 0
        away_fts = away_stats.get("failed_to_score", {}).get("total", 0) or 0

        # Goles totales
        home_gf = home_stats.get("goals", {}).get("for", {}).get("total", {}).get("total", 0) or 0
        home_gc = home_stats.get("goals", {}).get("against", {}).get("total", {}).get("total", 0) or 0
        away_gf = away_stats.get("goals", {}).get("for", {}).get("total", {}).get("total", 0) or 0
        away_gc = away_stats.get("goals", {}).get("against", {}).get("total", {}).get("total", 0) or 0

        # Barras predictivas
        comparativas = []
        # Poder ofensivo
        total_gf = home_gf + away_gf
        if total_gf > 0:
            comparativas.append({
                "label": "Poder ofensivo",
                "home": round(home_gf / total_gf * 100),
                "away": round(away_gf / total_gf * 100),
            })
        # Solidez defensiva (invertido: menos goles en contra = mejor)
        total_gc = home_gc + away_gc
        if total_gc > 0:
            comparativas.append({
                "label": "Solidez defensiva",
                "home": round((1 - home_gc / total_gc) * 100),
                "away": round((1 - away_gc / total_gc) * 100),
            })
        # Tarjetas (quien recibe mas)
        total_cards = home_yellow + away_yellow
        if total_cards > 0:
            comparativas.append({
                "label": "Mayor tarjetas",
                "home": round(home_yellow / total_cards * 100),
                "away": round(away_yellow / total_cards * 100),
            })
        # Valla invicta
        total_cs = home_clean + away_clean
        if total_cs > 0:
            comparativas.append({
                "label": "Valla invicta",
                "home": round(home_clean / total_cs * 100),
                "away": round(away_clean / total_cs * 100),
            })

        result["comparativas"] = comparativas

        # Prob gol por tiempo
        home_1st = _goals_half(home_goals_min, "1st")
        home_2nd = _goals_half(home_goals_min, "2nd")
        away_1st = _goals_half(away_goals_min, "1st")
        away_2nd = _goals_half(away_goals_min, "2nd")
        home_total_g = home_1st + home_2nd
        away_total_g = away_1st + away_2nd

        result["prob_gol_tiempo"] = {
            "home_1st": round(home_1st / max(home_total_g, 1) * 100),
            "home_2nd": round(home_2nd / max(home_total_g, 1) * 100),
            "away_1st": round(away_1st / max(away_total_g, 1) * 100),
            "away_2nd": round(away_2nd / max(away_total_g, 1) * 100),
        }

        # Racha (biggest streak)
        home_streak = home_stats.get("biggest", {}).get("streak", {})
        away_streak = away_stats.get("biggest", {}).get("streak", {})
        result["rachas"] = {
            "home_wins": home_streak.get("wins", 0) or 0,
            "home_draws": home_streak.get("draws", 0) or 0,
            "home_loses": home_streak.get("loses", 0) or 0,
            "away_wins": away_streak.get("wins", 0) or 0,
            "away_draws": away_streak.get("draws", 0) or 0,
            "away_loses": away_streak.get("loses", 0) or 0,
        }

    return result


def _parse_minute_stats(minute_data):
    """Parsea goles por intervalos de 15 min."""
    intervals = ["0-15", "16-30", "31-45", "46-60", "61-75", "76-90"]
    result = []
    for iv in intervals:
        entry = minute_data.get(iv, {})
        total = entry.get("total") or 0
        pct = entry.get("percentage")
        pct_val = int(pct.replace("%", "")) if pct else 0
        result.append({"intervalo": iv, "goles": total, "pct": pct_val})
    return result


def _count_cards(card_data):
    total = 0
    for k, v in card_data.items():
        if isinstance(v, dict):
            total += v.get("total") or 0
    return total


def _get_played(stats):
    fixtures = stats.get("fixtures", {})
    played = fixtures.get("played", {})
    return (played.get("total") or 0)


def _goals_half(minute_data, half):
    if half == "1st":
        keys = ["0-15", "16-30", "31-45"]
    else:
        keys = ["46-60", "61-75", "76-90"]
    total = 0
    for k in keys:
        entry = minute_data.get(k, {})
        total += entry.get("total") or 0
    return total


# ── football-data helpers ─────────────────────────────────

def _find_team(tabla, team_id):
    for t in tabla:
        if t["team"]["id"] == team_id:
            return t
    return None


def _calcular_forma(matches, team_id):
    if not matches:
        return {"form": "", "w": 0, "d": 0, "l": 0, "gf": 0, "gc": 0, "matches": 0,
                "ppg": 0, "gf_avg": 0, "gc_avg": 0, "clean_sheets": 0, "failed_to_score": 0}
    matches = sorted(matches, key=lambda x: x.get("utcDate", ""), reverse=True)[:10]
    w = d = l = gf = gc = cs = fts = 0
    form_str = ""
    for m in matches:
        ft = m.get("score", {}).get("fullTime", {})
        hg, ag = ft.get("home"), ft.get("away")
        if hg is None or ag is None: continue
        is_home = m["homeTeam"]["id"] == team_id
        my, their = (hg, ag) if is_home else (ag, hg)
        gf += my; gc += their
        if their == 0: cs += 1
        if my == 0: fts += 1
        if my > their: w += 1; form_str += "W"
        elif my == their: d += 1; form_str += "D"
        else: l += 1; form_str += "L"
    total = w + d + l
    return {
        "form": form_str[:5], "w": w, "d": d, "l": l, "gf": gf, "gc": gc,
        "matches": total,
        "ppg": round((w*3+d)/total, 2) if total > 0 else 0,
        "gf_avg": round(gf/total, 2) if total > 0 else 0,
        "gc_avg": round(gc/total, 2) if total > 0 else 0,
        "clean_sheets": cs, "failed_to_score": fts,
    }


def _ultimos_3(matches, team_id):
    if not matches: return []
    matches = sorted(matches, key=lambda x: x.get("utcDate", ""), reverse=True)[:3]
    result = []
    for m in matches:
        ft = m.get("score", {}).get("fullTime", {})
        hg, ag = ft.get("home"), ft.get("away")
        if hg is None: continue
        is_home = m["homeTeam"]["id"] == team_id
        my, their = (hg, ag) if is_home else (ag, hg)
        rival = m["awayTeam"]["name"] if is_home else m["homeTeam"]["name"]
        res = "W" if my > their else ("D" if my == their else "L")
        result.append({
            "fecha": m.get("utcDate", "")[:10], "rival": rival,
            "competicion": m.get("competition", {}).get("code", ""),
            "marcador": f"{hg}-{ag}", "local": is_home, "resultado": res,
        })
    return result


def _jugadores_destacados(goleadores_liga, team_id):
    jugadores = []
    for s in goleadores_liga:
        if s["team"]["id"] == team_id:
            jugadores.append({
                "nombre": s["player"]["name"],
                "goles": s.get("goals", 0), "asistencias": s.get("assists", 0),
                "partidos": s.get("playedMatches", 0),
                "promedio": round(s.get("goals", 0) / max(s.get("playedMatches", 1), 1), 2),
            })
    return jugadores[:3]


def _calcular_analisis(match_data, home_pos, away_pos, home_home, away_away,
                       home_form, away_form, h2h, home_name, away_name, tabla_total):
    total_equipos = len(tabla_total) if tabla_total else 20

    f3_home = round(home_form["ppg"]/3*100) if home_form["matches"] > 0 else 50
    f3_away = round(away_form["ppg"]/3*100) if away_form["matches"] > 0 else 50

    f5 = 50
    h2h_home_wins = h2h.get("homeTeam", {}).get("wins", 0)
    h2h_away_wins = h2h.get("awayTeam", {}).get("wins", 0)
    h2h_draws = h2h.get("homeTeam", {}).get("draws", h2h.get("awayTeam", {}).get("draws", 0))
    h2h_total = h2h.get("numberOfMatches", 0)
    if h2h_total > 0:
        f5 = round((h2h_home_wins*100 + h2h_draws*50) / h2h_total)

    f6_home = f6_away = 50
    if home_home:
        pg = home_home.get("playedGames", 0)
        if pg > 0: f6_home = round((home_home.get("won",0)*3+home_home.get("draw",0))/(pg*3)*100)
    if away_away:
        pg = away_away.get("playedGames", 0)
        if pg > 0: f6_away = round((away_away.get("won",0)*3+away_away.get("draw",0))/(pg*3)*100)

    f10_home = f10_away = 50
    if home_pos:
        f10_home = round((1-(home_pos.get("position",10)-1)/max(total_equipos-1,1))*100)
    if away_pos:
        f10_away = round((1-(away_pos.get("position",10)-1)/max(total_equipos-1,1))*100)

    prob_home = round(f3_home*0.30 + f5*0.15 + f6_home*0.30 + f10_home*0.25)
    prob_away = round(f3_away*0.30 + (100-f5)*0.15 + f6_away*0.30 + f10_away*0.25)
    prob_draw = max(0, 100 - prob_home - prob_away)
    total_prob = prob_home + prob_away + prob_draw
    if total_prob > 0:
        prob_home = round(prob_home/total_prob*100)
        prob_away = round(prob_away/total_prob*100)
        prob_draw = 100 - prob_home - prob_away

    exp_gf_h = home_form["gf_avg"] if home_form["matches"]>0 else 1.3
    exp_gc_h = home_form["gc_avg"] if home_form["matches"]>0 else 1.0
    exp_gf_a = away_form["gf_avg"] if away_form["matches"]>0 else 1.0
    exp_gc_a = away_form["gc_avg"] if away_form["matches"]>0 else 1.3
    total_goles_esp = round((exp_gf_h + exp_gf_a + exp_gc_h + exp_gc_a)/2, 2)

    home_fts_rate = home_form["failed_to_score"]/max(home_form["matches"],1)
    away_fts_rate = away_form["failed_to_score"]/max(away_form["matches"],1)
    home_cs_rate = home_form["clean_sheets"]/max(home_form["matches"],1)
    away_cs_rate = away_form["clean_sheets"]/max(away_form["matches"],1)
    home_scoring = 1-home_fts_rate
    away_scoring = 1-away_fts_rate

    mercados = []

    # 1X2
    if prob_home >= 50:
        s = _sintesis_1x2(home_name, prob_home, home_form, home_pos, f6_home, "home")
        mercados.append({"mercado": f"Resultado Final — Gana {home_name}", "prob": prob_home, "riesgo": 100-prob_home,
                         "tipo": "1X2", "aprobado": prob_home>=65, "sintesis": s})
    if prob_away >= 50:
        s = _sintesis_1x2(away_name, prob_away, away_form, away_pos, f6_away, "away")
        mercados.append({"mercado": f"Resultado Final — Gana {away_name}", "prob": prob_away, "riesgo": 100-prob_away,
                         "tipo": "1X2", "aprobado": prob_away>=65, "sintesis": s})
    if prob_draw >= 28:
        mercados.append({"mercado": "Resultado Final — Empate", "prob": prob_draw, "riesgo": 100-prob_draw,
                         "tipo": "1X2", "aprobado": prob_draw>=35,
                         "sintesis": "Equipos parejos en tabla y forma."})

    # DC
    dc_1x = prob_home + prob_draw
    dc_x2 = prob_away + prob_draw
    if dc_1x >= 60:
        mercados.append({"mercado": f"Doble Oportunidad 1X — {home_name}", "prob": dc_1x, "riesgo": 100-dc_1x,
                         "tipo": "DC", "aprobado": dc_1x>=75,
                         "sintesis": "Cubre victoria local o empate. Localia sostiene la probabilidad."})
    if dc_x2 >= 60:
        mercados.append({"mercado": f"Doble Oportunidad X2 — {away_name}", "prob": dc_x2, "riesgo": 100-dc_x2,
                         "tipo": "DC", "aprobado": dc_x2>=75, "sintesis": "Cubre victoria visitante o empate."})

    # O/U
    if total_goles_esp >= 2.5:
        p = min(88, round(50+(total_goles_esp-2.5)*22))
        mercados.append({"mercado": "Goles Totales Over 2.5", "prob": p, "riesgo": 100-p,
                         "tipo": "O/U", "aprobado": p>=65,
                         "sintesis": f"Goles esperados: {total_goles_esp}. GF promedio: {exp_gf_h}(L) + {exp_gf_a}(V)."})
    if total_goles_esp <= 2.5:
        p = min(88, round(50+(2.5-total_goles_esp)*22))
        mercados.append({"mercado": "Goles Totales Under 2.5", "prob": p, "riesgo": 100-p,
                         "tipo": "O/U", "aprobado": p>=65,
                         "sintesis": f"Goles esperados: {total_goles_esp}. Defensas solidas."})
    if total_goles_esp >= 1.8:
        p = min(92, round(55+(total_goles_esp-1.5)*18))
        mercados.append({"mercado": "Goles Totales Over 1.5", "prob": p, "riesgo": 100-p,
                         "tipo": "O/U", "aprobado": p>=80, "sintesis": f"{total_goles_esp} goles esperados."})

    # BTTS
    btts = min(85, max(20, round(home_scoring*50+away_scoring*50)))
    if btts >= 50:
        mercados.append({"mercado": "BTTS — Ambos Anotan", "prob": btts, "riesgo": 100-btts,
                         "tipo": "BTTS", "aprobado": btts>=65,
                         "sintesis": f"Local marca {round(home_scoring*100)}%, visitante {round(away_scoring*100)}%."})

    # GE Over 0.5
    ho = min(95, max(30, round(home_scoring*100)))
    if ho >= 60:
        mercados.append({"mercado": f"Goles Equipo — {home_name} Over 0.5", "prob": ho, "riesgo": 100-ho,
                         "tipo": "GE", "aprobado": ho>=80,
                         "sintesis": f"Marco en {round(home_scoring*100)}% de partidos. Promedio: {exp_gf_h} GF/P."})
    ao = min(95, max(30, round(away_scoring*100)))
    if ao >= 60:
        mercados.append({"mercado": f"Goles Equipo — {away_name} Over 0.5", "prob": ao, "riesgo": 100-ao,
                         "tipo": "GE", "aprobado": ao>=80,
                         "sintesis": f"Marco en {round(away_scoring*100)}% de partidos. Promedio: {exp_gf_a} GF/P."})

    # No 0-0
    p00 = round(home_fts_rate * away_cs_rate * 100)
    pno00 = min(95, 100-p00)
    if pno00 >= 75:
        mercados.append({"mercado": "El Partido No Termina 0-0", "prob": pno00, "riesgo": 100-pno00,
                         "tipo": "ESP", "aprobado": pno00>=85,
                         "sintesis": f"Promedio combinado: {total_goles_esp} goles."})

    mercados.sort(key=lambda x: x["prob"], reverse=True)

    veredicto = _generar_veredicto(home_name, away_name, prob_home, prob_draw, prob_away,
                                   home_form, away_form, home_pos, away_pos, mercados, total_goles_esp)

    return {
        "match": {"home": home_name, "away": away_name, "fecha": match_data.get("utcDate",""),
                  "jornada": match_data.get("matchday"), "competicion": match_data.get("competition",{}).get("name","")},
        "filtros": {"F3_forma": {"home": f3_home, "away": f3_away}, "F5_h2h": f5,
                    "F6_localvisit": {"home": f6_home, "away": f6_away},
                    "F10_posicion": {"home": f10_home, "away": f10_away}},
        "probabilidades": {"home": prob_home, "draw": prob_draw, "away": prob_away},
        "goles_esperados": total_goles_esp,
        "forma": {"home": home_form, "away": away_form},
        "h2h": {"total": h2h_total, "home_wins": h2h_home_wins, "away_wins": h2h_away_wins,
                "draws": h2h_draws, "total_goals": h2h.get("totalGoals", 0)},
        "posiciones": {
            "home": home_pos.get("position") if home_pos else None,
            "away": away_pos.get("position") if away_pos else None,
            "home_pts": home_pos.get("points") if home_pos else None,
            "away_pts": away_pos.get("points") if away_pos else None,
            "home_gf": home_pos.get("goalsFor") if home_pos else None,
            "home_gc": home_pos.get("goalsAgainst") if home_pos else None,
            "away_gf": away_pos.get("goalsFor") if away_pos else None,
            "away_gc": away_pos.get("goalsAgainst") if away_pos else None,
        },
        "mercados": mercados, "veredicto": veredicto,
    }


def _sintesis_1x2(team, prob, form, pos, f6, side):
    p = []
    if form["matches"] > 0:
        racha = sum(1 for c in form["form"] if c == "W")
        if racha >= 2: p.append(f"Racha de {racha} victorias")
        p.append(f"PPG: {form['ppg']}")
    if pos: p.append(f"#{pos.get('position','?')} con {pos.get('points',0)} pts")
    if side=="home" and f6>=60: p.append("Fuerte de local")
    elif side=="away" and f6>=60: p.append("Buen rendimiento visitante")
    if prob < 65: p.append("No alcanza umbral (>=65%)")
    return ". ".join(p)+"." if p else ""


def _generar_veredicto(home, away, ph, pd, pa, hf, af, hp, ap, mercados, ge):
    aprobados = [m for m in mercados if m["aprobado"]]
    if ph >= pa+15:
        fav, conf = home, ("alta" if ph>=65 else "moderada")
    elif pa >= ph+15:
        fav, conf = away, ("alta" if pa>=65 else "moderada")
    else:
        fav, conf = None, "baja"

    texto = f"{fav} es favorito con {max(ph,pa)}% — confianza {conf}." if fav else f"Partido equilibrado ({home} {ph}% vs {away} {pa}%)."
    if hf["matches"]>0: texto += f" Goles esperados: {ge}."
    if hp and ap: texto += f" Posiciones: {home} #{hp.get('position','?')} vs {away} #{ap.get('position','?')}."

    ta = " · ".join(f"{m['mercado']} ({m['prob']}%)" for m in aprobados[:4]) if aprobados else "Ninguno supera el umbral"
    return {"texto": texto, "confianza": conf, "favorito": fav, "mercados_aprobados": ta, "total_aprobados": len(aprobados)}


if __name__ == "__main__":
    app.run(debug=True)