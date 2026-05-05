"""
MatchIQ Backtest — Temporada 2024/2025
Trae todos los resultados, reconstruye forma partido a partido,
aplica el modelo de probabilidades y evalúa cada mercado.
"""
import requests, time, json, math
from collections import defaultdict
from datetime import datetime

FD_KEY = "e28d6269df9441fea1b6a19548e982c6"
FD_URL = "https://api.football-data.org/v4"
FD_HEADERS = {"X-Auth-Token": FD_KEY}

LIGAS = {
    "PL":  {"nombre": "Premier League",  "season": 2024},
    "PD":  {"nombre": "La Liga",         "season": 2024},
    "SA":  {"nombre": "Serie A",          "season": 2024},
    "BL1": {"nombre": "Bundesliga",       "season": 2024},
    "FL1": {"nombre": "Ligue 1",          "season": 2024},
}

def fd_get(ep, params=None):
    r = requests.get(f"{FD_URL}{ep}", headers=FD_HEADERS, params=params, timeout=20)
    if r.status_code == 429:
        print("  ⏳ Rate limit, esperando 60s...")
        time.sleep(60)
        r = requests.get(f"{FD_URL}{ep}", headers=FD_HEADERS, params=params, timeout=20)
    return r.json() if r.ok else {}

# ─── FORM TRACKER ────────────────────────────────────────────
class FormTracker:
    def __init__(self):
        self.matches = defaultdict(list)  # team -> [{date, home, gf, gc, result}]
        self.standings = defaultdict(lambda: {"w":0,"d":0,"l":0,"gf":0,"gc":0,"pts":0,"played":0})

    def add_result(self, home, away, hg, ag, date):
        hr = "W" if hg > ag else ("D" if hg == ag else "L")
        ar = "W" if ag > hg else ("D" if ag == hg else "L")
        self.matches[home].append({"date": date, "is_home": True, "gf": hg, "gc": ag, "result": hr, "opp": away})
        self.matches[away].append({"date": date, "is_home": False, "gf": ag, "gc": hg, "result": ar, "opp": home})
        # Standings
        for team, g_for, g_ag, res in [(home, hg, ag, hr), (away, ag, hg, ar)]:
            s = self.standings[team]
            s["played"] += 1
            s["gf"] += g_for; s["gc"] += g_ag
            if res == "W": s["w"] += 1; s["pts"] += 3
            elif res == "D": s["d"] += 1; s["pts"] += 1
            else: s["l"] += 1

    def get_form(self, team, n=10):
        ms = self.matches.get(team, [])[-n:]
        if not ms:
            return {"matches":0,"w":0,"d":0,"l":0,"gf":0,"gc":0,"ppg":0,"gf_avg":1.2,"gc_avg":1.0,
                    "clean_sheets":0,"failed_to_score":0}
        w = sum(1 for m in ms if m["result"]=="W")
        d = sum(1 for m in ms if m["result"]=="D")
        l = len(ms) - w - d
        gf = sum(m["gf"] for m in ms)
        gc = sum(m["gc"] for m in ms)
        cs = sum(1 for m in ms if m["gc"]==0)
        fts = sum(1 for m in ms if m["gf"]==0)
        n_m = len(ms)
        return {"matches":n_m,"w":w,"d":d,"l":l,"gf":gf,"gc":gc,
                "ppg":round((w*3+d)/n_m,2),"gf_avg":round(gf/n_m,2),"gc_avg":round(gc/n_m,2),
                "clean_sheets":cs,"failed_to_score":fts}

    def get_home_form(self, team, n=10):
        ms = [m for m in self.matches.get(team, []) if m["is_home"]][-n:]
        if not ms: return None
        w = sum(1 for m in ms if m["result"]=="W")
        d = sum(1 for m in ms if m["result"]=="D")
        return {"playedGames":len(ms),"won":w,"draw":d,"lost":len(ms)-w-d}

    def get_away_form(self, team, n=10):
        ms = [m for m in self.matches.get(team, []) if not m["is_home"]][-n:]
        if not ms: return None
        w = sum(1 for m in ms if m["result"]=="W")
        d = sum(1 for m in ms if m["result"]=="D")
        return {"playedGames":len(ms),"won":w,"draw":d,"lost":len(ms)-w-d}

    def get_position(self, team):
        ranked = sorted(self.standings.items(), key=lambda x: (-x[1]["pts"], -(x[1]["gf"]-x[1]["gc"]), -x[1]["gf"]))
        for i, (t, s) in enumerate(ranked):
            if t == team:
                return {"position": i+1, "points": s["pts"], "goalsFor": s["gf"], "goalDifference": s["gf"]-s["gc"]}
        return None

# ─── PROBABILITY MODEL (replica exacta de app.py) ───────────
def _cuota(prob_pct):
    if prob_pct <= 0: return 99.0
    return round(100 / prob_pct * 0.95, 2)

def calc_probabilities(hf, af, hh, aa, hp, ap, te=20):
    forma_h_score = round(hf["ppg"]/3*100) if hf["matches"]>0 else 50
    forma_a_score = round(af["ppg"]/3*100) if af["matches"]>0 else 50
    h2h_score = 50  # no H2H en backtest
    localia_h_score = localia_a_score = 50
    if hh and hh["playedGames"] > 0:
        localia_h_score = round((hh["won"]*3+hh["draw"])/(hh["playedGames"]*3)*100)
    if aa and aa["playedGames"] > 0:
        localia_a_score = round((aa["won"]*3+aa["draw"])/(aa["playedGames"]*3)*100)
    pos_h = hp.get("position") if hp else None
    pos_a = ap.get("position") if ap else None
    pos_h_score = round((1-(pos_h-1)/max(te-1,1))*100) if pos_h else 50
    pos_a_score = round((1-(pos_a-1)/max(te-1,1))*100) if pos_a else 50

    ph = round(forma_h_score*.25 + h2h_score*.10 + localia_h_score*.35 + pos_h_score*.30)
    pa = round(forma_a_score*.25 + (100-h2h_score)*.10 + localia_a_score*.35 + pos_a_score*.30)
    ph = min(95, ph+8)
    pd = max(0, 100-ph-pa)
    tp = ph+pa+pd
    if tp > 0:
        ph = round(ph/tp*100); pa = round(pa/tp*100); pd = 100-ph-pa

    egh = hf["gf_avg"] if hf["matches"]>0 else 1.3
    ech = hf["gc_avg"] if hf["matches"]>0 else 1.0
    ega = af["gf_avg"] if af["matches"]>0 else 1.0
    eca = af["gc_avg"] if af["matches"]>0 else 1.3
    ge = round((egh+ega+ech+eca)/2, 2)

    hfts = hf["failed_to_score"]/max(hf["matches"],1)
    afts = af["failed_to_score"]/max(af["matches"],1)
    hcs_r = hf["clean_sheets"]/max(hf["matches"],1)
    acs_r = af["clean_sheets"]/max(af["matches"],1)
    hsc = 1-hfts; asc = 1-afts

    return ph, pa, pd, ge, egh, ega, hsc, asc, hfts, afts, hcs_r, acs_r

def evaluate_markets(ph, pa, pd, ge, egh, ega, hsc, asc, hfts, afts, hcs_r, acs_r, hn, an):
    mercados = []

    # 1X2 — Local subir a 75%, Visit mantener 70%, Empate ELIMINAR (23.6% acierto)
    if ph >= 40:
        mercados.append({"tipo":"1X2_H","prob":ph,"aprobado":ph>=75})
    if pa >= 40:
        mercados.append({"tipo":"1X2_A","prob":pa,"aprobado":pa>=70})

    # DC — mantener, funcionan bien
    dc1x = ph+pd; dcx2 = pa+pd
    if dc1x >= 55:
        mercados.append({"tipo":"DC_1X","prob":dc1x,"aprobado":dc1x>=75})
    if dcx2 >= 55:
        mercados.append({"tipo":"DC_X2","prob":dcx2,"aprobado":dcx2>=75})

    # O/U 2.5 — subir Over a 75%, Under a 80%
    if ge >= 2.5:
        p = min(85, round(50+(ge-2.5)*20))
        mercados.append({"tipo":"O25","prob":p,"aprobado":p>=75})
    if ge <= 2.5:
        p = min(85, round(50+(2.5-ge)*20))
        mercados.append({"tipo":"U25","prob":p,"aprobado":p>=80})

    # O 1.5 — mantener, 81.2% perfecto
    if ge >= 1.8:
        p = min(92, round(60+(ge-1.5)*15))
        mercados.append({"tipo":"O15","prob":p,"aprobado":p>=80})

    # O/U 3.5 — Over subir a 85% (22% acierto era desastre), Under mantener
    if ge >= 3.0:
        p = min(80, round(40+(ge-3.0)*25))
        mercados.append({"tipo":"O35","prob":p,"aprobado":p>=85})
    if ge <= 3.0:
        p = min(80, round(40+(3.0-ge)*25))
        mercados.append({"tipo":"U35","prob":p,"aprobado":p>=70})

    # BTTS — subir a 82% (57% era moneda al aire)
    btts = min(82, max(20, round(hsc*50+asc*50)))
    if btts >= 45:
        mercados.append({"tipo":"BTTS","prob":btts,"aprobado":btts>=82})

    # GE Over 0.5 — Local mantener 85%, Visit subir a 90%
    ho = min(95, max(30, round(hsc*100)))
    if ho >= 70:
        mercados.append({"tipo":"GE_H05","prob":ho,"aprobado":ho>=85})
    ao = min(95, max(30, round(asc*100)))
    if ao >= 70:
        mercados.append({"tipo":"GE_A05","prob":ao,"aprobado":ao>=90})

    # GE Over 1.5 — Local subir a 78%, Visit subir a 82%
    h15 = min(85, max(20, round(egh/(egh+0.8)*100))) if egh >= 1.3 else 0
    if h15 >= 50:
        mercados.append({"tipo":"GE_H15","prob":h15,"aprobado":h15>=78})
    a15 = min(85, max(20, round(ega/(ega+0.8)*100))) if ega >= 1.3 else 0
    if a15 >= 50:
        mercados.append({"tipo":"GE_A15","prob":a15,"aprobado":a15>=82})

    # Clean Sheet — subir a 80%
    hcs_p = min(85, round(hcs_r*100*(1-asc+0.3)))
    if hcs_p >= 30:
        mercados.append({"tipo":"CS_H","prob":hcs_p,"aprobado":hcs_p>=80})
    acs_p = min(85, round(acs_r*100*(1-hsc+0.3)))
    if acs_p >= 30:
        mercados.append({"tipo":"CS_A","prob":acs_p,"aprobado":acs_p>=80})

    # Win to Nil — fix formula (antes era demasiado conservadora)
    wtn_h = min(80, round(ph * hcs_r)) if ph >= 55 and hcs_r >= 0.4 else 0
    if wtn_h >= 25:
        mercados.append({"tipo":"WTN_H","prob":wtn_h,"aprobado":wtn_h>=55})
    wtn_a = min(80, round(pa * acs_r)) if pa >= 55 and acs_r >= 0.4 else 0
    if wtn_a >= 25:
        mercados.append({"tipo":"WTN_A","prob":wtn_a,"aprobado":wtn_a>=55})

    # 1T Over/Under 0.5
    ght = ge*0.45
    p1o = min(88, round(50+(ght-0.5)*40)) if ght>=0.5 else max(20, round(ght/0.5*40))
    p1u = 100-p1o
    if p1o >= 50:
        mercados.append({"tipo":"HT_O05","prob":p1o,"aprobado":p1o>=75})
    if p1u >= 40:
        mercados.append({"tipo":"HT_U05","prob":p1u,"aprobado":p1u>=70})

    # No 0-0 — perfecto, mantener
    p00 = round(hfts*acs_r*100); pn00 = min(95, 100-p00)
    if pn00 >= 70:
        mercados.append({"tipo":"NO00","prob":pn00,"aprobado":pn00>=82})

    return mercados

def check_market(tipo, hg, ag):
    total = hg + ag
    checks = {
        "1X2_H": hg > ag,
        "1X2_A": ag > hg,
        "1X2_D": hg == ag,
        "DC_1X": hg >= ag,
        "DC_X2": ag >= hg,
        "O25": total > 2.5,
        "U25": total < 2.5,
        "O15": total > 1.5,
        "O35": total > 3.5,
        "U35": total < 3.5,
        "BTTS": hg > 0 and ag > 0,
        "GE_H05": hg > 0,
        "GE_A05": ag > 0,
        "GE_H15": hg >= 2,
        "GE_A15": ag >= 2,
        "CS_H": ag == 0,
        "CS_A": hg == 0,
        "WTN_H": hg > ag and ag == 0,
        "WTN_A": ag > hg and hg == 0,
        "NO00": total > 0,
        "HT_O05": None,  # no data
        "HT_U05": None,
    }
    return checks.get(tipo, None)

# ─── MAIN ────────────────────────────────────────────────────
def run_backtest():
    all_results = {}

    print("=" * 60)
    print("  MatchIQ BACKTEST — Temporada 2024/2025")
    print("=" * 60)

    for code, cfg in LIGAS.items():
        print(f"\n📥 Descargando {cfg['nombre']} ({code}) 2024/25...")
        data = fd_get(f"/competitions/{code}/matches", {"season": cfg["season"], "status": "FINISHED"})
        matches = data.get("matches", [])
        print(f"   {len(matches)} partidos terminados")
        time.sleep(7)  # rate limit

        if not matches:
            continue

        # Sort by date
        matches.sort(key=lambda m: m["utcDate"])

        tracker = FormTracker()
        te = len(set(m["homeTeam"]["name"] for m in matches if m["homeTeam"].get("name")))

        league_stats = defaultdict(lambda: {"total":0,"acertados":0,"aprobados_total":0,"aprobados_acertados":0})
        match_count = 0

        for match in matches:
            hn = match["homeTeam"].get("name")
            an = match["awayTeam"].get("name")
            if not hn or not an:
                continue

            sc = match.get("score", {}).get("fullTime", {})
            hg = sc.get("home", 0)
            ag = sc.get("away", 0)

            # Need at least 5 matches per team to evaluate
            hf = tracker.get_form(hn)
            af = tracker.get_form(an)

            if hf["matches"] >= 5 and af["matches"] >= 5:
                match_count += 1
                hh = tracker.get_home_form(hn)
                aa = tracker.get_away_form(an)
                hp = tracker.get_position(hn) or {}
                ap = tracker.get_position(an) or {}

                ph, pa, pd, ge, egh, ega, hsc, asc, hfts, afts, hcs_r, acs_r = \
                    calc_probabilities(hf, af, hh, aa, hp, ap, te)

                mercados = evaluate_markets(ph, pa, pd, ge, egh, ega, hsc, asc, hfts, afts, hcs_r, acs_r, hn, an)

                for m in mercados:
                    resultado = check_market(m["tipo"], hg, ag)
                    if resultado is None:
                        continue
                    s = league_stats[m["tipo"]]
                    s["total"] += 1
                    if resultado:
                        s["acertados"] += 1
                    if m["aprobado"]:
                        s["aprobados_total"] += 1
                        if resultado:
                            s["aprobados_acertados"] += 1

            # Add result AFTER evaluation (no peeking)
            tracker.add_result(hn, an, hg, ag, match["utcDate"])

        all_results[code] = {"nombre": cfg["nombre"], "matches": match_count, "stats": dict(league_stats)}
        print(f"   ✓ {match_count} partidos evaluados")

    return all_results

def print_report(results):
    # Aggregate across all leagues
    global_stats = defaultdict(lambda: {"total":0,"acertados":0,"aprobados_total":0,"aprobados_acertados":0})

    for code, data in results.items():
        for tipo, s in data["stats"].items():
            g = global_stats[tipo]
            g["total"] += s["total"]
            g["acertados"] += s["acertados"]
            g["aprobados_total"] += s["aprobados_total"]
            g["aprobados_acertados"] += s["aprobados_acertados"]

    nombres = {
        "1X2_H":"1X2 Local","1X2_A":"1X2 Visitante","1X2_D":"Empate",
        "DC_1X":"DC 1X","DC_X2":"DC X2",
        "O25":"Over 2.5","U25":"Under 2.5","O15":"Over 1.5","O35":"Over 3.5","U35":"Under 3.5",
        "BTTS":"BTTS","GE_H05":"Local O0.5","GE_A05":"Visit O0.5",
        "GE_H15":"Local O1.5","GE_A15":"Visit O1.5",
        "CS_H":"CS Local","CS_A":"CS Visit",
        "WTN_H":"WTN Local","WTN_A":"WTN Visit",
        "NO00":"No 0-0","HT_O05":"1T Over 0.5","HT_U05":"1T Under 0.5"
    }

    print("\n" + "=" * 80)
    print("  RESULTADOS GLOBALES — 5 LIGAS 2024/2025")
    print("=" * 80)

    total_matches = sum(d["matches"] for d in results.values())
    print(f"\n  Partidos evaluados: {total_matches}")
    print(f"  Ligas: {', '.join(d['nombre'] for d in results.values())}\n")

    print(f"  {'Mercado':<18} {'Evaluados':>9} {'Tasa Real':>10} {'Aprobados':>10} {'Tasa Aprob':>11} {'Rendim':>8}")
    print("  " + "─" * 70)

    rows = []
    for tipo, s in sorted(global_stats.items(), key=lambda x: x[0]):
        if s["total"] == 0: continue
        tasa = round(s["acertados"]/s["total"]*100, 1)
        aprob_tasa = round(s["aprobados_acertados"]/s["aprobados_total"]*100, 1) if s["aprobados_total"] > 0 else 0
        diff = round(aprob_tasa - tasa, 1) if s["aprobados_total"] > 0 else 0
        nombre = nombres.get(tipo, tipo)
        rows.append((nombre, s["total"], tasa, s["aprobados_total"], aprob_tasa, diff))
        marker = "✓" if aprob_tasa >= 60 else "⚠" if aprob_tasa >= 50 else "✗"
        print(f"  {nombre:<18} {s['total']:>9} {tasa:>9.1f}% {s['aprobados_total']:>10} {aprob_tasa:>10.1f}% {diff:>+7.1f}% {marker}")

    # Resumen aprobados
    total_aprob = sum(s["aprobados_total"] for s in global_stats.values())
    total_aprob_ok = sum(s["aprobados_acertados"] for s in global_stats.values())
    if total_aprob > 0:
        print("\n  " + "─" * 70)
        print(f"  {'TOTAL APROBADOS':<18} {'':>9} {'':>10} {total_aprob:>10} {round(total_aprob_ok/total_aprob*100,1):>10.1f}%")

    # Per league breakdown
    print("\n\n" + "=" * 80)
    print("  DESGLOSE POR LIGA")
    print("=" * 80)

    for code, data in results.items():
        aprob_t = sum(s["aprobados_total"] for s in data["stats"].values())
        aprob_ok = sum(s["aprobados_acertados"] for s in data["stats"].values())
        pct = round(aprob_ok/aprob_t*100, 1) if aprob_t > 0 else 0
        print(f"\n  {data['nombre']} ({code}): {data['matches']} partidos, {aprob_t} aprobados, {pct}% acierto")

    return global_stats, rows

def generate_html_report(results, global_stats, rows):
    nombres = {
        "1X2_H":"1X2 Local","1X2_A":"1X2 Visitante","1X2_D":"Empate",
        "DC_1X":"DC 1X","DC_X2":"DC X2",
        "O25":"Over 2.5","U25":"Under 2.5","O15":"Over 1.5","O35":"Over 3.5","U35":"Under 3.5",
        "BTTS":"BTTS","GE_H05":"Local O0.5","GE_A05":"Visit O0.5",
        "GE_H15":"Local O1.5","GE_A15":"Visit O1.5",
        "CS_H":"CS Local","CS_A":"CS Visit",
        "WTN_H":"WTN Local","WTN_A":"WTN Visit",
        "NO00":"No 0-0"
    }

    total_matches = sum(d["matches"] for d in results.values())
    total_aprob = sum(s["aprobados_total"] for s in global_stats.values())
    total_aprob_ok = sum(s["aprobados_acertados"] for s in global_stats.values())
    total_pct = round(total_aprob_ok/total_aprob*100, 1) if total_aprob > 0 else 0

    table_rows = ""
    for nombre, total, tasa, aprob, aprob_tasa, diff in rows:
        color = "#22c55e" if aprob_tasa >= 65 else "#f59e0b" if aprob_tasa >= 50 else "#ef4444"
        diff_c = "#22c55e" if diff > 0 else "#ef4444"
        table_rows += f"""<tr>
            <td>{nombre}</td><td>{total}</td><td>{tasa}%</td>
            <td>{aprob}</td><td style="color:{color};font-weight:700">{aprob_tasa}%</td>
            <td style="color:{diff_c}">{diff:+.1f}%</td>
        </tr>"""

    liga_cards = ""
    for code, data in results.items():
        at = sum(s["aprobados_total"] for s in data["stats"].values())
        ao = sum(s["aprobados_acertados"] for s in data["stats"].values())
        pct = round(ao/at*100, 1) if at > 0 else 0
        col = "#22c55e" if pct >= 65 else "#f59e0b" if pct >= 50 else "#ef4444"
        liga_cards += f"""<div style="background:#132040;border-radius:10px;padding:20px;border-left:3px solid {col}">
            <div style="font-size:13px;color:#7b8494;margin-bottom:6px">{data['nombre']}</div>
            <div style="font-size:28px;font-weight:700;color:{col};font-family:'JetBrains Mono',monospace">{pct}%</div>
            <div style="font-size:12px;color:#b0b8c8;margin-top:4px">{ao}/{at} aprobados · {data['matches']} partidos</div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MatchIQ Backtest 2024/25</title>
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=DM+Sans:wght@400;600;700&family=JetBrains+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#060d1f;color:#e8eaf0;font-family:'DM Sans',sans-serif;padding:30px 20px}}
h1{{font-family:'Bebas Neue',sans-serif;color:#d4a537;font-size:32px;letter-spacing:2px;margin-bottom:6px}}
.sub{{color:#7b8494;font-size:14px;margin-bottom:30px}}
.summary{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-bottom:30px}}
.sbox{{background:#0c1529;border:1px solid #1e3060;border-radius:12px;padding:24px;text-align:center}}
.sbox .val{{font-family:'JetBrains Mono',monospace;font-size:36px;font-weight:700;color:#d4a537}}
.sbox .lbl{{font-size:12px;color:#7b8494;text-transform:uppercase;letter-spacing:1px;margin-top:6px}}
.ligas{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:30px}}
table{{width:100%;border-collapse:collapse;background:#0c1529;border:1px solid #1e3060;border-radius:12px;overflow:hidden}}
th{{background:#132040;padding:12px 16px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:1px;color:#7b8494}}
td{{padding:12px 16px;border-bottom:1px solid #1e3060;font-size:14px;font-family:'JetBrains Mono',monospace}}
tr:hover{{background:#132040}}
.note{{margin-top:24px;padding:16px;background:#132040;border-radius:10px;border-left:3px solid #d4a537;font-size:13px;color:#b0b8c8;line-height:1.8}}
@media(max-width:768px){{.summary{{grid-template-columns:1fr}}.ligas{{grid-template-columns:1fr}}table{{font-size:12px}}td,th{{padding:8px 10px}}}}
</style></head><body>
<h1>⚡ MatchIQ Backtest</h1>
<div class="sub">Temporada 2024/2025 · {total_matches} partidos · 5 ligas europeas</div>

<div class="summary">
    <div class="sbox"><div class="val">{total_matches}</div><div class="lbl">Partidos evaluados</div></div>
    <div class="sbox"><div class="val">{total_aprob}</div><div class="lbl">Mercados aprobados</div></div>
    <div class="sbox"><div class="val" style="color:{'#22c55e' if total_pct>=65 else '#f59e0b'}">{total_pct}%</div><div class="lbl">Tasa acierto aprobados</div></div>
</div>

<h2 style="font-family:'Bebas Neue',sans-serif;color:#d4a537;font-size:22px;margin-bottom:14px">Por liga</h2>
<div class="ligas">{liga_cards}</div>

<h2 style="font-family:'Bebas Neue',sans-serif;color:#d4a537;font-size:22px;margin-bottom:14px">Detalle por mercado</h2>
<table>
<thead><tr><th>Mercado</th><th>Evaluados</th><th>Tasa real</th><th>Aprobados</th><th>Tasa aprob</th><th>Δ vs base</th></tr></thead>
<tbody>{table_rows}</tbody>
</table>

<div class="note">
<strong style="color:#d4a537">Cómo leer:</strong><br>
• <strong>Tasa real</strong>: % de veces que el resultado se dio (sin filtro de aprobación)<br>
• <strong>Tasa aprob</strong>: % de acierto solo entre los que el modelo aprobó<br>
• <strong>Δ vs base</strong>: diferencia entre aprobados y tasa real — si es positivo, el filtro agrega valor<br>
• Mercados sin datos de HT (1er Tiempo) no se pueden verificar con esta API
</div>
</body></html>"""

    with open("backtest_2024_25.html", "w", encoding="utf-8") as f:
        f.write(html)

if __name__ == "__main__":
    results = run_backtest()
    global_stats, rows = print_report(results)
    generate_html_report(results, global_stats, rows)
    print("\n✅ Reporte HTML guardado en backtest_2024_25.html")