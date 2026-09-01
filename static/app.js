let currentChat = null;
let editingChatId = null;
let openMenuId = null;
let menuEl = null;
let thinkingInterval = null;

// ---------- Autenticacao ----------
// Le o token da URL (?token=xxx) quando vem da landing, salva no sessionStorage
(function() {
  const params = new URLSearchParams(window.location.search);
  const tokenUrl = params.get("token");
  if (tokenUrl) {
    sessionStorage.setItem("dm_token", tokenUrl);
    // limpa o token da URL sem recarregar a pagina
    const url = new URL(window.location.href);
    url.searchParams.delete("token");
    window.history.replaceState({}, "", url.toString());
  }
})();

function getToken() {
  return sessionStorage.getItem("dm_token") || "";
}

function authHeaders() {
  const token = getToken();
  return token ? { "Authorization": `Bearer ${token}` } : {};
}

function authFetch(url, opts = {}) {
  opts.headers = { ...(opts.headers || {}), ...authHeaders() };
  return fetch(url, opts);
}

const chatWindow = document.getElementById("chat-window");
const chatTitle = document.getElementById("chat-title");
const emptyState = document.getElementById("empty-state");

function toggleEmptyState() {
  const hasMsgs = chatWindow.querySelector(".msg-row") !== null;
  emptyState.style.display = hasMsgs ? "none" : "flex";
}

function resolvePlotUrl(path) {
  return path.startsWith("/plots/") ? path : `/plots/${path.split("/").pop()}`;
}

// ---------- Menu de tres pontos (renomear / apagar) ----------

function closeMenu() {
  if (menuEl) { menuEl.remove(); menuEl = null; }
  openMenuId = null;
}

function openMenu(chat, btnEl) {
  closeMenu();
  openMenuId = chat.id;

  const rect = btnEl.getBoundingClientRect();
  const menu = document.createElement("div");
  menu.className = "tab-dropdown";
  menu.style.top = `${rect.bottom + 4}px`;
  menu.style.left = `${Math.max(8, rect.right - 170)}px`;
  menu.onclick = (e) => e.stopPropagation();

  const renameBtn = document.createElement("button");
  renameBtn.textContent = "✎ Mudar o nome";
  renameBtn.onclick = (e) => {
    e.stopPropagation();
    editingChatId = chat.id;
    closeMenu();
    loadChats();
  };
  menu.appendChild(renameBtn);

  const delBtn = document.createElement("button");
  delBtn.className = "danger";
  delBtn.textContent = "🗑 Apagar";
  delBtn.onclick = async (e) => {
    e.stopPropagation();
    try {
      const res = await authFetch(`/chats/${chat.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (currentChat === chat.id) currentChat = null;
    } catch (err) {
      alert("Nao foi possivel excluir a aba. Verifique se o servidor esta rodando.");
      console.error(err);
      return;
    }
    closeMenu();
    loadChats();
  };
  menu.appendChild(delBtn);

  document.body.appendChild(menu);
  menuEl = menu;
}

document.addEventListener("click", () => {
  if (menuEl) { closeMenu(); loadChats(); }
});

// ---------- Abas ----------

async function loadChats(justCreatedId) {
  const res = await authFetch("/chats");
  const chats = await res.json();
  const tabsDiv = document.getElementById("tabs");
  tabsDiv.innerHTML = "";

  chats.forEach(chat => {
    const item = document.createElement("div");
    item.className = "tab-item" + (chat.id === currentChat ? " active" : "") + (openMenuId === chat.id ? " menu-open" : "");

    if (editingChatId === chat.id) {
      const input = document.createElement("input");
      input.className = "tab-name-input";
      input.value = chat.name;
      input.maxLength = 40;

      const save = async () => {
        const novoNome = input.value.trim() || chat.name;
        editingChatId = null;
        await authFetch(`/chats/${chat.id}`, { method: "PUT", body: new URLSearchParams({ name: novoNome }) });
        loadChats();
      };
      input.onblur = save;
      input.onkeydown = (e) => {
        if (e.key === "Enter") input.blur();
        if (e.key === "Escape") { editingChatId = null; loadChats(); }
      };
      item.appendChild(input);
      tabsDiv.appendChild(item);
      setTimeout(() => { input.focus(); input.select(); }, 0);
      return;
    }

    const btn = document.createElement("button");
    btn.className = "tab";
    btn.onclick = () => selectChat(chat.id);

    const name = document.createElement("span");
    name.className = "tab-name";
    name.textContent = chat.name;
    btn.appendChild(name);
    item.appendChild(btn);

    const menuBtn = document.createElement("button");
    menuBtn.className = "tab-menu-btn";
    menuBtn.textContent = "\u22EF";
    menuBtn.onclick = (e) => {
      e.stopPropagation();
      if (openMenuId === chat.id) {
        closeMenu();
        loadChats();
      } else {
        openMenu(chat, menuBtn);
        item.classList.add("menu-open");
      }
    };
    item.appendChild(menuBtn);

    tabsDiv.appendChild(item);
  });

  document.getElementById("new-tab").disabled = chats.length >= 3;

  if (currentChat && !chats.find(c => c.id === currentChat)) currentChat = null;
  if (justCreatedId) {
    currentChat = justCreatedId;
    selectChat(justCreatedId);
  } else if (!currentChat && chats.length) {
    selectChat(chats[0].id);
  } else if (!chats.length) {
    chatTitle.textContent = "Selecione ou crie uma conversa";
    chatWindow.innerHTML = "";
    chatWindow.appendChild(emptyState);
    toggleEmptyState();
  }
}

async function selectChat(id) {
  currentChat = id;
  const chats = await (await authFetch("/chats")).json();
  const chat = chats.find(c => c.id === id);
  chatTitle.textContent = chat ? chat.name : "Conversa";
  loadChats();

  const res = await authFetch(`/chats/${id}/history`);
  const history = await res.json();
  chatWindow.innerHTML = "";
  chatWindow.appendChild(emptyState);
  history.forEach(msg => renderMessage(msg, false));
  toggleEmptyState();
  atualizarFonte();
}

// ---------- Fonte de dados (Oracle x CSV) ----------

async function atualizarFonte() {
  if (!currentChat) return;
  try {
    const resp = await authFetch(`/chats/${currentChat}/fonte`);
    const info = await resp.json();
    document.querySelectorAll("#fonte-switch button").forEach(btn => {
      const tipo = btn.dataset.fonte;
      btn.classList.toggle("active", tipo === info.tipo);
      btn.disabled = (tipo === "csv" && !info.csv_disponivel);
    });
  } catch (err) {
    console.error("Falha ao ler a fonte de dados:", err);
  }
}

document.querySelectorAll("#fonte-switch button").forEach(btn => {
  btn.addEventListener("click", async () => {
    if (!currentChat) return alert("Selecione uma aba primeiro.");
    if (btn.disabled || btn.classList.contains("active")) return;

    const tipo = btn.dataset.fonte;
    const textoOriginal = btn.textContent;
    btn.textContent = "...";
    try {
      const resp = await authFetch(`/chats/${currentChat}/fonte`, {
        method: "POST",
        body: new URLSearchParams({ tipo }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.detail || "Falha ao trocar a fonte.");
      alert(`Fonte alterada para ${tipo === "oracle" ? "Oracle" : "CSV"}: ${data.linhas} linhas.`);
    } catch (err) {
      alert(err.message);
    } finally {
      btn.textContent = textoOriginal;
      atualizarFonte();
    }
  });
});

// ---------- Grafico interativo (Chart.js) ----------

function renderInteractiveChart(bubble, payload) {
  const wrap = document.createElement("div");
  wrap.className = "chart-wrap chart-wrap-interactive";

  if (typeof Chart === "undefined") {
    wrap.innerHTML =
      '<div class="chart-error">Nao foi possivel carregar a biblioteca do grafico (Chart.js). ' +
      "Verifique sua conexao com a internet e recarregue a pagina.</div>";
    bubble.appendChild(wrap);
    console.error("Chart.js nao carregou: verifique a tag <script> do CDN em index.html e a conexao com a internet.");
    return;
  }

  const canvasBox = document.createElement("div");
  canvasBox.className = "chart-canvas-box";
  const canvas = document.createElement("canvas");
  canvasBox.appendChild(canvas);

  const tableBox = document.createElement("div");
  tableBox.className = "chart-table-box";
  tableBox.style.display = "none";

  const table = document.createElement("table");
  table.className = "data-table";
  const thead = document.createElement("thead");
  thead.innerHTML = `<tr><th>Categoria</th><th>${payload.value_label || "Valor"}</th></tr>`;
  const tbody = document.createElement("tbody");
  payload.labels.forEach((label, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${label}</td><td>${payload.values[i]}</td>`;
    tbody.appendChild(tr);
  });
  table.appendChild(thead);
  table.appendChild(tbody);
  tableBox.appendChild(table);

  const toggle = document.createElement("div");
  toggle.className = "chart-toggle";
  const chartBtn = document.createElement("button");
  chartBtn.textContent = "📈";
  chartBtn.title = "Ver grafico";
  chartBtn.className = "active";
  const tableBtn = document.createElement("button");
  tableBtn.textContent = "▤";
  tableBtn.title = "Ver tabela";

  chartBtn.onclick = (e) => {
    e.stopPropagation();
    canvasBox.style.display = "block";
    tableBox.style.display = "none";
    chartBtn.classList.add("active");
    tableBtn.classList.remove("active");
  };
  tableBtn.onclick = (e) => {
    e.stopPropagation();
    canvasBox.style.display = "none";
    tableBox.style.display = "block";
    tableBtn.classList.add("active");
    chartBtn.classList.remove("active");
  };
  toggle.appendChild(chartBtn);
  toggle.appendChild(tableBtn);

  wrap.appendChild(toggle);
  if (payload.title) {
    const h = document.createElement("div");
    h.className = "chart-title";
    h.textContent = payload.title;
    wrap.appendChild(h);
  }
  if (payload.subtitle) {
    const s = document.createElement("div");
    s.className = "chart-subtitle";
    s.textContent = payload.subtitle;
    wrap.appendChild(s);
  }
  wrap.appendChild(canvasBox);
  wrap.appendChild(tableBox);
  bubble.appendChild(wrap);

  new Chart(canvas, {
    type: "bar",
    data: {
      labels: payload.labels,
      datasets: [{
        label: payload.value_label || "Valor",
        data: payload.values,
        backgroundColor: "#4C8BF5",
        borderRadius: 4,
        maxBarThickness: 46,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#111110",
          titleColor: "#fff",
          bodyColor: "#fff",
          padding: 10,
          cornerRadius: 8,
          displayColors: true,
        },
      },
      scales: {
        x: { ticks: { color: "#9a9587" }, grid: { display: false } },
        y: {
          ticks: { color: "#9a9587" },
          grid: { color: "#3a3935" },
          beginAtZero: true,
        },
      },
    },
  });
}

// ---------- Mensagens ----------

function stripMarkdown(text) {
  return text
    .replace(/^#{1,6}\s*/gm, "")          // ### titulos
    .replace(/\*\*(.*?)\*\*/g, "$1")      // **negrito**
    .replace(/\*(.*?)\*/g, "$1")          // *italico*
    .replace(/^[-*]\s+/gm, "")            // - item de lista
    .replace(/`{1,3}([^`]*)`{1,3}/g, "$1"); // `codigo`
}

function renderMessage(msg, animate) {
  const row = document.createElement("div");
  row.className = "msg-row " + msg.role;

  const bubble = document.createElement("div");
  bubble.className = "msg " + msg.role;
  row.appendChild(bubble);
  chatWindow.appendChild(row);
  toggleEmptyState();

  const plotUrl = msg.plot_path || msg.plot_url;
  const tableUrl = msg.table_path || msg.table_url;
  const rawChartData = msg.chart_data;
  const content = msg.role === "assistant" ? stripMarkdown(msg.content || "") : msg.content;

  const finish = () => {
    if (rawChartData) {
      try {
        const payload = typeof rawChartData === "string" ? JSON.parse(rawChartData) : rawChartData;
        renderInteractiveChart(bubble, payload);
      } catch (err) {
        console.error("Erro ao renderizar grafico interativo:", err);
      }
    } else if (plotUrl) {
      const wrap = document.createElement("div");
      wrap.className = "chart-wrap";

      const img = document.createElement("img");
      img.src = resolvePlotUrl(plotUrl);
      wrap.appendChild(img);

      if (tableUrl) {
        const toggle = document.createElement("div");
        toggle.className = "chart-toggle";

        const chartBtn = document.createElement("button");
        chartBtn.textContent = "📈";
        chartBtn.title = "Ver grafico";
        chartBtn.className = "active";

        const tableBtn = document.createElement("button");
        tableBtn.textContent = "▤";
        tableBtn.title = "Ver tabela";

        chartBtn.onclick = () => {
          img.src = resolvePlotUrl(plotUrl);
          chartBtn.classList.add("active");
          tableBtn.classList.remove("active");
        };
        tableBtn.onclick = () => {
          img.src = resolvePlotUrl(tableUrl);
          tableBtn.classList.add("active");
          chartBtn.classList.remove("active");
        };

        toggle.appendChild(chartBtn);
        toggle.appendChild(tableBtn);
        wrap.appendChild(toggle);
      }

      bubble.appendChild(wrap);
    }
    chatWindow.scrollTop = chatWindow.scrollHeight;
  };

  if (animate && msg.role === "assistant") {
    typeWriter(bubble, content, finish);
  } else {
    bubble.textContent = content;
    finish();
  }
}

function typeWriter(el, text, onDone) {
  const cursor = document.createElement("span");
  cursor.className = "typing-cursor";
  let i = 0;
  const chunk = 3;
  const interval = setInterval(() => {
    i += chunk;
    el.textContent = text.slice(0, i);
    el.appendChild(cursor);
    chatWindow.scrollTop = chatWindow.scrollHeight;
    if (i >= text.length) {
      clearInterval(interval);
      cursor.remove();
      onDone();
    }
  }, 12);
}

// ---------- Indicador "pensando" ----------

function showThinking() {
  const row = document.createElement("div");
  row.className = "msg-row assistant";
  row.id = "thinking-row";

  const bubble = document.createElement("div");
  bubble.className = "msg assistant thinking";

  const label = document.createElement("span");
  label.className = "thinking-label";
  const texts = ["Carregando...", "Pensando...", "Procurando..."];
  let idx = 0;
  label.textContent = texts[idx];

  const dots = document.createElement("span");
  dots.className = "thinking-dots";
  const d1 = document.createElement("span");
  const d2 = document.createElement("span");
  const d3 = document.createElement("span");
  dots.appendChild(d1);
  dots.appendChild(d2);
  dots.appendChild(d3);

  bubble.appendChild(label);
  bubble.appendChild(dots);
  row.appendChild(bubble);
  chatWindow.appendChild(row);
  chatWindow.scrollTop = chatWindow.scrollHeight;

  if (thinkingInterval) clearInterval(thinkingInterval);
  thinkingInterval = setInterval(() => {
    idx = (idx + 1) % texts.length;
    label.textContent = texts[idx];
  }, 4000);
}

function removeThinking() {
  if (thinkingInterval) { clearInterval(thinkingInterval); thinkingInterval = null; }
  const row = document.getElementById("thinking-row");
  if (row) row.remove();
}

// ---------- Acoes ----------

document.getElementById("new-tab").onclick = async () => {
  const chats = await (await authFetch("/chats")).json();
  const nome = `Nova conversa ${chats.length + 1}`;
  const res = await authFetch("/chats", { method: "POST", body: new URLSearchParams({ name: nome }) });
  if (res.ok) {
    const data = await res.json();
    loadChats(data.id);
  } else {
    const err = await res.json();
    alert(err.detail || "Nao foi possivel criar a aba.");
  }
};

document.getElementById("csv-file").onchange = async (e) => {
  if (!currentChat) return alert("Selecione uma aba primeiro.");
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  const res = await authFetch(`/chats/${currentChat}/upload_csv`, { method: "POST", body: fd });
  const data = await res.json();
  if (res.ok) alert(`CSV carregado: ${data.linhas} linhas. Colunas: ${data.colunas.join(", ")}`);
  else alert(data.detail || "Erro ao carregar CSV.");
  e.target.value = "";
  atualizarFonte();
};

document.getElementById("message-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!currentChat) return alert("Selecione ou crie uma aba primeiro.");
  const input = document.getElementById("question");
  const question = input.value.trim();
  if (!question) return;

  renderMessage({ role: "user", content: question }, false);
  input.value = "";
  showThinking();

  try {
    const res = await authFetch(`/chats/${currentChat}/message`, {
      method: "POST",
      body: new URLSearchParams({ question }),
    });
    removeThinking();

    if (!res.ok) {
      let detail = `Erro HTTP ${res.status}.`;
      try { detail = (await res.json()).detail || detail; } catch (_) {}
      renderMessage({ role: "assistant", content: `Falha ao responder: ${detail}` }, true);
      return;
    }

    const data = await res.json();
    renderMessage({
      role: "assistant",
      content: data.answer,
      plot_url: data.plot_url,
      table_url: data.table_url,
      chart_data: data.chart_data,
    }, true);
  } catch (err) {
    removeThinking();
    renderMessage({ role: "assistant", content: "Nao consegui contatar o servidor. Confira se o uvicorn esta rodando no terminal." }, true);
  }
};

loadChats();
