# analisis.py
# Add your analysis functions and logic here.
def calcular_filtros(fixture_data, forma_local, forma_visitante):
    filtros = []
    ajuste = 0

    f = fixture_data["response"][0]
    local = f["teams"]["home"]
    visitante = f["teams"]["away"]

    # ── F6: Local con buen rendimiento en casa ──
    victorias_local = sum(
        1 for p in forma_local
        if p["teams"]["home"]["id"] == local["id"]
        and p["goals"]["home"] > p["goals"]["away"]
    )
    partidos_local = sum(
        1 for p in forma_local
        if p["teams"]["home"]["id"] == local["id"]
    )
    if partidos_local > 0:
        pct = victorias_local / partidos_local
        if pct >= 0.4:
            filtros.append({
                "codigo": "F6",
                "descripcion": f"{local['name']} ≥40% victorias en casa ✓",
                "ajuste": 0
            })

    # ── F10: Visitante jugó hace ≤3 días ──
    from datetime import datetime, timedelta
    if forma_visitante:
        ultimo = forma_visitante[-1]
        fecha_ultimo = datetime.fromisoformat(ultimo["fixture"]["date"][:10])
        fecha_partido = datetime.fromisoformat(f["fixture"]["date"][:10])
        dias_diff = (fecha_partido - fecha_ultimo).days
        if dias_diff <= 3:
            filtros.append({
                "codigo": "F10",
                "descripcion": f"{visitante['name']} jugó hace {dias_diff} días (fatiga)",
                "ajuste": -6
            })
            ajuste += -6

    # ── F3: Presión narrativa del local ──
    if f["league"].get("round", "").startswith("Group"):
        filtros.append({
            "codigo": "F3",
            "descripcion": f"{local['name']} necesita puntos (fase de grupos)",
            "ajuste": 0
        })

    return {
        "local": local["name"],
        "visitante": visitante["name"],
        "filtros": filtros,
        "ajuste_total": ajuste
    }


def calcular_mercados(prob_base_local, ajuste):
    prob = prob_base_local + ajuste

    mercados = []

    # No termina 0-0
    mercados.append({
        "nombre": "No Termina 0-0",
        "prob": min(97, 88 + (prob - 70) * 0.3),
        "tipo": "aprobado" if 88 + (prob - 70) * 0.3 >= 82 else "informativo"
    })

    # Doble oportunidad 1X
    mercados.append({
        "nombre": f"Doble Oportunidad Local 1X",
        "prob": min(97, 80 + (prob - 70) * 0.5),
        "tipo": "aprobado" if 80 + (prob - 70) * 0.5 >= 82 else "informativo"
    })

    # Resultado final local
    mercados.append({
        "nombre": "Resultado Final — Local",
        "prob": round(prob, 1),
        "tipo": "aprobado" if prob >= 82 else "informativo"
    })

    # Over 1.5
    mercados.append({
        "nombre": "Goles Totales Over 1.5",
        "prob": min(95, 75 + (prob - 70) * 0.4),
        "tipo": "aprobado" if 75 + (prob - 70) * 0.4 >= 82 else "informativo"
    })

    # Over 2.5
    mercados.append({
        "nombre": "Goles Totales Over 2.5",
        "prob": min(85, 60 + (prob - 70) * 0.4),
        "tipo": "aprobado" if 60 + (prob - 70) * 0.4 >= 82 else "informativo"
    })

    # Calcular riesgo
    for m in mercados:
        m["prob"] = round(m["prob"], 1)
        m["riesgo"] = round(100 - m["prob"], 1)

    return mercados