"""
MatchIQ — Backend Flask v3
- Login con usuario/contraseña
- Probabilidades recalibradas
- Cuotas teóricas
- Filtros con valor real
- Partidos recientes (2 dias)
"""
from flask import Flask, jsonify, render_template, request, redirect, url_for, session
from datetime import datetime, timedelta
from functools import wraps
import requests, time, math, os
 
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "matchiq-secret-key-2026-xyz")
 
# Usuario y contraseña (se pueden setear en Railway como variables de entorno)
APP_USER = os.environ.get("APP_USER", "matchiq")
APP_PASS = os.environ.get("APP_PASS", "futbol2026")
 
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
    if ck in _cache and now-_cache[ck][1]<CACHE_TTL_AS: return _cache[ck][0]
    try:
        r=requests.get(f"{AS_URL}{ep}",headers=AS_HEADERS,params=params,timeout=15)
        if r.status_code==429: return {"error":"rate_limit"}
        d=r.json()
        if d.get("response") is not None: _cache[ck]=(d,now)
        return d
    except Exception as e: return {"error":str(e)}
 
 
# ── AUTH ──────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("auth"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper
 
def api_login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("auth"):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper
 
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        user = request.form.get("user", "").strip()
        passw = request.form.get("pass", "").strip()
        if user == APP_USER and passw == APP_PASS:
            session["auth"] = True
            session.permanent = True
            return redirect(url_for("index"))
        error = "Credenciales incorrectas"
    return render_template("login.html", error=error)
 
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))
 
 
# ── RUTAS ─────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    return render_template("index.html", ligas={k:v["nombre"] for k,v in LIGAS.items()})
 
 
@app.route("/partidos/<codigo>")
@api_login_required
def partidos(codigo):
    hoy = datetime.utcnow()
    desde = (hoy - timedelta(days=2)).strftime("%Y-%m-%d")
    hasta = (hoy + timedelta(days=30)).strftime("%Y-%m-%d")
    data = fd_get(f"/competitions/{codigo}/matches", {"dateFrom": desde, "dateTo": hasta, "limit": 80})
    if "error" in data: return jsonify({"response":[],"error":data["error"]})
    matches=[]
    for m in data.get("matches",[]):
        refs=m.get("referees",[])
        score=m.get("score",{}).get("fullTime",{})
        estado=m["status"]
        resultado=None
        if estado=="FINISHED":
            resultado=f"{score.get('home',0)}-{score.get('away',0)}"
        matches.append({"id":m["id"],"fecha":m["utcDate"],"home":m["homeTeam"]["name"],"home_id":m["homeTeam"]["id"],
            "away":m["awayTeam"]["name"],"away_id":m["awayTeam"]["id"],"jornada":m.get("matchday"),
            "competicion":data.get("competition",{}).get("name",""),"estado":estado,
            "arbitro":refs[0]["name"] if refs else None,"resultado":resultado})
    return jsonify({"response":matches,"total":len(matches)})
 
 
@app.route("/analizar/<codigo>/<int:match_id>")
@api_login_required
def analizar(codigo, match_id):
    liga=LIGAS.get(codigo,{})
    md=fd_get(f"/matches/{match_id}")
    if "error" in md or "id" not in md: return jsonify({"error":"Partido no encontrado"})
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
    arb_perfil=_arbitro_perfil(arbitro_name,liga.get("as_id"),liga.get("season"))
 
    resultado=_analisis(md,hp,ap,hh,aa,hf,af,h2h,hn,an,tt)
    resultado["arbitro"]=arbitro_name
    resultado["arbitro_perfil"]=arb_perfil
    resultado["ultimos3"]={"home":hl3,"away":al3}
    resultado["jugadores"]={"home":jh,"away":ja}
    resultado["stats_avanzadas"]=_adv(hs,aws,hn,an)
    resultado["resumen"]=_resumen(hn,an,hf,af,hp,ap,hh,aa,h2h,arbitro_name,arb_perfil,resultado)
    return jsonify(resultado)
 
 
# ── ARBITRO ───────────────────────────────────────────────
def _arbitro_perfil(name,lid,season):
    if not name: return None
    return {"nombre":name,"descripcion":_ref_description(name)}
 
def _ref_description(name):
    refs_db={
        "michael oliver":{"estilo":"Estricto","tarjetas":"Alto","desc":"Árbitro FIFA de alto perfil. Tendencia a mostrar tarjetas en partidos de alta intensidad. No duda en sancionar penales polémicos."},
        "anthony taylor":{"estilo":"Equilibrado","tarjetas":"Medio","desc":"Experimentado árbitro internacional. Mantiene control sin exceso de tarjetas. Permite juego físico moderado."},
        "paul tierney":{"estilo":"Permisivo","tarjetas":"Bajo","desc":"Tiende a dejar jugar. Pocas tarjetas por partido. Favorece la continuidad del juego."},
        "simon hooper":{"estilo":"Moderado","tarjetas":"Medio","desc":"Árbitro de perfil medio. Consistente en sus decisiones. Sin tendencias marcadas."},
        "robert jones":{"estilo":"Estricto","tarjetas":"Alto","desc":"Perfil riguroso. Promedio alto de tarjetas amarillas. Controla el juego de forma firme."},
        "stuart attwell":{"estilo":"Moderado","tarjetas":"Medio","desc":"Árbitro con experiencia en Premier League. Equilibrado en sus intervenciones."},
        "chris kavanagh":{"estilo":"Estricto","tarjetas":"Alto","desc":"Árbitro FIFA con tendencia a tarjetas. Riguroso en faltas tácticas y protestas."},
        "john brooks":{"estilo":"Permisivo","tarjetas":"Bajo","desc":"Permite contacto físico. Baja intervención. Pocas tarjetas por partido."},
        "darren england":{"estilo":"Moderado","tarjetas":"Medio","desc":"Árbitro VAR reconvertido a campo. Perfil equilibrado con promedio estándar de tarjetas."},
        "tim robinson":{"estilo":"Moderado","tarjetas":"Medio","desc":"Perfil neutral. No destaca por ser estricto ni permisivo."},
        "david coote":{"estilo":"Estricto","tarjetas":"Alto","desc":"Árbitro controversial. Alto promedio de tarjetas y penales señalados."},
        "peter bankes":{"estilo":"Moderado","tarjetas":"Medio","desc":"Árbitro consistente. Intervenciones justas sin excesos."},
        "andy madley":{"estilo":"Permisivo","tarjetas":"Bajo","desc":"Deja fluir el juego. Bajo promedio de tarjetas. Interviene solo en faltas claras."},
        "jarred gillett":{"estilo":"Moderado","tarjetas":"Medio","desc":"Árbitro australiano en Premier League. Estilo equilibrado con buen manejo del VAR."},
        "tony harrington":{"estilo":"Moderado","tarjetas":"Medio","desc":"Perfil moderado. Consistente en sus decisiones a lo largo del partido."},
        "samuel barrott":{"estilo":"Moderado","tarjetas":"Medio","desc":"Árbitro joven en ascenso. Aún sin perfil definido marcado."},
    }
    key=name.lower().strip()
    if key in refs_db:
        r=refs_db[key]
        return f"Estilo: {r['estilo']} · Tarjetas: {r['tarjetas']}. {r['desc']}"
    return f"Sin perfil detallado disponible para {name}."
 
 
# ── api-sports ────────────────────────────────────────────
def _search_as(name,lid,season):
    d=as_get("/teams",{"league":lid,"season":season,"search":name.split(" ")[0]})
    if "error" in d or not d.get("response"): return None
    for t in d["response"]:
        if name.lower() in t["team"]["name"].lower() or t["team"]["name"].lower() in name.lower(): return t["team"]["id"]
    return d["response"][0]["team"]["id"] if d["response"] else None
 
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
        elif p["goles"]>=5: desc.append(f"Amenaza ofensiva con {p['goles']} goles en la temporada.")
        else: desc.append(f"Aporta {p['goles']} goles y {p['asistencias']or 0} asistencias.")
        if p["promedio"]>=0.5: desc.append(f"Promedio de {p['promedio']} goles/partido, candidato a anotar."); p["mercado_sugerido"]="Anotador en cualquier momento"
        elif p["asistencias"] and p["asistencias"]>=5: desc.append(f"Generador clave con {p['asistencias']} asistencias."); p["mercado_sugerido"]="Asistencia"
        else: p["mercado_sugerido"]="Anotador Over 0.5"
        p["descripcion"]=" ".join(desc)
    return players
 
def _racha(form):
    if not form:return("",0)
    f=form[0];c=0
    for x in form:
        if x==f:c+=1
        else:break
    return(f,c)
 
def _resumen(hn,an,hf,af,hp,ap,hh,aa,h2h,arb,arb_perfil,resultado):
    p=[]
    if hp and ap:
        diff=abs(hp.get("position",10)-ap.get("position",10))
        if diff<=3: p.append(f"Partido entre equipos cercanos en la tabla: {hn} (#{hp.get('position','?')}, {hp.get('points',0)} pts) vs {an} (#{ap.get('position','?')}, {ap.get('points',0)} pts).")
        elif hp.get("position",10)<ap.get("position",10): p.append(f"{hn} (#{hp.get('position','?')}) recibe a {an} (#{ap.get('position','?')}) con ventaja de {diff} posiciones en la tabla.")
        else: p.append(f"{an} (#{ap.get('position','?')}) visita a {hn} (#{hp.get('position','?')}), ubicado {diff} posiciones mas abajo.")
    if hf["matches"]>0:
        rh=_racha(hf["form"])
        if rh[0]=="W" and rh[1]>=3: p.append(f"{hn} llega en racha de {rh[1]} victorias consecutivas con {hf['gf_avg']} goles/partido.")
        elif rh[0]=="L" and rh[1]>=2: p.append(f"{hn} atraviesa mal momento con {rh[1]} derrotas consecutivas.")
        else: p.append(f"{hn}: {hf['ppg']} PPG en ultimos {hf['matches']} partidos ({hf['w']}V {hf['d']}E {hf['l']}D).")
    if af["matches"]>0:
        ra=_racha(af["form"])
        if ra[0]=="W" and ra[1]>=3: p.append(f"{an} llega fuerte con {ra[1]} victorias al hilo.")
        elif ra[0]=="L" and ra[1]>=2: p.append(f"{an} llega debilitado con {ra[1]} derrotas seguidas.")
        else: p.append(f"{an}: {af['ppg']} PPG con {af['gf_avg']} goles/partido.")
    if hh:
        pg=hh.get("playedGames",0);hw=hh.get("won",0)
        if pg>0:
            pct=round(hw/pg*100)
            if pct>=60: p.append(f"{hn} domina de local ({pct}% victorias en casa).")
            elif pct<=30: p.append(f"{hn} es debil como local ({pct}% victorias en casa).")
    h2t=h2h.get("numberOfMatches",0)
    if h2t>=3:
        hw2=h2h.get("homeTeam",{}).get("wins",0);aw2=h2h.get("awayTeam",{}).get("wins",0)
        if hw2>aw2: p.append(f"H2H favorece a {hn} ({hw2} victorias en {h2t} encuentros).")
        elif aw2>hw2: p.append(f"H2H favorece a {an} ({aw2} victorias en {h2t} encuentros).")
    ge=resultado.get("goles_esperados",2.5)
    if ge>=3.0: p.append(f"Se esperan goles ({ge}): ambos equipos tienen promedios ofensivos altos.")
    elif ge<=1.8: p.append(f"Perfil defensivo ({ge} goles esperados).")
    if arb: p.append(f"Arbitro: {arb}.")
    if arb_perfil and "Sin perfil" not in arb_perfil.get("descripcion",""): p.append(arb_perfil["descripcion"])
    return " ".join(p)
 
 
# ── ANALISIS (lógica recalibrada) ─────────────────────────
def _cuota(prob_pct):
    """Calcula cuota teorica desde probabilidad (con margen 5% tipo casa de apuestas)."""
    if prob_pct <= 0: return "—"
    cuota = round(100 / prob_pct * 0.95, 2)  # margen 5%
    return cuota
 
 
def _analisis(md,hp,ap,hh,aa,hf,af,h2h,hn,an,tt):
    te=len(tt)if tt else 20
 
    # ── FILTROS CON VALORES REALES ─────────────────────────
    forma_h_val = hf["ppg"] if hf["matches"]>0 else 0
    forma_a_val = af["ppg"] if af["matches"]>0 else 0
    forma_h_score=round(forma_h_val/3*100)if hf["matches"]>0 else 50
    forma_a_score=round(forma_a_val/3*100)if af["matches"]>0 else 50
 
    h2h_home_wins=h2h.get("homeTeam",{}).get("wins",0)
    h2h_away_wins=h2h.get("awayTeam",{}).get("wins",0)
    h2h_draws=h2h.get("homeTeam",{}).get("draws",h2h.get("awayTeam",{}).get("draws",0))
    h2t=h2h.get("numberOfMatches",0)
    h2h_score=50
    if h2t>0: h2h_score=round((h2h_home_wins*100+h2h_draws*50)/h2t)
 
    localia_h_val=0; localia_a_val=0
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
 
    # ── PROBABILIDADES RECALIBRADAS ─────────────────────────
    # Pesos: forma 25%, h2h 10%, local/visit 35%, posicion 30%
    ph=round(forma_h_score*.25+h2h_score*.10+localia_h_score*.35+pos_h_score*.30)
    pa=round(forma_a_score*.25+(100-h2h_score)*.10+localia_a_score*.35+pos_a_score*.30)
 
    # Ventaja de local por defecto (+8%)
    ph = min(95, ph + 8)
 
    # Normalizar
    pd=max(0,100-ph-pa)
    tp=ph+pa+pd
    if tp>0:
        ph=round(ph/tp*100)
        pa=round(pa/tp*100)
        pd=100-ph-pa
 
    # Goles esperados
    egh=hf["gf_avg"]if hf["matches"]>0 else 1.3
    ech=hf["gc_avg"]if hf["matches"]>0 else 1.0
    ega=af["gf_avg"]if af["matches"]>0 else 1.0
    eca=af["gc_avg"]if af["matches"]>0 else 1.3
    ge=round((egh+ega+ech+eca)/2,2)
 
    hfts=hf["failed_to_score"]/max(hf["matches"],1)
    afts=af["failed_to_score"]/max(af["matches"],1)
    hcs_r=hf["clean_sheets"]/max(hf["matches"],1)
    acs_r=af["clean_sheets"]/max(af["matches"],1)
    hsc=1-hfts;asc=1-afts
 
    mercados=[]
 
    # ── 1X2 ──
    if ph>=40:
        s=_s1x2(hn,an,ph,hf,hp,hh,localia_h_score,"home",ge)
        mercados.append({"mercado":f"Resultado Final — {hn}","prob":ph,"riesgo":100-ph,"cuota":_cuota(ph),
            "tipo":"1X2","aprobado":ph>=55,"sintesis":s})
    if pa>=40:
        s=_s1x2(an,hn,pa,af,ap,aa,localia_a_score,"away",ge)
        mercados.append({"mercado":f"Resultado Final — {an}","prob":pa,"riesgo":100-pa,"cuota":_cuota(pa),
            "tipo":"1X2","aprobado":pa>=55,"sintesis":s})
    if pd>=22:
        s=f"Equipos separados por {abs((pos_h or 10)-(pos_a or 10))} posiciones. "
        if hf["matches"]>0 and af["matches"]>0: s+=f"PPG similar: {hn} {hf['ppg']} vs {an} {af['ppg']}. "
        s+="Empate es resultado logico."
        mercados.append({"mercado":"Resultado Final — Empate","prob":pd,"riesgo":100-pd,"cuota":_cuota(pd),
            "tipo":"1X2","aprobado":pd>=30,"sintesis":s})
 
    # ── DOBLE CHANCE (umbrales mas bajos, alta probabilidad) ──
    dc1x=ph+pd; dcx2=pa+pd
    if dc1x>=55:
        s=f"{hn} o Empate cubre el escenario mas probable. "
        if localia_h_score>=60: s+=f"Localia fuerte ({localia_h_score}%). "
        s+=f"Solo pierde si gana {an} ({pa}%)."
        mercados.append({"mercado":f"Doble Oportunidad 1X — {hn}","prob":dc1x,"riesgo":100-dc1x,"cuota":_cuota(dc1x),
            "tipo":"DC","aprobado":dc1x>=65,"sintesis":s})
    if dcx2>=55:
        s=f"Cubre victoria de {an} o empate. Solo pierde si gana {hn} ({ph}%)."
        mercados.append({"mercado":f"Doble Oportunidad X2 — {an}","prob":dcx2,"riesgo":100-dcx2,"cuota":_cuota(dcx2),
            "tipo":"DC","aprobado":dcx2>=65,"sintesis":s})
 
    # ── O/U 2.5 ──
    if ge>=2.5:
        p=min(85,round(50+(ge-2.5)*20))
        s=f"Promedio combinado supera {ge} goles/partido. {hn} promedia {egh} GF/P. {an} promedia {ega} GF/P. "
        if hf["gc_avg"]>=1.5: s+=f"Defensa de {hn} concede {hf['gc_avg']} GC/P. "
        mercados.append({"mercado":"Goles Totales Over 2.5","prob":p,"riesgo":100-p,"cuota":_cuota(p),
            "tipo":"O/U","aprobado":p>=60,"sintesis":s})
    if ge<=2.5:
        p=min(85,round(50+(2.5-ge)*20))
        s=f"Goles esperados: {ge}. "
        if hcs_r>=0.3: s+=f"{hn} valla invicta en {round(hcs_r*100)}% de partidos. "
        if acs_r>=0.3: s+=f"{an} valla invicta en {round(acs_r*100)}% de partidos. "
        s+="Perfil defensivo favorece Under."
        mercados.append({"mercado":"Goles Totales Under 2.5","prob":p,"riesgo":100-p,"cuota":_cuota(p),
            "tipo":"O/U","aprobado":p>=60,"sintesis":s})
 
    # ── Over 1.5 ──
    if ge>=1.8:
        p=min(92,round(60+(ge-1.5)*15))
        mercados.append({"mercado":"Goles Totales Over 1.5","prob":p,"riesgo":100-p,"cuota":_cuota(p),
            "tipo":"O/U","aprobado":p>=75,
            "sintesis":f"Con {ge} goles esperados, alta probabilidad de al menos 2 goles. Mercado de bajo riesgo."})
 
    # ── BTTS ──
    btts=min(82,max(20,round(hsc*50+asc*50)))
    if btts>=45:
        s=f"{hn} marca en {round(hsc*100)}%, {an} en {round(asc*100)}%. "
        if hf["gc_avg"]>=1: s+=f"Defensa de {hn} permite {hf['gc_avg']} GC/P. "
        mercados.append({"mercado":"BTTS — Ambos Anotan","prob":btts,"riesgo":100-btts,"cuota":_cuota(btts),
            "tipo":"BTTS","aprobado":btts>=60,"sintesis":s})
 
    # ── GE Over 0.5 (umbral mas alto, debe ser muy probable) ──
    ho=min(95,max(30,round(hsc*100)))
    if ho>=70:
        mercados.append({"mercado":f"Goles Equipo — {hn} Over 0.5","prob":ho,"riesgo":100-ho,"cuota":_cuota(ho),
            "tipo":"GE","aprobado":ho>=85,
            "sintesis":f"{hn} marco en {round(hsc*100)}% de sus ultimos partidos con promedio de {egh} GF/P."})
    ao=min(95,max(30,round(asc*100)))
    if ao>=70:
        mercados.append({"mercado":f"Goles Equipo — {an} Over 0.5","prob":ao,"riesgo":100-ao,"cuota":_cuota(ao),
            "tipo":"GE","aprobado":ao>=85,
            "sintesis":f"{an} marco en {round(asc*100)}% de sus ultimos partidos con promedio de {ega} GF/P."})
 
    # ── No 0-0 ──
    p00=round(hfts*acs_r*100);pn00=min(95,100-p00)
    if pn00>=70:
        mercados.append({"mercado":"El Partido No Termina 0-0","prob":pn00,"riesgo":100-pn00,"cuota":_cuota(pn00),
            "tipo":"ESP","aprobado":pn00>=82,
            "sintesis":f"{hn} marco en {hf['matches']-hf['failed_to_score']} de {hf['matches']} partidos. Promedio combinado: {ge} goles."})
 
    mercados.sort(key=lambda x:x["prob"],reverse=True)
 
    # ── VEREDICTO ──
    aprobados=[m for m in mercados if m["aprobado"]]
    if ph>=pa+15:fav,conf=hn,("alta"if ph>=55 else"moderada")
    elif pa>=ph+15:fav,conf=an,("alta"if pa>=55 else"moderada")
    else:fav,conf=None,"baja"
    if fav:texto=f"Victoria de {fav} en tiempo reglamentario. "
    else:texto=f"Partido equilibrado entre {hn} ({ph}%) y {an} ({pa}%). Sin favorito claro. "
    if hf["matches"]>0:texto+=f"{hn} llega con {hf['ppg']} PPG vs {af['ppg']} PPG de {an}. "
    if hp and ap:texto+=f"Posiciones: {hn} #{hp.get('position','?')} vs {an} #{ap.get('position','?')}. "
    texto+=f"Goles esperados: {ge}."
    mp=f"{aprobados[0]['mercado']} ({aprobados[0]['prob']}% · cuota {aprobados[0]['cuota']})"if aprobados else"Ninguno supera el umbral"
    comb=""
    if len(aprobados)>=2:
        c2=[m for m in aprobados if m["tipo"]!=aprobados[0]["tipo"]]
        if c2:comb=f"{c2[0]['mercado']} ({c2[0]['prob']}% · cuota {c2[0]['cuota']}) como alternativa de mayor retorno con riesgo moderado."
    ta=" · ".join(f"{m['mercado']} ({m['prob']}%)"for m in aprobados[:4])if aprobados else"Ninguno"
 
    return{
        "match":{"home":hn,"away":an,"fecha":md.get("utcDate",""),"jornada":md.get("matchday"),"competicion":md.get("competition",{}).get("name","")},
        "filtros":{
            "Forma":{"home_val":f"{forma_h_val} PPG","away_val":f"{forma_a_val} PPG","home_score":forma_h_score,"away_score":forma_a_score,
                "desc":"Puntos por partido (PPG) en los ultimos 10 partidos. Un PPG >=2.0 es muy bueno, 1.5 es promedio, <=1.0 es malo."},
            "Historial":{"home_val":f"{h2h_home_wins}W","away_val":f"{h2h_away_wins}W","home_score":h2h_score,"away_score":100-h2h_score,
                "desc":f"Resultados directos entre ambos equipos en {h2t or 0} enfrentamientos. Muestra cuantas veces gano cada uno."},
            "Localia":{"home_val":f"{localia_h_val}% casa","away_val":f"{localia_a_val}% visita","home_score":localia_h_score,"away_score":localia_a_score,
                "desc":"Porcentaje de victorias como local (para el equipo de casa) y como visitante (para el visitante) en la liga actual."},
            "Posicion":{"home_val":f"#{pos_h or '—'}","away_val":f"#{pos_a or '—'}","home_score":pos_h_score,"away_score":pos_a_score,
                "desc":"Posicion actual en la tabla de la liga. Cuanto mas alto en la tabla, mejor."},
        },
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
        if r[0]=="W"and r[1]>=2:s+=f"{team} en racha de {r[1]} victorias. "
        elif r[0]=="L"and r[1]>=2:s+=f"Atencion: {team} viene de {r[1]} derrotas. "
        s+=f"PPG: {form['ppg']}, promedio {form['gf_avg']} goles/partido. "
    if pos:s+=f"#{pos.get('position','?')} con {pos.get('points',0)} pts. "
    if side=="home"and localia>=60:s+=f"Fuerte de local ({localia}%). "
    elif side=="away"and localia>=60:s+=f"Buen rendimiento visitante ({localia}%). "
    s+=f"Probabilidad {'supera' if prob>=55 else 'no alcanza'} umbral de aprobacion (>=55%). "
    return s
 
if __name__=="__main__":
    app.run(debug=True)