

// ── Contador animado (números por trás) ──
function animarContador(el) {
  const target  = parseInt(el.dataset.target || '0');
  const prefix  = el.dataset.prefix || '';
  const suffix  = el.dataset.suffix || '';
  const dur     = 2000;
  const start   = performance.now();

  function step(now) {
    const p = Math.min((now - start) / dur, 1);
    const ease = 1 - Math.pow(1 - p, 3);
    const val = Math.floor(ease * target);
    el.textContent = prefix + val.toLocaleString('pt-BR') + suffix;
    if (p < 1) requestAnimationFrame(step);
    else el.textContent = prefix + target.toLocaleString('pt-BR') + suffix;
  }
  requestAnimationFrame(step);
}

const obsContadores = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (e.isIntersecting) {
      animarContador(e.target);
      obsContadores.unobserve(e.target);
    }
  });
}, { threshold: 0.4 });

document.querySelectorAll('.numero-val[data-target]').forEach(el => obsContadores.observe(el));

// ── Destaques ao vivo (puxa da API) ──
(async function carregarDestaques() {
  const API = 'http://localhost:8001';
  try {
    // Hospital com mais internações
    const rH = await fetch(`${API}/api/custo-por-hospital?limite=1`);
    const dH = await rH.json();
    if (dH.length) {
      document.getElementById('dest-hospital').textContent =
        dH[0].hospital + ' — ' + dH[0].internacoes.toLocaleString('pt-BR') + ' internações';
    }
  } catch(e) {
    document.getElementById('dest-hospital').textContent = 'API offline — rode o backend';
  }

  try {
    // Município com maior custo
    const rM = await fetch(`${API}/api/analise/municipios-top?limite=1`);
    const dM = await rM.json();
    if (dM.length) {
      const custo = (dM[0].custo/1e6).toLocaleString('pt-BR',{minimumFractionDigits:1});
      document.getElementById('dest-municipio').textContent =
        dM[0].municipio + ' — R$ ' + custo + ' mi';
    }
  } catch(e) {
    document.getElementById('dest-municipio').textContent = 'API offline — rode o backend';
  }

  try {
    // Mês de pico (mais internações)
    const rC = await fetch(`${API}/api/custo-por-mes`);
    const dC = await rC.json();
    if (dC.length) {
      const pico = dC.reduce((a, b) => b.internacoes > a.internacoes ? b : a);
      const comp = pico.competencia;
      const mes = comp ? comp.slice(4,6) + '/' + comp.slice(0,4) : '—';
      document.getElementById('dest-mes').textContent =
        mes + ' — ' + pico.internacoes.toLocaleString('pt-BR') + ' internações';
    }
  } catch(e) {
    document.getElementById('dest-mes').textContent = 'API offline — rode o backend';
  }
})();

// ── FAQ accordion ──
function toggleFaq(btn) {
  const item = btn.closest('.faq-item');
  const isOpen = item.classList.contains('open');
  document.querySelectorAll('.faq-item.open').forEach(i => i.classList.remove('open'));
  if (!isOpen) item.classList.add('open');
}