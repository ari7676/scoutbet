from datetime import datetime, timedelta


def calcular_prob_base(forma_local, forma_visitante, home_id, away_id):
    """Calcula probabilidad base según forma reciente de ambos equipos"""

    def rendimiento(partidos, team_id, es_local):
        puntos = 0
        jugados = 0
        goles_a_favor = 0
        goles_en_contra = 0
        for p in partidos:
            h = p["teams"]["home"]["id"]
            a = p["teams"]["away"]["id"]
            gh = p["goals"]["home"] or 0
            ga = p["goals"]["away"] or 0
            if es_local and h == team_id:
                jugados += 1
                goles_a_favor += gh
                goles_en_contra += ga
                if gh > ga: puntos += 3
                elif gh == ga: puntos += 1
            elif not es_local and a == team_id:
                jugados += 1
                goles_a_favor += ga
                goles_en_contra += gh
                if ga > gh: puntos += 3
                elif ga == gh: puntos += 1
        return puntos, jugados, goles_a_favor, goles_en_contra

    pts_local, j_local, gf_local, gc_local = rendimiento(forma_local, home_id, True)
    pts_visit, j_visit, gf_visit, gc_visit = rendimiento(forma_visitante, away_id, False)

    # Porcentaje de puntos posibles
    pct_local = (pts_local / (j_local * 3)) * 100 if j_local > 0 else 50
    pct_visit = (pts_visit / (j_visit * 3)) * 100 if j_visit > 0 else 50

    # Promedio goles
    avg_gf_local = gf_local / j_local if j_local > 0 else 1.2
    avg_gc_visit = gc_visit / j_visit if j_visit > 0 else 1.2

    # Prob base: ventaja local + forma + goles
    prob = 55  # ventaja local base
    prob += (pct_local - 50) * 0.2
    prob -= (pct_visit - 50) * 0.15
    prob += (avg_gf_local - 1.2) * 3
    prob += (avg_gc_visit - 1.2) * 2

    return max(45, min(90, round(prob, 1)))


def calcular_filtros(fixture_data, forma_local, forma_visitante):
    filtros = []
    ajuste = 0

    f = fixture_data["response"][0]
    local = f["teams"]["home"]
    visitante = f["teams"]["away"]
    home_id = local["id"]
    away_id = visitante["id"]

    # ── F6: Local con buen rendimiento en casa ──
    victorias_local = sum(
        1 for p in forma_local
        if p["teams"]["home"]["id"] == home_id
        and (p["goals"]["home"] or 0) > (p["goals"]["away"] or 0)
    )
    partidos_local_casa = sum(
        1 for p in forma_local
        if p["teams"]["home"]["id"] == home_id
    )
    if partidos_local_casa > 0:
        pct = victorias_local / partidos_local_casa
        if pct >= 0.4:
            filtros.append({
                "codigo": "F6",
                "descripcion": f"{local['name']} ≥40% victorias en casa ✓",
                "ajuste": 0
            })

    # ── F10: Visitante jugó hace ≤3 días ──
    partidos_visit_ordenados = sorted(
        [p for p in forma_visitante if p["teams"]["away"]["id"] == away_id],
        key=lambda x: x["fixture"]["date"]
    )
    if partidos_visit_ordenados:
        ultimo = partidos_visit_ordenados[-1]
        fecha_ultimo = datetime.fromisoformat(ultimo["fixture"]["date"][:10])
        fecha_partido = datetime.fromisoformat(f["fixture"]["date"][:10])
        dias_diff = (fecha_partido - fecha_ultimo).days
        if 0 < dias_diff <= 3:
            filtros.append({
                "codigo": "F10",
                "descripcion": f"{visitante['name']} jugó hace {dias_diff} días (fatiga)",
                "ajuste": -6
            })
            ajuste += -6

    # ── F3: Fase de grupos / presión narrativa ──
    ronda = f["league"].get("round", "")
    if "Group" in ronda or "Fase" in ronda or "group" in ronda.lower():
        filtros.append({
            "codigo": "F3",
            "descripcion": f"Partido de fase de grupos — presión por clasificar",
            "ajuste": 0
        })

    # ── Calcular prob base con forma real ──
    prob_base = calcular_prob_base(forma_local, forma_visitante, home_id, away_id)

    return {
        "local": local["name"],
        "visitante": visitante["name"],
        "filtros": filtros,
        "ajuste_total": ajuste,
        "prob_base": prob_base
    }


def calcular_mercados(prob_base, ajuste):
    prob = prob_base + ajuste
    prob = max(45, min(90, prob))

    mercados = []

    def agregar(nombre, formula, umbral=82):
        val = round(min(97, max(30, formula)), 1)
        mercados.append({
            "nombre": nombre,
            "prob": val,
            "riesgo": round(100 - val, 1),
            "tipo": "aprobado" if val >= umbral else "informativo"
        })

    agregar("No Termina 0-0",        88 + (prob - 70) * 0.3)
    agregar("Doble Oportunidad 1X",  80 + (prob - 70) * 0.5)
    agregar("Resultado Final — Local", prob)
    agregar("Goles Totales Over 1.5", 75 + (prob - 70) * 0.4)
    agregar("Goles Totales Over 2.5", 60 + (prob - 70) * 0.4)
    agregar("Handicap Asiático -0.5", prob - 5)

    return mercados