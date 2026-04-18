from flask import Flask, jsonify, render_template
from datetime import date, timedelta
import requests

app = Flask(__name__)

API_KEY = "fb49b7a70ea23977f8e7711c5ed027b1"
BASE_URL = "https://v3.football.api-sports.io"

HEADERS = {
    "x-apisports-key": API_KEY
}

LIGAS = {
    "128": "Liga Profesional Argentina",
    "71":  "Brasileirao",
    "13":  "Copa Libertadores",
    "14":  "Copa Sudamericana",
    "39":  "Premier League",
    "140": "La Liga",
    "135": "Serie A",
    "78":  "Bundesliga"
}

@app.route("/")
def index():
    return render_template("index.html", ligas=LIGAS)

@app.route("/partidos/<liga_id>/<temporada>")
def partidos(liga_id, temporada):
    url = f"{BASE_URL}/fixtures?league={liga_id}&season={temporada}&from=2024-11-01&to=2024-11-30"
    r = requests.get(url, headers=HEADERS)
    data = r.json()
    return jsonify(data)

@app.route("/status")
def status():
    r = requests.get(f"{BASE_URL}/status", headers=HEADERS)
    return jsonify(r.json())

@app.route("/fixture/<fixture_id>")
def fixture(fixture_id):
    url = f"{BASE_URL}/fixtures?id={fixture_id}"
    r = requests.get(url, headers=HEADERS)
    return jsonify(r.json())

@app.route("/estadisticas/<fixture_id>")
def estadisticas(fixture_id):
    url = f"{BASE_URL}/fixtures/statistics?fixture={fixture_id}"
    r = requests.get(url, headers=HEADERS)
    return jsonify(r.json())

@app.route("/forma/<team_id>/<temporada>")
def forma(team_id, temporada):
    url = f"{BASE_URL}/fixtures?team={team_id}&season={temporada}&from=2024-10-01&to=2024-11-30"
    r = requests.get(url, headers=HEADERS)
    return jsonify(r.json())

@app.route("/analizar/<fixture_id>")
def analizar(fixture_id):
    from analisis import calcular_filtros, calcular_mercados

    # Traer datos del partido
    fx = requests.get(f"{BASE_URL}/fixtures?id={fixture_id}", headers=HEADERS).json()
    if not fx["response"]:
        return jsonify({"error": "Partido no encontrado"})

    f = fx["response"][0]
    home_id = f["teams"]["home"]["id"]
    away_id = f["teams"]["away"]["id"]
    season = f["league"]["season"]

    # Traer forma de ambos equipos
    forma_home = requests.get(
        f"{BASE_URL}/fixtures?team={home_id}&season={season}&from=2024-09-01&to=2024-11-30",
        headers=HEADERS
    ).json().get("response", [])

    forma_away = requests.get(
        f"{BASE_URL}/fixtures?team={away_id}&season={season}&from=2024-09-01&to=2024-11-30",
        headers=HEADERS
    ).json().get("response", [])

    # Calcular filtros y mercados
    resultado = calcular_filtros(fx, forma_home, forma_away)
    resultado["mercados"] = calcular_mercados(resultado["prob_base"], resultado["ajuste_total"])

    return jsonify(resultado)

if __name__ == "__main__":
    app.run(debug=True)
