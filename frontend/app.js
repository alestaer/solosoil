
const API_URL = "http://localhost:5000";
 
// ===== MAPA =====
const mapa = L.map('mapa').setView([40, 0], 2);
L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '© OpenStreetMap'
}).addTo(mapa);

let marcador = null;
let ultimasNotas = null;

// ===== SESSÃO DE ÁUDIO (recriável) =====
let sessao = null;

function criarSessao(reverbMix = 0.2) {
  const saida = new Tone.Gain(1).toDestination();
  const reverb = new Tone.Reverb({ decay: 3.5, wet: reverbMix }).connect(saida);
  const piano = new Tone.Sampler({
    urls: {
      A2: "A2.mp3", A3: "A3.mp3", A4: "A4.mp3", A5: "A5.mp3",
      C3: "C3.mp3", C4: "C4.mp3", C5: "C5.mp3",
    },
    release: 1.5,
    baseUrl: "https://tonejs.github.io/audio/salamander/",
  }).connect(reverb);
  return { piano, reverb, saida };
}

function destruirSessao() {
  if (!sessao) return;
  try { sessao.saida.gain.cancelScheduledValues(0); } catch (_) {}
  try { sessao.saida.gain.setValueAtTime(0, Tone.now()); } catch (_) {}
  try { sessao.piano.releaseAll(); } catch (_) {}
  const velha = sessao;
  setTimeout(() => {
    try { velha.piano.dispose();  } catch (_) {}
    try { velha.reverb.dispose(); } catch (_) {}
    try { velha.saida.dispose();  } catch (_) {}
  }, 50);
  sessao = null;
}

// ===== PARTITURA =====
function desenharPartitura(melodia, baixo) {
  const div = document.getElementById('partitura');
  div.innerHTML = '';

  const VF = Vex.Flow;
  const limiteMel = Math.min(melodia.length, 16);
  const limiteBaixo = Math.min((baixo || []).length, 32);
  const largura = Math.max(700, Math.max(limiteMel, limiteBaixo) * 50);

  const renderer = new VF.Renderer(div, VF.Renderer.Backends.SVG);
  renderer.resize(largura, 280);
  const context = renderer.getContext();

  // Pauta superior — clave de sol
  const staveSup = new VF.Stave(10, 20, largura - 20);
  staveSup.addClef('treble').setContext(context).draw();

  // Pauta inferior — clave de fá
  const staveInf = new VF.Stave(10, 140, largura - 20);
  staveInf.addClef('bass').setContext(context).draw();

  // Função auxiliar para converter as notas para VexFlow
  function paraVexFlow(notas, limite, clef) {
    return notas.slice(0, limite).map(n => {
      const match = n.nota.match(/^([A-G]#?)(\d+)$/);
      if (!match) return null;
      const chave = `${match[1].toLowerCase()}/${match[2]}`;
      let dur;
      if      (n.duracao >= 2)   dur = 'h';
      else if (n.duracao >= 1)   dur = 'q';
      else if (n.duracao >= 0.5) dur = '8';
      else                       dur = '16';

      const nota = new VF.StaveNote({ clef, keys: [chave], duration: dur });
      if (match[1].includes('#')) nota.addModifier(new VF.Accidental('#'), 0);
      return nota;
    }).filter(n => n !== null);
  }

  const notasMel  = paraVexFlow(melodia, limiteMel, 'treble');
  const notasBaixo = paraVexFlow(baixo || [], limiteBaixo, 'bass');

  try {
    if (notasMel.length > 0)
      VF.Formatter.FormatAndDraw(context, staveSup, notasMel);
    if (notasBaixo.length > 0)
      VF.Formatter.FormatAndDraw(context, staveInf, notasBaixo);
  } catch (e) {
    console.error('Erro VexFlow:', e);
    div.innerHTML = '<p style="color:#999">Não foi possível desenhar a pauta.</p>';
  }
}

// ===== RENDERIZAR PARÂMETROS DO SOLO =====
// Cada parâmetro com uma barra normalizada ao seu intervalo típico
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

function renderParam(chave, valor) {
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

function renderSolo(solo) {
  const ordem = ['pH', 'carbono_organico_g_kg', 'azoto_g_kg',
                 'areia_pct', 'argila_pct', 'limo_pct',
                 'cec', 'densidade_g_cm3', 'pedregoso_pct'];
  let html = ordem.map(k => renderParam(k, solo[k])).join('');

  // Textura
  if (solo.textura && solo.textura !== 'indefinida') {
    html += `<div class="param"><span class="nome">Textura</span>
             <span style="grid-column: 2 / 4; font-family: ui-monospace, monospace; font-size:12px;">
             ${solo.textura}</span></div>`;
  }

  // Retenção de água
  if (solo.retencao_agua) {
    const a = solo.retencao_agua;
    html += `<div style="margin-top:10px; padding-top:10px; border-top:1px solid #eee;">
      <div class="param">
        <span class="nome">Água disponível</span>
        <div class="barra"><div style="width:${Math.min(100, a.awc_pct * 4)}%; background:#3a7a8a"></div></div>
        <span class="valor">${a.awc_pct}%</span>
      </div>
      <div class="param">
        <span class="nome">Cap. de campo</span>
        <div class="barra"><div style="width:${a.capacidade_campo_pct}%; background:#3a7a8a"></div></div>
        <span class="valor">${a.capacidade_campo_pct}%</span>
      </div>
      <div class="param">
        <span class="nome">Ponto de murcha</span>
        <div class="barra"><div style="width:${a.ponto_murcha_pct}%; background:#3a7a8a"></div></div>
        <span class="valor">${a.ponto_murcha_pct}%</span>
      </div>
      <div style="font-size:12px; color:#666; margin-top:4px;">Retenção: <b>${a.classe}</b></div>
    </div>`;
  }

  document.getElementById('solo-params').innerHTML = html;
}

function renderMusica(m) {
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
  document.getElementById('musica-params').innerHTML =
    '<div class="musica-grid">' +
    linhas.map(([k, v]) => `<span class="k">${k}</span><span class="v">${v}</span>`).join('') +
    '</div>';
}
// ===== RENDERIZAR PRESSÃO HUMANA =====
const ROTULOS_CAT = {
  mina: "⛏ Minas / pedreiras",
  industria_pesada: "🏭 Indústria pesada (química, refinaria, fundição)",
  central_termica: "🔥 Centrais térmicas fósseis",
  aterro: "🗑 Aterros",
  industria_geral: "🏗 Zonas industriais",
  agricultura_intensiva: "🌾 Agricultura intensiva",
};

let camadaPontosOSM = null;

function renderPressaoHumana(ph) {
  const div = document.getElementById('pressao-humana');
  if (!ph || !ph.disponivel) {
    div.innerHTML = '<p class="placeholder">Sem dados de pressão humana ' +
      (ph?.erro ? '(falha de ligação ao OSM)' : '') + '</p>';
    return;
  }

  const total = Object.values(ph.contagens).reduce((s, n) => s + n, 0);
  if (total === 0) {
    div.innerHTML = `<p style="color:#5a7a3a; font-size:13px;">
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
      <div style="font-size:12px; color:#666; margin-bottom:8px;">
        Num raio de ${ph.raio_km} km — score: <b>${ph.score}</b>
      </div>
      ${linhas}`;
  }

  // Desenhar pontos no mapa
  if (camadaPontosOSM) mapa.removeLayer(camadaPontosOSM);
  camadaPontosOSM = L.layerGroup();
  const cores = {
    mina: "#8b2c00",
    industria_pesada: "#c4341a",
    central_termica: "#7a5500",
    aterro: "#5a4a3a",
    industria_geral: "#999999",
    agricultura_intensiva: "#a8a04a",
  };
  ph.elementos.forEach(el => {
    L.circleMarker([el.lat, el.lon], {
      radius: 6,
      color: cores[el.categoria] || "#666",
      fillColor: cores[el.categoria] || "#666",
      fillOpacity: 0.7,
      weight: 1,
    }).bindPopup(`<b>${el.nome}</b><br>${ROTULOS_CAT[el.categoria] || el.categoria}`)
      .addTo(camadaPontosOSM);
  });
  camadaPontosOSM.addTo(mapa);
}
function renderAvisos(solo) {
  const div = document.getElementById('avisos-bloco');
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

// ===== TOCAR =====
async function tocarNotas(melodia, baixo, reverbMix = 0.2) {
  await Tone.start();
  destruirSessao();
  sessao = criarSessao(reverbMix);
  const { piano } = sessao;

  await Tone.loaded();

  const agora = Tone.now() + 0.15;
  melodia.forEach(n => {
    piano.triggerAttackRelease(n.nota, n.duracao, agora + n.inicio, n.velocity);
  });
  if (baixo) {
    baixo.forEach(n => {
      piano.triggerAttackRelease(n.nota, n.duracao, agora + n.inicio, n.velocity);
    });
  }
}

// ===== LIMPAR PAINÉIS =====
function limparPaineis() {
  document.getElementById('solo-params').innerHTML =
    '<p class="placeholder">Ainda sem leitura.</p>';
  document.getElementById('musica-params').innerHTML =
    '<p class="placeholder">Ainda sem leitura.</p>';
  document.getElementById('pressao-humana').innerHTML =
    '<p class="placeholder">Ainda sem leitura.</p>';
  document.getElementById('avisos-bloco').innerHTML = '';
  document.getElementById('partitura').innerHTML = '';

  // remover pontos OSM do mapa
  if (camadaPontosOSM) {
    mapa.removeLayer(camadaPontosOSM);
    camadaPontosOSM = null;
  }

  // invalidar a sessão de áudio anterior
  ultimasNotas = null;
  document.getElementById('btn-tocar').disabled = true;
}

// ===== CLIQUE NO MAPA =====
mapa.on('click', async (e) => {
  const { lat, lng } = e.latlng;

  destruirSessao();
  limparPaineis();                         // <-- limpa tudo antes de pedir novos dados

  document.getElementById('coord').textContent =
    `Lat: ${lat.toFixed(3)}   Lon: ${lng.toFixed(3)}`;
  document.getElementById('estado').textContent = 'A buscar dados do solo...';
  document.getElementById('estado').classList.add('carregando');

  if (marcador) mapa.removeLayer(marcador);
  marcador = L.marker([lat, lng]).addTo(mapa);

  try {
    const r = await fetch(`${API_URL}/gerar?lat=${lat}&lon=${lng}`);
    const dados = await r.json();

    document.getElementById('estado').classList.remove('carregando');

    if (dados.erro) {
      document.getElementById('estado').textContent =
        dados.erro + (dados.dica ? ' — ' + dados.dica : '');
      // painéis já foram limpos no início; nada mais a fazer
      return;
    }

    renderSolo(dados.solo);
    renderMusica(dados.musica);
    renderPressaoHumana(dados.pressao_humana);
    renderAvisos(dados.solo);
    desenharPartitura(dados.melodia, dados.baixo);


    document.getElementById('estado').textContent = 'A tocar...';
    ultimasNotas = dados.notas;
    document.getElementById('btn-tocar').disabled = false;

    await tocarNotas(dados.melodia, dados.baixo, dados.musica.reverb_mix);

    const duracaoTotal = dados.musica.duracao_seg;
    setTimeout(() => {
      document.getElementById('estado').textContent = 'Pronto. Clica noutro ponto ou repete.';
    }, duracaoTotal * 1000);

  } catch (err) {
    document.getElementById('estado').classList.remove('carregando');
    document.getElementById('estado').textContent = 'Erro: ' + err.message;
  }
});

// ===== BOTÃO "Tocar de novo" =====
document.getElementById('btn-tocar').addEventListener('click', () => {
  if (ultimasNotas) {
    tocarNotas(ultimasNotas.melodia, ultimasNotas.baixo,
               sessao?.reverb?.wet?.value ?? 0.2);
  }
});