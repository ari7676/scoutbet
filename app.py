# ── BACKTEST ──────────────────────────────────────────────────────────────

import re

def _clasificar_mercado(texto):
    if not texto:
        return None
    t = texto.lower()
    if re.search(r'resultado final|victoria|gana|1x2', t) and 'doble' not in t:
        if 'local' in t or 'home' in t:
            return '1X2 Local'
        if 'visitante' in t or 'away' in t:
            return '1X2 Visitante'
        return '1X2'
    if 'doble' in t or 'double chance' in t:
        return 'Doble Oport.'
    if 'over 3.5' in t or 'más de 3.5' in t:
        return 'Over 3.5'
    if 'under 2.5' in t or 'menos de 2.5' in t:
        return 'Under 2.5'
    if 'over 2.5' in t or 'más de 2.5' in t:
        return 'Over 2.5'
    if 'over 1.5' in t or 'más de 1.5' in t:
        return 'Over 1.5'
    if 'btts' in t or 'ambos anotan' in t:
        if 'no' in t:
            return 'BTTS No'
        return 'BTTS'
    if 'clean sheet' in t or 'portería' in t:
        return 'Clean Sheet'
    if 'win to nil' in t or 'victoria a cero' in t:
        return 'Win to Nil'
    if 'empate' in t or 'draw' in t:
        return 'Empate'
    if '1er tiempo' in t or 'primer tiempo' in t:
        return 'HT'
    return 'Otro'


def _calcular_backtest():
    from collections import defaultdict
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT mercado_principal, mp_prob, mp_acertado,
               combinable, comb_prob, comb_acertado,
               resultado_home, resultado_away, home, away
        FROM predicciones
        WHERE verificado = 1
          AND resultado_home IS NOT NULL
    """)
    rows = cur.fetchall()
    conn.close()

    mercado_stats = defaultdict(lambda: {"n": 0, "ok": 0, "probs": []})
    calib_data = []

    UMBRALES_ACTUALES = {
        '1X2 Local': 70, '1X2 Visitante': 70, '1X2': 70,
        'Doble Oport.': 75, 'Over 3.5': 70, 'Over 2.5': 70,
        'Under 2.5': 70, 'Over 1.5': 80, 'BTTS': 70, 'BTTS No': 70,
        'Clean Sheet': 70, 'Win to Nil': 70, 'Empate': 35, 'HT': 75,
    }

    for row in rows:
        mp_texto, mp_prob, mp_acertado, comb_texto, comb_prob, comb_acertado, hg, ag, home, away = row

        if mp_texto and mp_prob is not None and mp_acertado is not None:
            tipo = _clasificar_mercado(mp_texto)
            if tipo:
                acertado = bool(mp_acertado)
                mercado_stats[tipo]["n"] += 1
                mercado_stats[tipo]["ok"] += 1 if acertado else 0
                mercado_stats[tipo]["probs"].append(mp_prob)
                calib_data.append((mp_prob, 1 if acertado else 0))

        if comb_texto and comb_prob is not None and comb_acertado is not None:
            tipo = _clasificar_mercado(comb_texto)
            if tipo:
                mercado_stats[tipo]["n"] += 1
                mercado_stats[tipo]["ok"] += 1 if bool(comb_acertado) else 0
                mercado_stats[tipo]["probs"].append(comb_prob)

    mercados_result = []
    for tipo, stats in sorted(mercado_stats.items(), key=lambda x: -x[1]["n"]):
        n = stats["n"]
        ok = stats["ok"]
        if n == 0:
            continue
        acc = round(ok / n * 100, 1)
        prob_media = round(sum(stats["probs"]) / len(stats["probs"]), 1)
        umbral = UMBRALES_ACTUALES.get(tipo, 70)
        mercados_result.append({
            "mercado": tipo, "n": n, "ok": ok, "accuracy": acc,
            "prob_media": prob_media, "umbral_actual": umbral,
            "estado": "bueno" if acc >= 80 else "marginal" if acc >= 70 else "bajo"
        })

    calib_buckets = []
    for low in range(50, 100, 5):
        high = low + 5
        bucket = [(p, a) for p, a in calib_data if low <= p < high]
        if bucket:
            acc_real = round(sum(a for _, a in bucket) / len(bucket) * 100, 1)
            calib_buckets.append({
                "rango": f"{low}–{high}%",
                "n": len(bucket),
                "accuracy_real": acc_real,
                "diferencia": round(acc_real - (low + 2), 1)
            })

    umbrales_optimos = []
    for tipo, stats in mercado_stats.items():
        if stats["n"] < 8:
            continue
        umbral_actual = UMBRALES_ACTUALES.get(tipo, 70)
        pares = list(zip(stats["probs"], [1] * stats["ok"] + [0] * (stats["n"] - stats["ok"])))
        acc_actual = round(stats["ok"] / stats["n"] * 100, 1)
        mejor_umbral = umbral_actual
        mejor_acc = acc_actual
        for t in range(60, 95):
            filtrado = [(p, a) for p, a in pares if p >= t]
            if len(filtrado) < max(5, stats["n"] * 0.3):
                break
            acc_t = round(sum(a for _, a in filtrado) / len(filtrado) * 100, 1)
            if acc_t > mejor_acc:
                mejor_acc = acc_t
                mejor_umbral = t
        n_actual = len([p for p in stats["probs"] if p >= umbral_actual])
        n_optimo = len([p for p in stats["probs"] if p >= mejor_umbral])
        umbrales_optimos.append({
            "mercado": tipo,
            "umbral_actual": umbral_actual,
            "umbral_optimo": mejor_umbral,
            "accuracy_actual": acc_actual,
            "accuracy_optima": mejor_acc,
            "ganancia_acc": round(mejor_acc - acc_actual, 1),
            "perdida_cobertura": n_optimo - n_actual
        })

    total_n = sum(s["n"] for s in mercado_stats.values())
    total_ok = sum(s["ok"] for s in mercado_stats.values())

    return {
        "resumen": {
            "total_predicciones": total_n,
            "total_acertadas": total_ok,
            "accuracy_global": round(total_ok / total_n * 100, 1) if total_n > 0 else 0,
            "mercados_evaluados": len(mercados_result)
        },
        "por_mercado": mercados_result,
        "calibracion": calib_buckets,
        "umbrales_optimos": umbrales_optimos
    }


@app.route("/backtest/json")
def backtest_json():
    if not session.get("logged_in"):
        return jsonify({"error": "no auth"}), 401
    try:
        return jsonify(_calcular_backtest())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/backtest")
def backtest_page():
    if not session.get("logged_in"):
        return redirect("/login")
    return render_template("backtest.html")