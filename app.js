// =====================================================================
//  CuriouSoil — frontend
//  Stack: Leaflet · Tone.js · VexFlow · vanilla JS
//  Features: comparação A/B + pesquisa de localização + pares curados
// =====================================================================

const API_URL = "https://solosoil.onrender.com";

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
  A: { pausado: false, notas: [], reverbMix: 0.2, tStart: 0, tPausa: 0, duracaoTotal: 0 },
  B: { pausado: false, notas: [], reverbMix: 0.2, tStart: 0, tPausa: 0, duracaoTotal: 0 },
};

function resetSlotState(slot) {
  slotState[slot] = { pausado: false, notas: [], reverbMix: 0.2, tStart: 0, tPausa: 0, duracaoTotal: 0 };
}

async function garantirToneIniciado() {
  if (!toneIniciado) {
    await Tone.start();
    toneIniciado = true;
  }
}

function criarSessao(reverbMix = 0.2) {
  const saida  = new Tone.Gain(1).toDestination();
  const reverb = new Tone.Reverb({ decay: 3.5, wet: reverbMix }).connect(saida);
  const piano  = new Tone.Sampler({
    urls: {
      A2: "A2.mp3", A3: "A3.mp3", A4: "A4.mp3", A5: "A5.mp3",
      C3: "C3.mp3", C4: "C4.mp3", C5: "C5.mp3",
    },
    release: 1.5,
    baseUrl: "https://tonejs.github.io/audio/salamander/",
  }).connect(reverb);
  return { piano, reverb, saida };
}

function destruirSessao(slot) {
  const s = sessoes[slot];
  if (!s) return;
  try { s.saida.gain.cancelScheduledValues(0); } catch (_) {}
  try { s.saida.gain.setValueAtTime(0, Tone.now()); } catch (_) {}
  try { s.piano.releaseAll(); } catch (_) {}
  setTimeout(() => {
    try { s.piano.dispose();  } catch (_) {}
    try { s.reverb.dispose(); } catch (_) {}
    try { s.saida.dispose();  } catch (_) {}
  }, 90);
  sessoes[slot] = null;
}

function silenciarOutras(slotActual) {
  ['A', 'B'].forEach(outro => {
    if (outro === slotActual) return;
    const s = sessoes[outro];
    if (!s) return;
    // fade-out do gain (~80ms)
    try {
      const t = Tone.now();
      s.saida.gain.cancelScheduledValues(t);
      s.saida.gain.setTargetAtTime(0, t, 0.08);
    } catch (_) {}
    try { s.piano.releaseAll(); } catch (_) {}
    // só marcar como "pausado" se estava efectivamente a tocar
    if (slotAtivo === outro) {
      slotState[outro].pausado = true;
      slotState[outro].tPausa = Tone.now();
      tokensTimer[outro]++;   // invalida o timer de fim pendente
      slotAtivo = null;
      setEstadoMsg(outro, 'Pausado.', false);
    }
  });
}

// Toca notas num slot. Silencia o outro, agenda as notas, guarda estado por slot.
// Devolve a duração total (segundos).
async function tocarNotas(slot, notas, reverbMix = 0.2) {
  await garantirToneIniciado();
  silenciarOutras(slot);

  if (!sessoes[slot]) sessoes[slot] = criarSessao(reverbMix);
  const sess = sessoes[slot];
  try { sess.piano.releaseAll(); } catch (_) {}
  try {
    const t = Tone.now();
    sess.saida.gain.cancelScheduledValues(t);
    sess.saida.gain.setTargetAtTime(1, t, 0.02);
  } catch (_) {}
  try {
    sess.reverb.wet.setTargetAtTime(reverbMix, Tone.now(), 0.02);
  } catch (_) {}

  await Tone.loaded();
  const tStart = Tone.now() + 0.15;
  const duracaoTotal = notas.reduce((acc, n) => Math.max(acc, n.inicio + n.duracao), 0);
  notas.forEach(n => {
    sess.piano.triggerAttackRelease(n.nota, n.duracao, tStart + n.inicio, n.velocity);
  });

  slotState[slot] = { pausado: false, notas, reverbMix, tStart, tPausa: 0, duracaoTotal };
  slotAtivo = slot;
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
      try { sess.piano.releaseAll(); } catch (_) {}
    }
    st.pausado = true;
    st.tPausa = Tone.now();
    tokensTimer[slot]++;   // invalida o timer de fim pendente
    slotAtivo = null;
    setEstadoMsg(slot, 'Pausado.', false);
    actualizarBotoesPlay();
    return;
  }

  // --- (2) Slot pausado → retomar ---
  if (st.pausado && st.notas.length > 0) {
    const elapsed = st.tPausa - st.tStart;
    const notasRestantes = st.notas.filter(n => n.inicio > elapsed);

    if (notasRestantes.length > 0) {
      // Re-agendar notas que ainda não tinham tocado
      if (!sessoes[slot]) sessoes[slot] = criarSessao(st.reverbMix);
      const sess = sessoes[slot];
      silenciarOutras(slot);
      try {
        const t = Tone.now();
        sess.saida.gain.cancelScheduledValues(t);
        sess.saida.gain.setTargetAtTime(1, t, 0.02);
      } catch (_) {}
      await Tone.loaded();
      const tNewStart = Tone.now() + 0.15;
      notasRestantes.forEach(n => {
        sess.piano.triggerAttackRelease(
          n.nota, n.duracao, tNewStart + (n.inicio - elapsed), n.velocity
        );
      });
      const novaDuracao = notasRestantes.reduce(
        (acc, n) => Math.max(acc, (n.inicio - elapsed) + n.duracao), 0
      );
      // actualizar estado para futuras pausas nesta sessão
      st.pausado = false;
      st.tStart  = tNewStart - elapsed;  // base equivalente para re-pausar correctamente
      st.tPausa  = 0;
      st.duracaoTotal = novaDuracao;
      slotAtivo = slot;

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
      const duracao = await tocarNotas(slot, st.notas, st.reverbMix);
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
  const duracao = await tocarNotas(slot, dados.notas, dados.musica.reverb_mix);
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

function renderMusica(slot, m) {
  const div = document.getElementById(`musica-params-${slot.toLowerCase()}`);
  if (!div) return;
  const linhas = [
    ['Escala',         m.escala],
    ['Tónica',         m.tonica],
    ['Andamento',      m.bpm + ' BPM'],
    ['Nº de notas',    m.n_notas],
    ['Sustain',        '×' + m.sustain],
    ['Reverb',         (m.reverb_mix * 100).toFixed(0) + '%'],
    ['Prob. silêncio', (m.prob_silencio * 100).toFixed(0) + '%'],
    ['Prob. dissonância', (m.prob_dissonancia * 100).toFixed(0) + '%'],
    ['Prob. acorde',   (m.prob_acorde * 100).toFixed(0) + '%'],
  ];
  div.innerHTML =
    '<div class="musica-grid">' +
    linhas.map(([k, v]) => `<span class="k">${k}</span><span class="v">${v}</span>`).join('') +
    '</div>';
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

function desenharPartitura(slot, notas) {
  const div = document.getElementById(`partitura-${slot.toLowerCase()}`);
  if (!div) return;
  div.innerHTML = '';
  const VF = Vex.Flow;
  const limite = Math.min(notas.length, 12);
  const largura = Math.max(540, limite * 50);

  const renderer = new VF.Renderer(div, VF.Renderer.Backends.SVG);
  renderer.resize(largura, 180);
  const context = renderer.getContext();

  const stave = new VF.Stave(10, 40, largura - 20);
  stave.addClef('treble').setContext(context).draw();

  const notasVF = notas.slice(0, limite).map(n => {
    const match = n.nota.match(/^([A-G]#?)(\d+)$/);
    if (!match) return null;
    const chave = `${match[1].toLowerCase()}/${match[2]}`;
    const duracaoVF = n.duracao >= 1 ? 'q' : (n.duracao >= 0.5 ? '8' : '16');
    const nota = new VF.StaveNote({ clef: 'treble', keys: [chave], duration: duracaoVF });
    if (match[1].includes('#')) nota.addModifier(new VF.Accidental('#'), 0);
    return nota;
  }).filter(n => n !== null);

  try {
    VF.Formatter.FormatAndDraw(context, stave, notasVF);
  } catch (e) {
    console.error('Erro VexFlow:', e);
    div.innerHTML = '<p class="placeholder">Não foi possível desenhar a pauta.</p>';
  }
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

      <div class="caixa larga partitura">
        <h3>Partitura</h3>
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
        renderPressaoHumana(slot, dados.dados.pressao_humana);
        renderAvisos(slot, dados.dados.solo);
        desenharPartitura(slot, dados.dados.notas);
        const btn = document.getElementById(`btn-tocar-${s}`);
        if (btn) btn.disabled = false;
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
    const r = await fetch(`${API_URL}/gerar?lat=${lat}&lon=${lon}`);
    const dados = await r.json();

    if (dados.erro) {
      setEstadoMsg(slot, dados.erro + (dados.dica ? ' — ' + dados.dica : ''), false);
      return;
    }

    estado[slot.toLowerCase()].dados = dados;
    setEstadoMsg(slot, 'A tocar...', false);
    renderEstado();

    const duracao = await tocarNotas(slot, dados.notas, dados.musica.reverb_mix);
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
renderEstado();   // pinta um shell vazio para a amostra A
