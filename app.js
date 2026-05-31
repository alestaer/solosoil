// =====================================================================
//  CuriouSoil — frontend
//  Stack: Leaflet · Tone.js · VexFlow · vanilla JS
//  Features: comparação A/B + pesquisa de localização + pares curados
// =====================================================================

// Em testes locais (localhost) fala com o backend local (python servidor.py -> :5000).
// Em produção continua a apontar para o backend no Render.
const API_URL = ['localhost', '127.0.0.1', '0.0.0.0'].includes(location.hostname)
  ? `${location.protocol}//${location.hostname}:5000`
  : 'https://solosoil.onrender.com';

// O backend no Render (plano gratuito) adormece; a 1ª chamada pode dar 502.
// Esta função repete algumas vezes, avisando que está "a acordar o servidor".
async function gerarFetch(url, slot, tentativas = 4) {
  for (let i = 0; i < tentativas; i++) {
    try {
      const r = await fetch(url);
      if ([502, 503, 504].includes(r.status)) throw new Error('servidor a acordar (' + r.status + ')');
      return await r.json();
    } catch (e) {
      if (i >= tentativas - 1) throw e;
      if (slot) setEstadoMsg(slot, 'A acordar o servidor… (até ~1 min na 1ª vez)', true);
      await new Promise((res) => setTimeout(res, 2500 * (i + 1)));
    }
  }
  throw new Error('sem resposta do servidor');
}

// ---------------------------------------------------------------------
//  DEFINIÇÕES GLOBAIS — instrumentos, dificuldade, duração, modo
//  (afectam a partitura e as exportações; a pré-escuta A/B é a piano)
// ---------------------------------------------------------------------
const INSTRUMENTOS_FALLBACK = [
  { id: "piano", nome: "Piano" }, { id: "violino", nome: "Violino" },
  { id: "violoncelo", nome: "Violoncelo" }, { id: "flauta", nome: "Flauta" },
  { id: "clarinete", nome: "Clarinete" }, { id: "oboe", nome: "Oboé" },
  { id: "trompete", nome: "Trompete" }, { id: "marimba", nome: "Marimba" },
  { id: "guitarra", nome: "Guitarra" }, { id: "contrabaixo", nome: "Contrabaixo" },
];
let NOMES_INSTR = Object.fromEntries(INSTRUMENTOS_FALLBACK.map((i) => [i.id, i.nome]));

// Família de cada instrumento — usada para escolher o TIMBRE na pré-escuta.
// Atualizada a partir de /instrumentos; este mapa é só o recuo (offline).
let FAMILIA_DE = {
  piano: 'teclas', celesta: 'teclas',
  violino: 'cordas', viola: 'cordas', violoncelo: 'cordas',
  contrabaixo: 'cordas', harpa: 'cordas', guitarra: 'cordas',
  flauta: 'madeiras', flautim: 'madeiras', oboe: 'madeiras',
  clarinete: 'madeiras', fagote: 'madeiras',
  trompete: 'metais', trompa: 'metais', trombone: 'metais', tuba: 'metais',
  marimba: 'percussao', vibrafone: 'percussao', xilofone: 'percussao', glockenspiel: 'percussao',
};
const familiaDeInstrumento = (id) => FAMILIA_DE[id] || 'teclas';

const fmtTempo = (s) => { s = Math.round(+s); return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`; };

let avisoCfgTimer = null;
function flashAvisoCfg(txt) {
  const el = document.getElementById("aviso-config");
  if (!el) return;
  el.textContent = txt; el.classList.add("visivel");
  clearTimeout(avisoCfgTimer);
  avisoCfgTimer = setTimeout(() => el.classList.remove("visivel"), 2600);
}

function toggleChipInstrumento(b) {
  if (document.getElementById("modo-eletronico")?.checked) return;
  const ativos = [...document.querySelectorAll("#lista-instrumentos .chip.ativo")];
  if (!b.classList.contains("ativo") && ativos.length >= 3) {
    flashAvisoCfg("Máximo de 3 instrumentos em simultâneo."); return;
  }
  b.classList.toggle("ativo");
  if (!document.querySelector("#lista-instrumentos .chip.ativo")) b.classList.add("ativo");
  document.querySelectorAll("#lista-instrumentos .chip").forEach((c) =>
    c.setAttribute("aria-pressed", c.classList.contains("ativo") ? "true" : "false"));
}

async function montarControlos() {
  const cont = document.getElementById("lista-instrumentos");
  let familias = null, estilos = null, listaFlat = INSTRUMENTOS_FALLBACK;
  try {
    const r = await fetch(`${API_URL}/instrumentos`);
    const d = await r.json();
    if (d && Array.isArray(d.instrumentos) && d.instrumentos.length) listaFlat = d.instrumentos;
    if (d && Array.isArray(d.familias) && d.familias.length) familias = d.familias;
    if (d && Array.isArray(d.estilos) && d.estilos.length) estilos = d.estilos;
  } catch (_) { /* usa fallback */ }
  NOMES_INSTR = Object.fromEntries(listaFlat.map((i) => [i.id, i.nome]));
  listaFlat.forEach((i) => { if (i.familia) FAMILIA_DE[i.id] = i.familia; });

  // ---- instrumentos por família (revelação progressiva) ----
  if (cont) {
    if (!familias) {
      familias = [{ id: "todos", nome: "Instrumentos",
                    instrumentos: listaFlat.map((i) => ({ id: i.id, nome: i.nome })) }];
    }
    montarInstrumentos(cont, familias);
  }

  // ---- estilos ----
  montarEstilos(estilos || [{ id: "livre", nome: "Livre" }]);

  // ---- modo eletrónico ----
  const tog = document.getElementById("modo-eletronico");
  if (tog && cont) tog.addEventListener("change", (e) => {
    cont.classList.toggle("desativado", e.target.checked);
    agendarRegeneracao();
  });

  // ---- dificuldade -> atualização automática ----
  document.querySelectorAll('input[name="dificuldade"]').forEach((r) =>
    r.addEventListener("change", agendarRegeneracao));

  // ---- duração ----
  const dur = document.getElementById("duracao");
  const lbl = document.getElementById("duracao-label");
  if (dur && lbl) {
    const ref = () => lbl.textContent = fmtTempo(dur.value) + (dur.value >= 273 ? "  (4′33″)" : "");
    dur.addEventListener("input", () => { ref(); agendarRegeneracao(); });
    ref();
  }
}

function montarInstrumentos(cont, familias) {
  cont.innerHTML = "";
  const barra = document.createElement("div"); barra.className = "familias-barra";
  const grupos = document.createElement("div"); grupos.className = "familia-grupos";
  const resumo = document.createElement("div"); resumo.className = "instr-selecionados"; resumo.id = "instr-selecionados";

  familias.forEach((fam) => {
    const fb = document.createElement("button");
    fb.type = "button"; fb.className = "familia-chip"; fb.dataset.fam = fam.id;
    fb.textContent = fam.nome;
    fb.setAttribute("aria-label", "Família: " + fam.nome);
    fb.addEventListener("click", () => abrirFamilia(fam.id));
    barra.appendChild(fb);

    const g = document.createElement("div");
    g.className = "familia-grupo chips"; g.dataset.fam = fam.id; g.hidden = true;
    (fam.instrumentos || []).forEach(({ id, nome }) => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "chip" + (id === "piano" ? " ativo" : "");
      b.dataset.id = id; b.textContent = nome;
      b.setAttribute("aria-pressed", id === "piano" ? "true" : "false");
      b.setAttribute("aria-label", "Instrumento: " + nome);
      b.addEventListener("click", () => {
        toggleChipInstrumento(b); atualizarResumoInstrumentos(); agendarRegeneracao();
      });
      g.appendChild(b);
    });
    grupos.appendChild(g);
  });

  cont.appendChild(barra); cont.appendChild(grupos); cont.appendChild(resumo);
  const famPiano = familias.find((f) => (f.instrumentos || []).some((i) => i.id === "piano"));
  abrirFamilia(famPiano ? famPiano.id : familias[0].id);
  atualizarResumoInstrumentos();
}

function abrirFamilia(famId) {
  document.querySelectorAll("#lista-instrumentos .familia-chip").forEach((c) =>
    c.classList.toggle("ativa", c.dataset.fam === famId));
  document.querySelectorAll("#lista-instrumentos .familia-grupo").forEach((g) =>
    g.hidden = (g.dataset.fam !== famId));
}

function atualizarResumoInstrumentos() {
  const resumo = document.getElementById("instr-selecionados");
  const ativos = [...document.querySelectorAll("#lista-instrumentos .chip.ativo")].map((c) => c.dataset.id);
  if (resumo) {
    resumo.textContent = ativos.length
      ? "Escolhidos: " + ativos.map((id) => NOMES_INSTR[id] || id).join(", ")
      : "";
  }
  document.querySelectorAll("#lista-instrumentos .familia-chip").forEach((fc) => {
    const temSel = document.querySelector(
      `#lista-instrumentos .familia-grupo[data-fam="${fc.dataset.fam}"] .chip.ativo`) != null;
    fc.classList.toggle("com-selecao", temSel);
  });
}

function montarEstilos(estilos) {
  const cont = document.getElementById("lista-estilos");
  if (!cont) return;
  cont.innerHTML = "";
  estilos.forEach(({ id, nome }) => {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "chip chip-estilo" + (id === "livre" ? " ativo" : "");
    b.dataset.estilo = id; b.textContent = nome;
    b.setAttribute("aria-pressed", id === "livre" ? "true" : "false");
    b.setAttribute("aria-label", "Estilo: " + nome);
    b.addEventListener("click", () => {
      cont.querySelectorAll(".chip-estilo").forEach((c) => {
        c.classList.remove("ativo"); c.setAttribute("aria-pressed", "false");
      });
      b.classList.add("ativo"); b.setAttribute("aria-pressed", "true");
      agendarRegeneracao();
    });
    cont.appendChild(b);
  });
}

function lerConfig() {
  const electronico = !!document.getElementById("modo-eletronico")?.checked;
  const dificuldade = (document.querySelector('input[name="dificuldade"]:checked') || {}).value || "intermedio";
  const duracao = parseInt(document.getElementById("duracao")?.value, 10) || 75;
  const estiloEl = document.querySelector("#lista-estilos .chip-estilo.ativo");
  const estilo = estiloEl ? estiloEl.dataset.estilo : "livre";
  const ativos = [...document.querySelectorAll("#lista-instrumentos .chip.ativo")].map((c) => c.dataset.id);
  const instrumentos = electronico ? ["piano", "violoncelo", "contrabaixo"]
                                    : (ativos.length ? ativos : ["piano"]);
  return { electronico, dificuldade, duracao, estilo, instrumentos };
}

// Atualização automática: ao mudar qualquer definição, regenera as amostras
// já colocadas no mapa (usa as coordenadas guardadas em estado.a/estado.b).
// Debounce para não disparar a cada arrasto do cursor de duração.
let regenTimer = null;
function agendarRegeneracao() {
  clearTimeout(regenTimer);
  regenTimer = setTimeout(() => {
    ['A', 'B'].forEach((slot) => {
      const s = estado[slot.toLowerCase()];
      if (s && s.lat != null && s.lon != null) regenerarSlot(slot);
    });
  }, 450);
}

async function regenerarSlot(slot) {
  const s = estado[slot.toLowerCase()];
  if (!s || s.lat == null) return;
  // o áudio que estava a tocar ficou desatualizado: para-o (sem auto-tocar o novo)
  destruirSessao(slot);
  if (slotAtivo === slot) slotAtivo = null;
  limparHighlights(slot);
  resetSlotState(slot);
  s.carregando = true; s.estadoMsg = 'A actualizar a peça...';
  renderEstado();
  try {
    const cfg = lerConfig();
    const q = new URLSearchParams({
      lat: s.lat, lon: s.lon,
      dificuldade: cfg.dificuldade, duracao: cfg.duracao,
      modo: cfg.electronico ? "electronico" : "acustico",
      estilo: cfg.estilo,
      instrumentos: cfg.instrumentos.join(","),
    });
    const dados = await gerarFetch(`${API_URL}/gerar?${q.toString()}`, slot);
    if (dados.erro) { s.carregando = false; setEstadoMsg(slot, dados.erro, false); return; }
    s.dados = dados; s.carregando = false;
    setEstadoMsg(slot, msgPronto(), false);
    renderEstado();
    actualizarBotoesPlay();
  } catch (err) {
    s.carregando = false;
    setEstadoMsg(slot, 'Erro ao actualizar: ' + err.message, false);
  }
}

// ---------------------------------------------------------------------
//  PARES CURADOS — cada par junta um ponto com forte intervenção humana
//  e outro de referência prístina dentro do mesmo bioma/clima.
// ---------------------------------------------------------------------
const PARES_SUGERIDOS = [
  {
    titulo: "Mineração polimetálica vs reserva alentejana",
    intervencionado: {
      nome: "Aljustrel", lat: 37.876, lon: -8.165,
      porque: "Minas activas de cobre, zinco e chumbo na Faixa Piritosa"
    },
    pristino: {
      nome: "Serra de São Mamede", lat: 39.310, lon: -7.380,
      porque: "Parque natural alentejano em xistos não explorados"
    }
  },
  {
    titulo: "Petroquímica costeira vs litoral protegido",
    intervencionado: {
      nome: "Complexo de Sines", lat: 37.954, lon: -8.812,
      porque: "Refinaria e zona industrial portuária com emissões fósseis"
    },
    pristino: {
      nome: "Serra da Arrábida", lat: 38.490, lon: -8.985,
      porque: "Parque natural costeiro em calcário, mesmo litoral atlântico"
    }
  },
  {
    titulo: "Acidente nuclear vs floresta primária temperada",
    intervencionado: {
      nome: "Chernobyl (zona de exclusão)", lat: 51.415, lon: 30.220,
      porque: "Solo contaminado por radionuclídeos desde 1986"
    },
    pristino: {
      nome: "Floresta de Białowieża", lat: 52.700, lon: 23.860,
      porque: "Última floresta primária da Europa, mesma latitude"
    }
  },
  {
    titulo: "Agricultura intensiva sob plástico vs reserva semiárida",
    intervencionado: {
      nome: "Almería (mar de plásticos)", lat: 36.785, lon: -2.685,
      porque: "Estufas intensivas com fertilização química há décadas"
    },
    pristino: {
      nome: "Cabo de Gata", lat: 36.762, lon: -2.135,
      porque: "Reserva semiárida andaluza adjacente, sem irrigação"
    }
  },
  {
    titulo: "Fundição de níquel vs reserva ártica da UNESCO",
    intervencionado: {
      nome: "Norilsk", lat: 69.350, lon: 88.180,
      porque: "Maior emissor mundial de SO2, solo metalífero ácido"
    },
    pristino: {
      nome: "Planalto de Putorana", lat: 69.000, lon: 94.500,
      porque: "Reserva ártica intocada à mesma latitude siberiana"
    }
  }
];

// ---------------------------------------------------------------------
//  MAPA (igual ao anterior)
// ---------------------------------------------------------------------
const mapa = L.map('mapa').setView([40, 0], 2);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '© OpenStreetMap'
}).addTo(mapa);

const marcadores  = { A: null, B: null };
const camadasOSM  = { A: null, B: null };

function criarIconeAB(slot) {
  return L.divIcon({
    className: 'marcador-ab',
    html: `<div class="m-ab m-ab-${slot.toLowerCase()}">${slot}</div>`,
    iconSize: [28, 28],
    iconAnchor: [14, 14]
  });
}

// ---------------------------------------------------------------------
//  ESTADO CENTRAL
//  estado.a / estado.b = { lat, lon, dados, estadoMsg, carregando }
//  estado.modo = 'vazio' | 'single' | 'comparar'
// ---------------------------------------------------------------------
const estado = {
  a: null,
  b: null,
  modo: 'vazio',
};

function recalcularModo() {
  if (estado.a && estado.b) estado.modo = 'comparar';
  else if (estado.a || estado.b) estado.modo = 'single';
  else estado.modo = 'vazio';
}

// ---------------------------------------------------------------------
//  ÁUDIO — duas sessões independentes (A e B) + controlo de pausa
// ---------------------------------------------------------------------
const sessoes = { A: null, B: null };
let slotAtivo = null;              // 'A' | 'B' | null  — qual está actualmente a emitir som
let tokensTimer = { A: 0, B: 0 };  // anula timers obsoletos quando se re-toca
let toneIniciado = false;          // Tone.start() só pode ser chamado uma vez por gesto

// Estado de pausa independente por slot (não partilha o AudioContext global)
const slotState = {
  A: { pausado: false, notas: [], reverbMix: 0.2, electronico: false, tStart: 0, tPausa: 0, duracaoTotal: 0 },
  B: { pausado: false, notas: [], reverbMix: 0.2, electronico: false, tStart: 0, tPausa: 0, duracaoTotal: 0 },
};

function resetSlotState(slot) {
  slotState[slot] = { pausado: false, notas: [], reverbMix: 0.2, electronico: false, tStart: 0, tPausa: 0, duracaoTotal: 0 };
}

// Timing e elementos SVG das notas de cada slot (para highlight sincronizado)
const partituraInfo = {
  A: { els: [], timings: [] },
  B: { els: [], timings: [] },
};
const highlightTimers = { A: [], B: [] };

// Aplica / remove estilo de highlight directamente nos paths SVG de um grupo de nota
function setNotaHighlight(el, activa) {
  if (!el) return;
  el.querySelectorAll('path, rect').forEach(p => {
    p.style.fill   = activa ? 'var(--musgo)' : '';
    p.style.stroke = activa ? 'var(--musgo)' : '';
  });
}

// Cancela timers e remove highlight visual de todas as notas do slot
function limparHighlights(slot) {
  highlightTimers[slot].forEach(id => clearTimeout(id));
  highlightTimers[slot] = [];
  (partituraInfo[slot]?.els || []).forEach(el => setNotaHighlight(el, false));
}

// Agenda os timeouts que iluminam cada nota, com offset 'elapsed' (segundos)
function agendarHighlights(slot, elapsed = 0) {
  limparHighlights(slot);
  const info = partituraInfo[slot];
  if (!info?.els.length) return;

  info.timings.forEach((t, i) => {
    if (t.inicio < elapsed - 0.05) return;   // já passou
    const delay = Math.max(0, (t.inicio - elapsed + 0.15) * 1000);
    const id = setTimeout(() => {
      const cur = partituraInfo[slot];
      if (!cur) return;
      cur.els.forEach((el, j) => setNotaHighlight(el, j === i));
    }, delay);
    highlightTimers[slot].push(id);
  });

  // Apaga tudo quando a melodia termina
  const fimRestante = info.timings
    .filter(t => t.inicio >= elapsed - 0.05)
    .reduce((acc, t) => Math.max(acc, t.inicio - elapsed + t.duracao), 0);
  const endId = setTimeout(() => {
    (partituraInfo[slot]?.els || []).forEach(el => setNotaHighlight(el, false));
  }, (fimRestante + 0.15 + 0.4) * 1000);
  highlightTimers[slot].push(endId);
}

async function garantirToneIniciado() {
  if (!toneIniciado) {
    await Tone.start();
    toneIniciado = true;
  }
}

// Amostras de piano (Salamander) — timbre realista para a família "teclas".
const SALAMANDER = {
  urls: { A2: "A2.mp3", A3: "A3.mp3", A4: "A4.mp3", A5: "A5.mp3",
          C3: "C3.mp3", C4: "C4.mp3", C5: "C5.mp3" },
  release: 1.2, baseUrl: "https://tonejs.github.io/audio/salamander/",
};

// Cria uma voz Tone para uma família de instrumentos. Em modo eletrónico,
// usa timbres mais ricos. Robusto: se algo falhar, recua para um sintetizador
// simples (a reprodução nunca parte por causa de uma voz).
function criarVozFamilia(familia, electronico, destino) {
  let v;
  try {
    if (electronico) {
      if (familia === 'cordas' || familia === 'teclas') {
        v = new Tone.PolySynth(Tone.Synth);
        v.set({ oscillator: { type: 'fatsawtooth', spread: 28, count: 3 },
                envelope: { attack: 0.6, decay: 0.5, sustain: 0.8, release: 3.2 } });
        v.volume.value = -16;
      } else if (familia === 'metais') {
        v = new Tone.PolySynth(Tone.Synth);
        v.set({ oscillator: { type: 'sawtooth' },
                envelope: { attack: 0.05, decay: 0.3, sustain: 0.6, release: 1.2 } });
        v.volume.value = -14;
      } else {
        v = new Tone.PolySynth(Tone.Synth);
        v.set({ oscillator: { type: 'triangle' },
                envelope: { attack: 0.02, decay: 0.4, sustain: 0.5, release: 1.6 } });
        v.volume.value = -10;
      }
    } else if (familia === 'teclas') {
      v = new Tone.Sampler(SALAMANDER);
    } else if (familia === 'cordas') {           // arco: ataque suave, cauda longa
      v = new Tone.PolySynth(Tone.Synth);
      v.set({ oscillator: { type: 'sawtooth' },
              envelope: { attack: 0.22, decay: 0.3, sustain: 0.85, release: 1.7 } });
      v.volume.value = -13;
    } else if (familia === 'madeiras') {         // sopro: ar, doce
      v = new Tone.PolySynth(Tone.Synth);
      v.set({ oscillator: { type: 'triangle' },
              envelope: { attack: 0.06, decay: 0.2, sustain: 0.75, release: 0.9 } });
      v.volume.value = -8;
    } else if (familia === 'metais') {           // metal: ataque firme, brilho
      v = new Tone.PolySynth(Tone.Synth);
      v.set({ oscillator: { type: 'sawtooth' },
              envelope: { attack: 0.04, decay: 0.2, sustain: 0.7, release: 0.7 } });
      v.volume.value = -14;
    } else if (familia === 'percussao') {        // lâminas: ataque seco, sem sustain
      v = new Tone.PolySynth(Tone.Synth);
      v.set({ oscillator: { type: 'triangle' },
              envelope: { attack: 0.004, decay: 0.6, sustain: 0.0, release: 0.5 } });
      v.volume.value = -6;
    } else {
      v = new Tone.PolySynth(Tone.Synth); v.volume.value = -8;
    }
  } catch (_) {
    try { v = new Tone.PolySynth(Tone.Synth); } catch (__) { v = null; }
  }
  if (v && destino) { try { v.connect(destino); } catch (_) {} }
  return v;
}

function criarSessao(reverbMix = 0.2, electronico = false) {
  const saida = new Tone.Gain(1).toDestination();
  const reverb = new Tone.Reverb({ decay: electronico ? 5.5 : 3.5, wet: reverbMix }).connect(saida);
  let entrada = reverb;
  let delay = null;
  if (electronico) {
    delay = new Tone.FeedbackDelay({ delayTime: 0.38, feedback: 0.3, wet: 0.22 }).connect(reverb);
    entrada = delay;
  }
  return { vozes: {}, reverb, saida, entrada, delay, electronico };
}

// devolve (criando se preciso) a voz da família, ligada à cadeia da sessão
function vozDaSessao(sess, familia) {
  if (!sess.vozes[familia]) {
    sess.vozes[familia] = criarVozFamilia(familia, sess.electronico, sess.entrada);
  }
  return sess.vozes[familia];
}

function libertarVozes(sess) {
  if (!sess) return;
  Object.values(sess.vozes || {}).forEach(v => {
    try { v.releaseAll ? v.releaseAll() : (v.triggerRelease && v.triggerRelease()); } catch (_) {}
  });
}

function destruirSessao(slot) {
  const s = sessoes[slot];
  if (!s) return;
  try { s.saida.gain.cancelScheduledValues(0); } catch (_) {}
  try { s.saida.gain.setValueAtTime(0, Tone.now()); } catch (_) {}
  libertarVozes(s);
  setTimeout(() => {
    Object.values(s.vozes || {}).forEach(v => { try { v.dispose(); } catch (_) {} });
    try { s.delay && s.delay.dispose(); } catch (_) {}
    try { s.reverb.dispose(); } catch (_) {}
    try { s.saida.dispose(); } catch (_) {}
  }, 90);
  sessoes[slot] = null;
}

function silenciarOutras(slotActual) {
  ['A', 'B'].forEach(outro => {
    if (outro === slotActual) return;
    const s = sessoes[outro];
    if (!s) return;
    try {
      const t = Tone.now();
      s.saida.gain.cancelScheduledValues(t);
      s.saida.gain.setTargetAtTime(0, t, 0.08);
    } catch (_) {}
    libertarVozes(s);
    if (slotAtivo === outro) {
      slotState[outro].pausado = true;
      slotState[outro].tPausa = Tone.now();
      tokensTimer[outro]++;
      slotAtivo = null;
      highlightTimers[outro].forEach(id => clearTimeout(id));
      highlightTimers[outro] = [];
      setEstadoMsg(outro, 'Pausado.', false);
    }
  });
}

// Constrói a lista de notas a tocar a partir das PARTES (cada nota leva a sua
// família, para escolher o timbre). Expande acordes em notas separadas.
function notasDeDados(dados) {
  const out = [];
  ((dados && dados.partes) || []).forEach(parte => {
    const fam = familiaDeInstrumento(parte.instrumento);
    (parte.compassos || []).forEach(comp => comp.forEach(ev => {
      if (ev.is_rest) return;
      out.push({ nota: ev.nome, inicio: ev.inicio_seg, duracao: ev.duracao_seg,
                 velocity: ev.velocity, familia: fam });
      (ev.acorde || []).forEach(a => out.push({
        nota: midiNome(a.midi), inicio: ev.inicio_seg, duracao: ev.duracao_seg,
        velocity: ev.velocity, familia: fam,
      }));
    }));
  });
  return out;
}

// Agenda uma lista de notas (cada uma com .familia) na sessão, a partir de
// tBase, descontando `elapsed` (para retoma). Devolve a duração restante.
function agendarNotas(sess, notas, tBase, elapsed = 0) {
  let dur = 0;
  notas.forEach(n => {
    const ini = n.inicio - elapsed;
    if (ini < -0.02) return;
    const voz = vozDaSessao(sess, n.familia);
    if (!voz) return;
    try { voz.triggerAttackRelease(n.nota, Math.max(0.06, n.duracao), tBase + Math.max(0, ini), n.velocity); } catch (_) {}
    dur = Math.max(dur, Math.max(0, ini) + n.duracao);
  });
  return dur;
}

// Toca um slot (multi-instrumento). Silencia o outro, agenda por família,
// guarda estado por slot. Devolve a duração total (segundos).
async function tocarNotas(slot, notas, reverbMix = 0.2, electronico = false) {
  await garantirToneIniciado();
  silenciarOutras(slot);

  if (!sessoes[slot]) sessoes[slot] = criarSessao(reverbMix, electronico);
  const sess = sessoes[slot];
  libertarVozes(sess);
  try {
    const t = Tone.now();
    sess.saida.gain.cancelScheduledValues(t);
    sess.saida.gain.setTargetAtTime(1, t, 0.02);
  } catch (_) {}
  try { sess.reverb.wet.setTargetAtTime(reverbMix, Tone.now(), 0.02); } catch (_) {}

  // Pré-cria as vozes das famílias presentes ANTES de esperar pelos samples,
  // para que Tone.loaded() aguarde também os samples do piano.
  [...new Set(notas.map(n => n.familia))].forEach(f => vozDaSessao(sess, f));
  await Tone.loaded();
  const tStart = Tone.now() + 0.15;
  const duracaoTotal = agendarNotas(sess, notas, tStart, 0);

  slotState[slot] = { pausado: false, notas, reverbMix, electronico, tStart, tPausa: 0, duracaoTotal };
  slotAtivo = slot;
  agendarHighlights(slot, 0);
  return duracaoTotal;
}

// pressionaPlay: chamado pelo botão de cada slot.
// Três casos: (1) a tocar → pausar; (2) pausado → retomar; (3) inactivo → tocar do zero.
// Pausa/retoma são 100% por slot — o AudioContext nunca é suspenso globalmente.
async function pressionaPlay(slot) {
  const s = slot.toLowerCase();
  const dados = estado[s]?.dados;
  if (!dados) return;

  await garantirToneIniciado();
  const st = slotState[slot];

  // --- (1) Slot activo a tocar → pausar ---
  if (slotAtivo === slot) {
    const sess = sessoes[slot];
    if (sess) {
      try {
        const t = Tone.now();
        sess.saida.gain.cancelScheduledValues(t);
        sess.saida.gain.setValueAtTime(0, t);   // mute imediato
      } catch (_) {}
      libertarVozes(sess);
    }
    st.pausado = true;
    st.tPausa = Tone.now();
    tokensTimer[slot]++;   // invalida o timer de fim pendente
    slotAtivo = null;
    // Cancela timers futuros mas mantém o highlight visual na nota actual
    highlightTimers[slot].forEach(id => clearTimeout(id));
    highlightTimers[slot] = [];
    setEstadoMsg(slot, 'Pausado.', false);
    actualizarBotoesPlay();
    return;
  }

  // --- (2) Slot pausado → retomar ---
  if (st.pausado && (st.notas || []).length > 0) {
    const elapsed = st.tPausa - st.tStart;
    const notasRestantes = st.notas.filter(n => n.inicio > elapsed - 0.02);

    if (notasRestantes.length > 0) {
      if (!sessoes[slot]) sessoes[slot] = criarSessao(st.reverbMix, st.electronico);
      const sess = sessoes[slot];
      silenciarOutras(slot);
      try {
        const t = Tone.now();
        sess.saida.gain.cancelScheduledValues(t);
        sess.saida.gain.setTargetAtTime(1, t, 0.02);
      } catch (_) {}
      [...new Set(notasRestantes.map(n => n.familia))].forEach(f => vozDaSessao(sess, f));
      await Tone.loaded();
      const tNewStart = Tone.now() + 0.15;
      const novaDuracao = agendarNotas(sess, notasRestantes, tNewStart, elapsed);
      st.pausado = false;
      st.tStart  = tNewStart - elapsed;  // base equivalente para re-pausar correctamente
      st.tPausa  = 0;
      st.duracaoTotal = novaDuracao;
      slotAtivo = slot;
      agendarHighlights(slot, elapsed);

      setEstadoMsg(slot, 'A tocar...', false);
      const meuToken = ++tokensTimer[slot];
      setTimeout(() => {
        if (tokensTimer[slot] === meuToken && slotAtivo === slot && !slotState[slot].pausado) {
          slotAtivo = null;
          setEstadoMsg(slot, msgPronto(), false);
          actualizarBotoesPlay();
        }
      }, novaDuracao * 1000 + 250);
    } else {
      // Todas as notas já passaram — recomeçar do início
      st.pausado = false;
      setEstadoMsg(slot, 'A tocar...', false);
      const duracao = await tocarNotas(slot, st.notas, st.reverbMix, st.electronico);
      const meuToken = ++tokensTimer[slot];
      setTimeout(() => {
        if (tokensTimer[slot] === meuToken && slotAtivo === slot && !slotState[slot].pausado) {
          slotAtivo = null;
          setEstadoMsg(slot, msgPronto(), false);
          actualizarBotoesPlay();
        }
      }, duracao * 1000 + 250);
    }
    actualizarBotoesPlay();
    return;
  }

  // --- (3) Slot inactivo (nunca tocou ou já terminou) → tocar do zero ---
  setEstadoMsg(slot, 'A tocar...', false);
  const duracao = await tocarNotas(slot, notasDeDados(dados), dados.musica.reverb_mix,
                                   dados.musica.modo_geracao === 'electronico');
  const meuToken = ++tokensTimer[slot];
  setTimeout(() => {
    if (tokensTimer[slot] === meuToken && slotAtivo === slot && !slotState[slot].pausado) {
      slotAtivo = null;
      setEstadoMsg(slot, msgPronto(), false);
      actualizarBotoesPlay();
    }
  }, duracao * 1000 + 250);
  actualizarBotoesPlay();
}

const ICONE_PLAY  = '<path d="M6 4l14 8-14 8z"/>';
const ICONE_PAUSE = '<path d="M6 4h4v16H6zM14 4h4v16h-4z"/>';

function actualizarBotoesPlay() {
  ['A', 'B'].forEach(slot => {
    const s = slot.toLowerCase();
    const btn = document.getElementById(`btn-tocar-${s}`);
    if (!btn) return;
    const span = btn.querySelector('.btn-label');
    const svg  = btn.querySelector('svg');
    if (!span || !svg) return;

    if (slotAtivo === slot) {
      span.textContent = 'Pausar';
      svg.innerHTML = ICONE_PAUSE;
    } else if (slotState[slot].pausado) {
      span.textContent = 'Retomar';
      svg.innerHTML = ICONE_PLAY;
    } else {
      span.textContent = (estado.modo === 'comparar') ? `Tocar ${slot}` : 'Tocar de novo';
      svg.innerHTML = ICONE_PLAY;
    }
  });
}

// ---------------------------------------------------------------------
//  ESCALAS / LABELS para os parâmetros do solo
// ---------------------------------------------------------------------
const ESCALAS_PARAM = {
  pH:                    { min: 3, max: 10, unidade: '' },
  carbono_organico_g_kg: { min: 0, max: 80, unidade: 'g/kg' },
  azoto_g_kg:            { min: 0, max: 6,  unidade: 'g/kg' },
  areia_pct:             { min: 0, max: 100, unidade: '%' },
  argila_pct:            { min: 0, max: 100, unidade: '%' },
  limo_pct:              { min: 0, max: 100, unidade: '%' },
  cec:                   { min: 0, max: 400, unidade: '' },
  densidade_g_cm3:       { min: 0.8, max: 2.0, unidade: 'g/cm³' },
  pedregoso_pct:         { min: 0, max: 80, unidade: '%' },
};
const LABELS_PARAM = {
  pH: 'pH',
  carbono_organico_g_kg: 'Carbono org.',
  azoto_g_kg: 'Azoto',
  areia_pct: 'Areia',
  argila_pct: 'Argila',
  limo_pct: 'Limo',
  cec: 'CEC',
  densidade_g_cm3: 'Densidade',
  pedregoso_pct: 'Pedregosidade',
};

function paramHTML(chave, valor) {
  const cfg = ESCALAS_PARAM[chave];
  if (!cfg || valor == null) return '';
  const pct = Math.max(0, Math.min(100, ((valor - cfg.min) / (cfg.max - cfg.min)) * 100));
  return `
    <div class="param">
      <span class="nome">${LABELS_PARAM[chave]}</span>
      <div class="barra"><div style="width:${pct}%"></div></div>
      <span class="valor">${valor}${cfg.unidade ? ' ' + cfg.unidade : ''}</span>
    </div>`;
}

const ROTULOS_CAT = {
  mina: "⛏ Minas / pedreiras",
  industria_pesada: "🏭 Indústria pesada (química, refinaria, fundição)",
  central_termica: "🔥 Centrais térmicas fósseis",
  aterro: "🗑 Aterros",
  industria_geral: "🏗 Zonas industriais",
  agricultura_intensiva: "🌾 Agricultura intensiva",
};
const CORES_OSM = {
  mina: "#8b2c00",
  industria_pesada: "#c4341a",
  central_termica: "#7a5500",
  aterro: "#5a4a3a",
  industria_geral: "#999999",
  agricultura_intensiva: "#a8a04a",
};

// ---------------------------------------------------------------------
//  RENDERIZADORES (por slot)
// ---------------------------------------------------------------------
function renderSolo(slot, solo) {
  const div = document.getElementById(`solo-params-${slot.toLowerCase()}`);
  if (!div) return;
  const ordem = ['pH', 'carbono_organico_g_kg', 'azoto_g_kg',
                 'areia_pct', 'argila_pct', 'limo_pct',
                 'cec', 'densidade_g_cm3', 'pedregoso_pct'];
  let html = ordem.map(k => paramHTML(k, solo[k])).join('');

  if (solo.textura && solo.textura !== 'indefinida') {
    html += `<div class="param"><span class="nome">Textura</span>
             <span style="grid-column: 2 / 4; font-family: var(--mono); font-size:12px;">
             ${solo.textura}</span></div>`;
  }

  if (solo.retencao_agua) {
    const a = solo.retencao_agua;
    html += `<div style="margin-top:10px; padding-top:10px; border-top:1px solid var(--linha);">
      <div class="param">
        <span class="nome">Água disponível</span>
        <div class="barra"><div style="width:${Math.min(100, a.awc_pct * 4)}%"></div></div>
        <span class="valor">${a.awc_pct}%</span>
      </div>
      <div class="param">
        <span class="nome">Cap. de campo</span>
        <div class="barra"><div style="width:${a.capacidade_campo_pct}%"></div></div>
        <span class="valor">${a.capacidade_campo_pct}%</span>
      </div>
      <div class="param">
        <span class="nome">Ponto de murcha</span>
        <div class="barra"><div style="width:${a.ponto_murcha_pct}%"></div></div>
        <span class="valor">${a.ponto_murcha_pct}%</span>
      </div>
      <div style="font-size:12px; color:var(--tinta-leve); margin-top:4px;">
        Retenção: <b style="color:var(--tinta);">${a.classe}</b>
      </div>
    </div>`;
  }
  div.innerHTML = html;
}

function pct0(x) { return Math.round((x || 0) * 100) + '%'; }
function renderMusica(slot, m) {
  const div = document.getElementById(`musica-params-${slot.toLowerCase()}`);
  if (!div) return;
  const linhas = [
    ['Tonalidade',     m.tonalidade || m.escala || '—'],
    ['Estilo',         m.estilo_nome || '—'],
    ['Compasso',       m.compasso || '—'],
    ['Andamento',      (m.bpm ?? '—') + ' BPM'],
    ['Dificuldade',    m.dificuldade || '—'],
    ['Duração',        fmtTempo(m.duracao_seg || 0)],
    ['Nº compassos',   m.n_compassos ?? '—'],
    ['Frase',          (m.frase_compassos ?? '—') + ' comp.'],
    ['Vivacidade',     pct0(m.vivacidade)],
    ['Reverberação',   pct0(m.reverb_mix)],
    ['Prob. salto',    pct0(m.prob_salto)],
    ['Prob. silêncio', pct0(m.prob_silencio)],
    ['Prob. staccato', pct0(m.prob_staccato)],
    ['Prob. acorde',   pct0(m.prob_acorde)],
    ['Cromatismo',     pct0(m.prob_cromatico)],
    ['Dissonância',    pct0(m.prob_dissonancia)],
  ];
  div.innerHTML =
    '<div class="musica-grid">' +
    linhas.map(([k, v]) => `<span class="k">${k}</span><span class="v">${v}</span>`).join('') +
    '</div>';
}
function renderExplicacao(slot, cartoes) {
  const div = document.getElementById(`explicacao-${slot.toLowerCase()}`);
  if (!div) return;
  if (!cartoes || !cartoes.length) {
    div.innerHTML = '<p class="placeholder">Sem leitura.</p>';
    return;
  }
  div.innerHTML = '<div class="legenda">' + cartoes.map(c =>
    `<div class="cartao ${c.tipo}"><div class="ct">${c.titulo}</div><div class="cx">${c.texto}</div></div>`
  ).join('') + '</div>';
}

function renderPressaoHumana(slot, ph) {
  const div = document.getElementById(`pressao-humana-${slot.toLowerCase()}`);
  if (!div) return;
  if (!ph || !ph.disponivel) {
    div.innerHTML = '<p class="placeholder">Sem dados de pressão humana ' +
      (ph?.erro ? '(falha de ligação ao OSM)' : '') + '</p>';
    return;
  }

  const total = Object.values(ph.contagens).reduce((s, n) => s + n, 0);
  if (total === 0) {
    div.innerHTML = `<p style="color: var(--musgo-escuro); font-size: var(--t-sm);">
      Nenhuma fonte conhecida de pressão humana num raio de ${ph.raio_km} km.</p>`;
  } else {
    const linhas = Object.entries(ph.contagens)
      .filter(([_, n]) => n > 0)
      .sort((a, b) => b[1] - a[1])
      .map(([cat, n]) => `
        <div class="param">
          <span class="nome" style="grid-column: 1 / 3;">${ROTULOS_CAT[cat] || cat}</span>
          <span class="valor">${n}</span>
        </div>`).join('');

    div.innerHTML = `
      <div style="font-size:12px; color:var(--tinta-leve); margin-bottom:8px;">
        Num raio de ${ph.raio_km} km — score: <b style="color:var(--tinta);">${ph.score}</b>
      </div>
      ${linhas}`;
  }

  // pontos no mapa — uma camada por slot
  if (camadasOSM[slot]) mapa.removeLayer(camadasOSM[slot]);
  camadasOSM[slot] = L.layerGroup();
  ph.elementos.forEach(el => {
    L.circleMarker([el.lat, el.lon], {
      radius: 6,
      color: CORES_OSM[el.categoria] || "#666",
      fillColor: CORES_OSM[el.categoria] || "#666",
      fillOpacity: 0.7,
      weight: 1,
    }).bindPopup(`<b>${el.nome}</b><br>${ROTULOS_CAT[el.categoria] || el.categoria}` +
                 `<br><span style="font-size:11px; color:#888;">amostra ${slot}</span>`)
      .addTo(camadasOSM[slot]);
  });
  camadasOSM[slot].addTo(mapa);
}

function renderAvisos(slot, solo) {
  const div = document.getElementById(`avisos-bloco-${slot.toLowerCase()}`);
  if (!div) return;
  const avisos = solo.avisos || [];
  const score = solo.saude_score ?? 10;
  if (avisos.length === 0) {
    div.innerHTML = `<div class="avisos ok"><b>Solo saudável</b>Saúde: ${score}/10</div>`;
  } else {
    div.innerHTML = `<div class="avisos">
      <b>Sinais de alerta (saúde: ${score}/10)</b>
      ${avisos.map(a => '• ' + a).join('<br>')}
    </div>`;
  }
}

function criarStaveNoteVF(VF, n, clave) {
  if (n.is_rest) {
    const key = clave === 'bass' ? 'd/3' : 'b/4';
    return new VF.StaveNote({ clef: clave, keys: [key], duration: n.vex_dur });
  }
  const tons = [n, ...(n.acorde || [])];
  const keys = tons.map(t => t.vexkey);
  const sn = new VF.StaveNote({ clef: clave, keys, duration: n.vex_dur });
  tons.forEach((t, i) => { if (t.vex_acc) { try { sn.addModifier(new VF.Accidental(t.vex_acc), i); } catch (_) {} } });
  if (n.staccato) { try { sn.addModifier(new VF.Articulation('a.').setPosition(3), 0); } catch (_) {} }
  return sn;
}

// Desenha a pauta a partir de `dados` (contrato novo: partes + musica),
// com armação, compasso e barras. Reconstrói os timings/elementos da melodia
// para manter o highlight sincronizado. Mostra ~12 compassos (a peça completa
// fica no MusicXML exportado).
function desenharPartitura(slot, dados) {
  const div = document.getElementById(`partitura-${slot.toLowerCase()}`);
  if (!div) return;
  div.innerHTML = '';
  partituraInfo[slot] = { els: [], timings: [] };

  const partes = dados && dados.partes;
  const musica = dados && dados.musica;
  if (!partes || !partes.length || !musica) {
    div.innerHTML = '<p class="placeholder">Sem partitura.</p>';
    return;
  }

  const VF = Vex.Flow;
  const meter = musica.compasso || '4/4';
  const numBeats = musica.compasso_num || 4;
  const beatValue = musica.compasso_den || 4;
  const keySpec = musica.keysig_vexflow || 'C';
  const nV = partes.length;
  const totalM = partes[0].compassos.length;
  const maxComp = Math.min(totalM, 12);

  const largura = Math.max(div.clientWidth || 700, 700);
  const Wbase = 200, Wfirst = 270, staveGap = 92, padTop = 10;
  const perRow = Math.max(1, Math.min(4, Math.floor((largura - 20) / 230)));
  const rows = Math.ceil(maxComp / perRow);
  const rowHeight = nV * staveGap + 36;
  const totalW = 10 + Wfirst + (perRow - 1) * Wbase + 14;
  const totalH = rows * rowHeight + 16;

  let renderer, ctx;
  try {
    renderer = new VF.Renderer(div, VF.Renderer.Backends.SVG);
    renderer.resize(totalW, totalH);
    ctx = renderer.getContext();
  } catch (e) {
    div.innerHTML = '<p class="placeholder">Não foi possível desenhar a pauta.</p>';
    return;
  }

  const elsMel = [], timingsMel = [];
  const idxMelodia = Math.max(0, partes.findIndex(p => p.papel === 'melodia'));
  // grand staff (piano): partes que partilham o mesmo grupo_grand -> chaveta
  const ehGrand = nV >= 2 && partes.every(p => p.grupo_grand && p.grupo_grand === partes[0].grupo_grand);

  for (let m = 0; m < maxComp; m++) {
    const row = Math.floor(m / perRow), col = m % perRow;
    const primeira = col === 0;
    const x = primeira ? 10 : (10 + Wfirst + (col - 1) * Wbase);
    const w = primeira ? Wfirst : Wbase;
    const yRow = padTop + row * rowHeight;
    const pautas = [];

    for (let vi = 0; vi < nV; vi++) {
      const parte = partes[vi];
      const clave = parte.clef;
      const stave = new VF.Stave(x, yRow + vi * staveGap, w);
      if (primeira) {
        stave.addClef(clave);
        try { stave.addKeySignature(keySpec); } catch (_) {}
        if (row === 0) { try { stave.addTimeSignature(meter); } catch (_) {} }
      }
      stave.setContext(ctx).draw();
      pautas.push(stave);

      const med = parte.compassos[m] || [];
      const notas = med.map(n => criarStaveNoteVF(VF, n, clave));
      try {
        const voice = new VF.Voice({ num_beats: numBeats, beat_value: beatValue });
        if (voice.setMode && VF.Voice.Mode) voice.setMode(VF.Voice.Mode.SOFT);
        else if (voice.setStrict) voice.setStrict(false);
        voice.addTickables(notas);
        new VF.Formatter().joinVoices([voice]).format([voice], w - (primeira ? 86 : 22));
        voice.draw(ctx, stave);
        try { VF.Beam.generateBeams(notas.filter(sn => !sn.isRest())).forEach(b => b.setContext(ctx).draw()); } catch (_) {}
        if (vi === idxMelodia) {
          med.forEach((n, k) => {
            if (n.is_rest) return;
            elsMel.push(notas[k].attrs?.el ?? null);
            timingsMel.push({ inicio: n.inicio_seg, duracao: n.duracao_seg });
          });
        }
      } catch (e) { console.warn('compasso', m, 'parte', vi, e); }
    }

    if (primeira && nV > 1) {
      try {
        const tipo = ehGrand ? VF.StaveConnector.type.BRACE
                             : VF.StaveConnector.type.SINGLE_LEFT;
        new VF.StaveConnector(pautas[0], pautas[nV - 1])
          .setType(tipo).setContext(ctx).draw();
        if (ehGrand) {
          new VF.StaveConnector(pautas[0], pautas[nV - 1])
            .setType(VF.StaveConnector.type.SINGLE_LEFT).setContext(ctx).draw();
        }
      } catch (_) {}
    }
  }
  partituraInfo[slot] = { els: elsMel, timings: timingsMel };
}

// ---------------------------------------------------------------------
//  EXPORTAÇÃO — MusicXML + MIDI (do servidor) e WAV (render offline)
// ---------------------------------------------------------------------
const _NN = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B'];
const midiNome = (m) => _NN[((m % 12) + 12) % 12] + (Math.floor(m / 12) - 1);
function nomeParaMidi(nome) {
  const m = String(nome).match(/^([A-G])(#|b)?(-?\d+)$/);
  if (!m) return 48;
  const base = { C:0, D:2, E:4, F:5, G:7, A:9, B:11 }[m[1]];
  const acc = m[2] === '#' ? 1 : (m[2] === 'b' ? -1 : 0);
  return base + acc + 12 * (parseInt(m[3], 10) + 1);
}
function descarregar(blob, nome) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = nome;
  document.body.appendChild(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}
function exportarMusicXML(slot) {
  const d = estado[slot.toLowerCase()]?.dados;
  if (!d?.exportacao?.musicxml) return;
  descarregar(new Blob([d.exportacao.musicxml], { type: 'application/vnd.recordare.musicxml+xml' }),
    `curiousoil_${slot}.musicxml`);
}
function exportarMIDI(slot) {
  const d = estado[slot.toLowerCase()]?.dados;
  if (!d?.exportacao?.midi_base64) return;
  const bin = atob(d.exportacao.midi_base64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  descarregar(new Blob([bytes], { type: 'audio/midi' }), `curiousoil_${slot}.mid`);
}
function bufferParaWav(ab) {
  const nCh = ab.numberOfChannels, sr = ab.sampleRate, len = ab.length;
  const blockAlign = nCh * 2, dataSize = len * blockAlign;
  const buf = new ArrayBuffer(44 + dataSize), view = new DataView(buf);
  const ws = (o, s) => { for (let i = 0; i < s.length; i++) view.setUint8(o + i, s.charCodeAt(i)); };
  ws(0, 'RIFF'); view.setUint32(4, 36 + dataSize, true); ws(8, 'WAVE'); ws(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, nCh, true);
  view.setUint32(24, sr, true); view.setUint32(28, sr * blockAlign, true);
  view.setUint16(32, blockAlign, true); view.setUint16(34, 16, true);
  ws(36, 'data'); view.setUint32(40, dataSize, true);
  const chans = []; for (let c = 0; c < nCh; c++) chans.push(ab.getChannelData(c));
  let off = 44;
  for (let i = 0; i < len; i++) for (let c = 0; c < nCh; c++) {
    let s = Math.max(-1, Math.min(1, chans[c][i]));
    view.setInt16(off, s < 0 ? s * 0x8000 : s * 0x7FFF, true); off += 2;
  }
  return new Blob([view], { type: 'audio/wav' });
}
function criarVozRender(papel, electronico) {
  let s;
  if (electronico) {
    if (papel === 'baixo') {
      s = new Tone.MonoSynth({ oscillator: { type: 'sine' }, envelope: { attack: 0.05, decay: 0.3, sustain: 0.85, release: 2.6 } });
      s.volume.value = -8;
    } else if (papel === 'harmonia') {
      s = new Tone.PolySynth(Tone.Synth); s.set({ oscillator: { type: 'sawtooth' }, envelope: { attack: 1.4, decay: 0.6, sustain: 0.7, release: 4 } }); s.volume.value = -14;
    } else {
      s = new Tone.PolySynth(Tone.Synth); s.set({ oscillator: { type: 'triangle' }, envelope: { attack: 0.3, decay: 0.4, sustain: 0.6, release: 2.4 } }); s.volume.value = -7;
    }
    return s;
  }
  if (papel === 'baixo') {
    s = new Tone.MonoSynth({ oscillator: { type: 'sine' }, envelope: { attack: 0.01, decay: 0.5, sustain: 0.3, release: 0.8 } }); s.volume.value = -6;
  } else {
    s = new Tone.PolySynth(Tone.Synth); s.set({ oscillator: { type: 'triangle' }, envelope: { attack: 0.01, decay: 0.4, sustain: 0.3, release: 0.9 } }); s.volume.value = -6;
  }
  return s;
}
async function exportarWAV(slot) {
  const d = estado[slot.toLowerCase()]?.dados;
  if (!d || (!d.melodia && !d.baixo)) return;
  const btn = document.getElementById(`btn-wav-${slot.toLowerCase()}`);
  if (btn) btn.disabled = true;
  setEstadoMsg(slot, 'A gerar áudio (WAV)…', true);
  const electronico = d.musica?.modo_geracao === 'electronico';
  const mel = d.melodia || [], bx = d.baixo || [];
  const fim = [...mel, ...bx].reduce((a, n) => Math.max(a, n.inicio + n.duracao), 0);
  const dur = fim + (electronico ? 3 : 1.5) + 0.2;
  try {
    await garantirToneIniciado();
    const ab = await Tone.Offline(() => {
      const master = new Tone.Gain(0.9).toDestination();
      const reverb = new Tone.Freeverb({ roomSize: electronico ? 0.85 : 0.6, dampening: 3000, wet: d.musica?.reverb_mix ?? 0.2 }).connect(master);
      let entrada = reverb;
      if (electronico) entrada = new Tone.FeedbackDelay({ delayTime: 0.38, feedback: 0.3, wet: 0.25 }).connect(reverb);
      const sMel = criarVozRender('melodia', electronico); sMel.connect(entrada);
      const sBx = criarVozRender('baixo', electronico); sBx.connect(entrada);
      const escala = electronico ? 1.4 : 1.05;
      mel.forEach(n => { try { sMel.triggerAttackRelease(n.nota, Math.max(0.08, n.duracao * escala), n.inicio + 0.1, n.velocity); } catch (_) {} });
      bx.forEach(n => { try { sBx.triggerAttackRelease(n.nota, Math.max(0.08, n.duracao * escala), n.inicio + 0.1, n.velocity); } catch (_) {} });
      if (electronico && bx.length) {
        let raiz = Infinity; bx.forEach(n => { const mm = nomeParaMidi(n.nota); if (mm < raiz) raiz = mm; });
        if (isFinite(raiz)) {
          const pad = new Tone.PolySynth(Tone.Synth);
          pad.set({ oscillator: { type: 'sine' }, envelope: { attack: 3, decay: 1, sustain: 0.9, release: 6 } });
          pad.volume.value = -18; pad.connect(entrada);
          pad.triggerAttackRelease([midiNome(raiz - 12), midiNome(raiz - 5)], fim + 2, 0.1);
        }
      }
    }, dur);
    const native = ab.get ? ab.get() : ab;
    descarregar(bufferParaWav(native), `curiousoil_${slot}_${Date.now()}.wav`);
    setEstadoMsg(slot, msgPronto(), false);
  } catch (e) {
    console.warn('WAV falhou', e);
    setEstadoMsg(slot, 'Não foi possível gerar o WAV.', false);
  } finally {
    if (btn) btn.disabled = false;
  }
}
function wireExportar(slot) {
  const s = slot.toLowerCase();
  const map = [['btn-xml-' + s, exportarMusicXML], ['btn-midi-' + s, exportarMIDI], ['btn-wav-' + s, exportarWAV]];
  map.forEach(([id, fn]) => {
    const b = document.getElementById(id);
    if (b) { b.disabled = false; b.onclick = () => fn(slot); }
  });
}

// ---------------------------------------------------------------------
//  CONSTRUÇÃO DOS PAINÉIS (HTML)
// ---------------------------------------------------------------------
function htmlPainel(slot) {
  const s = slot.toLowerCase();
  const fecharBtn = slot === 'B'
    ? `<button class="btn-fechar" id="btn-fechar-b" title="Fechar amostra B">✕ Fechar B</button>`
    : '<span></span>';
  const labelBtn = slot === 'A' && estado.modo !== 'comparar'
    ? 'Tocar de novo'
    : `Tocar ${slot}`;

  return `
    <div class="painel lado lado-${s}">
      <div class="lado-cab">
        <span class="lado-tag">Amostra ${slot}</span>
        ${fecharBtn}
      </div>

      <div class="caixa">
        <h3>Localização</h3>
        <p class="coord" id="coord-${s}">— · —</p>
        <p class="estado" id="estado-${s}"></p>
        <button class="btn-tocar" id="btn-tocar-${s}" disabled>
          <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M6 4l14 8-14 8z"/>
          </svg>
          <span class="btn-label">${labelBtn}</span>
        </button>
        <div id="avisos-bloco-${s}"></div>
      </div>

      <div class="caixa">
        <h3>Parâmetros do solo</h3>
        <div id="solo-params-${s}"><p class="placeholder">Ainda sem leitura. Clica no mapa para começar.</p></div>
      </div>

      <div class="caixa">
        <h3>Pressão humana próxima</h3>
        <div id="pressao-humana-${s}"><p class="placeholder">Ainda sem leitura.</p></div>
      </div>

      <div class="caixa">
        <h3>Parâmetros musicais</h3>
        <div id="musica-params-${s}"><p class="placeholder">Ainda sem leitura.</p></div>
      </div>

      <div class="caixa larga">
        <h3>Como o solo virou música</h3>
        <div id="explicacao-${s}"><p class="placeholder">Sem leitura.</p></div>
      </div>

      <div class="caixa larga partitura">
        <h3>Partitura
          <span class="acoes-part">
            <button class="mini-export" id="btn-xml-${s}" disabled title="Abre no MuseScore/Finale">MusicXML</button>
            <button class="mini-export" id="btn-midi-${s}" disabled title="Ficheiro MIDI">MIDI</button>
            <button class="mini-export" id="btn-wav-${s}" disabled title="Áudio WAV">WAV</button>
          </span>
        </h3>
        <div class="part-svg-wrap" id="partitura-${s}"></div>
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------
//  renderEstado — redesenha o painel a partir do estado central
// ---------------------------------------------------------------------
function renderEstado() {
  const cont = document.getElementById('painel-container');
  recalcularModo();
  cont.classList.toggle('comparar', estado.modo === 'comparar');

  // Slots a mostrar
  const slots = [];
  if (estado.modo === 'vazio') slots.push('A');                       // shell vazio para A
  if (estado.a) slots.push('A');
  if (estado.b) slots.push('B');
  // garantir unicidade
  const slotsUnicos = [...new Set(slots)];

  cont.innerHTML = slotsUnicos.map(htmlPainel).join('');

  // popular cada slot a partir do estado
  slotsUnicos.forEach(slot => {
    const dados = estado[slot.toLowerCase()];
    const s = slot.toLowerCase();

    if (dados) {
      const coordEl = document.getElementById(`coord-${s}`);
      if (coordEl) {
        coordEl.textContent = `Lat: ${dados.lat.toFixed(3)}   Lon: ${dados.lon.toFixed(3)}`;
      }
      const estadoEl = document.getElementById(`estado-${s}`);
      if (estadoEl) {
        estadoEl.textContent = dados.estadoMsg || '';
        estadoEl.classList.toggle('carregando', !!dados.carregando);
      }
      if (dados.dados && !dados.dados.erro) {
        renderSolo(slot, dados.dados.solo);
        renderMusica(slot, dados.dados.musica);
        renderExplicacao(slot, dados.dados.musica.explicacao);
        renderPressaoHumana(slot, dados.dados.pressao_humana);
        renderAvisos(slot, dados.dados.solo);
        desenharPartitura(slot, dados.dados);
        const btn = document.getElementById(`btn-tocar-${s}`);
        if (btn) btn.disabled = false;
        wireExportar(slot);
      }
    }

    // botão tocar / pausar / retomar
    const btn = document.getElementById(`btn-tocar-${s}`);
    if (btn) {
      btn.addEventListener('click', () => pressionaPlay(slot));
    }

    // botão fechar (só B)
    if (slot === 'B') {
      const fechar = document.getElementById('btn-fechar-b');
      if (fechar) fechar.addEventListener('click', () => fecharSlot('B'));
    }
  });
}

function msgPronto() {
  return estado.modo === 'comparar'
    ? 'Pronto. Clica para comparar ou substituir.'
    : 'Pronto. Clica noutro ponto ou repete.';
}

function setEstadoMsg(slot, msg, carregando = false) {
  const s = slot.toLowerCase();
  if (estado[s]) {
    estado[s].estadoMsg = msg;
    estado[s].carregando = carregando;
  }
  const el = document.getElementById(`estado-${s}`);
  if (el) {
    el.textContent = msg;
    el.classList.toggle('carregando', carregando);
  }
}

// ---------------------------------------------------------------------
//  FECHAR SLOT B (volta a modo single)
// ---------------------------------------------------------------------
function fecharSlot(slot) {
  destruirSessao(slot);
  if (marcadores[slot]) { mapa.removeLayer(marcadores[slot]); marcadores[slot] = null; }
  if (camadasOSM[slot]) { mapa.removeLayer(camadasOSM[slot]); camadasOSM[slot] = null; }
  estado[slot.toLowerCase()] = null;
  if (slotAtivo === slot) slotAtivo = null;
  limparHighlights(slot);
  resetSlotState(slot);
  tokensTimer[slot]++;
  recalcularModo();
  renderEstado();
  actualizarBotoesPlay();
}

// ---------------------------------------------------------------------
//  SELECIONAR PONTO — chamado por clique no mapa, pesquisa ou par
// ---------------------------------------------------------------------
async function selecionarPonto(lat, lon, opts = {}) {
  // Normalizar coordenadas: ao arrastar o mapa para lá do antimeridiano, o
  // Leaflet devolve longitudes como -341. Reduz-se ao intervalo [-180, 180].
  lon = ((Number(lon) + 180) % 360 + 360) % 360 - 180;
  lat = Math.max(-90, Math.min(90, Number(lat)));

  // decidir o slot
  let slot = opts.slot;
  if (!slot) {
    if (estado.modo === 'vazio')      slot = 'A';
    else if (estado.modo === 'single') slot = estado.a ? 'B' : 'A';
    else {
      // comparar: precisa de modal antes
      abrirModalSubstituir(lat, lon, opts.eventoOriginal || null);
      return;
    }
  }

  // esconder dica do mapa
  document.getElementById('mapa-dica')?.classList.add('escondida');
  // fechar dropdown de pesquisa, se aberto
  fecharResultadosProcura();

  // limpar audio + camada OSM do slot
  destruirSessao(slot);
  if (slotAtivo === slot) slotAtivo = null;
  limparHighlights(slot);
  resetSlotState(slot);
  tokensTimer[slot]++;
  if (camadasOSM[slot]) {
    mapa.removeLayer(camadasOSM[slot]);
    camadasOSM[slot] = null;
  }

  // gravar entrada no estado
  estado[slot.toLowerCase()] = {
    lat, lon,
    dados: null,
    estadoMsg: 'A buscar dados do solo...',
    carregando: true,
  };

  renderEstado();

  // marcador
  if (marcadores[slot]) mapa.removeLayer(marcadores[slot]);
  marcadores[slot] = L.marker([lat, lon], { icon: criarIconeAB(slot) }).addTo(mapa);

  // flyTo se a pesquisa pediu
  if (opts.flyTo) {
    mapa.flyTo([lat, lon], Math.max(mapa.getZoom(), 9), { duration: 1.0 });
  }

  // fetch
  try {
    const cfg = lerConfig();
    const q = new URLSearchParams({
      lat, lon,
      dificuldade: cfg.dificuldade, duracao: cfg.duracao,
      modo: cfg.electronico ? "electronico" : "acustico",
      estilo: cfg.estilo,
      instrumentos: cfg.instrumentos.join(","),
    });
    const dados = await gerarFetch(`${API_URL}/gerar?${q.toString()}`, slot);

    if (dados.erro) {
      setEstadoMsg(slot, dados.erro + (dados.dica ? ' — ' + dados.dica : ''), false);
      return;
    }

    estado[slot.toLowerCase()].dados = dados;
    setEstadoMsg(slot, 'A tocar...', false);
    renderEstado();

    const duracao = await tocarNotas(slot, notasDeDados(dados), dados.musica.reverb_mix,
                                     dados.musica.modo_geracao === 'electronico');
    actualizarBotoesPlay();

    const meuToken = ++tokensTimer[slot];
    setTimeout(() => {
      if (tokensTimer[slot] === meuToken && slotAtivo === slot && !slotState[slot].pausado) {
        slotAtivo = null;
        setEstadoMsg(slot, msgPronto(), false);
        actualizarBotoesPlay();
      }
    }, duracao * 1000 + 250);

  } catch (err) {
    setEstadoMsg(slot, 'Erro: ' + err.message, false);
  }
}

// ---------------------------------------------------------------------
//  MODAL POPOVER — Substituir A ou B?
// ---------------------------------------------------------------------
function abrirModalSubstituir(lat, lon, evento) {
  fecharModalSubstituir();
  const wrap = document.querySelector('.mapa-wrap');

  const m = document.createElement('div');
  m.className = 'popover-substituir';
  m.id = 'popover-substituir';

  // posicionar perto do clique ou centrado no mapa
  if (evento && evento.containerPoint) {
    const x = evento.containerPoint.x;
    const y = evento.containerPoint.y;
    const wrapRect = wrap.getBoundingClientRect();
    const left = Math.min(wrapRect.width - 256, Math.max(8, x - 120));
    const top  = Math.min(wrapRect.height - 170, Math.max(8, y + 16));
    m.style.left = left + 'px';
    m.style.top  = top + 'px';
  } else {
    m.style.left = '50%';
    m.style.top  = '50%';
    m.style.transform = 'translate(-50%, -50%)';
  }

  m.innerHTML = `
    <div class="popover-titulo">Já tens A e B. Onde colocar este ponto?</div>
    <div class="popover-coord">${lat.toFixed(3)}, ${lon.toFixed(3)}</div>
    <div class="popover-botoes">
      <button class="popover-btn popover-a" data-slot="A">Substituir A</button>
      <button class="popover-btn popover-b" data-slot="B">Substituir B</button>
    </div>
    <button class="popover-cancelar">Cancelar</button>
  `;
  wrap.appendChild(m);

  m.querySelectorAll('.popover-btn').forEach(b => {
    b.addEventListener('click', () => {
      const escolhido = b.dataset.slot;
      fecharModalSubstituir();
      selecionarPonto(lat, lon, { slot: escolhido });
    });
  });
  m.querySelector('.popover-cancelar').addEventListener('click', fecharModalSubstituir);

  // Esc fecha
  const escHandler = (ev) => {
    if (ev.key === 'Escape') { fecharModalSubstituir(); document.removeEventListener('keydown', escHandler); }
  };
  document.addEventListener('keydown', escHandler);
}

function fecharModalSubstituir() {
  const m = document.getElementById('popover-substituir');
  if (m) m.remove();
}

// ---------------------------------------------------------------------
//  CLIQUE NO MAPA
// ---------------------------------------------------------------------
mapa.on('click', async (e) => {
  const { lat, lng } = e.latlng;
  await selecionarPonto(lat, lng, { eventoOriginal: e });
});

// ---------------------------------------------------------------------
//  PESQUISA — Nominatim + parser de coordenadas + dropdown
// ---------------------------------------------------------------------
const inputProc      = document.getElementById('procurar-input');
const resultadosProc = document.getElementById('procurar-resultados');
const spinnerProc    = document.getElementById('proc-spinner');

let timerDebounce = null;
let indiceDestacado = -1;
let resultadosActuais = [];

// regex para "40.12, -8.45" ou "40,12 -8,45" — tolerante a vírgula decimal e separadores
function parsearCoords(texto) {
  const t = texto.trim();
  // tentar primeiro com . como decimal:
  let m = t.match(/^\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*$/);
  if (m) {
    return { lat: parseFloat(m[1]), lon: parseFloat(m[2]) };
  }
  // ou com vírgula como decimal (ex: "40,123 -8,456"):
  m = t.match(/^\s*(-?\d{1,3},\d+)\s+(-?\d{1,3},\d+)\s*$/);
  if (m) {
    return { lat: parseFloat(m[1].replace(',', '.')), lon: parseFloat(m[2].replace(',', '.')) };
  }
  return null;
}

function fecharResultadosProcura() {
  resultadosProc.hidden = true;
  resultadosProc.innerHTML = '';
  indiceDestacado = -1;
  resultadosActuais = [];
}

function renderResultadosProcura(resultados) {
  resultadosActuais = resultados;
  indiceDestacado = -1;
  if (resultados.length === 0) {
    resultadosProc.innerHTML = '<div class="proc-vazio">Sem resultados.</div>';
    resultadosProc.hidden = false;
    return;
  }
  resultadosProc.innerHTML = resultados.map((r, i) => `
    <div class="proc-item" data-i="${i}">
      <span class="proc-item-nome">${r.nome}</span>
      <span class="proc-item-coord">${r.lat.toFixed(4)}, ${r.lon.toFixed(4)}</span>
    </div>
  `).join('');
  resultadosProc.hidden = false;

  resultadosProc.querySelectorAll('.proc-item').forEach(el => {
    el.addEventListener('click', () => {
      const i = parseInt(el.dataset.i, 10);
      selecionarResultadoProcura(i);
    });
  });
}

function selecionarResultadoProcura(i) {
  const r = resultadosActuais[i];
  if (!r) return;
  inputProc.value = r.nome;
  fecharResultadosProcura();
  selecionarPonto(r.lat, r.lon, { flyTo: true });
}

async function chamarNominatim(query) {
  // Em browser não conseguimos forçar User-Agent (header protegido),
  // mas Nominatim aceita Referer automático. Adicionamos 'accept-language' para PT.
  const url = `https://nominatim.openstreetmap.org/search?q=${encodeURIComponent(query)}&format=json&limit=5&accept-language=pt`;
  const r = await fetch(url, { headers: { 'Accept': 'application/json' } });
  if (!r.ok) throw new Error('Nominatim ' + r.status);
  const dados = await r.json();
  return dados.map(d => ({
    nome: d.display_name,
    lat: parseFloat(d.lat),
    lon: parseFloat(d.lon),
  }));
}

inputProc.addEventListener('input', () => {
  const valor = inputProc.value;
  clearTimeout(timerDebounce);

  if (!valor.trim()) {
    fecharResultadosProcura();
    return;
  }

  // 1) tentar parsear como coordenadas
  const coords = parsearCoords(valor);
  if (coords) {
    renderResultadosProcura([{
      nome: `Coordenadas: ${coords.lat.toFixed(4)}, ${coords.lon.toFixed(4)}`,
      lat: coords.lat,
      lon: coords.lon,
    }]);
    return;
  }

  // 2) caso contrário, Nominatim com debounce 300ms
  spinnerProc.hidden = false;
  timerDebounce = setTimeout(async () => {
    try {
      const res = await chamarNominatim(valor);
      renderResultadosProcura(res);
    } catch (e) {
      resultadosProc.innerHTML = '<div class="proc-vazio">Erro: ' + e.message + '</div>';
      resultadosProc.hidden = false;
    } finally {
      spinnerProc.hidden = true;
    }
  }, 300);
});

inputProc.addEventListener('keydown', (e) => {
  if (resultadosProc.hidden) return;
  const itens = resultadosProc.querySelectorAll('.proc-item');
  if (itens.length === 0) {
    if (e.key === 'Escape') fecharResultadosProcura();
    return;
  }
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    indiceDestacado = (indiceDestacado + 1) % itens.length;
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    indiceDestacado = (indiceDestacado - 1 + itens.length) % itens.length;
  } else if (e.key === 'Enter') {
    e.preventDefault();
    const i = indiceDestacado >= 0 ? indiceDestacado : 0;
    selecionarResultadoProcura(i);
    return;
  } else if (e.key === 'Escape') {
    fecharResultadosProcura();
    return;
  } else {
    return;
  }
  itens.forEach((it, i) => it.classList.toggle('destacado', i === indiceDestacado));
  itens[indiceDestacado].scrollIntoView({ block: 'nearest' });
});

// fechar dropdown ao clicar fora
document.addEventListener('click', (e) => {
  if (!e.target.closest('.procurar')) fecharResultadosProcura();
});

// ---------------------------------------------------------------------
//  PARES SUGERIDOS — render + handlers
// ---------------------------------------------------------------------
function renderParesSugeridos() {
  const div = document.getElementById('pares-lista');
  if (!div) return;

  div.innerHTML = PARES_SUGERIDOS.map((par, i) => {
    const a = par.intervencionado;
    const b = par.pristino;
    return `
      <div class="par" data-par="${i}">
        <button class="par-chip par-chip-interv"
                data-lat="${a.lat}" data-lon="${a.lon}"
                title="${a.porque}">${a.nome}</button>
        <span class="par-vs">vs</span>
        <button class="par-chip par-chip-prist"
                data-lat="${b.lat}" data-lon="${b.lon}"
                title="${b.porque}">${b.nome}</button>
      </div>
    `;
  }).join('');

  div.querySelectorAll('.par-chip').forEach(chip => {
    chip.addEventListener('click', () => {
      const lat = parseFloat(chip.dataset.lat);
      const lon = parseFloat(chip.dataset.lon);
      selecionarPonto(lat, lon, { flyTo: true });
    });
  });
}

// ---------------------------------------------------------------------
//  TOGGLE DE TEMA (claro / escuro)
// ---------------------------------------------------------------------
const ICONE_LUA = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';
const ICONE_SOL = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/></svg>';

function actualizarIconeTema() {
  const btn = document.getElementById('toggle-tema');
  if (!btn) return;
  const ehEscuro = document.documentElement.getAttribute('data-tema') === 'escuro';
  btn.innerHTML = ehEscuro ? ICONE_SOL : ICONE_LUA;
  btn.title = ehEscuro ? 'Mudar para tema claro' : 'Mudar para tema escuro';
}

document.getElementById('toggle-tema')?.addEventListener('click', () => {
  const ehEscuro = document.documentElement.getAttribute('data-tema') === 'escuro';
  if (ehEscuro) {
    document.documentElement.removeAttribute('data-tema');
    try { localStorage.setItem('curiosoil-tema', 'claro'); } catch (_) {}
  } else {
    document.documentElement.setAttribute('data-tema', 'escuro');
    try { localStorage.setItem('curiosoil-tema', 'escuro'); } catch (_) {}
  }
  actualizarIconeTema();
});

// ---------------------------------------------------------------------
//  ARRANQUE
// ---------------------------------------------------------------------
actualizarIconeTema();
renderParesSugeridos();
montarControlos();
renderEstado();   // pinta um shell vazio para a amostra A
