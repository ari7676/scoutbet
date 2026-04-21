"""
ScoutBet v2 — Backend Flask
API: football-data.org (free tier: 10 req/min)
"""

from flask import Flask, jsonify, render_template
from datetime import datetime, timedelta
import requests, time, threading, math

app = Flask(__name__)

# ── football-data.org ─────────────────────────────────────
FD_KEY = "e28d6269df9441fea1b6a19548e982c6"
FD_URL = "https://api.football-data.org/v4"
FD_HEADERS = {"X-Auth-Token": FD_KEY}

LIGAS = {
    "PL":  {"nombre": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League",  "id": 2021},
    "PD":  {"nombre": "🇪🇸 La Liga",           "id": 2014},
    "SA":  {"nombre": "🇮🇹 Serie A",            "id": 2019},
    "BL1": {"nombre": "🇩🇪 Bundesliga",         "id": 2002},
    "FL1": {"nombre": "🇫🇷 Ligue 1",            "id": 2015},
    "CL":  {"nombre": "🏆 Champions League",    "id": 2001},
    "BSA": {"nombre": "🇧🇷 Brasileirão",        "id": 2013},
}

# ── Cache ─────────────────────────────────────────────────
_cache = {}
CACHE_TTL = 300

def fd_get(endpoint, params=None):
    cache_key = endpoint + str(params or "")
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
            "id": m["id"],
            "fecha": m["utcDate"],
            "home": m["homeTeam"]["name"],
            "home_id": m["homeTeam"]["id"],
            "home_crest": m["homeTeam"].get("crest", ""),
            "away": m["awayTeam"]["name"],
            "away_id": m["awayTeam"]["id"],
            "away_crest": m["awayTeam"].get("crest", ""),
            "jornada": m.get("matchday"),
            "competicion": data.get("competition", {}).get("name", ""),
            "estado": m["status"],
            "arbitro": arbitro,
        })
    return jsonify({"response": matches, "total": len(matches)})


@app.route("/standings/<codigo>")
def standings(codigo):
    data = fd_get(f"/competitions/{codigo}/standings")
    if "error" in data:
        return jsonify({"error": data["error"]})
    return jsonify(data)


@app.route("/goleadores/<codigo>")
def goleadores(codigo):
    data = fd_get(f"/competitions/{codigo}/scorers", {"limit": 15})
    if "error" in data:
        return jsonify({"error": data["error"]})
    scorers = []
    for s in data.get("scorers", []):
        scorers.append({
            "jugador": s["player"]["name"],
            "equipo": s["team"]["name"],
            "equipo_id": s["team"]["id"],
            "goles": s.get("goals", 0),
            "asistencias": s.get("assists", 0),
            "partidos": s.get("playedMatches", 0),
        })
    return jsonify({"scorers": scorers})


@app.route("/analizar/<codigo>/<int:match_id>")
def analizar(codigo, match_id):

    # 1) Datos del partido + head2head
    match_data = fd_get(f"/matches/{match_id}")
    if "error" in match_data or "id" not in match_data:
        return jsonify({"error": "Partido no encontrado"})

    home_id = match_data["homeTeam"]["id"]
    away_id = match_data["awayTeam"]["id"]
    home_name = match_data["homeTeam"]["name"]
    away_name = match_data["awayTeam"]["name"]

    # 2) Tabla de posiciones
    standings_data = fd_get(f"/competitions/{codigo}/standings")
    tabla_total = []
    tabla_home = []
    tabla_away = []
    for s in standings_data.get("standings", []):
        if s["type"] == "TOTAL":
            tabla_total = s["table"]
        elif s["type"] == "HOME":
            tabla_home = s["table"]
        elif s["type"] == "AWAY":
            tabla_away = s["table"]

    home_pos = _find_team(tabla_total, home_id)
    away_pos = _find_team(tabla_total, away_id)
    home_home = _find_team(tabla_home, home_id)
    away_away = _find_team(tabla_away, away_id)

    # 3) Forma reciente (ultimos 10 terminados)
    forma_home_raw = fd_get(f"/teams/{home_id}/matches", {"status": "FINISHED", "limit": 10})
    forma_away_raw = fd_get(f"/teams/{away_id}/matches", {"status": "FINISHED", "limit": 10})

    home_form = _calcular_forma(forma_home_raw.get("matches", []), home_id)
    away_form = _calcular_forma(forma_away_raw.get("matches", []), away_id)

    # Ultimos 3 detallados
    home_last3 = _ultimos_3(forma_home_raw.get("matches", []), home_id)
    away_last3 = _ultimos_3(forma_away_raw.get("matches", []), away_id)

    # 4) Head to Head
    h2h = match_data.get("head2head", {})

    # 5) Arbitro
    refs = match_data.get("referees", [])
    arbitro = refs[0]["name"] if refs else None

    # 6) Goleadores de la liga
    scorers_data = fd_get(f"/competitions/{codigo}/scorers", {"limit": 15})
    goleadores_liga = scorers_data.get("scorers", [])

    jugadores_home = _jugadores_destacados(goleadores_liga, home_id)
    jugadores_away = _jugadores_destacados(goleadores_liga, away_id)

    # 7) Calcular analisis completo
    resultado = _calcular_analisis(
        match_data, home_pos, away_pos, home_home, away_away,
        home_form, away_form, h2h, home_name, away_name, tabla_total
    )

    resultado["arbitro"] = arbitro
    resultado["ultimos3"] = {"home": home_last3, "away": away_last3}
    resultado["jugadores"] = {"home": jugadores_home, "away": jugadores_away}

    return jsonify(resultado)


# ── Helpers ───────────────────────────────────────────────

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
        score = m.get("score", {})
        ft = score.get("fullTime", {})
        home_goals = ft.get("home")
        away_goals = ft.get("away")
        if home_goals is None or away_goals is None:
            continue

        is_home = m["homeTeam"]["id"] == team_id
        my_goals = home_goals if is_home else away_goals
        their_goals = away_goals if is_home else home_goals

        gf += my_goals
        gc += their_goals
        if their_goals == 0:
            cs += 1
        if my_goals == 0:
            fts += 1

        if my_goals > their_goals:
            w += 1; form_str += "W"
        elif my_goals == their_goals:
            d += 1; form_str += "D"
        else:
            l += 1; form_str += "L"

    total = w + d + l
    return {
        "form": form_str[:5],
        "w": w, "d": d, "l": l,
        "gf": gf, "gc": gc,
        "matches": total,
        "ppg": round((w * 3 + d) / total, 2) if total > 0 else 0,
        "gf_avg": round(gf / total, 2) if total > 0 else 0,
        "gc_avg": round(gc / total, 2) if total > 0 else 0,
        "clean_sheets": cs,
        "failed_to_score": fts,
    }


def _ultimos_3(matches, team_id):
    if not matches:
        return []
    matches = sorted(matches, key=lambda x: x.get("utcDate", ""), reverse=True)[:3]
    result = []
    for m in matches:
        score = m.get("score", {})
        ft = score.get("fullTime", {})
        home_goals = ft.get("home")
        away_goals = ft.get("away")
        if home_goals is None:
            continue
        is_home = m["homeTeam"]["id"] == team_id
        rival = m["awayTeam"]["name"] if is_home else m["homeTeam"]["name"]
        comp = m.get("competition", {}).get("code", "")
        my_goals = home_goals if is_home else away_goals
        their_goals = away_goals if is_home else home_goals

        if my_goals > their_goals:
            res = "W"
        elif my_goals == their_goals:
            res = "D"
        else:
            res = "L"

        result.append({
            "fecha": m.get("utcDate", "")[:10],
            "rival": rival,
            "competicion": comp,
            "marcador": f"{home_goals}-{away_goals}",
            "local": is_home,
            "resultado": res,
        })
    return result


def _jugadores_destacados(goleadores_liga, team_id):
    jugadores = []
    for s in goleadores_liga:
        if s["team"]["id"] == team_id:
            jugadores.append({
                "nombre": s["player"]["name"],
                "goles": s.get("goals", 0),
                "asistencias": s.get("assists", 0),
                "partidos": s.get("playedMatches", 0),
                "promedio": round(s.get("goals", 0) / max(s.get("playedMatches", 1), 1), 2),
            })
    return jugadores[:3]


def _calcular_analisis(match_data, home_pos, away_pos, home_home, away_away,
                       home_form, away_form, h2h, home_name, away_name, tabla_total):

    total_equipos = len(tabla_total) if tabla_total else 20

    # ── FILTROS ────────────────────────────────────────────
    f3_home = round(home_form["ppg"] / 3 * 100) if home_form["matches"] > 0 else 50
    f3_away = round(away_form["ppg"] / 3 * 100) if away_form["matches"] > 0 else 50

    f5 = 50
    h2h_home_wins = h2h.get("homeTeam", {}).get("wins", 0)
    h2h_away_wins = h2h.get("awayTeam", {}).get("wins", 0)
    h2h_draws = h2h.get("homeTeam", {}).get("draws", h2h.get("awayTeam", {}).get("draws", 0))
    h2h_total = h2h.get("numberOfMatches", 0)
    if h2h_total > 0:
        f5 = round((h2h_home_wins * 100 + h2h_draws * 50) / h2h_total)

    f6_home = 50
    f6_away = 50
    if home_home:
        pg = home_home.get("playedGames", 0)
        if pg > 0:
            f6_home = round((home_home.get("won", 0) * 3 + home_home.get("draw", 0)) / (pg * 3) * 100)
    if away_away:
        pg = away_away.get("playedGames", 0)
        if pg > 0:
            f6_away = round((away_away.get("won", 0) * 3 + away_away.get("draw", 0)) / (pg * 3) * 100)

    f10_home = 50
    f10_away = 50
    if home_pos:
        pos = home_pos.get("position", 10)
        f10_home = round((1 - (pos - 1) / max(total_equipos - 1, 1)) * 100)
    if away_pos:
        pos = away_pos.get("position", 10)
        f10_away = round((1 - (pos - 1) / max(total_equipos - 1, 1)) * 100)

    # ── PROBABILIDAD ───────────────────────────────────────
    prob_home = round(f3_home * 0.30 + f5 * 0.15 + f6_home * 0.30 + f10_home * 0.25)
    prob_away = round(f3_away * 0.30 + (100 - f5) * 0.15 + f6_away * 0.30 + f10_away * 0.25)
    prob_draw = max(0, 100 - prob_home - prob_away)

    total_prob = prob_home + prob_away + prob_draw
    if total_prob > 0:
        prob_home = round(prob_home / total_prob * 100)
        prob_away = round(prob_away / total_prob * 100)
        prob_draw = 100 - prob_home - prob_away

    # ── GOLES ESPERADOS ────────────────────────────────────
    exp_gf_home = home_form["gf_avg"] if home_form["matches"] > 0 else 1.3
    exp_gc_home = home_form["gc_avg"] if home_form["matches"] > 0 else 1.0
    exp_gf_away = away_form["gf_avg"] if away_form["matches"] > 0 else 1.0
    exp_gc_away = away_form["gc_avg"] if away_form["matches"] > 0 else 1.3
    total_goles_esp = round((exp_gf_home + exp_gf_away + exp_gc_home + exp_gc_away) / 2, 2)

    home_cs_rate = home_form["clean_sheets"] / max(home_form["matches"], 1)
    away_cs_rate = away_form["clean_sheets"] / max(away_form["matches"], 1)
    home_fts_rate = home_form["failed_to_score"] / max(home_form["matches"], 1)
    away_fts_rate = away_form["failed_to_score"] / max(away_form["matches"], 1)
    home_scoring_rate = 1 - home_fts_rate
    away_scoring_rate = 1 - away_fts_rate

    # ── MERCADOS ───────────────────────────────────────────
    mercados = []

    # 1X2
    if prob_home >= 50:
        riesgo = 100 - prob_home
        sintesis = _sintesis_1x2(home_name, prob_home, home_form, home_pos, f3_home, f6_home, "home")
        mercados.append({"mercado": f"Resultado Final — Gana {home_name}", "prob": prob_home, "riesgo": riesgo,
                         "tipo": "1X2", "aprobado": prob_home >= 65, "sintesis": sintesis})

    if prob_away >= 50:
        riesgo = 100 - prob_away
        sintesis = _sintesis_1x2(away_name, prob_away, away_form, away_pos, f3_away, f6_away, "away")
        mercados.append({"mercado": f"Resultado Final — Gana {away_name}", "prob": prob_away, "riesgo": riesgo,
                         "tipo": "1X2", "aprobado": prob_away >= 65, "sintesis": sintesis})

    if prob_draw >= 28:
        riesgo = 100 - prob_draw
        mercados.append({"mercado": "Resultado Final — Empate", "prob": prob_draw, "riesgo": riesgo,
                         "tipo": "1X2", "aprobado": prob_draw >= 35,
                         "sintesis": "Equipos parejos en tabla y forma. Empate como resultado posible."})

    # Doble Chance
    dc_1x = prob_home + prob_draw
    dc_x2 = prob_away + prob_draw
    if dc_1x >= 60:
        riesgo = 100 - dc_1x
        mercados.append({"mercado": f"Doble Oportunidad 1X — {home_name}", "prob": dc_1x, "riesgo": riesgo,
                         "tipo": "DC", "aprobado": dc_1x >= 75,
                         "sintesis": f"Cubre victoria local o empate. Localía y forma sostienen la probabilidad."})
    if dc_x2 >= 60:
        riesgo = 100 - dc_x2
        mercados.append({"mercado": f"Doble Oportunidad X2 — {away_name}", "prob": dc_x2, "riesgo": riesgo,
                         "tipo": "DC", "aprobado": dc_x2 >= 75,
                         "sintesis": f"Cubre victoria visitante o empate."})

    # Over/Under 2.5
    if total_goles_esp >= 2.5:
        prob_over = min(88, round(50 + (total_goles_esp - 2.5) * 22))
        riesgo = 100 - prob_over
        mercados.append({"mercado": "Goles Totales Over 2.5", "prob": prob_over, "riesgo": riesgo,
                         "tipo": "O/U", "aprobado": prob_over >= 65,
                         "sintesis": f"Goles esperados: {total_goles_esp}. Promedio combinado GF: {exp_gf_home} (L) + {exp_gf_away} (V)."})

    if total_goles_esp <= 2.5:
        prob_under = min(88, round(50 + (2.5 - total_goles_esp) * 22))
        riesgo = 100 - prob_under
        mercados.append({"mercado": "Goles Totales Under 2.5", "prob": prob_under, "riesgo": riesgo,
                         "tipo": "O/U", "aprobado": prob_under >= 65,
                         "sintesis": f"Goles esperados: {total_goles_esp}. Defensas solidas reducen probabilidad de +3 goles."})

    # Over/Under 1.5
    if total_goles_esp >= 1.8:
        prob_o15 = min(92, round(55 + (total_goles_esp - 1.5) * 18))
        mercados.append({"mercado": "Goles Totales Over 1.5", "prob": prob_o15, "riesgo": 100 - prob_o15,
                         "tipo": "O/U", "aprobado": prob_o15 >= 80,
                         "sintesis": f"Alta probabilidad con {total_goles_esp} goles esperados."})

    # BTTS
    btts_prob = round((home_scoring_rate * 50 + away_scoring_rate * 50))
    btts_prob = min(85, max(20, btts_prob))
    riesgo = 100 - btts_prob
    if btts_prob >= 50:
        mercados.append({"mercado": "BTTS — Ambos Anotan", "prob": btts_prob, "riesgo": riesgo,
                         "tipo": "BTTS", "aprobado": btts_prob >= 65,
                         "sintesis": f"Local marca en {round(home_scoring_rate*100)}% de partidos, visitante en {round(away_scoring_rate*100)}%."})

    # Goles equipo Over 0.5
    home_o05 = round(home_scoring_rate * 100)
    home_o05 = min(95, max(30, home_o05))
    if home_o05 >= 60:
        mercados.append({"mercado": f"Goles Equipo — {home_name} Over 0.5", "prob": home_o05, "riesgo": 100 - home_o05,
                         "tipo": "GE", "aprobado": home_o05 >= 80,
                         "sintesis": f"{home_name} marco en {round(home_scoring_rate*100)}% de sus ultimos partidos. Promedio: {exp_gf_home} GF/P."})

    away_o05 = round(away_scoring_rate * 100)
    away_o05 = min(95, max(30, away_o05))
    if away_o05 >= 60:
        mercados.append({"mercado": f"Goles Equipo — {away_name} Over 0.5", "prob": away_o05, "riesgo": 100 - away_o05,
                         "tipo": "GE", "aprobado": away_o05 >= 80,
                         "sintesis": f"{away_name} marco en {round(away_scoring_rate*100)}% de sus ultimos partidos. Promedio: {exp_gf_away} GF/P."})

    # No termina 0-0
    prob_00 = round(home_fts_rate * away_cs_rate * 100)
    prob_no_00 = min(95, 100 - prob_00)
    if prob_no_00 >= 75:
        mercados.append({"mercado": "El Partido No Termina 0-0", "prob": prob_no_00, "riesgo": 100 - prob_no_00,
                         "tipo": "ESP", "aprobado": prob_no_00 >= 85,
                         "sintesis": f"Promedio combinado: {total_goles_esp} goles. Ambos mantienen tasas altas de conversion."})

    mercados.sort(key=lambda x: x["prob"], reverse=True)

    # ── VEREDICTO ──────────────────────────────────────────
    veredicto = _generar_veredicto(
        home_name, away_name, prob_home, prob_draw, prob_away,
        home_form, away_form, home_pos, away_pos, mercados, total_goles_esp
    )

    return {
        "match": {
            "home": home_name, "away": away_name,
            "fecha": match_data.get("utcDate", ""),
            "jornada": match_data.get("matchday"),
            "competicion": match_data.get("competition", {}).get("name", ""),
        },
        "filtros": {
            "F3_forma": {"home": f3_home, "away": f3_away},
            "F5_h2h": f5,
            "F6_localvisit": {"home": f6_home, "away": f6_away},
            "F10_posicion": {"home": f10_home, "away": f10_away},
        },
        "probabilidades": {"home": prob_home, "draw": prob_draw, "away": prob_away},
        "goles_esperados": total_goles_esp,
        "forma": {"home": home_form, "away": away_form},
        "h2h": {
            "total": h2h_total, "home_wins": h2h_home_wins,
            "away_wins": h2h_away_wins, "draws": h2h_draws,
            "total_goals": h2h.get("totalGoals", 0),
        },
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
        "mercados": mercados,
        "veredicto": veredicto,
    }


def _sintesis_1x2(team, prob, form, pos, f3, f6, side):
    partes = []
    if form["matches"] > 0:
        racha_w = 0
        for c in form["form"]:
            if c == "W": racha_w += 1
            else: break
        if racha_w >= 2:
            partes.append(f"Racha de {racha_w} victorias consecutivas")
        partes.append(f"PPG: {form['ppg']}")
    if pos:
        partes.append(f"#{pos.get('position', '?')} en la tabla con {pos.get('points', 0)} pts")
    if side == "home" and f6 >= 60:
        partes.append("Fuerte de local")
    elif side == "away" and f6 >= 60:
        partes.append("Buen rendimiento visitante")
    if prob < 65:
        partes.append("No alcanza umbral de aprobacion directa (>=65%)")
    return ". ".join(partes) + "." if partes else ""


def _generar_veredicto(home, away, ph, pd, pa, hf, af, hp, ap, mercados, goles_esp):
    aprobados = [m for m in mercados if m["aprobado"]]
    mejor = mercados[0] if mercados else None

    if ph >= pa + 15:
        favorito = home
        tipo = "victoria local"
        confianza = "alta" if ph >= 65 else "moderada"
    elif pa >= ph + 15:
        favorito = away
        tipo = "victoria visitante"
        confianza = "alta" if pa >= 65 else "moderada"
    else:
        favorito = None
        tipo = "partido parejo"
        confianza = "baja"

    if favorito:
        texto = f"{favorito} es favorito con {max(ph,pa)}% de probabilidad — confianza {confianza}."
    else:
        texto = f"Partido equilibrado ({home} {ph}% vs {away} {pa}%). Sin favorito claro."

    if hf["matches"] > 0 and af["matches"] > 0:
        texto += f" Goles esperados: {goles_esp}."

    if hp and ap:
        texto += f" Posiciones: {home} #{hp.get('position','?')} vs {away} #{ap.get('position','?')}."

    if aprobados:
        nombres = [f"{m['mercado']} ({m['prob']}%)" for m in aprobados[:4]]
        texto_aprobados = " · ".join(nombres)
    else:
        texto_aprobados = "Ninguno supera el umbral de aprobacion"

    return {
        "texto": texto,
        "confianza": confianza,
        "favorito": favorito,
        "mercados_aprobados": texto_aprobados,
        "total_aprobados": len(aprobados),
    }


if __name__ == "__main__":
    app.run(debug=True)