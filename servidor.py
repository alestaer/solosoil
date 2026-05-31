# -*- coding: utf-8 -*-
"""
CuriouSoil — sonificação educativa de solos.

Filosofia do mapeamento
------------------------
Separamos *o que o solo é* (qualidades intrínsecas → conteúdo e carácter da
música) de *o que os humanos lhe fizeram* (pressão/degradação → distorções
sobrepostas: dissonância, cromatismo, rupturas). Um solo saudável soa íntegro
e consonante; um solo degradado soa perturbado. Isto é claro pedagogicamente
e eficaz como artivismo.

A "dificuldade" funciona como ENVELOPE (define os intervalos de tempo,
compasso, âmbito, densidade rítmica permitidos) e o SOLO define a POSIÇÃO
dentro desse envelope. Assim a peça é sempre exequível por alunos do nível
escolhido, e na mesma continua a distinguir audivelmente solos diferentes.
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import numpy as np
import concurrent.futures
import os
import io
import base64
import struct
import hashlib
import time
from xml.sax.saxutils import escape as xml_escape

app = Flask(__name__)
CORS(app)

# ============================================================
# TEORIA — escalas, tonalidades, soletração
# ============================================================
ESCALA_MAIOR = [0, 2, 4, 5, 7, 9, 11]
ESCALA_MENOR = [0, 2, 3, 5, 7, 8, 10]          # menor natural (poucos acidentes)

NOMES_LETRA = ["C", "D", "E", "F", "G", "A", "B"]
PC_NATURAL = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

# Ordem dos sustenidos / bemóis na armação
ORDEM_SUST = ["F", "C", "G", "D", "A", "E", "B"]
ORDEM_BEMOL = ["B", "E", "A", "D", "G", "C", "F"]

# Tonalidades limitadas a <= 3 acidentes (exequíveis), ordenadas por brilho
# (lado bemol = grave/escuro = ácido ; lado sustenido = brilhante = alcalino)
# (tonic_pc, vexflow_keysig, fifths, etiqueta_pt)
TONALIDADES_MAIOR = [
    (3,  "Eb", -3, "Mi♭ maior"),
    (10, "Bb", -2, "Si♭ maior"),
    (5,  "F",  -1, "Fá maior"),
    (0,  "C",   0, "Dó maior"),
    (7,  "G",   1, "Sol maior"),
    (2,  "D",   2, "Ré maior"),
    (9,  "A",   3, "Lá maior"),
]
TONALIDADES_MENOR = [
    (0,  "Cm",  -3, "Dó menor"),
    (7,  "Gm",  -2, "Sol menor"),
    (2,  "Dm",  -1, "Ré menor"),
    (9,  "Am",   0, "Lá menor"),
    (4,  "Em",   1, "Mi menor"),
    (11, "Bm",   2, "Si menor"),
    (6,  "F#m",  3, "Fá♯ menor"),
]


def armacao_alteracoes(fifths):
    """Devolve {letra: alteracao(-1/0/+1)} implicada pela armação de clave."""
    alt = {l: 0 for l in NOMES_LETRA}
    if fifths > 0:
        for i in range(fifths):
            alt[ORDEM_SUST[i]] = 1
    elif fifths < 0:
        for i in range(-fifths):
            alt[ORDEM_BEMOL[i]] = -1
    return alt


class Tonalidade:
    """Encapsula uma tonalidade concreta e sabe soletrar notas MIDI."""

    def __init__(self, tonic_pc, keysig, fifths, etiqueta, modo):
        self.tonic_pc = tonic_pc
        self.keysig = keysig
        self.fifths = fifths
        self.etiqueta = etiqueta
        self.modo = modo                      # "maior" | "menor"
        self.escala_int = ESCALA_MAIOR if modo == "maior" else ESCALA_MENOR
        self.scale_pcs = [(tonic_pc + i) % 12 for i in self.escala_int]
        self.sig_alt = armacao_alteracoes(fifths)
        # Soletração diatónica: pc -> (letra, alteracao)
        self._spell = {}
        for letra in NOMES_LETRA:
            pc = (PC_NATURAL[letra] + self.sig_alt[letra]) % 12
            self._spell[pc] = (letra, self.sig_alt[letra])

    def grau_para_midi(self, grau, oitava_base):
        """grau: índice na escala (pode ser <0 ou >6, dá a volta com oitavas)."""
        tam = len(self.escala_int)
        oitava = grau // tam
        idx = grau % tam
        semitons = self.escala_int[idx] + 12 * oitava
        return self.tonic_pc + semitons + 12 * (oitava_base + 1)

    def soletrar(self, midi, alteracao_extra=0):
        """
        Devolve dicionário de soletração para uma nota MIDI.
        alteracao_extra: +1/-1 para cromatismo (nota fora da escala).
        """
        pc = midi % 12
        oitava = midi // 12 - 1
        if alteracao_extra == 0 and pc in self._spell:
            letra, alt = self._spell[pc]
        else:
            # Nota cromática: soletra a partir da letra diatónica vizinha
            base_pc = (pc - alteracao_extra) % 12
            if base_pc in self._spell:
                letra, base_alt = self._spell[base_pc]
                alt = base_alt + alteracao_extra
            else:
                # fallback: escolhe sustenido em tons sustenidos, bemol em bemóis
                if self.fifths >= 0:
                    base = (pc - 1) % 12
                    letra = self._spell.get(base, ("C", 0))[0]
                    alt = 1
                else:
                    base = (pc + 1) % 12
                    letra = self._spell.get(base, ("C", 0))[0]
                    alt = -1
        # Corrigir oitava quando a alteração empurra a letra para outra oitava
        # (ex.: Cb pertence à oitava anterior). Mantemos simples: usa oitava do pc.
        glifo = {-2: "bb", -1: "b", 0: "", 1: "#", 2: "##"}[max(-2, min(2, alt))]
        vexkey = f"{letra.lower()}{glifo}/{oitava}"
        # Acidente a DESENHAR: só quando difere da armação para aquela letra
        sig = self.sig_alt[letra]
        vex_acc = None
        if alt != sig:
            vex_acc = {-2: "bb", -1: "b", 0: "n", 1: "#", 2: "##"}[max(-2, min(2, alt))]
        return {
            "letra": letra, "alteracao": alt, "oitava": oitava,
            "vexkey": vexkey, "vex_acc": vex_acc,
        }


def escolher_tonalidade(pH, saude_score, contaminacao_score):
    """pH -> centro tonal (brilho) ; saúde -> maior/menor."""
    if pH is None:
        pH = 6.5
    modo = "maior" if saude_score >= 7 else "menor"
    lista = TONALIDADES_MAIOR if modo == "maior" else TONALIDADES_MENOR
    # pH 3.5 (ácido, escuro) -> idx 0 ; pH 9 (alcalino, brilhante) -> idx 6
    t = (float(np.clip(pH, 3.5, 9.0)) - 3.5) / (9.0 - 3.5)
    idx = int(round(t * (len(lista) - 1)))
    tonic_pc, keysig, fifths, etiqueta = lista[idx]
    return Tonalidade(tonic_pc, keysig, fifths, etiqueta, modo)


NOMES_NOTAS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_para_nome(n):
    return f"{NOMES_NOTAS[n % 12]}{n // 12 - 1}"


# ============================================================
# DURAÇÕES — sem pontos nem quiálteras (garante métrica e leitura simples)
# dur_q = comprimento em semínimas (1.0 = semínima)
# ============================================================
# dur_q -> (vexflow, musicxml_type)
DUR_TABELA = {
    0.25: ("16", "16th"),
    0.5:  ("8",  "eighth"),
    1.0:  ("q",  "quarter"),
    2.0:  ("h",  "half"),
    4.0:  ("w",  "whole"),
}


def dur_para_vex(dur_q, is_rest):
    vex, _ = DUR_TABELA[dur_q]
    return (vex + "r") if is_rest else vex


# ============================================================
# CÉLULAS RÍTMICAS por compasso e por nível (cada célula soma ao compasso)
# Estrutura: meter_id -> { "facil":[...], "medio":[...], "dificil":[...] }
# (somas verificadas nos testes)
# ============================================================
CELULAS = {
    "4/4": {  # soma 4.0
        "facil":  [[4.0], [2.0, 2.0], [2.0, 1.0, 1.0], [1.0, 1.0, 2.0],
                   [1.0, 1.0, 1.0, 1.0]],
        "medio":  [[1.0, 1.0, 1.0, 1.0], [2.0, 1.0, 1.0],
                   [0.5, 0.5, 1.0, 1.0, 1.0], [1.0, 0.5, 0.5, 1.0, 1.0],
                   [1.0, 1.0, 0.5, 0.5, 1.0], [0.5, 0.5, 0.5, 0.5, 1.0, 1.0]],
        "dificil": [[0.5, 0.5, 0.5, 0.5, 1.0, 1.0],
                    [0.25, 0.25, 0.5, 1.0, 1.0, 1.0],
                    [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 1.0],
                    [1.0, 0.5, 0.25, 0.25, 0.5, 0.5, 1.0],
                    [0.25, 0.25, 0.25, 0.25, 1.0, 1.0, 1.0]],
    },
    "3/4": {  # soma 3.0
        "facil":  [[2.0, 1.0], [1.0, 2.0], [1.0, 1.0, 1.0]],
        "medio":  [[1.0, 1.0, 1.0], [2.0, 1.0],
                   [0.5, 0.5, 1.0, 1.0], [1.0, 0.5, 0.5, 1.0]],
        "dificil": [[0.5, 0.5, 0.5, 0.5, 1.0], [1.0, 0.5, 0.5, 0.5, 0.5],
                    [0.25, 0.25, 0.5, 1.0, 1.0]],
    },
    "2/4": {  # soma 2.0
        "facil":  [[2.0], [1.0, 1.0]],
        "medio":  [[1.0, 1.0], [0.5, 0.5, 1.0], [1.0, 0.5, 0.5]],
        "dificil": [[0.5, 0.5, 0.5, 0.5], [0.25, 0.25, 0.5, 1.0]],
    },
    "6/8": {  # soma 3.0, base colcheia, agrupar 3+3
        "facil":  [[1.0, 0.5, 1.0, 0.5]],
        "medio":  [[1.0, 0.5, 1.0, 0.5], [0.5, 0.5, 0.5, 1.0, 0.5],
                   [1.0, 0.5, 0.5, 0.5, 0.5]],
        "dificil": [[0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
                    [0.5, 0.5, 0.5, 1.0, 0.5]],
    },
    "5/4": {  # soma 5.0 (só avançado)
        "facil":  [[2.0, 2.0, 1.0]],
        "medio":  [[2.0, 2.0, 1.0], [1.0, 1.0, 1.0, 2.0], [2.0, 1.0, 1.0, 1.0]],
        "dificil": [[1.0, 0.5, 0.5, 1.0, 1.0, 1.0], [2.0, 1.0, 0.5, 0.5, 1.0]],
    },
    "7/8": {  # soma 3.5 (só avançado), base colcheia
        "facil":  [[1.0, 1.0, 0.5, 0.5, 0.5]],
        "medio":  [[1.0, 1.0, 0.5, 0.5, 0.5], [1.0, 0.5, 1.0, 0.5, 0.5]],
        "dificil": [[0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
                    [1.0, 0.5, 0.5, 0.5, 0.5, 0.5]],
    },
}

META_BATIDAS = {  # comprimento do compasso em semínimas
    "4/4": 4.0, "3/4": 3.0, "2/4": 2.0, "6/8": 3.0, "5/4": 5.0, "7/8": 3.5,
}
META_VEX = {  # numerador/denominador para VexFlow + MusicXML
    "4/4": (4, 4), "3/4": (3, 4), "2/4": (2, 4),
    "6/8": (6, 8), "5/4": (5, 4), "7/8": (7, 8),
}


# ============================================================
# DIFICULDADE — o envelope
# ============================================================
DIFICULDADE = {
    "basico": {
        "bpm": (60, 76),
        "metros": ["4/4", "3/4"],
        "tiers_ritmo": ["facil"],
        "ambito_graus": 7,        # ~1 oitava (graus da escala)
        "max_cromatismo": 0.05,
        "max_dissonancia": 0.04,
        "max_salto_graus": 3,     # salto máximo (3 graus ~ 4ª/5ª)
        "permite_acordes": False,
        "min_dur": 0.5,           # colcheia mínima
    },
    "intermedio": {
        "bpm": (80, 100),
        "metros": ["4/4", "3/4", "2/4", "6/8"],
        "tiers_ritmo": ["facil", "medio"],
        "ambito_graus": 11,       # ~1.5 oitavas
        "max_cromatismo": 0.18,
        "max_dissonancia": 0.15,
        "max_salto_graus": 4,
        "permite_acordes": True,
        "min_dur": 0.5,
    },
    "avancado": {
        "bpm": (104, 132),
        "metros": ["4/4", "3/4", "2/4", "6/8", "5/4", "7/8"],
        "tiers_ritmo": ["facil", "medio", "dificil"],
        "ambito_graus": 15,       # ~2 oitavas
        "max_cromatismo": 0.35,
        "max_dissonancia": 0.30,
        "max_salto_graus": 6,
        "permite_acordes": True,
        "min_dur": 0.25,          # semicolcheia
    },
}


# ============================================================
# INSTRUMENTOS — âmbitos (MIDI) e programas General MIDI
# ============================================================
INSTRUMENTOS = {
    "piano":       {"nome": "Piano",        "gm": 0,  "min": 36, "max": 84, "papel_pref": "qualquer"},
    "violino":     {"nome": "Violino",      "gm": 40, "min": 55, "max": 96, "papel_pref": "melodia"},
    "violoncelo":  {"nome": "Violoncelo",   "gm": 42, "min": 36, "max": 72, "papel_pref": "baixo"},
    "flauta":      {"nome": "Flauta",       "gm": 73, "min": 60, "max": 93, "papel_pref": "melodia"},
    "clarinete":   {"nome": "Clarinete",    "gm": 71, "min": 50, "max": 89, "papel_pref": "melodia"},
    "oboe":        {"nome": "Oboé",         "gm": 68, "min": 58, "max": 88, "papel_pref": "melodia"},
    "trompete":    {"nome": "Trompete",     "gm": 56, "min": 54, "max": 82, "papel_pref": "melodia"},
    "marimba":     {"nome": "Marimba",      "gm": 12, "min": 45, "max": 84, "papel_pref": "qualquer"},
    "guitarra":    {"nome": "Guitarra",     "gm": 24, "min": 40, "max": 81, "papel_pref": "harmonia"},
    "contrabaixo": {"nome": "Contrabaixo",  "gm": 43, "min": 28, "max": 60, "papel_pref": "baixo"},
}
PAPEIS_CLAVE = {"melodia": "treble", "harmonia": "treble", "baixo": "bass"}


def atribuir_papeis(ids_instr):
    """Distribui até 3 instrumentos por papéis (melodia/harmonia/baixo)."""
    ids = [i for i in ids_instr if i in INSTRUMENTOS][:3]
    if not ids:
        ids = ["piano"]
    if len(ids) == 1:
        return [("melodia", ids[0])]
    if len(ids) == 2:
        return [("melodia", ids[0]), ("baixo", ids[1])]
    return [("melodia", ids[0]), ("harmonia", ids[1]), ("baixo", ids[2])]


# ============================================================
# SOILGRIDS
# ============================================================
def extrair_valor(dados, propriedade, fator_escala):
    try:
        for camada in dados["properties"]["layers"]:
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
        "lon": lon, "lat": lat,
        "property": ["phh2o", "soc", "sand", "clay", "silt",
                     "cec", "nitrogen", "bdod", "cfvo"],
        "depth": ["0-5cm", "5-15cm", "15-30cm"], "value": "mean",
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
    if areia is None or argila is None:
        return None
    S = areia / 100.0
    C = argila / 100.0
    OM = ((c_org or 10) * 1.724) / 10.0
    theta_1500 = (-0.024 * S + 0.487 * C + 0.006 * OM
                  + 0.005 * (S * OM) - 0.013 * (C * OM)
                  + 0.068 * (S * C) + 0.031)
    theta_1500 = max(theta_1500, 0.01)
    theta_33 = (-0.251 * S + 0.195 * C + 0.011 * OM
                + 0.006 * (S * OM) - 0.027 * (C * OM)
                + 0.452 * (S * C) + 0.299)
    theta_33 = max(theta_33, theta_1500 + 0.01)
    awc = (theta_33 - theta_1500) * 100
    if awc < 8:    classe = "muito baixa"
    elif awc < 12: classe = "baixa"
    elif awc < 18: classe = "média"
    elif awc < 22: classe = "alta"
    else:          classe = "muito alta"
    return {
        "capacidade_campo_pct": round(theta_33 * 100, 1),
        "ponto_murcha_pct":     round(theta_1500 * 100, 1),
        "awc_pct":              round(awc, 1),
        "classe":               classe,
    }


# ============================================================
# OVERPASS (OSM) — fontes potenciais de pressão humana
# ============================================================
CATEGORIAS_OSM = {
    "mina": {"peso": 3, "queries": [
        'node["landuse"="quarry"]', 'way["landuse"="quarry"]',
        'node["man_made"="mineshaft"]', 'node["industrial"="mine"]',
        'way["industrial"="mine"]']},
    "industria_pesada": {"peso": 3, "queries": [
        'node["industrial"="refinery"]', 'way["industrial"="refinery"]',
        'node["industrial"="chemical"]', 'way["industrial"="chemical"]',
        'node["industrial"="smelting"]', 'way["industrial"="smelting"]']},
    "central_termica": {"peso": 2, "queries": [
        'node["power"="plant"]["plant:source"~"coal|oil|gas"]',
        'way["power"="plant"]["plant:source"~"coal|oil|gas"]']},
    "aterro": {"peso": 2, "queries": [
        'node["landuse"="landfill"]', 'way["landuse"="landfill"]']},
    "industria_geral": {"peso": 1, "queries": [
        'node["landuse"="industrial"]', 'way["landuse"="industrial"]']},
    "agricultura_intensiva": {"peso": 1, "queries": [
        'node["landuse"="farmland"]["produce"]']},
}
OVERPASS_SERVIDORES = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]


def buscar_overpass(lat, lon, raio_m=10000):
    blocos = []
    for cat, info in CATEGORIAS_OSM.items():
        for q in info["queries"]:
            blocos.append(f'{q}(around:{raio_m},{lat},{lon});')
    query = f"[out:json][timeout:25];\n(\n{chr(10).join(blocos)}\n);\nout center tags 50;\n"
    ultimo_erro, dados = None, None
    for url in OVERPASS_SERVIDORES:
        try:
            r = requests.post(url, data={"data": query}, timeout=30,
                              headers={"User-Agent": "CuriouSoil/2.0"})
            r.raise_for_status()
            dados = r.json()
            break
        except Exception as e:
            ultimo_erro = f"{type(e).__name__}: {e}"
            continue
    if dados is None:
        return {"disponivel": False, "erro": ultimo_erro or "Overpass falhou",
                "contagens": {}, "elementos": [], "pressao_score": 0,
                "raio_km": raio_m / 1000}
    contagens = {cat: 0 for cat in CATEGORIAS_OSM}
    elementos = []
    for el in dados.get("elements", []):
        tags = el.get("tags", {})
        cat = None
        if tags.get("landuse") == "quarry" or tags.get("man_made") == "mineshaft" or tags.get("industrial") == "mine":
            cat = "mina"
        elif tags.get("industrial") in ("refinery", "chemical", "smelting"):
            cat = "industria_pesada"
        elif tags.get("power") == "plant" and tags.get("plant:source", "") in ("coal", "oil", "gas"):
            cat = "central_termica"
        elif tags.get("landuse") == "landfill":
            cat = "aterro"
        elif tags.get("landuse") == "industrial":
            cat = "industria_geral"
        elif tags.get("landuse") == "farmland":
            cat = "agricultura_intensiva"
        if cat is None:
            continue
        contagens[cat] += 1
        if "lat" in el and "lon" in el:
            la, lo = el["lat"], el["lon"]
        elif "center" in el:
            la, lo = el["center"]["lat"], el["center"]["lon"]
        else:
            continue
        elementos.append({"categoria": cat,
                          "nome": tags.get("name") or tags.get("operator") or cat,
                          "lat": la, "lon": lo})
    pressao = 0.0
    for cat, n in contagens.items():
        if n > 0:
            pressao += CATEGORIAS_OSM[cat]["peso"] * (1 + float(np.log1p(n - 1)))
    elementos = sorted(elementos, key=lambda e: e["categoria"])[:30]
    return {"disponivel": True, "contagens": contagens, "elementos": elementos,
            "pressao_score": round(float(pressao), 2), "raio_km": raio_m / 1000}


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
TPQ = 480


def _vlq(n):
    """Variable-length quantity."""
    buf = n & 0x7F
    n >>= 7
    out = bytearray()
    out.append(buf)
    while n:
        out.insert(0, (n & 0x7F) | 0x80)
        n >>= 7
    return bytes(out)


def _track_chunk(events):
    body = bytearray()
    for ev in events:
        body += ev
    body += _vlq(0) + bytes([0xFF, 0x2F, 0x00])   # end of track
    return b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


def construir_midi(partes, bpm):
    # Faixa 0: tempo
    micros = int(60_000_000 / bpm)
    tempo_meta = _vlq(0) + bytes([0xFF, 0x51, 0x03]) + micros.to_bytes(3, "big")
    faixa0 = _track_chunk([tempo_meta])

    faixas = [faixa0]
    for parte in partes:
        gm = INSTRUMENTOS.get(parte["instrumento"], {}).get("gm", 0)
        canal = 0
        # eventos absolutos (tick, tipo, dados)
        abs_events = []
        # program change no tick 0
        abs_events.append((0, 0, bytes([0xC0 | canal, gm & 0x7F])))
        for compasso in parte["compassos"]:
            for ev in compasso:
                if ev["is_rest"]:
                    continue
                t0 = int(round((ev["inicio_seg"] * bpm / 60.0) * TPQ))
                durt = max(1, int(round((ev["duracao_seg"] * bpm / 60.0) * TPQ)))
                vel = int(np.clip(ev["velocity"] * 127, 1, 127))
                notas = [ev["midi"]] + [a["midi"] for a in ev.get("acorde", [])]
                for nm in notas:
                    abs_events.append((t0, 1, bytes([0x90 | canal, nm & 0x7F, vel])))
                    abs_events.append((t0 + durt, 0, bytes([0x80 | canal, nm & 0x7F, 0])))
        # ordenar por tick, note-off antes de note-on no mesmo tick
        abs_events.sort(key=lambda e: (e[0], e[1]))
        delta_events = []
        prev = 0
        for tick, _tipo, data in abs_events:
            delta = max(0, tick - prev)
            delta_events.append(_vlq(delta) + data)
            prev = tick
        faixas.append(_track_chunk(delta_events))

    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(faixas), TPQ)
    return header + b"".join(faixas)


# ============================================================
# ORQUESTRADOR — junta tudo
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


@app.route("/instrumentos")
def listar_instrumentos():
    return jsonify({
        "instrumentos": [{"id": k, "nome": v["nome"]} for k, v in INSTRUMENTOS.items()],
        "dificuldades": list(DIFICULDADE.keys()),
        "duracao_max_seg": 273,
    })


def flatten_para_legado(partes):
    """Achata as partes no formato antigo {nota, inicio, duracao, velocity},
    usado pelo leitor de piano A/B do frontend (acordes expandidos em notas
    separadas). Vozes em clave de sol -> melodia; em clave de fá -> baixo."""
    melodia, baixo = [], []
    for parte in partes:
        destino = baixo if parte["clef"] == "bass" else melodia
        for comp in parte["compassos"]:
            for ev in comp:
                if ev["is_rest"]:
                    continue
                destino.append({"nota": ev["nome"], "inicio": ev["inicio_seg"],
                                "duracao": ev["duracao_seg"], "velocity": ev["velocity"]})
                for a in ev.get("acorde", []):
                    destino.append({"nota": midi_para_nome(a["midi"]),
                                    "inicio": ev["inicio_seg"], "duracao": ev["duracao_seg"],
                                    "velocity": ev["velocity"]})
    melodia.sort(key=lambda n: n["inicio"])
    baixo.sort(key=lambda n: n["inicio"])
    return melodia, baixo


def _ler_opcoes():
    dif = (request.args.get("dificuldade") or "intermedio").lower()
    if dif not in DIFICULDADE:
        dif = "intermedio"
    try:
        dur = float(request.args.get("duracao", 60))
    except (TypeError, ValueError):
        dur = 60.0
    dur = float(np.clip(dur, 10, 273))         # 4'33" = 273 s
    modo = (request.args.get("modo") or "acustico").lower()
    instr = (request.args.get("instrumentos") or "piano").split(",")
    instr = [i.strip() for i in instr if i.strip()]
    seed = request.args.get("seed")
    return {"dificuldade": dif, "duracao_seg": dur, "modo": modo,
            "instrumentos": instr, "seed": seed}


@app.route("/gerar")
def gerar():
    try:
        lat = float(request.args.get("lat"))
        lon = float(request.args.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"erro": "Coordenadas inválidas"}), 400

    opcoes = _ler_opcoes()
    # Semente determinística: a mesma localização + as mesmas definições geram
    # sempre a MESMA peça (reprodutível em sala de aula). Um seed explícito
    # (botão "Regenerar") permite pedir uma variação sobre a mesma localização.
    if opcoes["seed"]:
        chave = str(opcoes["seed"])
    else:
        chave = "|".join([
            f"{round(lat, 3)}", f"{round(lon, 3)}",
            opcoes["dificuldade"], str(int(opcoes["duracao_seg"])),
            opcoes["modo"], ",".join(opcoes["instrumentos"]),
        ])
    semente = int(hashlib.md5(chave.encode("utf-8")).hexdigest(), 16) % (2**32)
    np.random.seed(semente)

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f_solo = pool.submit(solo_em_cache, lat, lon)
        f_osm = pool.submit(osm_em_cache, lat, lon, 10000)
        try:
            _, valores = f_solo.result()
        except requests.RequestException as e:
            return jsonify({"erro": f"Falha ao contactar SoilGrids: {e}"}), 502
        osm = f_osm.result()

    if all(v is None for v in valores.values()):
        return jsonify({
            "erro": "Sem dados de solo nesta localização (oceano ou zona não coberta)",
            "dica": "Tenta um ponto mais para o interior do continente.",
        }), 404

    em_falta = [k for k, v in valores.items() if v is None]
    agua = calcular_retencao_agua(valores["areia"], valores["argila"], valores["c_org"])
    diag = diagnosticar(valores, agua, osm)

    tonal, p, metro, n_compassos, partes, duracao_real = montar_peca(
        valores, agua, diag, osm, opcoes)

    num, den = META_VEX[metro]
    musicxml = construir_musicxml(partes, tonal, metro)
    midi_bytes = construir_midi(partes, p["bpm"])
    midi_b64 = base64.b64encode(midi_bytes).decode("ascii")
    melodia_legado, baixo_legado = flatten_para_legado(partes)

    def arr(x, n=2):
        return round(x, n) if x is not None else None

    return jsonify({
        "solo": {
            "pH": arr(valores["pH"]),
            "carbono_organico_g_kg": arr(valores["c_org"]),
            "areia_pct": arr(valores["areia"]),
            "argila_pct": arr(valores["argila"]),
            "limo_pct": arr(valores["limo"]),
            "azoto_g_kg": arr(valores["azoto"], 3),
            "cec": arr(valores["cec"]),
            "densidade_g_cm3": arr(valores["densidade"]),
            "pedregoso_pct": arr(valores["pedregoso"]),
            "textura": diag["textura"],
            "retencao_agua": agua,
            "avisos": diag["avisos"],
            "saude_score": diag["saude_score"],
            "valores_em_falta_substituidos": em_falta,
        },
        "pressao_humana": {
            "disponivel": osm.get("disponivel", False),
            "raio_km": osm.get("raio_km", 10),
            "score": diag["pressao_humana_score"],
            "contagens": osm.get("contagens", {}),
            "elementos": osm.get("elementos", []),
            "erro": osm.get("erro"),
        },
        "musica": {
            "tonalidade": tonal.etiqueta,
            "modo": tonal.modo,
            "keysig_vexflow": tonal.keysig,
            "compasso": f"{num}/{den}",
            "compasso_num": num, "compasso_den": den,
            "bpm": round(p["bpm"], 1),
            "n_compassos": n_compassos,
            "duracao_seg": duracao_real,
            "dificuldade": opcoes["dificuldade"],
            "modo_geracao": opcoes["modo"],
            "reverb_mix": round(p["reverb_mix"], 2),
            # parâmetros expostos para o painel educativo
            "prob_salto": round(p["prob_salto"], 2),
            "prob_silencio": round(p["prob_silencio"], 2),
            "vivacidade": round(p["vivacidade"], 2),
            "prob_staccato": round(p["prob_staccato"], 2),
            "frase_compassos": p["frase_compassos"],
            "prob_acorde": round(p["prob_acorde"], 2),
            "prob_cromatico": round(p["prob_cromatico"], 2),
            "prob_dissonancia": round(p["prob_dissonancia"], 2),
            "explicacao": explicar(valores, diag, agua, osm, tonal, metro, p),
            # compatibilidade com o painel antigo
            "escala": tonal.modo,
            "tonica": NOMES_NOTAS[tonal.tonic_pc],
            "sustain": round(p["legato"], 2),
        },
        "partes": [
            {"papel": pt["papel"], "instrumento": pt["instrumento"],
             "instrumento_nome": pt["instrumento_nome"], "gm": pt["gm"],
             "clef": pt["clef"], "compassos": pt["compassos"]}
            for pt in partes
        ],
        "exportacao": {
            "musicxml": musicxml,
            "midi_base64": midi_b64,
        },
        "melodia": melodia_legado,
        "baixo": baixo_legado,
        "notas": melodia_legado,
        "semente": semente,
    })
import os

if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta, debug=False)
