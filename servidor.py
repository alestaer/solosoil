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

from flask import Flask, request, jsonify, send_from_directory
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

# Diretório onde está este ficheiro (serve o frontend, se estiver ao lado).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@app.route("/")
def _index():
    # Serve a página, se index.html estiver ao lado do servidor.
    # (Em produção com frontend separado, esta rota fica simplesmente sem uso.)
    if os.path.exists(os.path.join(BASE_DIR, "index.html")):
        return send_from_directory(BASE_DIR, "index.html")
    return jsonify({"servico": "CuriouSoil", "rotas": ["/gerar", "/instrumentos", "/debug"]})


@app.route("/<path:ficheiro>")
def _estatico(ficheiro):
    # Serve app.js / favicon / etc. quando o frontend é servido por este processo.
    if ficheiro in ("app.js", "index.html", "favicon.ico") and os.path.exists(os.path.join(BASE_DIR, ficheiro)):
        return send_from_directory(BASE_DIR, ficheiro)
    return jsonify({"erro": "não encontrado"}), 404

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
# ESTILOS — carácter musical aproximado.
# O estilo "doma" a aleatoriedade para soar convencional: limita
# cromatismo/dissonância e escolhe a linguagem harmónica, o padrão de
# baixo e o tipo de acordes. O SOLO continua a definir tonalidade,
# modo (maior/menor), registo e densidade — a degradação manifesta-se
# como tristeza/escuridão/esparsidade, não como ruído atonal.
#   crom_cap / diss_cap : tetos de cromatismo e dissonância
#   salto_mult          : escala a probabilidade de saltos
#   legato_bonus        : soma ao legato (notas mais ligadas)
#   staccato_mult       : escala o staccato
#   consonante          : melodia procura notas do acorde nos tempos fortes
#   harmonia_tipo       : triade | setima | estendida | tintinnabuli
#   baixo_padrao        : sustentado | raiz_quinta | alberti | arpejo | caminhante | pedal | tintinnabuli
#   prog_voc            : vocabulário de progressão
#   tempo_mult          : multiplica o andamento (dentro do envelope)
#   densidade_mult      : empurra para mais/menos notas por compasso
#   reverb_bonus        : soma à reverberação sugerida
#   tintinnabuli        : geração à Arvo Pärt (voz-M diatónica + voz-T da tríade)
# ============================================================
ESTILOS = {
    "livre": {
        "nome": "Livre (artivismo)", "crom_cap": 1.0, "diss_cap": 1.0,
        "salto_mult": 1.0, "legato_bonus": 0.0, "staccato_mult": 1.0,
        "consonante": False, "harmonia_tipo": "triade", "baixo_padrao": "caminhante",
        "prog_voc": "livre", "tempo_mult": 1.0, "densidade_mult": 1.0,
        "reverb_bonus": 0.0, "tintinnabuli": False,
    },
    "classico": {
        "nome": "Clássico", "crom_cap": 0.03, "diss_cap": 0.0,
        "salto_mult": 0.9, "legato_bonus": 0.05, "staccato_mult": 1.0,
        "consonante": True, "harmonia_tipo": "triade", "baixo_padrao": "alberti",
        "prog_voc": "funcional", "tempo_mult": 1.0, "densidade_mult": 1.0,
        "reverb_bonus": 0.0, "tintinnabuli": False,
    },
    "romantico": {
        "nome": "Romântico", "crom_cap": 0.12, "diss_cap": 0.06,
        "salto_mult": 1.15, "legato_bonus": 0.2, "staccato_mult": 0.4,
        "consonante": True, "harmonia_tipo": "setima", "baixo_padrao": "arpejo",
        "prog_voc": "romantica", "tempo_mult": 0.92, "densidade_mult": 0.95,
        "reverb_bonus": 0.12, "tintinnabuli": False,
    },
    "impressionista": {
        "nome": "Impressionista", "crom_cap": 0.0, "diss_cap": 0.0,
        "salto_mult": 0.7, "legato_bonus": 0.28, "staccato_mult": 0.15,
        "consonante": True, "harmonia_tipo": "estendida", "baixo_padrao": "pedal",
        "prog_voc": "modal", "tempo_mult": 0.95, "densidade_mult": 0.9,
        "reverb_bonus": 0.28, "tintinnabuli": False,
    },
    "minimal": {
        "nome": "Minimal (Arvo Pärt)", "crom_cap": 0.0, "diss_cap": 0.0,
        "salto_mult": 0.0, "legato_bonus": 0.35, "staccato_mult": 0.0,
        "consonante": True, "harmonia_tipo": "tintinnabuli", "baixo_padrao": "tintinnabuli",
        "prog_voc": "minimal", "tempo_mult": 0.78, "densidade_mult": 0.55,
        "reverb_bonus": 0.3, "tintinnabuli": True,
    },
}
INSTRUMENTOS = {
    # Teclas
    "piano":       {"nome": "Piano",        "gm": 0,  "min": 28, "max": 96, "familia": "teclas",     "papel_pref": "qualquer"},
    "celesta":     {"nome": "Celesta",      "gm": 8,  "min": 60, "max": 105,"familia": "teclas",     "papel_pref": "melodia"},
    # Cordas
    "violino":     {"nome": "Violino",      "gm": 40, "min": 55, "max": 100,"familia": "cordas",     "papel_pref": "melodia"},
    "viola":       {"nome": "Viola",        "gm": 41, "min": 48, "max": 88, "familia": "cordas",     "papel_pref": "harmonia"},
    "violoncelo":  {"nome": "Violoncelo",   "gm": 42, "min": 36, "max": 76, "familia": "cordas",     "papel_pref": "baixo"},
    "contrabaixo": {"nome": "Contrabaixo",  "gm": 43, "min": 28, "max": 60, "familia": "cordas",     "papel_pref": "baixo"},
    "harpa":       {"nome": "Harpa",        "gm": 46, "min": 36, "max": 96, "familia": "cordas",     "papel_pref": "qualquer"},
    "guitarra":    {"nome": "Guitarra",     "gm": 24, "min": 40, "max": 84, "familia": "cordas",     "papel_pref": "harmonia"},
    # Madeiras
    "flauta":      {"nome": "Flauta",       "gm": 73, "min": 60, "max": 96, "familia": "madeiras",   "papel_pref": "melodia"},
    "flautim":     {"nome": "Flautim",      "gm": 72, "min": 74, "max": 108,"familia": "madeiras",   "papel_pref": "melodia"},
    "oboe":        {"nome": "Oboé",         "gm": 68, "min": 58, "max": 91, "familia": "madeiras",   "papel_pref": "melodia"},
    "clarinete":   {"nome": "Clarinete",    "gm": 71, "min": 50, "max": 90, "familia": "madeiras",   "papel_pref": "melodia"},
    "fagote":      {"nome": "Fagote",       "gm": 70, "min": 34, "max": 75, "familia": "madeiras",   "papel_pref": "baixo"},
    # Metais
    "trompete":    {"nome": "Trompete",     "gm": 56, "min": 55, "max": 82, "familia": "metais",     "papel_pref": "melodia"},
    "trompa":      {"nome": "Trompa",       "gm": 60, "min": 41, "max": 77, "familia": "metais",     "papel_pref": "harmonia"},
    "trombone":    {"nome": "Trombone",     "gm": 57, "min": 40, "max": 72, "familia": "metais",     "papel_pref": "baixo"},
    "tuba":        {"nome": "Tuba",         "gm": 58, "min": 28, "max": 58, "familia": "metais",     "papel_pref": "baixo"},
    # Percussão afinada
    "marimba":     {"nome": "Marimba",      "gm": 12, "min": 45, "max": 84, "familia": "percussao",  "papel_pref": "qualquer"},
    "vibrafone":   {"nome": "Vibrafone",    "gm": 11, "min": 53, "max": 89, "familia": "percussao",  "papel_pref": "harmonia"},
    "xilofone":    {"nome": "Xilofone",     "gm": 13, "min": 65, "max": 96, "familia": "percussao",  "papel_pref": "melodia"},
    "glockenspiel":{"nome": "Glockenspiel", "gm": 9,  "min": 79, "max": 108,"familia": "percussao",  "papel_pref": "melodia"},
}

# Grupos para a interface (revelação progressiva): família -> instrumentos
FAMILIAS = [
    {"id": "teclas",    "nome": "Teclas",    "instrumentos": ["piano", "celesta"]},
    {"id": "cordas",    "nome": "Cordas",    "instrumentos": ["violino", "viola", "violoncelo", "contrabaixo", "harpa", "guitarra"]},
    {"id": "madeiras",  "nome": "Madeiras",  "instrumentos": ["flauta", "flautim", "oboe", "clarinete", "fagote"]},
    {"id": "metais",    "nome": "Metais",    "instrumentos": ["trompete", "trompa", "trombone", "tuba"]},
    {"id": "percussao", "nome": "Percussão", "instrumentos": ["marimba", "vibrafone", "xilofone", "glockenspiel"]},
]
PAPEIS_CLAVE = {"melodia": "treble", "harmonia": "treble", "baixo": "bass"}


def atribuir_papeis(ids_instr, estilo="livre"):
    """Distribui até 3 instrumentos por papéis. O piano a solo recebe um
    grand staff (duas pautas: mão direita = melodia, mão esquerda = baixo)."""
    ids = [i for i in ids_instr if i in INSTRUMENTOS][:3]
    if not ids:
        ids = ["piano"]
    if len(ids) == 1 and ids[0] in ("piano", "harpa", "celesta"):
        return [
            {"papel": "melodia", "instrumento": ids[0], "clef": "treble", "grupo": "g1", "staff": 1},
            {"papel": "baixo",   "instrumento": ids[0], "clef": "bass",   "grupo": "g1", "staff": 2},
        ]
    if len(ids) == 1:
        return [{"papel": "melodia", "instrumento": ids[0], "clef": "treble", "grupo": None, "staff": None}]
    if len(ids) == 2:
        return [
            {"papel": "melodia", "instrumento": ids[0], "clef": "treble", "grupo": None, "staff": None},
            {"papel": "baixo",   "instrumento": ids[1], "clef": "bass",   "grupo": None, "staff": None},
        ]
    return [
        {"papel": "melodia",  "instrumento": ids[0], "clef": "treble", "grupo": None, "staff": None},
        {"papel": "harmonia", "instrumento": ids[1], "clef": "treble", "grupo": None, "staff": None},
        {"papel": "baixo",    "instrumento": ids[2], "clef": "bass",   "grupo": None, "staff": None},
    ]


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
    cont = 0
    if v["pH"] is not None:
        if v["pH"] < 4.5:
            avisos.append("solo muito ácido"); cont += 2
        elif v["pH"] > 8.5:
            avisos.append("solo muito alcalino / salino"); cont += 2
    if v["c_org"] is not None and v["c_org"] < 5:
        avisos.append("pobre em matéria orgânica"); cont += 1
    if v["azoto"] is not None and v["azoto"] < 0.5:
        avisos.append("baixo em azoto"); cont += 1
    if v["densidade"] is not None and v["densidade"] > 1.6:
        avisos.append("solo compactado"); cont += 1
    if v["cec"] is not None and v["cec"] < 50:
        avisos.append("baixa retenção de nutrientes"); cont += 1
    if v["pedregoso"] is not None and v["pedregoso"] > 30:
        avisos.append("muito pedregoso")
    if agua and agua["awc_pct"] < 8:
        avisos.append("baixa retenção de água")

    if osm and osm.get("disponivel"):
        c = osm["contagens"]
        if c.get("mina", 0) > 0:
            avisos.append(f"⚠ {c['mina']} mina(s)/pedreira(s) num raio de {osm['raio_km']:.0f} km")
        if c.get("industria_pesada", 0) > 0:
            avisos.append(f"⚠ {c['industria_pesada']} instalação(ões) química/refinaria/fundição próximas")
        if c.get("central_termica", 0) > 0:
            avisos.append(f"⚠ {c['central_termica']} central(is) térmica(s) fóssil(eis) próximas")
        if c.get("aterro", 0) > 0:
            avisos.append(f"{c['aterro']} aterro(s) próximo(s)")
        if c.get("industria_geral", 0) >= 3:
            avisos.append(f"{c['industria_geral']} zonas industriais próximas")

    pressao = osm["pressao_score"] if osm and osm.get("disponivel") else 0
    cont += int(np.clip(pressao / 2, 0, 5))

    textura = "indefinida"
    if all(v[k] is not None for k in ("areia", "argila", "limo")):
        a, ar, li = v["areia"], v["argila"], v["limo"]
        if ar > 40:   textura = "argiloso"
        elif a > 70:  textura = "arenoso"
        elif li > 40: textura = "limoso"
        else:         textura = "franco"

    saude = max(0, 10 - cont * 2)
    return {"avisos": avisos, "contaminacao_score": cont,
            "pressao_humana_score": round(pressao, 2),
            "textura": textura, "saude_score": saude}


# ============================================================
# MAPEAMENTO SOLO -> PARÂMETROS MUSICAIS
# ============================================================
def escolher_metro(textura, dif, saude, pressao):
    """Compasso a partir da textura, restringido pelo nível e degradação."""
    permitidos = DIFICULDADE[dif]["metros"]
    sugestao = {"argiloso": "3/4", "arenoso": "2/4",
                "limoso": "6/8", "franco": "4/4"}.get(textura, "4/4")
    # Solo muito degradado em nível avançado -> "terreno instável"
    if dif == "avancado" and (saude <= 2 or pressao >= 6):
        sugestao = "7/8" if textura in ("arenoso", "limoso") else "5/4"
    if sugestao in permitidos:
        return sugestao
    # Fallback hierárquico
    for alt in ("4/4", "3/4", "2/4", "6/8"):
        if alt in permitidos:
            return alt
    return permitidos[0]


def parametros_musicais(v, diag, agua, osm, dif, estilo="livre"):
    env = DIFICULDADE[dif]
    est = ESTILOS.get(estilo, ESTILOS["livre"])
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
    saude     = diag["saude_score"]

    contagens = osm.get("contagens", {}) if osm else {}
    n_minas   = contagens.get("mina", 0)
    n_pesada  = contagens.get("industria_pesada", 0)

    # --- ANDAMENTO: só pela dificuldade (sem influência de areia/argila) ---
    bpm_min, bpm_max = env["bpm"]
    bpm = float(np.clip((bpm_min + bpm_max) / 2 + np.random.uniform(-4, 4),
                        bpm_min, bpm_max))

    # --- TEXTURA MELÓDICA: areia=saltos+espaço ; argila=conjunto+ligado ---
    eixo = (areia - argila) / 100.0                      # -1 (argila) .. +1 (areia)
    prob_salto = float(np.clip(0.18 + eixo * 0.30 + n_minas * 0.04, 0.05, 0.55))
    prob_silencio = float(np.clip(0.04 + max(0, eixo) * 0.18
                                  + pedregoso / 400 + n_minas * 0.02, 0.0, 0.30))

    # --- VIVACIDADE (carbono orgânico): densidade rítmica + staccato ---
    vivacidade = float(np.clip(c_org / 40.0, 0.0, 1.0))  # 0..1
    prob_staccato = float(np.clip(0.10 + vivacidade * 0.55, 0.0, 0.7))

    # --- ÁGUA (AWC): comprimento das frases + tendência a notas longas ---
    frase_compassos = int(np.clip(round(2 + awc / 6.0), 2, 8))   # 2..8 compassos
    legato = float(np.clip(awc / 25.0, 0.2, 1.0))                # favorece notas longas

    # --- DINÂMICA (azoto): energia vital ---
    vel_base = float(np.clip(0.45 + azoto * 0.12, 0.4, 0.85))

    # --- HARMONIA (CEC): riqueza de acordes ---
    prob_acorde = 0.0
    if env["permite_acordes"]:
        prob_acorde = float(np.clip((cec - 60) / 350, 0.0, 0.4))

    # --- DEGRADAÇÃO humana: cromatismo (indústria) + dissonância ---
    prob_cromatico = float(np.clip(n_pesada * 0.07 + max(0, (4 - saude)) * 0.03,
                                   0.0, env["max_cromatismo"]))
    prob_dissonancia = float(np.clip(contam * 0.03 + n_pesada * 0.04,
                                     0.0, env["max_dissonancia"]))

    # --- REGISTO / peso: solo compacto soa mais grave. POR OITAVAS, para
    #     manter a peça diatónica à tonalidade (aplicado só em registo_para_papel). ---
    if densidade >= 1.55:
        transpor = -12
    elif densidade <= 1.05:
        transpor = 12
    else:
        transpor = 0

    # --- REVERB (frontend): mais água -> mais cauda ---
    reverb_mix = float(np.clip(awc / 35.0, 0.08, 0.55))

    # =========================================================
    # ESTILO — doma a aleatoriedade e fixa a linguagem musical.
    # O solo já definiu tonalidade/modo/registo/densidade; aqui
    # limitamos o "ruído" e escolhemos baixo/acordes/progressão.
    # =========================================================
    bpm = float(np.clip(bpm * est["tempo_mult"], bpm_min, bpm_max))
    legato = float(np.clip(legato + est["legato_bonus"], 0.2, 1.0))
    prob_staccato = float(np.clip(prob_staccato * est["staccato_mult"], 0.0, 0.7))
    prob_salto = float(np.clip(prob_salto * est["salto_mult"], 0.0, 0.6))
    prob_cromatico = float(np.clip(prob_cromatico, 0.0, est["crom_cap"]))
    prob_dissonancia = float(np.clip(prob_dissonancia, 0.0, est["diss_cap"]))
    reverb_mix = float(np.clip(reverb_mix + est["reverb_bonus"], 0.08, 0.9))
    # densidade: empurra a vivacidade efetiva (usada na escolha de células)
    vivacidade_efetiva = float(np.clip(vivacidade * est["densidade_mult"], 0.0, 1.0))
    if est["densidade_mult"] < 1.0:
        # estilos esparsos preferem frases mais longas e respiradas
        prob_silencio = float(np.clip(prob_silencio + (1 - est["densidade_mult"]) * 0.15, 0.0, 0.4))

    return {
        "bpm": bpm,
        "prob_salto": prob_salto,
        "prob_silencio": prob_silencio,
        "vivacidade": vivacidade_efetiva,
        "prob_staccato": prob_staccato,
        "frase_compassos": frase_compassos,
        "legato": legato,
        "vel_base": vel_base,
        "prob_acorde": prob_acorde,
        "prob_cromatico": prob_cromatico,
        "prob_dissonancia": prob_dissonancia,
        "transpor": transpor,
        "reverb_mix": reverb_mix,
        "max_salto_graus": env["max_salto_graus"],
        "ambito_graus": env["ambito_graus"],
        "min_dur": env["min_dur"],
        # campos de estilo
        "estilo": estilo,
        "consonante": est["consonante"],
        "harmonia_tipo": est["harmonia_tipo"],
        "baixo_padrao": est["baixo_padrao"],
        "prog_voc": est["prog_voc"],
        "tintinnabuli": est["tintinnabuli"],
    }


# ============================================================
# GERAÇÃO RÍTMICA — escolhe células válidas (somas garantidas)
# ============================================================
def escolher_celula(metro, dif, vivacidade, legato):
    tiers = DIFICULDADE[dif]["tiers_ritmo"]
    banco = CELULAS[metro]
    # Pesos por tier consoante vivacidade (mais vivo -> tiers densos)
    candidatos = []
    pesos = []
    for tier in tiers:
        for cel in banco[tier]:
            candidatos.append(cel)
            n = len(cel)
            dens = n / META_BATIDAS[metro]          # notas por semínima (densidade)
            # vivacidade puxa para mais notas; legato puxa para menos
            p = 1.0 + vivacidade * dens * 2.5 - legato * (dens * 1.5)
            pesos.append(max(0.05, p))
    pesos = np.array(pesos)
    pesos = pesos / pesos.sum()
    i = int(np.random.choice(len(candidatos), p=pesos))
    return list(candidatos[i])


# ============================================================
# PROGRESSÃO HARMÓNICA (graus da escala, índices 0..6)
# ============================================================
def progressao(tonal, n_compassos, prog_voc="livre", frase=4):
    """Devolve o grau-raiz (0..6) de cada compasso. Garante cadência no fim
    de cada frase (penúltimo = dominante V, último = tónica I/i) nos estilos
    funcionais. Graus: 0=I 1=ii 2=iii 3=IV 4=V 5=vi 6=vii."""
    maior = (tonal.modo == "maior")
    if prog_voc == "funcional":
        ciclos = ([0, 3, 4, 0], [0, 5, 3, 4], [0, 4, 5, 3], [0, 1, 4, 0]) if maior \
            else ([0, 5, 4, 0], [0, 3, 4, 0], [0, 5, 2, 4])
    elif prog_voc == "romantica":
        ciclos = ([0, 5, 3, 4], [0, 4, 5, 2], [0, 3, 1, 4]) if maior \
            else ([0, 5, 2, 4], [0, 3, 5, 4], [0, 6, 2, 4])
    elif prog_voc == "modal":
        # planagem/ambiente: pouca função, oscila por graus próximos
        ciclos = ([0, 3, 4, 3], [0, 1, 5, 3], [0, 5, 3, 0])
    elif prog_voc == "minimal":
        ciclos = ([0, 0, 5, 0], [0, 3, 0, 0])
    else:  # livre (artivismo)
        ciclos = ([0, 4, 5, 3],) if maior else ([0, 5, 2, 4],)

    funcional = prog_voc in ("funcional", "romantica")
    prog = []
    ciclo = ciclos[0]
    for c in range(n_compassos):
        pos_na_frase = c % frase
        if pos_na_frase == 0:
            ciclo = ciclos[int(np.random.randint(len(ciclos)))]
        grau = ciclo[pos_na_frase % len(ciclo)]
        # cadência: penúltimo da frase -> V ; último -> I
        if funcional and frase >= 2:
            if pos_na_frase == frase - 1:
                grau = 0
            elif pos_na_frase == frase - 2:
                grau = 4
        prog.append(grau)
    return prog


# ============================================================
# MELODIA
# ============================================================
def _graus_acorde(grau_raiz):
    """Conjunto de classes de grau (mod 7) que formam a tríade da raiz."""
    return {(grau_raiz) % 7, (grau_raiz + 2) % 7, (grau_raiz + 4) % 7}


def _snap_acorde(grau, grau_raiz):
    """Aproxima `grau` ao grau de acorde mais próximo (mantém a oitava)."""
    alvos = _graus_acorde(grau_raiz)
    melhor, melhor_d = grau, 99
    for cand in range(grau - 3, grau + 4):
        if cand % 7 in alvos and abs(cand - grau) < melhor_d:
            melhor, melhor_d = cand, abs(cand - grau)
    return melhor


def gerar_melodia(tonal, p, metro, n_compassos, oitava_base, lim_min, lim_max, prog=None):
    escala = tonal.escala_int
    tam = len(escala)
    beats = META_BATIDAS[metro]
    seg_por_q = 60.0 / p["bpm"]
    amb = p["ambito_graus"]

    compassos = []
    tempo_q = 0.0
    grau = 0                          # arranca na tónica
    for c in range(n_compassos):
        eventos = []
        celula = escolher_celula(metro, dif_atual(p), p["vivacidade"], p["legato"])
        ultima_da_frase = ((c + 1) % p["frase_compassos"] == 0)
        n_ev = len(celula)
        for i, dur_q in enumerate(celula):
            # Limita duração ao envelope (nunca mais curto que min_dur)
            if dur_q < p["min_dur"]:
                dur_q = p["min_dur"]
            is_rest = (np.random.random() < p["prob_silencio"]
                       and not (ultima_da_frase and i == n_ev - 1))
            if is_rest:
                eventos.append(_evento_pausa(dur_q, tonal, tempo_q, seg_por_q))
                tempo_q += dur_q
                continue

            # Movimento melódico
            if ultima_da_frase and i == n_ev - 1:
                grau = 0                                      # cadência na tónica
            else:
                if np.random.random() < p["prob_salto"]:
                    salto = int(np.random.randint(2, p["max_salto_graus"] + 1))
                    delta = salto * (1 if np.random.random() < 0.5 else -1)
                else:
                    delta = 1 if np.random.random() < 0.55 else -1
                grau = int(np.clip(grau + delta, -amb // 2, amb // 2))

            # Consonância (estilos convencionais): tempos fortes e notas longas
            # caem numa nota do acorde do compasso -> soa "certo" ao ouvido.
            tempo_forte = (i == 0) or (dur_q >= 2.0)
            if p.get("consonante") and prog is not None and tempo_forte \
                    and not (ultima_da_frase and i == n_ev - 1):
                grau = int(np.clip(_snap_acorde(grau, prog[c]), -amb // 2, amb // 2))

            # grau -> MIDI (registo já vem de oitava_base; sem transpor cromático)
            midi = tonal.grau_para_midi(grau, oitava_base)

            # Cromatismo (nota fora da escala) — nunca em tempos fortes consonantes
            alteracao_extra = 0
            permite_crom = not (p.get("consonante") and tempo_forte)
            if permite_crom and not (ultima_da_frase and i == n_ev - 1) \
                    and np.random.random() < p["prob_cromatico"]:
                alteracao_extra = 1 if np.random.random() < 0.5 else -1
                midi += alteracao_extra

            midi = int(np.clip(midi, lim_min, lim_max))

            # Dinâmica: arco (mais forte a meio)
            frac = c / max(1, n_compassos - 1)
            arco = 1.0 - abs(frac - 0.5) * 0.5
            vel = float(np.clip(p["vel_base"] * arco + np.random.uniform(-0.05, 0.05),
                                0.35, 0.95))
            staccato = (np.random.random() < p["prob_staccato"]
                        and dur_q <= 1.0)

            sol = tonal.soletrar(midi, alteracao_extra)
            ev = _evento_nota(midi, dur_q, vel, staccato, sol, tempo_q, seg_por_q,
                              p["legato"])

            # Acorde (engrossa a textura)
            if (p["prob_acorde"] > 0 and not staccato
                    and np.random.random() < p["prob_acorde"]
                    and not (ultima_da_frase and i == n_ev - 1)):
                ev["acorde"] = []
                for add in (2, 4):                            # 3ª e 5ª da escala
                    g2 = grau + add
                    m2 = int(np.clip(tonal.grau_para_midi(g2, oitava_base),
                                     lim_min, lim_max))
                    s2 = tonal.soletrar(m2)
                    ev["acorde"].append({"midi": m2, "vexkey": s2["vexkey"],
                                         "vex_acc": s2["vex_acc"],
                                         "step": s2["letra"], "alter": s2["alteracao"],
                                         "octave": s2["oitava"]})
            eventos.append(ev)
            tempo_q += dur_q
        compassos.append(eventos)
    return compassos


# pequeno truque: guardar a dificuldade nos params para escolher_celula
def dif_atual(p):
    return p.get("_dif", "intermedio")


def _evento_nota(midi, dur_q, vel, staccato, sol, tempo_q, seg_por_q, legato):
    dur_soa = dur_q * (0.55 if staccato else (0.85 + 0.13 * legato))
    return {
        "is_rest": False,
        "midi": midi,
        "nome": midi_para_nome(midi),
        "vexkey": sol["vexkey"], "vex_acc": sol["vex_acc"],
        "step": sol["letra"], "alter": sol["alteracao"], "octave": sol["oitava"],
        "vex_dur": dur_para_vex(dur_q, False),
        "dur_q": dur_q,
        "staccato": staccato,
        "velocity": round(vel, 2),
        "inicio_seg": round(tempo_q * seg_por_q, 4),
        "duracao_seg": round(dur_soa * seg_por_q, 4),
    }


def _evento_pausa(dur_q, tonal, tempo_q, seg_por_q):
    # arredonda pausas a valores sem ponto
    if dur_q not in DUR_TABELA:
        dur_q = min(DUR_TABELA, key=lambda d: abs(d - dur_q))
    return {
        "is_rest": True, "midi": None, "nome": "rest",
        "vexkey": "b/4", "vex_acc": None,
        "vex_dur": dur_para_vex(dur_q, True),
        "dur_q": dur_q, "staccato": False, "velocity": 0,
        "inicio_seg": round(tempo_q * seg_por_q, 4), "duracao_seg": 0,
    }


# ============================================================
# HARMONIA (acordes por compasso)
# ============================================================
def gerar_harmonia(tonal, p, metro, n_compassos, oitava_base, lim_min, lim_max, prog):
    seg_por_q = 60.0 / p["bpm"]
    beats = META_BATIDAS[metro]
    compassos = []
    tempo_q = 0.0
    for c in range(n_compassos):
        eventos = []
        grau_raiz = prog[c]
        tipo = p.get("harmonia_tipo", "triade")
        if tipo == "setima":
            graus_acorde = (0, 2, 4, 6)
        elif tipo == "estendida":
            graus_acorde = (0, 2, 4, 8)        # add9 (cor impressionista)
        elif tipo == "tintinnabuli":
            graus_acorde = (0, 2, 4)           # só tríade (preenchido pela voz-T)
        else:
            graus_acorde = (0, 2, 4)
        notas_acorde = []
        for add in graus_acorde:
            g = grau_raiz + add
            m = int(np.clip(tonal.grau_para_midi(g, oitava_base),
                            lim_min, lim_max))
            notas_acorde.append(m)
        # Acorde sustentado no compasso inteiro (fácil) ou em batidas
        dur_q = beats
        # garante valor notável; se compasso > 4 ou ímpar, parte em metades
        if dur_q in DUR_TABELA:
            blocos = [dur_q]
        elif dur_q == 3.0:
            blocos = [2.0, 1.0]
        elif dur_q == 5.0:
            blocos = [2.0, 2.0, 1.0]
        elif dur_q == 3.5:
            blocos = [2.0, 1.0, 0.5]
        else:
            blocos = [2.0, 2.0]
        for b in blocos:
            base = notas_acorde[0]
            s = tonal.soletrar(base)
            ev = {
                "is_rest": False, "midi": base, "nome": midi_para_nome(base),
                "vexkey": s["vexkey"], "vex_acc": s["vex_acc"],
                "step": s["letra"], "alter": s["alteracao"], "octave": s["oitava"],
                "vex_dur": dur_para_vex(b, False), "dur_q": b,
                "staccato": False,
                "velocity": round(float(np.clip(p["vel_base"] * 0.7, 0.3, 0.7)), 2),
                "inicio_seg": round(tempo_q * seg_por_q, 4),
                "duracao_seg": round(b * 0.95 * seg_por_q, 4),
                "acorde": [],
            }
            for m in notas_acorde[1:]:
                s2 = tonal.soletrar(m)
                ev["acorde"].append({"midi": m, "vexkey": s2["vexkey"],
                                     "vex_acc": s2["vex_acc"], "step": s2["letra"],
                                     "alter": s2["alteracao"], "octave": s2["oitava"]})
            eventos.append(ev)
            tempo_q += b
        compassos.append(eventos)
    return compassos


# ============================================================
# BAIXO (fundamental/quinta da progressão)
# ============================================================
def _blocos_sustentado(beats):
    """Decompõe um comprimento (em semínimas) em valores SEM ponto que somam
    exatamente o compasso (re-articula a fundamental em vez de usar ligaduras)."""
    restante = round(beats, 4)
    blocos = []
    for val in (4.0, 2.0, 1.0, 0.5, 0.25):
        while restante >= val - 1e-9:
            blocos.append(val)
            restante = round(restante - val, 4)
    return blocos or [beats]


_RITMO_BAIXO = {
    "4/4": [1.0, 1.0, 1.0, 1.0], "3/4": [1.0, 1.0, 1.0], "2/4": [1.0, 1.0],
    "6/8": [1.0, 0.5, 1.0, 0.5], "5/4": [1.0, 1.0, 1.0, 1.0, 1.0],
    "7/8": [1.0, 1.0, 1.0, 0.5],
}


def gerar_baixo(tonal, p, metro, n_compassos, oitava_base, lim_min, lim_max, prog):
    seg_por_q = 60.0 / p["bpm"]
    beats = META_BATIDAS[metro]
    padrao_nome = p.get("baixo_padrao", "caminhante")
    compassos = []
    tempo_q = 0.0
    for c in range(n_compassos):
        eventos = []
        grau_raiz = prog[c]
        ultima = ((c + 1) % p["frase_compassos"] == 0)

        # Construir a sequência (grau, duração) conforme o padrão de estilo.
        if ultima or padrao_nome in ("sustentado", "pedal"):
            raiz = 0 if padrao_nome == "pedal" else grau_raiz
            padrao = [(raiz, d) for d in _blocos_sustentado(beats)]
        else:
            ritmo = _RITMO_BAIXO.get(metro, _blocos_sustentado(beats))
            n = len(ritmo)
            if padrao_nome == "raiz_quinta":
                graus = [grau_raiz if k % 2 == 0 else grau_raiz + 4 for k in range(n)]
            elif padrao_nome == "alberti":
                ciclo = [grau_raiz, grau_raiz + 4, grau_raiz + 2, grau_raiz + 4]
                graus = [ciclo[k % 4] for k in range(n)]
            elif padrao_nome == "arpejo":
                ciclo = [grau_raiz, grau_raiz + 2, grau_raiz + 4, grau_raiz + 7]
                graus = [ciclo[k % 4] for k in range(n)]
            else:  # caminhante (livre)
                graus = [grau_raiz, grau_raiz + 4, grau_raiz + 2, grau_raiz + 4,
                         grau_raiz, grau_raiz + 4][:n]
                if len(graus) < n:
                    graus += [grau_raiz] * (n - len(graus))
            padrao = list(zip(graus, ritmo))

        for g, dur_q in padrao:
            if dur_q not in DUR_TABELA:
                dur_q = min(DUR_TABELA, key=lambda d: abs(d - dur_q))
            m = int(np.clip(tonal.grau_para_midi(g, oitava_base),
                            lim_min, lim_max))
            s = tonal.soletrar(m)
            eventos.append({
                "is_rest": False, "midi": m, "nome": midi_para_nome(m),
                "vexkey": s["vexkey"], "vex_acc": s["vex_acc"],
                "step": s["letra"], "alter": s["alteracao"], "octave": s["oitava"],
                "vex_dur": dur_para_vex(dur_q, False), "dur_q": dur_q,
                "staccato": False,
                "velocity": round(float(np.clip(p["vel_base"] * 0.6, 0.3, 0.65)), 2),
                "inicio_seg": round(tempo_q * seg_por_q, 4),
                "duracao_seg": round(dur_q * (0.96 if p["legato"] > 0.6 else 0.9) * seg_por_q, 4),
            })
            tempo_q += dur_q
        compassos.append(eventos)
    return compassos


# ============================================================
# VOZES À ARVO PÄRT (tintinnabuli)
#   voz-M = a melodia (lenta, por graus conjuntos, consonante)
#   voz-T = para cada nota da melodia, a nota da TRÍADE DA TÓNICA mais
#           próxima, transposta para o registo da parte (espelha o ritmo).
#   bordão = tónica sustentada por baixo (Für Alina).
# ============================================================
def _tonic_triade_pcs(tonal):
    terca = 4 if tonal.modo == "maior" else 3
    return {tonal.tonic_pc % 12, (tonal.tonic_pc + terca) % 12, (tonal.tonic_pc + 7) % 12}


def _evento_de(ev_modelo, midi, tonal, vel):
    """Constrói um evento com o ritmo de outro, mas outra altura."""
    s = tonal.soletrar(midi)
    return {
        "is_rest": False, "midi": midi, "nome": midi_para_nome(midi),
        "vexkey": s["vexkey"], "vex_acc": s["vex_acc"],
        "step": s["letra"], "alter": s["alteracao"], "octave": s["oitava"],
        "vex_dur": ev_modelo["vex_dur"], "dur_q": ev_modelo["dur_q"],
        "staccato": False, "velocity": round(vel, 2),
        "inicio_seg": ev_modelo["inicio_seg"], "duracao_seg": ev_modelo["duracao_seg"],
    }


def gerar_voz_tintinnabuli(melodia_comps, tonal, p, oitava_base, lim_min, lim_max):
    triade = _tonic_triade_pcs(tonal)
    centro = int(np.clip(tonal.tonic_pc + 12 * (oitava_base + 1) + 7, lim_min + 5, lim_max - 5))
    compassos = []
    for comp in melodia_comps:
        eventos = []
        for ev in comp:
            if ev["is_rest"]:
                eventos.append(dict(ev))
                continue
            m = ev["midi"]
            # nota da tríade mais próxima da melodia
            melhor, melhor_d = m, 99
            for d in range(-6, 7):
                if (m + d) % 12 in triade and abs(d) < melhor_d:
                    melhor, melhor_d = m + d, abs(d)
            # transporta por oitavas para o registo desta parte
            while melhor - centro > 6:
                melhor -= 12
            while centro - melhor > 6:
                melhor += 12
            melhor = int(np.clip(melhor, lim_min, lim_max))
            eventos.append(_evento_de(ev, melhor, tonal,
                                      float(np.clip(p["vel_base"] * 0.6, 0.3, 0.7))))
        compassos.append(eventos)
    return compassos


def gerar_pedal_tonica(tonal, p, metro, n_compassos, oitava_base, lim_min, lim_max):
    seg_por_q = 60.0 / p["bpm"]
    beats = META_BATIDAS[metro]
    centro = int(np.clip(tonal.tonic_pc + 12 * (oitava_base + 1), lim_min + 2, lim_max - 2))
    # tónica mais grave dentro do âmbito (bordão)
    while centro - 12 >= lim_min:
        centro -= 12
    compassos = []
    tempo_q = 0.0
    for c in range(n_compassos):
        eventos = []
        for dur_q in _blocos_sustentado(beats):
            s = tonal.soletrar(centro)
            eventos.append({
                "is_rest": False, "midi": centro, "nome": midi_para_nome(centro),
                "vexkey": s["vexkey"], "vex_acc": s["vex_acc"],
                "step": s["letra"], "alter": s["alteracao"], "octave": s["oitava"],
                "vex_dur": dur_para_vex(dur_q, False), "dur_q": dur_q, "staccato": False,
                "velocity": round(float(np.clip(p["vel_base"] * 0.5, 0.28, 0.6)), 2),
                "inicio_seg": round(tempo_q * seg_por_q, 4),
                "duracao_seg": round(dur_q * 0.98 * seg_por_q, 4),
            })
            tempo_q += dur_q
        compassos.append(eventos)
    return compassos


# ============================================================
# OITAVA BASE por papel + âmbito do instrumento
# ============================================================
def registo_para_papel(papel, instr_id, transpor, tonic_pc):
    info = INSTRUMENTOS.get(instr_id, INSTRUMENTOS["piano"])
    lim_min, lim_max = info["min"], info["max"]
    centro = {"melodia": 67, "harmonia": 55, "baixo": 43}.get(papel, 60)
    centro = int(np.clip(centro + transpor, lim_min + 7, lim_max - 7))
    # oitava_base tal que a tónica caia perto do centro do âmbito
    oitava_base = int(round((centro - tonic_pc) / 12)) - 1
    return oitava_base, lim_min, lim_max


# ============================================================
# EXPORTAÇÃO — MusicXML (partwise)
# ============================================================
MXML_TYPE = {0.25: "16th", 0.5: "eighth", 1.0: "quarter", 2.0: "half", 4.0: "whole"}
DIVISIONS = 4   # semínima = 4 divisões


def _mxml_pitch(step, alter, octave, indent="        "):
    s = [f"{indent}<pitch>", f"{indent}  <step>{step}</step>"]
    if alter:
        s.append(f"{indent}  <alter>{alter}</alter>")
    s.append(f"{indent}  <octave>{octave}</octave>")
    s.append(f"{indent}</pitch>")
    return "\n".join(s)


def _mxml_nota(ev, voz, staff=None):
    """Constrói um <note> com a ORDEM de elementos válida em MusicXML:
    [chord], pitch|rest, duration, voice, type, [staff], [notations].
    Em partes de pauta única, `staff` é None e o elemento é omitido."""
    dur = int(round(ev["dur_q"] * DIVISIONS))
    tipo = MXML_TYPE.get(round(ev["dur_q"], 2), "quarter")
    out = ["      <note>"]
    if ev["is_rest"]:
        out.append("        <rest/>")
        out.append(f"        <duration>{dur}</duration>")
        out.append(f"        <voice>{voz}</voice>")
        out.append(f"        <type>{tipo}</type>")
        if staff is not None:
            out.append(f"        <staff>{staff}</staff>")
        out.append("      </note>")
        return "\n".join(out)
    # nota principal
    out.append(_mxml_pitch(ev["step"], ev["alter"], ev["octave"]))
    out.append(f"        <duration>{dur}</duration>")
    out.append(f"        <voice>{voz}</voice>")
    out.append(f"        <type>{tipo}</type>")
    if staff is not None:
        out.append(f"        <staff>{staff}</staff>")
    if ev.get("staccato"):
        out.append("        <notations><articulations><staccato/>"
                   "</articulations></notations>")
    out.append("      </note>")
    # notas do acorde (mesmo ataque)
    for n in ev.get("acorde", []):
        c = ["      <note>", "        <chord/>"]
        c.append(_mxml_pitch(n["step"], n["alter"], n["octave"]))
        c.append(f"        <duration>{dur}</duration>")
        c.append(f"        <voice>{voz}</voice>")
        c.append(f"        <type>{tipo}</type>")
        if staff is not None:
            c.append(f"        <staff>{staff}</staff>")
        c.append("      </note>")
        out.append("\n".join(c))
    return "\n".join(out)


def _agrupar_partes(partes):
    """Agrupa partes em 'score-parts'. Partes com o mesmo `grupo_grand`
    formam um grand staff (lista ordenada por staff_no); as restantes ficam
    sozinhas. Devolve lista de listas de partes."""
    grupos = []
    indice = {}
    for parte in partes:
        g = parte.get("grupo_grand")
        if g is None:
            grupos.append([parte])
        elif g in indice:
            grupos[indice[g]].append(parte)
        else:
            indice[g] = len(grupos)
            grupos.append([parte])
    for grupo in grupos:
        grupo.sort(key=lambda pt: pt.get("staff_no") or 1)
    return grupos


def construir_musicxml(partes, tonal, metro, titulo="CuriouSoil"):
    num, den = META_VEX[metro]
    beats_div = int(round(META_BATIDAS[metro] * DIVISIONS))   # duração do compasso
    fifths = tonal.fifths
    modo = "major" if tonal.modo in ("maior", "major") else "minor"
    clef_map = {"treble": ("G", 2), "bass": ("F", 4)}
    grupos = _agrupar_partes(partes)

    L = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
         '"http://www.musicxml.org/dtds/partwise.dtd">',
         '<score-partwise version="3.1">',
         '  <work><work-title>' + xml_escape(titulo) + '</work-title></work>',
         '  <identification><encoding><software>CuriouSoil</software>'
         '</encoding></identification>',
         '  <part-list>']
    for idx, grupo in enumerate(grupos):
        pid = f"P{idx+1}"
        base = grupo[0]
        nome = base.get("instrumento_nome") or base["papel"]
        if len(grupo) == 1:
            etiqueta = f'{nome} ({base["papel"]})'
        else:
            etiqueta = nome
        gm = base.get("gm", 0)
        L.append(f'    <score-part id="{pid}">')
        L.append(f'      <part-name>{xml_escape(etiqueta)}</part-name>')
        L.append(f'      <score-instrument id="{pid}-I"><instrument-name>'
                 f'{xml_escape(nome)}</instrument-name></score-instrument>')
        L.append(f'      <midi-instrument id="{pid}-I"><midi-program>{gm+1}'
                 f'</midi-program></midi-instrument>')
        L.append('    </score-part>')
    L.append('  </part-list>')

    for idx, grupo in enumerate(grupos):
        pid = f"P{idx+1}"
        grand = len(grupo) > 1
        n_compassos = len(grupo[0]["compassos"])
        L.append(f'  <part id="{pid}">')
        for m_idx in range(n_compassos):
            L.append(f'    <measure number="{m_idx+1}">')
            if m_idx == 0:
                L.append('      <attributes>')
                L.append(f'        <divisions>{DIVISIONS}</divisions>')
                L.append(f'        <key><fifths>{fifths}</fifths>'
                         f'<mode>{modo}</mode></key>')
                L.append(f'        <time><beats>{num}</beats>'
                         f'<beat-type>{den}</beat-type></time>')
                if grand:
                    L.append('        <staves>2</staves>')
                    for st_i, pt in enumerate(grupo, start=1):
                        sign, line = clef_map[pt["clef"]]
                        L.append(f'        <clef number="{st_i}"><sign>{sign}</sign>'
                                 f'<line>{line}</line></clef>')
                else:
                    sign, line = clef_map[grupo[0]["clef"]]
                    L.append(f'        <clef><sign>{sign}</sign><line>{line}</line></clef>')
                L.append('      </attributes>')
            if grand:
                for st_i, pt in enumerate(grupo, start=1):
                    if st_i > 1:
                        L.append(f'      <backup><duration>{beats_div}</duration></backup>')
                    for ev in pt["compassos"][m_idx]:
                        L.append(_mxml_nota(ev, voz=st_i, staff=st_i))
            else:
                for ev in grupo[0]["compassos"][m_idx]:
                    L.append(_mxml_nota(ev, voz=1, staff=None))
            L.append('    </measure>')
        L.append('  </part>')
    L.append('</score-partwise>')
    return "\n".join(L)


# ============================================================
# EXPORTAÇÃO — MIDI (SMF tipo 1, escrito à mão, sem dependências)
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


def _meta_texto(tipo, texto):
    dados = texto.encode("latin-1", "replace")[:127]
    return _vlq(0) + bytes([0xFF, tipo, len(dados)]) + dados


def construir_midi(partes, bpm, tonal=None, metro=None):
    # ---- Faixa 0: andamento + compasso + tonalidade (metadados para o DAW) ----
    micros = int(60_000_000 / bpm)
    meta0 = [_meta_texto(0x03, "CuriouSoil"),
             _vlq(0) + bytes([0xFF, 0x51, 0x03]) + micros.to_bytes(3, "big")]
    if metro is not None and metro in META_VEX:
        num, den = META_VEX[metro]
        potencia = {2: 1, 4: 2, 8: 3, 16: 4}.get(den, 2)
        meta0.append(_vlq(0) + bytes([0xFF, 0x58, 0x04, num & 0x7F, potencia, 24, 8]))
    if tonal is not None:
        sf = int(np.clip(tonal.fifths, -7, 7)) & 0xFF      # byte com sinal
        mi = 0 if tonal.modo in ("maior", "major") else 1
        meta0.append(_vlq(0) + bytes([0xFF, 0x59, 0x02, sf, mi]))
    faixa0 = _track_chunk(meta0)

    faixas = [faixa0]
    canais = [ch for ch in range(16) if ch != 9]   # 9 = percussão (evitar)
    for i, parte in enumerate(partes):
        gm = INSTRUMENTOS.get(parte["instrumento"], {}).get("gm", 0)
        canal = canais[i % len(canais)]
        nome_faixa = f'{parte.get("instrumento_nome", parte["instrumento"])} — {parte["papel"]}'
        abs_events = []
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
        eventos_faixa = [_meta_texto(0x03, nome_faixa),
                         _vlq(0) + bytes([0xC0 | canal, gm & 0x7F])]
        prev = 0
        for tick, _tipo, data in abs_events:
            delta = max(0, tick - prev)
            eventos_faixa.append(_vlq(delta) + data)
            prev = tick
        faixas.append(_track_chunk(eventos_faixa))

    header = b"MThd" + struct.pack(">IHHH", 6, 1, len(faixas), TPQ)
    return header + b"".join(faixas)


# ============================================================
# ORQUESTRADOR — junta tudo
# ============================================================
def montar_peca(valores, agua, diag, osm, opcoes):
    dif = opcoes["dificuldade"]
    estilo = opcoes.get("estilo", "livre")
    duracao_alvo = opcoes["duracao_seg"]
    instrumentos = opcoes["instrumentos"]

    tonal = escolher_tonalidade(valores["pH"], diag["saude_score"],
                                diag["contaminacao_score"])
    p = parametros_musicais(valores, diag, agua, osm, dif, estilo)
    p["_dif"] = dif

    metro = escolher_metro(diag["textura"], dif, diag["saude_score"],
                           diag["pressao_humana_score"])
    beats = META_BATIDAS[metro]
    seg_por_q = 60.0 / p["bpm"]
    seg_por_compasso = beats * seg_por_q
    n_compassos = max(p["frase_compassos"],
                      int(round(duracao_alvo / seg_por_compasso)))
    n_compassos = int(np.ceil(n_compassos / p["frase_compassos"]) * p["frase_compassos"])
    n_compassos = int(np.clip(n_compassos, p["frase_compassos"], 400))

    prog = progressao(tonal, n_compassos, p["prog_voc"], p["frase_compassos"])
    papeis = atribuir_papeis(instrumentos, estilo)

    # 1ª passagem: melodia (voz-M) primeiro, para o tintinnabuli a poder espelhar
    melodia_comps = None
    for entry in papeis:
        if entry["papel"] == "melodia":
            ob, lmin, lmax = registo_para_papel("melodia", entry["instrumento"],
                                                p["transpor"], tonal.tonic_pc)
            melodia_comps = gerar_melodia(tonal, p, metro, n_compassos, ob, lmin, lmax, prog)
            entry["_comps"] = melodia_comps

    # 2ª passagem: restantes vozes
    partes = []
    for entry in papeis:
        papel = entry["papel"]
        instr_id = entry["instrumento"]
        ob, lmin, lmax = registo_para_papel(papel, instr_id, p["transpor"], tonal.tonic_pc)
        if papel == "melodia":
            comps = entry["_comps"]
        elif p["tintinnabuli"]:
            if papel == "baixo":
                comps = gerar_pedal_tonica(tonal, p, metro, n_compassos, ob, lmin, lmax)
            else:  # harmonia (ou 2ª voz) -> tintinnabuli a espelhar a melodia
                comps = gerar_voz_tintinnabuli(melodia_comps or [], tonal, p, ob, lmin, lmax)
        elif papel == "harmonia":
            comps = gerar_harmonia(tonal, p, metro, n_compassos, ob, lmin, lmax, prog)
        else:  # baixo
            comps = gerar_baixo(tonal, p, metro, n_compassos, ob, lmin, lmax, prog)

        partes.append({
            "papel": papel,
            "instrumento": instr_id,
            "instrumento_nome": INSTRUMENTOS[instr_id]["nome"],
            "gm": INSTRUMENTOS[instr_id]["gm"],
            "clef": entry["clef"],
            "grupo_grand": entry.get("grupo"),
            "staff_no": entry.get("staff"),
            "compassos": comps,
        })

    duracao_real = round(n_compassos * seg_por_compasso, 2)
    return tonal, p, metro, n_compassos, partes, duracao_real


# ============================================================
# CACHE — SoilGrids é estático e o Overpass é lento; guardamos por
# coordenada arredondada para responder depressa e aguentar uma turma.
# ============================================================
_CACHE_SOLO = {}
_CACHE_OSM = {}
_TTL_SOLO = 24 * 3600        # solo praticamente não muda
_TTL_OSM = 6 * 3600


def _chave_coord(lat, lon):
    return (round(lat, 3), round(lon, 3))   # ~100 m de resolução


def solo_em_cache(lat, lon):
    k = _chave_coord(lat, lon)
    agora = time.time()
    e = _CACHE_SOLO.get(k)
    if e and agora - e[0] < _TTL_SOLO:
        return e[1]
    resultado = buscar_solo(lat, lon)        # pode levantar RequestException
    _CACHE_SOLO[k] = (agora, resultado)
    return resultado


def osm_em_cache(lat, lon, raio_m=10000):
    k = _chave_coord(lat, lon)
    agora = time.time()
    e = _CACHE_OSM.get(k)
    if e and agora - e[0] < _TTL_OSM:
        return e[1]
    resultado = buscar_overpass(lat, lon, raio_m)
    if resultado.get("disponivel"):          # só guardamos sucessos
        _CACHE_OSM[k] = (agora, resultado)
    return resultado


# ============================================================
# EXPLICAÇÃO PEDAGÓGICA — porque é que ESTE solo soa assim
# Devolve cartões {tipo, titulo, texto} com as relações dominantes em vigor.
# ============================================================
def explicar(v, diag, agua, osm, tonal, metro, p):
    cartoes = []

    # --- Estrutura (sempre): tonalidade, carácter, compasso ---
    pH = v["pH"]
    if pH is not None:
        if pH < 6.2:
            lado = "para o lado grave e escuro (solo ácido)"
        elif pH > 7.3:
            lado = "para o lado brilhante (solo alcalino)"
        else:
            lado = "para uma zona central (perto do neutro)"
        cartoes.append({"tipo": "estrutura", "titulo": "Tonalidade",
            "texto": f"O pH {pH:.1f} escolhe {tonal.etiqueta}: o pH inclina a música {lado}."})
    else:
        cartoes.append({"tipo": "estrutura", "titulo": "Tonalidade",
            "texto": f"Sem pH fiável aqui; usámos {tonal.etiqueta} por defeito."})

    if tonal.modo == "maior":
        nota = "Um solo íntegro soa consonante, em modo maior."
    else:
        nota = "Um solo frágil ou degradado soa mais sombrio, em modo menor."
    cartoes.append({"tipo": "estrutura", "titulo": "Carácter",
        "texto": f"Saúde do solo {diag['saude_score']}/10 → modo {tonal.modo}. {nota}"})

    extra = " O terreno é irregular, daí o compasso assimétrico." if metro in ("5/4", "7/8") else ""
    cartoes.append({"tipo": "estrutura", "titulo": "Compasso",
        "texto": f"A textura {diag['textura']} dá o compasso {metro}.{extra}"})

    # --- Solo (escolhe as relações mais salientes) ---
    areia, argila = v["areia"], v["argila"]
    if areia is not None and argila is not None:
        if areia - argila > 15:
            cartoes.append({"tipo": "solo", "titulo": "Areia → saltos",
                "texto": f"{areia:.0f}% de areia: melodia mais aos saltos e com pausas — um som solto e arejado."})
        elif argila - areia > 15:
            cartoes.append({"tipo": "solo", "titulo": "Argila → graus",
                "texto": f"{argila:.0f}% de argila: melodia por notas vizinhas e ligada — um som coeso."})

    c_org = v["c_org"]
    if c_org is not None:
        if p["vivacidade"] > 0.5:
            cartoes.append({"tipo": "solo", "titulo": "Carbono → vivacidade",
                "texto": f"{c_org:.0f} g/kg de carbono orgânico: ritmo vivo, mais notas e staccato — solo cheio de vida."})
        elif c_org < 8:
            cartoes.append({"tipo": "solo", "titulo": "Pouco carbono → calma",
                "texto": f"Apenas {c_org:.0f} g/kg de carbono: ritmo mais parado e notas longas — solo pobre em vida."})

    if agua:
        if agua["awc_pct"] >= 16:
            cartoes.append({"tipo": "solo", "titulo": "Água → frases longas",
                "texto": f"{agua['awc_pct']:.0f}% de água disponível: frases compridas e bastante reverberação."})
        elif agua["awc_pct"] < 10:
            cartoes.append({"tipo": "solo", "titulo": "Pouca água → frases curtas",
                "texto": f"Só {agua['awc_pct']:.0f}% de água: frases curtas e um som mais seco."})

    dens = v["densidade"]
    if dens is not None and dens > 1.5:
        cartoes.append({"tipo": "solo", "titulo": "Compactação → grave",
            "texto": f"Densidade {dens:.2f} g/cm³: solo compactado, com o registo empurrado para o grave."})

    # --- Pressão humana (distorções) ---
    cont = osm.get("contagens", {}) if osm else {}
    if cont.get("mina", 0) > 0:
        cartoes.append({"tipo": "pressao", "titulo": "Pedreiras → fraturas",
            "texto": f"{cont['mina']} mina(s)/pedreira(s) por perto: mais saltos bruscos e silêncios — uma paisagem fraturada."})
    if cont.get("industria_pesada", 0) > 0:
        cartoes.append({"tipo": "pressao", "titulo": "Indústria → cromatismo",
            "texto": f"{cont['industria_pesada']} instalação(ões) pesada(s): notas fora da tonalidade e dissonância — tensão química."})
    if diag["contaminacao_score"] >= 4 and not cont.get("industria_pesada"):
        cartoes.append({"tipo": "pressao", "titulo": "Degradação → tensão",
            "texto": "Sinais de degradação acumulados: a harmonia fica mais tensa e dissonante."})

    return cartoes[:8]


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
        # lista simples (compatibilidade com a versão anterior do frontend)
        "instrumentos": [{"id": k, "nome": v["nome"], "familia": v["familia"]}
                         for k, v in INSTRUMENTOS.items()],
        # grupos para revelação progressiva
        "familias": [
            {"id": f["id"], "nome": f["nome"],
             "instrumentos": [{"id": i, "nome": INSTRUMENTOS[i]["nome"]}
                              for i in f["instrumentos"] if i in INSTRUMENTOS]}
            for f in FAMILIAS
        ],
        "estilos": [{"id": k, "nome": v["nome"]} for k, v in ESTILOS.items()],
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
    estilo = (request.args.get("estilo") or "livre").lower()
    if estilo not in ESTILOS:
        estilo = "livre"
    instr = (request.args.get("instrumentos") or "piano").split(",")
    instr = [i.strip() for i in instr if i.strip()]
    seed = request.args.get("seed")
    return {"dificuldade": dif, "duracao_seg": dur, "modo": modo, "estilo": estilo,
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
            opcoes["modo"], opcoes["estilo"], ",".join(opcoes["instrumentos"]),
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
    midi_bytes = construir_midi(partes, p["bpm"], tonal, metro)
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
            "estilo": opcoes["estilo"],
            "estilo_nome": ESTILOS.get(opcoes["estilo"], ESTILOS["livre"])["nome"],
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
             "clef": pt["clef"], "grupo_grand": pt.get("grupo_grand"),
             "staff_no": pt.get("staff_no"), "compassos": pt["compassos"]}
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


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=porta, debug=False)
