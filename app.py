"""
MatchIQ — Backend Flask
APIs: football-data.org (principal) + api-sports.io (stats avanzadas)
Narrativas inteligentes generadas desde datos estadísticos
"""

from flask import Flask, jsonify, render_template
from datetime import datetime, timedelta
import requests, time, math

app = Flask(__name__)

FD_KEY = "e28d6269df9441fea1b6a19548e982c6"
FD_URL = "https://api.football-data.org/v4"
FD_HEADERS = {"X-Auth-Token": FD_KEY}

AS_KEY = "fb49b7a70ea23977f8e7711c5ed027b1"
AS_URL = "https://v3.football.api-sports.io"
AS_HEADERS = {"x-apisports-key": AS_KEY}

LIGAS = {
    "PL":  {"nombre": "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League",  "as_id": 39,  "season": 2025},
    "PD":  {"nombre": "🇪🇸 La Liga",           "as_id": 140, "season": 2025},
    "SA":  {"nombre": "🇮🇹 Serie A",            "as_id": 135, "season": 2025},
    "BL1": {"nombre": "🇩🇪 Bundesliga",         "as_id": 78,  "season": 2025},
    "FL1": {"nombre": "🇫🇷 Ligue 1",            "as_id": 61,  "season": 2025},
    "CL":  {"nombre": "🏆 Champions League",    "as_id": 2,   "season": 2025},
    "BSA": {"nombre": "🇧🇷 Brasileirão",        "as_id": 71,  "season": 2026},
}

_cache = {}
CACHE_TTL = 300
CACHE_TTL_AS = 43200

def fd_get(endpoint, params=None):
    ck = "fd:" + endpoint + str(params or "")
    now = time.time()
    if ck in _cache and now - _cache[ck][1] < CACHE_TTL: return _cache[ck][0]
    try:
        r = requests.get(f"{FD_URL}{endpoint}", headers=FD_HEADERS, params=params, timeout=15)
        if r.status_code == 429: time.sleep(6); r = requests.get(f"{FD_URL}{endpoint}", headers=FD_HEADERS, params=params, timeout=15)
        d = r.json(); _cache[ck] = (d, now); return d
    except Exception as e: return {"error": str(e)}

def as_get(endpoint, params=None):
    ck = "as:" + endpoint + str(params or "")
    now = time.time()
    if ck in _cache and now - _cache[ck][1] < CACHE_TTL_AS: return _cache[ck][0]
    try:
        r = requests.get(f"{AS_URL}{endpoint}", headers=AS_HEADERS, params=params, timeout=15)
        if r.status_code == 429: return {"error": "rate_limit"}
        d = r.json()
        if d.get("response") is not None: _cache[ck] = (d, now)
        return d
    except Exception as e: return {"error": str(e)}


@app.route("/")
def index():
    return render_template("index.html", ligas={k: v["nombre"] for k, v in LIGAS.items()})

@app.route("/partidos/<codigo>")
def partidos(codigo):
    data = fd_get(f"/competitions/{codigo}/matches", {"status": "SCHEDULED", "limit": 50})
    if "error" in data: return jsonify({"response": [], "error": data["error"]})
    matches = []
    for m in data.get("matches", []):
        refs = m.get("referees", [])
        matches.append({"id": m["id"], "fecha": m["utcDate"], "home": m["homeTeam"]["name"], "home_id": m["homeTeam"]["id"],
            "away": m["awayTeam"]["name"], "away_id": m["awayTeam"]["id"], "jornada": m.get("matchday"),
            "competicion": data.get("competition", {}).get("name", ""), "estado": m["status"],
            "arbitro": refs[0]["name"] if refs else None})
    return jsonify({"response": matches, "total": len(matches)})

@app.route("/analizar/<codigo>/<int:match_id>")
def analizar(codigo, match_id):
    liga = LIGAS.get(codigo, {})
    match_data = fd_get(f"/matches/{match_id}")
    if "error" in match_data or "id" not in match_data: return jsonify({"error": "Partido no encontrado"})

    home_id, away_id = match_data["homeTeam"]["id"], match_data["awayTeam"]["id"]
    home_name, away_name = match_data["homeTeam"]["name"], match_data["awayTeam"]["name"]

    standings_data = fd_get(f"/competitions/{codigo}/standings")
    tt, th, ta = [], [], []
    for s in standings_data.get("standings", []):
        if s["type"]=="TOTAL": tt=s["table"]
        elif s["type"]=="HOME": th=s["table"]
        elif s["type"]=="AWAY": ta=s["table"]

    hp, ap = _find(tt, home_id), _find(tt, away_id)
    hh, aa = _find(th, home_id), _find(ta, away_id)

    fhr = fd_get(f"/teams/{home_id}/matches", {"status": "FINISHED", "limit": 10})
    far = fd_get(f"/teams/{away_id}/matches", {"status": "FINISHED", "limit": 10})
    hf = _forma(fhr.get("matches", []), home_id)
    af = _forma(far.get("matches", []), away_id)
    hl3 = _u3(fhr.get("matches", []), home_id)
    al3 = _u3(far.get("matches", []), away_id)

    h2h = match_data.get("head2head", {})
    refs = match_data.get("referees", [])
    arbitro = refs[0]["name"] if refs else None

    sc = fd_get(f"/competitions/{codigo}/scorers", {"limit": 15})
    jh = _jugadores(sc.get("scorers", []), home_id)
    ja = _jugadores(sc.get("scorers", []), away_id)

    hs = _get_as_stats(home_name, liga.get("as_id"), liga.get("season"))
    aws = _get_as_stats(away_name, liga.get("as_id"), liga.get("season"))

    resultado = _analisis(match_data, hp, ap, hh, aa, hf, af, h2h, home_name, away_name, tt)
    resultado["arbitro"] = arbitro
    resultado["ultimos3"] = {"home": hl3, "away": al3}
    resultado["jugadores"] = {"home": _enrich_players(jh, home_name, hf), "away": _enrich_players(ja, away_name, af)}
    resultado["stats_avanzadas"] = _adv_stats(hs, aws, home_name, away_name)
    resultado["resumen"] = _resumen(home_name, away_name, hf, af, hp, ap, hh, aa, h2h, arbitro, resultado)

    return jsonify(resultado)


# ── api-sports ────────────────────────────────────────────
def _search_as(name, lid, season):
    d = as_get("/teams", {"league": lid, "season": season, "search": name.split(" ")[0]})
    if "error" in d or not d.get("response"): return None
    for t in d["response"]:
        if name.lower() in t["team"]["name"].lower() or t["team"]["name"].lower() in name.lower(): return t["team"]["id"]
    return d["response"][0]["team"]["id"] if d["response"] else None

def _get_as_stats(name, lid, season):
    if not lid: return None
    tid = _search_as(name, lid, season)
    if not tid: return None
    d = as_get("/teams/statistics", {"league": lid, "season": season, "team": tid})
    return d.get("response") if "error" not in d else None

def _adv_stats(hs, aws, hn, an):
    if not hs and not aws: return None
    r = {}
    if hs and aws:
        hgm = hs.get("goals",{}).get("for",{}).get("minute",{})
        agm = aws.get("goals",{}).get("for",{}).get("minute",{})
        r["goles_por_tiempo"] = {"home": _parse_min(hgm), "away": _parse_min(agm)}

        hc, ac = hs.get("cards",{}), aws.get("cards",{})
        hy, ay = _cnt_cards(hc.get("yellow",{})), _cnt_cards(ac.get("yellow",{}))
        hr, ar = _cnt_cards(hc.get("red",{})), _cnt_cards(ac.get("red",{}))
        hpp, app_ = _played(hs), _played(aws)
        r["tarjetas"] = {"home_yellow":hy,"away_yellow":ay,"home_red":hr,"away_red":ar,
            "home_yellow_avg":round(hy/max(hpp,1),2),"away_yellow_avg":round(ay/max(app_,1),2)}

        hgf = hs.get("goals",{}).get("for",{}).get("total",{}).get("total",0) or 0
        hgc = hs.get("goals",{}).get("against",{}).get("total",{}).get("total",0) or 0
        agf = aws.get("goals",{}).get("for",{}).get("total",{}).get("total",0) or 0
        agc = aws.get("goals",{}).get("against",{}).get("total",{}).get("total",0) or 0
        hcs = hs.get("clean_sheet",{}).get("total",0) or 0
        acs = aws.get("clean_sheet",{}).get("total",0) or 0

        comps = []
        tgf = hgf+agf
        if tgf>0: comps.append({"label":"Poder ofensivo","home":round(hgf/tgf*100),"away":round(agf/tgf*100)})
        tgc = hgc+agc
        if tgc>0: comps.append({"label":"Solidez defensiva","home":round((1-hgc/tgc)*100),"away":round((1-agc/tgc)*100)})
        tc = hy+ay
        if tc>0: comps.append({"label":"Mayor tarjetas","home":round(hy/tc*100),"away":round(ay/tc*100)})
        tcs = hcs+acs
        if tcs>0: comps.append({"label":"Valla invicta","home":round(hcs/tcs*100),"away":round(acs/tcs*100)})
        r["comparativas"] = comps

        h1 = _goals_half(hgm,"1st"); h2 = _goals_half(hgm,"2nd")
        a1 = _goals_half(agm,"1st"); a2 = _goals_half(agm,"2nd")
        ht, at = h1+h2, a1+a2
        r["prob_gol_tiempo"] = {"home_1st":round(h1/max(ht,1)*100),"home_2nd":round(h2/max(ht,1)*100),
            "away_1st":round(a1/max(at,1)*100),"away_2nd":round(a2/max(at,1)*100)}

        r["rachas"] = {
            "home_wins": hs.get("biggest",{}).get("streak",{}).get("wins",0) or 0,
            "away_wins": aws.get("biggest",{}).get("streak",{}).get("wins",0) or 0,
        }
    return r

def _parse_min(md):
    return [{"intervalo":iv,"goles":(md.get(iv,{}).get("total") or 0),"pct":int((md.get(iv,{}).get("percentage") or "0%").replace("%",""))} for iv in ["0-15","16-30","31-45","46-60","61-75","76-90"]]

def _cnt_cards(cd):
    return sum(v.get("total",0) or 0 for v in cd.values() if isinstance(v,dict))

def _played(s): return (s.get("fixtures",{}).get("played",{}).get("total") or 0)

def _goals_half(md, half):
    keys = ["0-15","16-30","31-45"] if half=="1st" else ["46-60","61-75","76-90"]
    return sum(md.get(k,{}).get("total",0) or 0 for k in keys)


# ── football-data helpers ─────────────────────────────────
def _find(t, tid):
    for x in t:
        if x["team"]["id"]==tid: return x
    return None

def _forma(matches, tid):
    if not matches: return {"form":"","w":0,"d":0,"l":0,"gf":0,"gc":0,"matches":0,"ppg":0,"gf_avg":0,"gc_avg":0,"clean_sheets":0,"failed_to_score":0}
    matches = sorted(matches, key=lambda x: x.get("utcDate",""), reverse=True)[:10]
    w=d=l=gf=gc=cs=fts=0; fs=""
    for m in matches:
        ft=m.get("score",{}).get("fullTime",{});hg,ag=ft.get("home"),ft.get("away")
        if hg is None: continue
        ih=m["homeTeam"]["id"]==tid; my,th=(hg,ag) if ih else (ag,hg)
        gf+=my;gc+=th
        if th==0:cs+=1
        if my==0:fts+=1
        if my>th:w+=1;fs+="W"
        elif my==th:d+=1;fs+="D"
        else:l+=1;fs+="L"
    t=w+d+l
    return {"form":fs[:5],"w":w,"d":d,"l":l,"gf":gf,"gc":gc,"matches":t,
        "ppg":round((w*3+d)/t,2) if t>0 else 0,"gf_avg":round(gf/t,2) if t>0 else 0,
        "gc_avg":round(gc/t,2) if t>0 else 0,"clean_sheets":cs,"failed_to_score":fts}

def _u3(matches, tid):
    if not matches: return []
    matches = sorted(matches, key=lambda x: x.get("utcDate",""), reverse=True)[:3]
    r = []
    for m in matches:
        ft=m.get("score",{}).get("fullTime",{});hg,ag=ft.get("home"),ft.get("away")
        if hg is None: continue
        ih=m["homeTeam"]["id"]==tid; my,th=(hg,ag) if ih else (ag,hg)
        res="W" if my>th else ("D" if my==th else "L")
        r.append({"fecha":m.get("utcDate","")[:10],"rival":m["awayTeam"]["name"] if ih else m["homeTeam"]["name"],
            "competicion":m.get("competition",{}).get("code",""),"marcador":f"{hg}-{ag}","local":ih,"resultado":res})
    return r

def _jugadores(scorers, tid):
    j = []
    for s in scorers:
        if s["team"]["id"]==tid:
            j.append({"nombre":s["player"]["name"],"goles":s.get("goals",0),"asistencias":s.get("assists",0),
                "partidos":s.get("playedMatches",0),"promedio":round(s.get("goals",0)/max(s.get("playedMatches",1),1),2)})
    return j[:3]

def _enrich_players(players, team, form):
    """Agrega narrativa a cada jugador."""
    for p in players:
        desc = []
        if p["goles"] >= 10:
            desc.append(f"Goleador principal de {team} con {p['goles']} goles en {p['partidos']} partidos.")
        elif p["goles"] >= 5:
            desc.append(f"Amenaza ofensiva con {p['goles']} goles en la temporada.")
        else:
            desc.append(f"Aporta {p['goles']} goles y {p['asistencias'] or 0} asistencias.")

        if p["promedio"] >= 0.5:
            desc.append(f"Promedio de {p['promedio']} goles/partido, candidato a anotar.")
            p["mercado_sugerido"] = "Anotador en cualquier momento"
        elif p["asistencias"] and p["asistencias"] >= 5:
            desc.append(f"Generador clave con {p['asistencias']} asistencias.")
            p["mercado_sugerido"] = "Asistencia"
        else:
            p["mercado_sugerido"] = "Anotador Over 0.5"

        p["descripcion"] = " ".join(desc)
    return players


# ── RESUMEN PRE-PARTIDO ───────────────────────────────────
def _resumen(hn, an, hf, af, hp, ap, hh, aa, h2h, arbitro, resultado):
    partes = []

    # Contexto general
    if hp and ap:
        diff = abs((hp.get("position",10)) - (ap.get("position",10)))
        if diff <= 3:
            partes.append(f"Partido entre equipos cercanos en la tabla: {hn} (#{hp.get('position','?')}, {hp.get('points',0)} pts) vs {an} (#{ap.get('position','?')}, {ap.get('points',0)} pts).")
        elif hp.get("position",10) < ap.get("position",10):
            partes.append(f"{hn} (#{hp.get('position','?')}) recibe a {an} (#{ap.get('position','?')}) con ventaja en la tabla de {diff} posiciones.")
        else:
            partes.append(f"{an} (#{ap.get('position','?')}) visita a {hn} (#{hp.get('position','?')}), ubicado {diff} posiciones mas abajo.")

    # Forma
    if hf["matches"] > 0:
        racha_h = _racha(hf["form"])
        if racha_h[0] == "W" and racha_h[1] >= 3:
            partes.append(f"{hn} llega en racha de {racha_h[1]} victorias consecutivas, con {hf['gf_avg']} goles/partido.")
        elif racha_h[0] == "L" and racha_h[1] >= 2:
            partes.append(f"{hn} atraviesa un mal momento con {racha_h[1]} derrotas consecutivas.")
        else:
            partes.append(f"{hn} tiene un rendimiento de {hf['ppg']} PPG en sus ultimos {hf['matches']} partidos ({hf['w']}V {hf['d']}E {hf['l']}D).")

    if af["matches"] > 0:
        racha_a = _racha(af["form"])
        if racha_a[0] == "W" and racha_a[1] >= 3:
            partes.append(f"{an} llega fuerte con {racha_a[1]} victorias al hilo.")
        elif racha_a[0] == "L" and racha_a[1] >= 2:
            partes.append(f"{an} llega debilitado con {racha_a[1]} derrotas seguidas.")
        else:
            partes.append(f"{an} promedía {af['ppg']} PPG con {af['gf_avg']} goles/partido.")

    # Localia
    if hh:
        pg = hh.get("playedGames", 0)
        hw = hh.get("won", 0)
        if pg > 0:
            pct = round(hw/pg*100)
            if pct >= 60:
                partes.append(f"{hn} domina de local con {pct}% de victorias en casa ({hw}V en {pg}PJ).")
            elif pct <= 30:
                partes.append(f"Sorprendentemente, {hn} es debil como local ({pct}% victorias en casa).")

    # H2H
    h2h_total = h2h.get("numberOfMatches", 0)
    if h2h_total >= 3:
        hw = h2h.get("homeTeam",{}).get("wins",0)
        aw = h2h.get("awayTeam",{}).get("wins",0)
        if hw > aw:
            partes.append(f"En {h2h_total} enfrentamientos directos, {hn} domina con {hw} victorias vs {aw}.")
        elif aw > hw:
            partes.append(f"El historial favorece a {an} con {aw} victorias en {h2h_total} encuentros.")
        else:
            partes.append(f"Historial equilibrado con {hw} victorias cada uno en {h2h_total} partidos.")

    # Goles
    ge = resultado.get("goles_esperados", 2.5)
    if ge >= 3.0:
        partes.append(f"Se esperan goles ({ge} esperados): ambos equipos tienen promedios ofensivos altos.")
    elif ge <= 1.8:
        partes.append(f"Partido con perfil defensivo ({ge} goles esperados). Las defensas predominan.")

    # Arbitro
    if arbitro:
        partes.append(f"Arbitro designado: {arbitro}.")

    return " ".join(partes)


def _racha(form):
    if not form: return ("", 0)
    first = form[0]
    count = 0
    for c in form:
        if c == first: count += 1
        else: break
    return (first, count)


# ── ANALISIS COMPLETO ─────────────────────────────────────
def _analisis(md, hp, ap, hh, aa, hf, af, h2h, hn, an, tt):
    te = len(tt) if tt else 20

    f3h = round(hf["ppg"]/3*100) if hf["matches"]>0 else 50
    f3a = round(af["ppg"]/3*100) if af["matches"]>0 else 50

    f5 = 50
    h2hw = h2h.get("homeTeam",{}).get("wins",0)
    h2aw = h2h.get("awayTeam",{}).get("wins",0)
    h2d = h2h.get("homeTeam",{}).get("draws",h2h.get("awayTeam",{}).get("draws",0))
    h2t = h2h.get("numberOfMatches",0)
    if h2t>0: f5 = round((h2hw*100+h2d*50)/h2t)

    f6h=f6a=50
    if hh:
        pg=hh.get("playedGames",0)
        if pg>0: f6h=round((hh.get("won",0)*3+hh.get("draw",0))/(pg*3)*100)
    if aa:
        pg=aa.get("playedGames",0)
        if pg>0: f6a=round((aa.get("won",0)*3+aa.get("draw",0))/(pg*3)*100)

    f10h=f10a=50
    if hp: f10h=round((1-(hp.get("position",10)-1)/max(te-1,1))*100)
    if ap: f10a=round((1-(ap.get("position",10)-1)/max(te-1,1))*100)

    ph=round(f3h*.30+f5*.15+f6h*.30+f10h*.25)
    pa=round(f3a*.30+(100-f5)*.15+f6a*.30+f10a*.25)
    pd=max(0,100-ph-pa)
    tp=ph+pa+pd
    if tp>0: ph=round(ph/tp*100);pa=round(pa/tp*100);pd=100-ph-pa

    egh=hf["gf_avg"] if hf["matches"]>0 else 1.3
    ech=hf["gc_avg"] if hf["matches"]>0 else 1.0
    ega=af["gf_avg"] if af["matches"]>0 else 1.0
    eca=af["gc_avg"] if af["matches"]>0 else 1.3
    ge=round((egh+ega+ech+eca)/2,2)

    hfts=hf["failed_to_score"]/max(hf["matches"],1)
    afts=af["failed_to_score"]/max(af["matches"],1)
    hcs=hf["clean_sheets"]/max(hf["matches"],1)
    acs=af["clean_sheets"]/max(af["matches"],1)
    hsc=1-hfts; asc=1-afts

    mercados = []

    # 1X2
    if ph>=50:
        s = _sint_1x2(hn, an, ph, hf, hp, hh, f6h, "home", ge)
        mercados.append({"mercado":f"Resultado Final — {hn}","prob":ph,"riesgo":100-ph,"tipo":"1X2","aprobado":ph>=65,"sintesis":s})
    if pa>=50:
        s = _sint_1x2(an, hn, pa, af, ap, aa, f6a, "away", ge)
        mercados.append({"mercado":f"Resultado Final — {an}","prob":pa,"riesgo":100-pa,"tipo":"1X2","aprobado":pa>=65,"sintesis":s})
    if pd>=28:
        s = f"Equipos separados por {abs((hp.get('position',10) if hp else 10)-(ap.get('position',10) if ap else 10))} posiciones. "
        if hf["matches"]>0 and af["matches"]>0:
            s += f"PPG similar: {hn} {hf['ppg']} vs {an} {af['ppg']}. "
        s += "El empate es un resultado logico en un partido parejo."
        mercados.append({"mercado":"Resultado Final — Empate","prob":pd,"riesgo":100-pd,"tipo":"1X2","aprobado":pd>=35,"sintesis":s})

    # DC
    dc1x=ph+pd; dcx2=pa+pd
    if dc1x>=60:
        s = f"{hn} o Empate cubre el escenario mas probable. "
        if f6h>=60: s+=f"Localia fuerte (F6: {f6h}). "
        if ph>=50: s+=f"{hn} parte como favorito ({ph}%), el empate funciona como seguro. "
        s += f"Umbral {'superado' if dc1x>=75 else 'no alcanzado'} (>=75%)."
        mercados.append({"mercado":f"Doble Oportunidad 1X — {hn}","prob":dc1x,"riesgo":100-dc1x,"tipo":"DC","aprobado":dc1x>=75,"sintesis":s})
    if dcx2>=60:
        s = f"Cubre victoria de {an} o empate. "
        if pa>=45: s+=f"{an} tiene {pa}% de probabilidad de ganar. "
        mercados.append({"mercado":f"Doble Oportunidad X2 — {an}","prob":dcx2,"riesgo":100-dcx2,"tipo":"DC","aprobado":dcx2>=75,"sintesis":s})

    # O/U
    if ge>=2.5:
        p=min(88,round(50+(ge-2.5)*22))
        s = f"Promedio combinado supera {ge} goles/partido. {hn} marco en {round(hsc*100)}% de sus partidos (promedio {egh} GF/P). "
        s += f"{an} promedia {ega} goles. "
        if hf["gc_avg"]>=1.5: s+=f"La defensa de {hn} concede {hf['gc_avg']} goles/partido, favoreciendo el Over. "
        mercados.append({"mercado":"Goles Totales Over 2.5","prob":p,"riesgo":100-p,"tipo":"O/U","aprobado":p>=65,"sintesis":s})
    if ge<=2.5:
        p=min(88,round(50+(2.5-ge)*22))
        s = f"Goles esperados: {ge}. "
        if hcs>=0.3: s+=f"{hn} mantuvo valla invicta en {round(hcs*100)}% de partidos. "
        if acs>=0.3: s+=f"{an} mantuvo valla invicta en {round(acs*100)}% de partidos. "
        s+="Perfil defensivo favorece Under."
        mercados.append({"mercado":"Goles Totales Under 2.5","prob":p,"riesgo":100-p,"tipo":"O/U","aprobado":p>=65,"sintesis":s})
    if ge>=1.8:
        p=min(92,round(55+(ge-1.5)*18))
        mercados.append({"mercado":"Goles Totales Over 1.5","prob":p,"riesgo":100-p,"tipo":"O/U","aprobado":p>=80,
            "sintesis":f"Con {ge} goles esperados, alta probabilidad de al menos 2 goles en el partido."})

    # BTTS
    btts=min(85,max(20,round(hsc*50+asc*50)))
    if btts>=50:
        s = f"{hn} marca en {round(hsc*100)}% de partidos, {an} en {round(asc*100)}%. "
        if hf["gc_avg"]>=1: s+=f"La defensa de {hn} permite {hf['gc_avg']} GC/P, dando oportunidades al visitante. "
        mercados.append({"mercado":"BTTS — Ambos Anotan","prob":btts,"riesgo":100-btts,"tipo":"BTTS","aprobado":btts>=65,"sintesis":s})

    # GE
    ho=min(95,max(30,round(hsc*100)))
    if ho>=60:
        s = f"{hn} marco en {round(hsc*100)}% de sus ultimos partidos con promedio de {egh} GF/P. "
        if hf["gf"]>=15: s+=f"Acumula {hf['gf']} goles en {hf['matches']} partidos. "
        mercados.append({"mercado":f"Goles Equipo — {hn} Over 0.5","prob":ho,"riesgo":100-ho,"tipo":"GE","aprobado":ho>=80,"sintesis":s})
    ao=min(95,max(30,round(asc*100)))
    if ao>=60:
        s = f"{an} marco en {round(asc*100)}% de sus ultimos partidos con promedio de {ega} GF/P. "
        mercados.append({"mercado":f"Goles Equipo — {an} Over 0.5","prob":ao,"riesgo":100-ao,"tipo":"GE","aprobado":ao>=80,"sintesis":s})

    # No 0-0
    p00=round(hfts*acs*100); pn00=min(95,100-p00)
    if pn00>=75:
        s = f"{hn} marco en {hf['matches']-hf['failed_to_score']} de {hf['matches']} partidos. Promedio combinado: {ge} goles. "
        s += "Probabilidad alta de al menos 1 gol en el encuentro."
        mercados.append({"mercado":"El Partido No Termina 0-0","prob":pn00,"riesgo":100-pn00,"tipo":"ESP","aprobado":pn00>=85,"sintesis":s})

    mercados.sort(key=lambda x: x["prob"], reverse=True)

    # VEREDICTO
    veredicto = _veredicto(hn, an, ph, pd, pa, hf, af, hp, ap, mercados, ge)

    return {
        "match":{"home":hn,"away":an,"fecha":md.get("utcDate",""),"jornada":md.get("matchday"),"competicion":md.get("competition",{}).get("name","")},
        "filtros":{"F3_forma":{"home":f3h,"away":f3a},"F5_h2h":f5,"F6_localvisit":{"home":f6h,"away":f6a},"F10_posicion":{"home":f10h,"away":f10a}},
        "probabilidades":{"home":ph,"draw":pd,"away":pa},"goles_esperados":ge,
        "forma":{"home":hf,"away":af},
        "h2h":{"total":h2t,"home_wins":h2hw,"away_wins":h2aw,"draws":h2d,"total_goals":h2h.get("totalGoals",0)},
        "posiciones":{"home":hp.get("position") if hp else None,"away":ap.get("position") if ap else None,
            "home_pts":hp.get("points") if hp else None,"away_pts":ap.get("points") if ap else None,
            "home_gf":hp.get("goalsFor") if hp else None,"home_gc":hp.get("goalsAgainst") if hp else None,
            "away_gf":ap.get("goalsFor") if ap else None,"away_gc":ap.get("goalsAgainst") if ap else None},
        "mercados":mercados,"veredicto":veredicto,
    }


def _sint_1x2(team, rival, prob, form, pos, home_away, f6, side, ge):
    s = ""
    if form["matches"]>0:
        r = _racha(form["form"])
        if r[0]=="W" and r[1]>=2: s+=f"{team} en racha de {r[1]} victorias consecutivas. "
        elif r[0]=="L" and r[1]>=2: s+=f"Atencion: {team} viene de {r[1]} derrotas seguidas. "
        s+=f"PPG: {form['ppg']}, promedio {form['gf_avg']} goles/partido. "
    if pos: s+=f"#{pos.get('position','?')} en la tabla con {pos.get('points',0)} pts. "
    if side=="home" and f6>=60: s+=f"Fuerte de local (F6: {f6}). "
    elif side=="away" and f6>=60: s+=f"Buen rendimiento como visitante (F6: {f6}). "
    if prob<65: s+=f"Probabilidad ({prob}%) no alcanza umbral de aprobacion directa (>=65%). "
    elif prob>=65: s+=f"Supera umbral de aprobacion con {prob}%. "
    return s


def _veredicto(hn, an, ph, pd, pa, hf, af, hp, ap, mercados, ge):
    aprobados = [m for m in mercados if m["aprobado"]]
    mejor = mercados[0] if mercados else None

    if ph>=pa+15: fav,conf=hn,("alta" if ph>=65 else "moderada")
    elif pa>=ph+15: fav,conf=an,("alta" if pa>=65 else "moderada")
    else: fav,conf=None,"baja"

    # Texto principal
    if fav:
        texto = f"Victoria de {fav} en tiempo reglamentario — confianza {conf}. "
    else:
        texto = f"Partido equilibrado entre {hn} ({ph}%) y {an} ({pa}%). Sin favorito claro. "

    # Razonamiento
    if hf["matches"]>0 and af["matches"]>0:
        texto += f"{hn} llega con {hf['ppg']} PPG vs {af['ppg']} PPG de {an}. "
    if hp and ap:
        texto += f"Posiciones: {hn} #{hp.get('position','?')} vs {an} #{ap.get('position','?')}. "
    texto += f"Goles esperados: {ge}. "

    # Mercado principal
    if aprobados:
        mp = aprobados[0]
        mercado_principal = f"{mp['mercado']} ({mp['prob']}%)"
    else:
        mercado_principal = "Ninguno supera el umbral de aprobacion"

    # Combinable
    combinable = ""
    if len(aprobados) >= 2:
        # Buscar la mejor combinacion
        comb = [m for m in aprobados if m["tipo"] != aprobados[0]["tipo"]]
        if comb:
            combinable = f"{comb[0]['mercado']} ({comb[0]['prob']}%) como alternativa de mayor retorno con riesgo moderado."

    ta = " · ".join(f"{m['mercado']} ({m['prob']}%)" for m in aprobados[:4]) if aprobados else "Ninguno"

    return {"texto":texto,"confianza":conf,"favorito":fav,"mercados_aprobados":ta,
        "total_aprobados":len(aprobados),"mercado_principal":mercado_principal,"combinable":combinable}


if __name__ == "__main__":
    app.run(debug=True)