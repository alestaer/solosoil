from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import numpy as np
import concurrent.futures

app = Flask(__name__)
CORS(app)

# ============================================================
# ESCALAS
# ============================================================
ESCALAS = {
    "maior":         [0, 2, 4, 5, 7, 9, 11],
    "lidio":         [0, 2, 4, 6, 7, 9, 11],
    "mixolidio":     [0, 2, 4, 5, 7, 9, 10],
    "menor":         [0, 2, 3, 5, 7, 8, 10],
    "dorico":        [0, 2, 3, 5, 7, 9, 10],
    "frigio":        [0, 1, 3, 5, 7, 8, 10],
    "locrio":        [0, 1, 3, 5, 6, 8, 10],
    "cromatica":     [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    "tons_inteiros": [0, 2, 4, 6, 8, 10],
}

NOMES_NOTAS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

def midi_para_nome(n):
    return f"{NOMES_NOTAS[n % 12]}{n // 12 - 1}"

# ============================================================
# SOILGRIDS
# ============================================================
def extrair_valor(dados, propriedade, fator_escala):
    try:
        camadas = dados["properties"]["layers"]
        for camada in camadas:
            if camada["name"] == propriedade:
                for prof in camada["depths"]:
                    valor = prof["values"].get("mean")
                    if valor is not None:
                        return valor / fator_escala
        return None
    except (KeyError, IndexError, TypeError):
        return None

def buscar_solo(lat, lon):
    url = "https://rest.isric.org/soilgrids/v2.0/properties/query"
    params = {
        "lon": lon,
        "lat": lat,
        "property": ["phh2o", "soc", "sand", "clay", "silt",
                     "cec", "nitrogen", "bdod", "cfvo"],
        "depth": ["0-5cm", "5-15cm", "15-30cm"],
        "value": "mean"
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    dados = r.json()

    valores = {
        "pH":        extrair_valor(dados, "phh2o", 10),
        "c_org":     extrair_valor(dados, "soc",   10),
        "areia":     extrair_valor(dados, "sand",  10),
        "argila":    extrair_valor(dados, "clay",  10),
        "limo":      extrair_valor(dados, "silt",  10),
        "cec":       extrair_valor(dados, "cec",   10),
        "azoto":     extrair_valor(dados, "nitrogen", 100),
        "densidade": extrair_valor(dados, "bdod",  100),
        "pedregoso": extrair_valor(dados, "cfvo",  10),
    }
    return dados, valores

# ============================================================
# RETENÇÃO DE ÁGUA — Saxton & Rawls (2006), simplificado
# ============================================================
def calcular_retencao_agua(areia, argila, c_org):
    """
    Estima a capacidade de água disponível para as plantas (AWC), em mm
    de água por cm de solo, a partir da textura e da matéria orgânica.
    Baseado em pedotransfer functions simplificadas.

    Devolve:
      - capacidade_campo: % volumétrica retida a -33 kPa
      - ponto_murcha:     % volumétrica retida a -1500 kPa
      - awc:              água disponível (cap_campo - ponto_murcha), em %
      - classe:           descrição qualitativa
    """
    if areia is None or argila is None:
        return None

    # Converter % para fracções
    S = areia / 100.0
    C = argila / 100.0
    # Matéria orgânica (%) ≈ carbono orgânico (g/kg) × 1.724 / 10
    OM = ((c_org or 10) * 1.724) / 10.0  # em %

    # Ponto de murcha (-1500 kPa)
    theta_1500 = (-0.024 * S + 0.487 * C + 0.006 * OM
                  + 0.005 * (S * OM) - 0.013 * (C * OM)
                  + 0.068 * (S * C) + 0.031)
    theta_1500 = max(theta_1500, 0.01)

    # Capacidade de campo (-33 kPa)
    theta_33 = (-0.251 * S + 0.195 * C + 0.011 * OM
                + 0.006 * (S * OM) - 0.027 * (C * OM)
                + 0.452 * (S * C) + 0.299)
    theta_33 = max(theta_33, theta_1500 + 0.01)

    awc = (theta_33 - theta_1500) * 100  # em %

    if awc < 8:
        classe = "muito baixa"
    elif awc < 12:
        classe = "baixa"
    elif awc < 18:
        classe = "média"
    elif awc < 22:
        classe = "alta"
    else:
        classe = "muito alta"

    return {
        "capacidade_campo_pct": round(theta_33 * 100, 1),
        "ponto_murcha_pct":     round(theta_1500 * 100, 1),
        "awc_pct":              round(awc, 1),
        "classe":               classe,
    }


# ============================================================
# OVERPASS (OpenStreetMap) — fontes potenciais de contaminação
# ============================================================
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Categorias OSM relevantes, com peso para o score de pressão humana
CATEGORIAS_OSM = {
    "mina": {
        "peso": 3,
        "queries": [
            'node["landuse"="quarry"]',
            'way["landuse"="quarry"]',
            'node["man_made"="mineshaft"]',
            'node["industrial"="mine"]',
            'way["industrial"="mine"]',
        ],
    },
    "industria_pesada": {
        "peso": 3,
        "queries": [
            'node["industrial"="refinery"]',
            'way["industrial"="refinery"]',
            'node["industrial"="chemical"]',
            'way["industrial"="chemical"]',
            'node["industrial"="smelting"]',
            'way["industrial"="smelting"]',
        ],
    },
    "central_termica": {
        "peso": 2,
        "queries": [
            'node["power"="plant"]["plant:source"~"coal|oil|gas"]',
            'way["power"="plant"]["plant:source"~"coal|oil|gas"]',
        ],
    },
    "aterro": {
        "peso": 2,
        "queries": [
            'node["landuse"="landfill"]',
            'way["landuse"="landfill"]',
        ],
    },
    "industria_geral": {
        "peso": 1,
        "queries": [
            'node["landuse"="industrial"]',
            'way["landuse"="industrial"]',
        ],
    },
    "agricultura_intensiva": {
        "peso": 1,
        "queries": [
            'node["landuse"="farmland"]["produce"]',
        ],
    },
}

OVERPASS_SERVIDORES = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

def buscar_overpass(lat, lon, raio_m=10000):
    """
    Procura fontes potenciais de contaminação num raio à volta do ponto.
    Tenta vários servidores Overpass até um responder.
    """
    blocos = []
    for cat, info in CATEGORIAS_OSM.items():
        for q in info["queries"]:
            blocos.append(f'{q}(around:{raio_m},{lat},{lon});')

    query = f"""
[out:json][timeout:25];
(
{chr(10).join(blocos)}
);
out center tags 50;
"""

    ultimo_erro = None
    dados = None
    for url in OVERPASS_SERVIDORES:
        print(f"[Overpass] A tentar {url}...")
        try:
            r = requests.post(url, data={"data": query}, timeout=30,
                              headers={"User-Agent": "CuriouSoil/1.0"})
            r.raise_for_status()
            dados = r.json()
            print(f"[Overpass] OK ({len(dados.get('elements', []))} elementos)")
            break
        except Exception as e:
            ultimo_erro = f"{type(e).__name__}: {e}"
            print(f"[Overpass]   falhou: {ultimo_erro}")
            continue

    if dados is None:
        return {
            "disponivel": False,
            "erro": ultimo_erro or "Todos os servidores Overpass falharam",
            "contagens": {},
            "elementos": [],
            "pressao_score": 0,
        }

    contagens = {cat: 0 for cat in CATEGORIAS_OSM}
    elementos = []

    for el in dados.get("elements", []):
        tags = el.get("tags", {})
        cat_encontrada = None
        if tags.get("landuse") == "quarry" or tags.get("man_made") == "mineshaft" or tags.get("industrial") == "mine":
            cat_encontrada = "mina"
        elif tags.get("industrial") in ("refinery", "chemical", "smelting"):
            cat_encontrada = "industria_pesada"
        elif tags.get("power") == "plant" and tags.get("plant:source", "") in ("coal", "oil", "gas"):
            cat_encontrada = "central_termica"
        elif tags.get("landuse") == "landfill":
            cat_encontrada = "aterro"
        elif tags.get("landuse") == "industrial":
            cat_encontrada = "industria_geral"
        elif tags.get("landuse") == "farmland":
            cat_encontrada = "agricultura_intensiva"

        if cat_encontrada is None:
            continue

        contagens[cat_encontrada] += 1

        if "lat" in el and "lon" in el:
            el_lat, el_lon = el["lat"], el["lon"]
        elif "center" in el:
            el_lat, el_lon = el["center"]["lat"], el["center"]["lon"]
        else:
            continue

        nome = tags.get("name") or tags.get("operator") or cat_encontrada
        elementos.append({
            "categoria": cat_encontrada,
            "nome": nome,
            "lat": el_lat,
            "lon": el_lon,
        })

    pressao = 0
    for cat, n in contagens.items():
        if n > 0:
            peso = CATEGORIAS_OSM[cat]["peso"]
            pressao += peso * (1 + np.log1p(n - 1))

    elementos = sorted(elementos, key=lambda e: e["categoria"])[:30]

    return {
        "disponivel": True,
        "contagens": contagens,
        "elementos": elementos,
        "pressao_score": round(float(pressao), 2),
        "raio_km": raio_m / 1000,
    }
# ============================================================
# DIAGNÓSTICO
# ============================================================
def diagnosticar(v, agua, osm):
    avisos = []
    contaminacao_score = 0

    if v["pH"] is not None:
        if v["pH"] < 4.5:
            avisos.append("solo muito ácido")
            contaminacao_score += 2
        elif v["pH"] > 8.5:
            avisos.append("solo muito alcalino / salino")
            contaminacao_score += 2

    if v["c_org"] is not None and v["c_org"] < 5:
        avisos.append("pobre em matéria orgânica")
        contaminacao_score += 1

    if v["azoto"] is not None and v["azoto"] < 0.5:
        avisos.append("baixo em azoto")
        contaminacao_score += 1

    if v["densidade"] is not None and v["densidade"] > 1.6:
        avisos.append("solo compactado")
        contaminacao_score += 1

    if v["cec"] is not None and v["cec"] < 50:
        avisos.append("baixa retenção de nutrientes")
        contaminacao_score += 1

    if v["pedregoso"] is not None and v["pedregoso"] > 30:
        avisos.append("muito pedregoso")

    if agua and agua["awc_pct"] < 8:
        avisos.append("baixa retenção de água")

    # Avisos vindos do OSM
    if osm and osm.get("disponivel"):
        c = osm["contagens"]
        if c.get("mina", 0) > 0:
            avisos.append(f"⚠ {c['mina']} mina(s) num raio de {osm['raio_km']:.0f} km")
        if c.get("industria_pesada", 0) > 0:
            avisos.append(f"⚠ {c['industria_pesada']} instalação(ões) química/refinaria/fundição próximas")
        if c.get("central_termica", 0) > 0:
            avisos.append(f"⚠ {c['central_termica']} central(is) térmica(s) fóssil(eis) próximas")
        if c.get("aterro", 0) > 0:
            avisos.append(f"{c['aterro']} aterro(s) próximo(s)")
        if c.get("industria_geral", 0) >= 3:
            avisos.append(f"{c['industria_geral']} zonas industriais próximas")

    # Pressão humana adiciona ao score de contaminação
    pressao = osm["pressao_score"] if osm and osm.get("disponivel") else 0
    contaminacao_score += int(np.clip(pressao / 2, 0, 5))

    textura = "indefinida"
    if all(v[k] is not None for k in ("areia", "argila", "limo")):
        a, ar, li = v["areia"], v["argila"], v["limo"]
        if ar > 40:   textura = "argiloso"
        elif a > 70:  textura = "arenoso"
        elif li > 40: textura = "limoso"
        else:         textura = "franco"

    return {
        "avisos": avisos,
        "contaminacao_score": contaminacao_score,
        "pressao_humana_score": round(pressao, 2),
        "textura": textura,
    }

# ============================================================
# MAPEAMENTO MUSICAL
# ============================================================
def escolher_escala(pH, contaminacao_score):
    if contaminacao_score >= 4: return "locrio"
    if contaminacao_score >= 3: return "tons_inteiros"
    if pH is None:              return "menor"
    if pH < 4.5:                return "frigio"
    if pH < 5.5:                return "menor"
    if pH < 6.5:                return "dorico"
    if pH < 7.2:                return "maior"
    if pH < 7.8:                return "mixolidio"
    return "lidio"

def parametros_musicais(v, diag, agua):
    pH        = v["pH"]        if v["pH"]        is not None else 6.5
    c_org     = v["c_org"]     if v["c_org"]     is not None else 10
    areia     = v["areia"]     if v["areia"]     is not None else 40
    argila    = v["argila"]    if v["argila"]    is not None else 20
    azoto     = v["azoto"]     if v["azoto"]     is not None else 1.0
    densidade = v["densidade"] if v["densidade"] is not None else 1.3
    cec       = v["cec"]       if v["cec"]       is not None else 100
    pedregoso = v["pedregoso"] if v["pedregoso"] is not None else 5
    awc       = agua["awc_pct"] if agua else 15

    contam = diag["contaminacao_score"]

    # Andamento
    bpm = 90 - (argila - areia) * 0.3
    bpm = float(np.clip(bpm, 50, 130))
    if contam >= 3: bpm *= 0.85

    # Nº de notas
    n_notas = int(np.clip(16 + c_org * 0.8, 16, 64))

    # Tónica
    tonica = int(60 + (pH - 6.5) * 3)
    tonica = int(np.clip(tonica, 48, 72))

    # Oitavas
    if argila > 40:
        oitavas = [0, 12]
    elif areia > 60 or pedregoso > 20:
        oitavas = [-12, 0, 12, 24]
    else:
        oitavas = [0, 12]

    # Dinâmica
    vel_base = float(np.clip(0.45 + azoto * 0.15, 0.4, 0.95))

    # Duração base
    if densidade > 1.55:
        duracoes = [0.125, 0.25, 0.25, 0.5]
    elif densidade < 1.1:
        duracoes = [0.5, 0.75, 1.0, 1.5]
    else:
        duracoes = [0.25, 0.5, 0.5, 1.0]

    # *** RETENÇÃO DE ÁGUA = SUSTAIN / REVERB / LEGATO ***
    # AWC alta → notas mais "molhadas", com mais cauda (sustain longo).
    # AWC baixa → notas secas, staccato.
    sustain = float(np.clip(awc / 25.0, 0.3, 1.6))    # multiplicador de duração
    reverb_mix = float(np.clip(awc / 30.0, 0.05, 0.7))  # 0–1, enviado ao frontend

    # Probabilidades
    prob_silencio    = float(np.clip(pedregoso / 100, 0.0, 0.3))
    prob_dissonancia = float(np.clip(contam * 0.08, 0.0, 0.35))
    prob_acorde      = float(np.clip((cec - 80) / 300, 0.0, 0.35))

    return {
        "bpm": bpm,
        "n_notas": n_notas,
        "tonica": tonica,
        "oitavas": oitavas,
        "vel_base": vel_base,
        "duracoes": duracoes,
        "sustain": sustain,
        "reverb_mix": reverb_mix,
        "prob_silencio": prob_silencio,
        "prob_dissonancia": prob_dissonancia,
        "prob_acorde": prob_acorde,
    }

def gerar_notas(escala_nome, p):
    escala = ESCALAS[escala_nome]
    notas = []
    tempo = 0.0
    fator_tempo = 90.0 / p["bpm"]

    for _ in range(p["n_notas"]):
        duracao_base = float(np.random.choice(p["duracoes"])) * fator_tempo
        duracao = duracao_base * p["sustain"]

        if np.random.random() < p["prob_silencio"]:
            tempo += duracao_base
            continue

        if np.random.random() < p["prob_dissonancia"]:
            intervalo = int(np.random.randint(0, 12))
        else:
            intervalo = int(np.random.choice(escala))

        oitava = int(np.random.choice(p["oitavas"]))
        midi_num = int(np.clip(p["tonica"] + intervalo + oitava, 36, 96))

        vel = float(np.clip(p["vel_base"] + np.random.uniform(-0.1, 0.1), 0.3, 1.0))

        if np.random.random() < p["prob_acorde"]:
            terca  = int(np.clip(midi_num + escala[2 % len(escala)], 36, 96))
            quinta = int(np.clip(midi_num + escala[4 % len(escala)], 36, 96))
            for n in (midi_num, terca, quinta):
                notas.append({
                    "nota": midi_para_nome(n), "midi": n,
                    "inicio": round(tempo, 3),
                    "duracao": round(duracao, 3),
                    "velocity": round(vel, 2)
                })
        else:
            notas.append({
                "nota": midi_para_nome(midi_num), "midi": midi_num,
                "inicio": round(tempo, 3),
                "duracao": round(duracao, 3),
                "velocity": round(vel, 2)
            })

        tempo += duracao_base   # avançar pelo ritmo, não pelo sustain (notas podem sobrepor-se = legato)

    return notas

# ============================================================
# ROTAS
# ============================================================
@app.route("/debug")
def debug():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"erro": "Coordenadas inválidas"}), 400
    try:
        dados, valores = buscar_solo(lat, lon)
        return jsonify({"valores_extraidos": valores, "resposta_crua": dados})
    except Exception as e:
        return jsonify({"erro": str(e)}), 502

@app.route("/gerar")
def gerar():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"erro": "Coordenadas inválidas"}), 400

    # Lançar as duas pesquisas em paralelo
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_solo = pool.submit(buscar_solo, lat, lon)
        f_osm  = pool.submit(buscar_overpass, lat, lon, 10000)

        try:
            _, valores = f_solo.result()
        except requests.RequestException as e:
            return jsonify({"erro": f"Falha ao contactar SoilGrids: {e}"}), 502

        osm = f_osm.result()  # já tem fallback interno

    if all(v is None for v in valores.values()):
        return jsonify({
            "erro": "Sem dados de solo nesta localização (oceano ou zona não coberta)",
            "dica": "Tenta um ponto mais para o interior do continente.",
        }), 404

    em_falta = [k for k, v in valores.items() if v is None]

    agua = calcular_retencao_agua(valores["areia"], valores["argila"], valores["c_org"])
    diag = diagnosticar(valores, agua, osm)
    escala_nome = escolher_escala(valores["pH"], diag["contaminacao_score"])
    p = parametros_musicais(valores, diag, agua)
    notas = gerar_notas(escala_nome, p)

    def arr(x, n=2):
        return round(x, n) if x is not None else None

    return jsonify({
        "solo": {
            "pH":                    arr(valores["pH"]),
            "carbono_organico_g_kg": arr(valores["c_org"]),
            "areia_pct":             arr(valores["areia"]),
            "argila_pct":            arr(valores["argila"]),
            "limo_pct":              arr(valores["limo"]),
            "azoto_g_kg":            arr(valores["azoto"], 3),
            "cec":                   arr(valores["cec"]),
            "densidade_g_cm3":       arr(valores["densidade"]),
            "pedregoso_pct":         arr(valores["pedregoso"]),
            "textura":               diag["textura"],
            "retencao_agua":         agua,
            "avisos":                diag["avisos"],
            "saude_score":           max(0, 10 - diag["contaminacao_score"] * 2),
            "valores_em_falta_substituidos": em_falta,
        },
        "pressao_humana": {
            "disponivel":  osm.get("disponivel", False),
            "raio_km":     osm.get("raio_km", 10),
            "score":       diag["pressao_humana_score"],
            "contagens":   osm.get("contagens", {}),
            "elementos":   osm.get("elementos", []),
            "erro":        osm.get("erro"),
        },
        "musica": {
            "escala":           escala_nome,
            "tonica":           midi_para_nome(p["tonica"]),
            "bpm":              round(p["bpm"], 1),
            "n_notas":          p["n_notas"],
            "sustain":          round(p["sustain"], 2),
            "reverb_mix":       round(p["reverb_mix"], 2),
            "prob_silencio":    round(p["prob_silencio"], 2),
            "prob_dissonancia": round(p["prob_dissonancia"], 2),
            "prob_acorde":      round(p["prob_acorde"], 2),
        },
        "notas": notas
    })

import os

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta, debug=False)
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import numpy as np
import concurrent.futures

app = Flask(__name__)
CORS(app)

# ============================================================
# ESCALAS
# ============================================================
ESCALAS = {
    "maior":         [0, 2, 4, 5, 7, 9, 11],
    "lidio":         [0, 2, 4, 6, 7, 9, 11],
    "mixolidio":     [0, 2, 4, 5, 7, 9, 10],
    "menor":         [0, 2, 3, 5, 7, 8, 10],
    "dorico":        [0, 2, 3, 5, 7, 9, 10],
    "frigio":        [0, 1, 3, 5, 7, 8, 10],
    "locrio":        [0, 1, 3, 5, 6, 8, 10],
    "cromatica":     [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
    "tons_inteiros": [0, 2, 4, 6, 8, 10],
}

NOMES_NOTAS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

def midi_para_nome(n):
    return f"{NOMES_NOTAS[n % 12]}{n // 12 - 1}"

# ============================================================
# SOILGRIDS
# ============================================================
def extrair_valor(dados, propriedade, fator_escala):
    try:
        camadas = dados["properties"]["layers"]
        for camada in camadas:
            if camada["name"] == propriedade:
                for prof in camada["depths"]:
                    valor = prof["values"].get("mean")
                    if valor is not None:
                        return valor / fator_escala
        return None
    except (KeyError, IndexError, TypeError):
        return None

def buscar_solo(lat, lon):
    url = "https://rest.isric.org/soilgrids/v2.0/properties/query"
    params = {
        "lon": lon,
        "lat": lat,
        "property": ["phh2o", "soc", "sand", "clay", "silt",
                     "cec", "nitrogen", "bdod", "cfvo"],
        "depth": ["0-5cm", "5-15cm", "15-30cm"],
        "value": "mean"
    }
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    dados = r.json()

    valores = {
        "pH":        extrair_valor(dados, "phh2o", 10),
        "c_org":     extrair_valor(dados, "soc",   10),
        "areia":     extrair_valor(dados, "sand",  10),
        "argila":    extrair_valor(dados, "clay",  10),
        "limo":      extrair_valor(dados, "silt",  10),
        "cec":       extrair_valor(dados, "cec",   10),
        "azoto":     extrair_valor(dados, "nitrogen", 100),
        "densidade": extrair_valor(dados, "bdod",  100),
        "pedregoso": extrair_valor(dados, "cfvo",  10),
    }
    return dados, valores

# ============================================================
# RETENÇÃO DE ÁGUA — Saxton & Rawls (2006), simplificado
# ============================================================
def calcular_retencao_agua(areia, argila, c_org):
    """
    Estima a capacidade de água disponível para as plantas (AWC), em mm
    de água por cm de solo, a partir da textura e da matéria orgânica.
    Baseado em pedotransfer functions simplificadas.

    Devolve:
      - capacidade_campo: % volumétrica retida a -33 kPa
      - ponto_murcha:     % volumétrica retida a -1500 kPa
      - awc:              água disponível (cap_campo - ponto_murcha), em %
      - classe:           descrição qualitativa
    """
    if areia is None or argila is None:
        return None

    # Converter % para fracções
    S = areia / 100.0
    C = argila / 100.0
    # Matéria orgânica (%) ≈ carbono orgânico (g/kg) × 1.724 / 10
    OM = ((c_org or 10) * 1.724) / 10.0  # em %

    # Ponto de murcha (-1500 kPa)
    theta_1500 = (-0.024 * S + 0.487 * C + 0.006 * OM
                  + 0.005 * (S * OM) - 0.013 * (C * OM)
                  + 0.068 * (S * C) + 0.031)
    theta_1500 = max(theta_1500, 0.01)

    # Capacidade de campo (-33 kPa)
    theta_33 = (-0.251 * S + 0.195 * C + 0.011 * OM
                + 0.006 * (S * OM) - 0.027 * (C * OM)
                + 0.452 * (S * C) + 0.299)
    theta_33 = max(theta_33, theta_1500 + 0.01)

    awc = (theta_33 - theta_1500) * 100  # em %

    if awc < 8:
        classe = "muito baixa"
    elif awc < 12:
        classe = "baixa"
    elif awc < 18:
        classe = "média"
    elif awc < 22:
        classe = "alta"
    else:
        classe = "muito alta"

    return {
        "capacidade_campo_pct": round(theta_33 * 100, 1),
        "ponto_murcha_pct":     round(theta_1500 * 100, 1),
        "awc_pct":              round(awc, 1),
        "classe":               classe,
    }


# ============================================================
# OVERPASS (OpenStreetMap) — fontes potenciais de contaminação
# ============================================================
OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Categorias OSM relevantes, com peso para o score de pressão humana
CATEGORIAS_OSM = {
    "mina": {
        "peso": 3,
        "queries": [
            'node["landuse"="quarry"]',
            'way["landuse"="quarry"]',
            'node["man_made"="mineshaft"]',
            'node["industrial"="mine"]',
            'way["industrial"="mine"]',
        ],
    },
    "industria_pesada": {
        "peso": 3,
        "queries": [
            'node["industrial"="refinery"]',
            'way["industrial"="refinery"]',
            'node["industrial"="chemical"]',
            'way["industrial"="chemical"]',
            'node["industrial"="smelting"]',
            'way["industrial"="smelting"]',
        ],
    },
    "central_termica": {
        "peso": 2,
        "queries": [
            'node["power"="plant"]["plant:source"~"coal|oil|gas"]',
            'way["power"="plant"]["plant:source"~"coal|oil|gas"]',
        ],
    },
    "aterro": {
        "peso": 2,
        "queries": [
            'node["landuse"="landfill"]',
            'way["landuse"="landfill"]',
        ],
    },
    "industria_geral": {
        "peso": 1,
        "queries": [
            'node["landuse"="industrial"]',
            'way["landuse"="industrial"]',
        ],
    },
    "agricultura_intensiva": {
        "peso": 1,
        "queries": [
            'node["landuse"="farmland"]["produce"]',
        ],
    },
}

OVERPASS_SERVIDORES = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

def buscar_overpass(lat, lon, raio_m=10000):
    """
    Procura fontes potenciais de contaminação num raio à volta do ponto.
    Tenta vários servidores Overpass até um responder.
    """
    blocos = []
    for cat, info in CATEGORIAS_OSM.items():
        for q in info["queries"]:
            blocos.append(f'{q}(around:{raio_m},{lat},{lon});')

    query = f"""
[out:json][timeout:25];
(
{chr(10).join(blocos)}
);
out center tags 50;
"""

    ultimo_erro = None
    dados = None
    for url in OVERPASS_SERVIDORES:
        print(f"[Overpass] A tentar {url}...")
        try:
            r = requests.post(url, data={"data": query}, timeout=30,
                              headers={"User-Agent": "CuriouSoil/1.0"})
            r.raise_for_status()
            dados = r.json()
            print(f"[Overpass] OK ({len(dados.get('elements', []))} elementos)")
            break
        except Exception as e:
            ultimo_erro = f"{type(e).__name__}: {e}"
            print(f"[Overpass]   falhou: {ultimo_erro}")
            continue

    if dados is None:
        return {
            "disponivel": False,
            "erro": ultimo_erro or "Todos os servidores Overpass falharam",
            "contagens": {},
            "elementos": [],
            "pressao_score": 0,
        }

    contagens = {cat: 0 for cat in CATEGORIAS_OSM}
    elementos = []

    for el in dados.get("elements", []):
        tags = el.get("tags", {})
        cat_encontrada = None
        if tags.get("landuse") == "quarry" or tags.get("man_made") == "mineshaft" or tags.get("industrial") == "mine":
            cat_encontrada = "mina"
        elif tags.get("industrial") in ("refinery", "chemical", "smelting"):
            cat_encontrada = "industria_pesada"
        elif tags.get("power") == "plant" and tags.get("plant:source", "") in ("coal", "oil", "gas"):
            cat_encontrada = "central_termica"
        elif tags.get("landuse") == "landfill":
            cat_encontrada = "aterro"
        elif tags.get("landuse") == "industrial":
            cat_encontrada = "industria_geral"
        elif tags.get("landuse") == "farmland":
            cat_encontrada = "agricultura_intensiva"

        if cat_encontrada is None:
            continue

        contagens[cat_encontrada] += 1

        if "lat" in el and "lon" in el:
            el_lat, el_lon = el["lat"], el["lon"]
        elif "center" in el:
            el_lat, el_lon = el["center"]["lat"], el["center"]["lon"]
        else:
            continue

        nome = tags.get("name") or tags.get("operator") or cat_encontrada
        elementos.append({
            "categoria": cat_encontrada,
            "nome": nome,
            "lat": el_lat,
            "lon": el_lon,
        })

    pressao = 0
    for cat, n in contagens.items():
        if n > 0:
            peso = CATEGORIAS_OSM[cat]["peso"]
            pressao += peso * (1 + np.log1p(n - 1))

    elementos = sorted(elementos, key=lambda e: e["categoria"])[:30]

    return {
        "disponivel": True,
        "contagens": contagens,
        "elementos": elementos,
        "pressao_score": round(float(pressao), 2),
        "raio_km": raio_m / 1000,
    }
# ============================================================
# DIAGNÓSTICO
# ============================================================
def diagnosticar(v, agua, osm):
    avisos = []
    contaminacao_score = 0

    if v["pH"] is not None:
        if v["pH"] < 4.5:
            avisos.append("solo muito ácido")
            contaminacao_score += 2
        elif v["pH"] > 8.5:
            avisos.append("solo muito alcalino / salino")
            contaminacao_score += 2

    if v["c_org"] is not None and v["c_org"] < 5:
        avisos.append("pobre em matéria orgânica")
        contaminacao_score += 1

    if v["azoto"] is not None and v["azoto"] < 0.5:
        avisos.append("baixo em azoto")
        contaminacao_score += 1

    if v["densidade"] is not None and v["densidade"] > 1.6:
        avisos.append("solo compactado")
        contaminacao_score += 1

    if v["cec"] is not None and v["cec"] < 50:
        avisos.append("baixa retenção de nutrientes")
        contaminacao_score += 1

    if v["pedregoso"] is not None and v["pedregoso"] > 30:
        avisos.append("muito pedregoso")

    if agua and agua["awc_pct"] < 8:
        avisos.append("baixa retenção de água")

    # Avisos vindos do OSM
    if osm and osm.get("disponivel"):
        c = osm["contagens"]
        if c.get("mina", 0) > 0:
            avisos.append(f"⚠ {c['mina']} mina(s) num raio de {osm['raio_km']:.0f} km")
        if c.get("industria_pesada", 0) > 0:
            avisos.append(f"⚠ {c['industria_pesada']} instalação(ões) química/refinaria/fundição próximas")
        if c.get("central_termica", 0) > 0:
            avisos.append(f"⚠ {c['central_termica']} central(is) térmica(s) fóssil(eis) próximas")
        if c.get("aterro", 0) > 0:
            avisos.append(f"{c['aterro']} aterro(s) próximo(s)")
        if c.get("industria_geral", 0) >= 3:
            avisos.append(f"{c['industria_geral']} zonas industriais próximas")

    # Pressão humana adiciona ao score de contaminação
    pressao = osm["pressao_score"] if osm and osm.get("disponivel") else 0
    contaminacao_score += int(np.clip(pressao / 2, 0, 5))

    textura = "indefinida"
    if all(v[k] is not None for k in ("areia", "argila", "limo")):
        a, ar, li = v["areia"], v["argila"], v["limo"]
        if ar > 40:   textura = "argiloso"
        elif a > 70:  textura = "arenoso"
        elif li > 40: textura = "limoso"
        else:         textura = "franco"

    return {
        "avisos": avisos,
        "contaminacao_score": contaminacao_score,
        "pressao_humana_score": round(pressao, 2),
        "textura": textura,
    }

# ============================================================
# MAPEAMENTO MUSICAL
# ============================================================
DURACAO_ALVO_SEG = 30.0  # toda a peça dura ~30s

def escolher_escala(pH, contaminacao_score):
    if contaminacao_score >= 5: return "locrio"
    if contaminacao_score >= 4: return "frigio"
    if pH is None:              return "menor"
    if pH < 4.5:                return "frigio"
    if pH < 5.5:                return "menor"
    if pH < 6.5:                return "dorico"
    if pH < 7.2:                return "maior"
    if pH < 7.8:                return "mixolidio"
    return "lidio"

def parametros_musicais(v, diag, agua):
    pH        = v["pH"]        if v["pH"]        is not None else 6.5
    c_org     = v["c_org"]     if v["c_org"]     is not None else 10
    areia     = v["areia"]     if v["areia"]     is not None else 40
    argila    = v["argila"]    if v["argila"]    is not None else 20
    azoto     = v["azoto"]     if v["azoto"]     is not None else 1.0
    densidade = v["densidade"] if v["densidade"] is not None else 1.3
    cec       = v["cec"]       if v["cec"]       is not None else 100
    pedregoso = v["pedregoso"] if v["pedregoso"] is not None else 5
    awc       = agua["awc_pct"] if agua else 15
    contam    = diag["contaminacao_score"]

    # Andamento — areia = rápido, argila = lento
    bpm = 90 - (argila - areia) * 0.25
    bpm = float(np.clip(bpm, 60, 120))
    if contam >= 4:
        bpm *= 0.9

    # Tónica baseada no pH (mais grave se ácido, mais agudo se alcalino)
    tonica = int(60 + (pH - 6.5) * 2)
    tonica = int(np.clip(tonica, 55, 67))   # mantém num registo cantável

    # Dinâmica base — azoto = energia vital
    vel_base = float(np.clip(0.5 + azoto * 0.12, 0.45, 0.85))

    # Retenção de água → sustain e reverb
    sustain    = float(np.clip(awc / 22.0, 0.5, 1.4))
    reverb_mix = float(np.clip(awc / 35.0, 0.1, 0.55))

    # Probabilidades (mais contidas que antes)
    prob_silencio    = float(np.clip(pedregoso / 200, 0.0, 0.15))
    prob_dissonancia = float(np.clip(contam * 0.05, 0.0, 0.25))
    prob_acorde      = float(np.clip((cec - 80) / 400, 0.0, 0.25))

    # Salto melódico — solos pedregosos/arenosos = mais saltos; argilosos = mais legato
    prob_salto = float(np.clip(0.15 + (areia - argila) / 400 + pedregoso / 400, 0.1, 0.45))

    return {
        "bpm": bpm,
        "tonica": tonica,
        "vel_base": vel_base,
        "sustain": sustain,
        "reverb_mix": reverb_mix,
        "prob_silencio": prob_silencio,
        "prob_dissonancia": prob_dissonancia,
        "prob_acorde": prob_acorde,
        "prob_salto": prob_salto,
    }

def gerar_melodia(escala_nome, p):
    """
    Gera melodia em clave de sol com:
    - duração fixa de ~30s
    - grelha rítmica em colcheias/semínimas
    - caminhar melódico (intervalos pequenos)
    - frases de 4 compassos com cadência
    - arco dinâmico
    """
    escala = ESCALAS[escala_nome]
    bpm = p["bpm"]
    seg_por_pulsacao = 60.0 / bpm

    # Quantas pulsações cabem em 30 segundos
    n_pulsacoes = int(DURACAO_ALVO_SEG / seg_por_pulsacao)
    # Arredondar para múltiplo de 4 (compassos completos)
    n_compassos = max(4, n_pulsacoes // 4)
    n_pulsacoes = n_compassos * 4

    # Padrões rítmicos por compasso (em pulsações) — todos somam 4
    padroes = [
        [1, 1, 1, 1],            # quatro semínimas
        [0.5, 0.5, 1, 1, 1],     # duas colcheias + três semínimas
        [1, 0.5, 0.5, 1, 1],
        [0.5, 0.5, 0.5, 0.5, 1, 1],
        [1, 1, 2],               # com mínima
        [2, 1, 1],
        [0.5, 0.5, 1, 2],
    ]

    notas = []
    tempo_seg = 0.0
    grau_atual = 0   # índice na escala
    tonica = p["tonica"]

    for c in range(n_compassos):
        padrao = padroes[np.random.randint(len(padroes))]

        # Cadência no último compasso de cada frase de 4: termina na tónica
        ultima_do_compasso = (c % 4 == 3)

        for i, dur_pulsacoes in enumerate(padrao):
            duracao_seg = dur_pulsacoes * seg_por_pulsacao * p["sustain"]
            duracao_grelha = dur_pulsacoes * seg_por_pulsacao  # avanço real no tempo

            # Silêncio?
            if np.random.random() < p["prob_silencio"]:
                tempo_seg += duracao_grelha
                continue

            # Última nota da frase (compasso múltiplo de 4)? Volta à tónica
            if ultima_do_compasso and i == len(padrao) - 1:
                grau_atual = 0  # tónica
            else:
                # Caminhar melódico: pequenos passos a maior parte das vezes
                if np.random.random() < p["prob_salto"]:
                    delta = np.random.choice([-3, -2, 2, 3])
                else:
                    delta = np.random.choice([-1, 1])
                grau_atual = int(np.clip(grau_atual + delta, -7, 7))

            # Converte grau → semitons (com oitavas se sair fora da escala)
            grau = grau_atual % len(escala)
            oitava = (grau_atual // len(escala)) * 12
            semitons = escala[grau] + oitava

            # Dissonância cromática (nota de passagem)
            if np.random.random() < p["prob_dissonancia"]:
                semitons += np.random.choice([-1, 1])

            midi_num = int(np.clip(tonica + semitons, 55, 84))

            # Arco dinâmico: mais forte a meio, mais suave nas pontas
            progresso = c / max(1, n_compassos - 1)
            arco = 1.0 - abs(progresso - 0.5) * 0.6  # entre 0.4 e 1.0
            vel = float(np.clip(
                p["vel_base"] * arco + np.random.uniform(-0.05, 0.05),
                0.35, 0.95
            ))

            # Acorde?
            if np.random.random() < p["prob_acorde"] and not ultima_do_compasso:
                terca  = int(np.clip(midi_num + escala[(grau + 2) % len(escala)], 55, 84))
                quinta = int(np.clip(midi_num + escala[(grau + 4) % len(escala)], 55, 84))
                for n_midi in (midi_num, terca, quinta):
                    notas.append({
                        "nota": midi_para_nome(n_midi), "midi": n_midi,
                        "inicio": round(tempo_seg, 3),
                        "duracao": round(duracao_seg, 3),
                        "velocity": round(vel, 2),
                    })
            else:
                notas.append({
                    "nota": midi_para_nome(midi_num), "midi": midi_num,
                    "inicio": round(tempo_seg, 3),
                    "duracao": round(duracao_seg, 3),
                    "velocity": round(vel, 2),
                })

            tempo_seg += duracao_grelha

    return notas, n_compassos

def gerar_baixo(escala_nome, p, n_compassos):
    """
    Linha de baixo em clave de fá, com várias notas por compasso.
    - Progressão harmónica I IV V vi (ou variante cromática em modos escuros).
    - Cada compasso é preenchido com um padrão de baixo: fundamental,
      quinta e oitava do grau, em semínimas ou colcheias.
    - Registo entre C2 e C3.
    """
    escala = ESCALAS[escala_nome]
    bpm = p["bpm"]
    seg_por_pulsacao = 60.0 / bpm
    tonica_baixo = p["tonica"] - 24  # duas oitavas abaixo da melodia

    # Progressão harmónica simples
    progressao_base = [0, 3, 4, 5, 0, 3, 4, 0]   # I IV V vi | I IV V I
    if escala_nome in ("locrio", "frigio") and p["prob_dissonancia"] > 0.15:
        progressao_base = [0, 1, 4, 6, 0, 1, 4, 0]

    # Padrões de baixo (em pulsações dentro de 1 compasso de 4 tempos).
    # Cada tuplo é (offset_em_pulsacoes, duracao_em_pulsacoes, grau_relativo)
    # grau_relativo: 0 = fundamental, 4 = quinta, 7 = oitava (índices da escala)
    padroes_baixo = [
        # walking simples em semínimas: fund - quinta - oitava - quinta
        [(0, 1, 0), (1, 1, 4), (2, 1, 7), (3, 1, 4)],
        # fund longa + arpejo
        [(0, 2, 0), (2, 1, 4), (3, 1, 7)],
        # arpejo ascendente
        [(0, 1, 0), (1, 1, 2), (2, 1, 4), (3, 1, 7)],
        # bombo: fund repetida + quinta
        [(0, 1, 0), (1, 1, 0), (2, 1, 4), (3, 1, 0)],
        # padrão em colcheias na primeira metade
        [(0, 0.5, 0), (0.5, 0.5, 4), (1, 1, 7), (2, 1, 4), (3, 1, 0)],
    ]

    # Andamento lento => padrões mais simples (menos notas).
    # Andamento rápido => padrões mais movidos.
    if bpm < 75:
        padroes_disponiveis = padroes_baixo[:2]
    elif bpm < 95:
        padroes_disponiveis = padroes_baixo[:4]
    else:
        padroes_disponiveis = padroes_baixo

    notas = []
    tempo_seg = 0.0

    for c in range(n_compassos):
        grau_compasso = progressao_base[c % len(progressao_base)]
        padrao = padroes_disponiveis[np.random.randint(len(padroes_disponiveis))]

        # Último compasso da frase de 4 => padrão sempre simples a terminar na fundamental
        ultima_da_frase = (c % 4 == 3)
        if ultima_da_frase:
            padrao = [(0, 4, 0)]  # semibreve na fundamental

        for offset, dur_pulsacoes, grau_rel in padrao:
            grau_total = (grau_compasso + grau_rel) % len(escala)
            oitava_extra = ((grau_compasso + grau_rel) // len(escala)) * 12
            semitons = escala[grau_total] + oitava_extra
            midi_num = int(np.clip(tonica_baixo + semitons, 36, 55))

            inicio = tempo_seg + offset * seg_por_pulsacao
            duracao_seg = dur_pulsacoes * seg_por_pulsacao * 0.92  # quase ligado

            velocity = round(float(np.clip(
                p["vel_base"] * 0.65 + np.random.uniform(-0.04, 0.04),
                0.3, 0.7
            )), 2)

            notas.append({
                "nota": midi_para_nome(midi_num),
                "midi": midi_num,
                "inicio": round(inicio, 3),
                "duracao": round(duracao_seg, 3),
                "velocity": velocity,
            })

        tempo_seg += 4 * seg_por_pulsacao  # avança 1 compasso completo

    return notas

# ============================================================
# ROTAS
# ============================================================
@app.route("/debug")
def debug():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"erro": "Coordenadas inválidas"}), 400
    try:
        dados, valores = buscar_solo(lat, lon)
        return jsonify({"valores_extraidos": valores, "resposta_crua": dados})
    except Exception as e:
        return jsonify({"erro": str(e)}), 502

@app.route("/gerar")
def gerar():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"erro": "Coordenadas inválidas"}), 400

    # Lançar as duas pesquisas em paralelo
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_solo = pool.submit(buscar_solo, lat, lon)
        f_osm  = pool.submit(buscar_overpass, lat, lon, 10000)

        try:
            _, valores = f_solo.result()
        except requests.RequestException as e:
            return jsonify({"erro": f"Falha ao contactar SoilGrids: {e}"}), 502

        osm = f_osm.result()  # já tem fallback interno

    if all(v is None for v in valores.values()):
        return jsonify({
            "erro": "Sem dados de solo nesta localização (oceano ou zona não coberta)",
            "dica": "Tenta um ponto mais para o interior do continente.",
        }), 404

    em_falta = [k for k, v in valores.items() if v is None]

    agua = calcular_retencao_agua(valores["areia"], valores["argila"], valores["c_org"])
    diag = diagnosticar(valores, agua, osm)
    escala_nome = escolher_escala(valores["pH"], diag["contaminacao_score"])
    p = parametros_musicais(valores, diag, agua)

    melodia, n_compassos = gerar_melodia(escala_nome, p)
    baixo = gerar_baixo(escala_nome, p, n_compassos)

    def arr(x, n=2):
        return round(x, n) if x is not None else None

    return jsonify({
        "solo": {
            "pH":                    arr(valores["pH"]),
            "carbono_organico_g_kg": arr(valores["c_org"]),
            "areia_pct":             arr(valores["areia"]),
            "argila_pct":            arr(valores["argila"]),
            "limo_pct":              arr(valores["limo"]),
            "azoto_g_kg":            arr(valores["azoto"], 3),
            "cec":                   arr(valores["cec"]),
            "densidade_g_cm3":       arr(valores["densidade"]),
            "pedregoso_pct":         arr(valores["pedregoso"]),
            "textura":               diag["textura"],
            "retencao_agua":         agua,
            "avisos":                diag["avisos"],
            "saude_score":           max(0, 10 - diag["contaminacao_score"] * 2),
            "valores_em_falta_substituidos": em_falta,
        },
        "pressao_humana": {
            "disponivel":  osm.get("disponivel", False),
            "raio_km":     osm.get("raio_km", 10),
            "score":       diag["pressao_humana_score"],
            "contagens":   osm.get("contagens", {}),
            "elementos":   osm.get("elementos", []),
            "erro":        osm.get("erro"),
        },
        "musica": {
            "escala":           escala_nome,
            "tonica":           midi_para_nome(p["tonica"]),
            "bpm":              round(p["bpm"], 1),
            "n_compassos":      n_compassos,
            "duracao_seg":      DURACAO_ALVO_SEG,
            "sustain":          round(p["sustain"], 2),
            "reverb_mix":       round(p["reverb_mix"], 2),
            "prob_silencio":    round(p["prob_silencio"], 2),
            "prob_dissonancia": round(p["prob_dissonancia"], 2),
            "prob_acorde":      round(p["prob_acorde"], 2),
        },
        "melodia": melodia,
        "baixo":   baixo,
        "notas":   melodia,   # mantido por retrocompatibilidade
    })
import os

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta, debug=False)
