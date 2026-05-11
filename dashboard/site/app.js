// SM 速報 BUY ダッシュボード フロントエンド
// data/signals_<window>.json と data/meta.json を読み、 リスト + チャート iframe を描画。

const WINDOWS = ["1h", "6h", "24h", "7d"];
const DATA_BASE = "./data";

const state = {
  window: "24h",
  sort: "distinct_buyers",
  minBuyers: 1,
  search: "",
  source: "dexscreener",
  selectedMint: null,
  payloads: {}, // window -> payload
};

const els = {
  updated: document.getElementById("updated"),
  counts: document.getElementById("counts"),
  tabs: document.querySelectorAll("#window-tabs .tab"),
  sort: document.getElementById("sort"),
  minBuyers: document.getElementById("min-buyers"),
  search: document.getElementById("search"),
  list: document.getElementById("token-list"),
  emptyMsg: document.getElementById("empty-msg"),
  chartEmpty: document.getElementById("chart-empty"),
  chartDetail: document.getElementById("chart-detail"),
  chartFrame: document.getElementById("chart-frame"),
  detailImage: document.getElementById("detail-image"),
  detailSymbol: document.getElementById("detail-symbol"),
  detailName: document.getElementById("detail-name"),
  detailStats: document.getElementById("detail-stats"),
  detailOpenLink: document.getElementById("detail-open-link"),
  buyersCount: document.getElementById("buyers-count"),
  buyersList: document.getElementById("buyers-list"),
  srcBtns: document.querySelectorAll(".src-btn"),
  resizer: document.getElementById("resizer"),
  backBtn: document.getElementById("back-btn"),
  copyCaBtn: document.getElementById("copy-ca-btn"),
  snapshotBtn: document.getElementById("snapshot-btn"),
  toast: document.getElementById("toast"),
};

const MOBILE_BP = 800;
const isMobile = () => window.matchMedia(`(max-width: ${MOBILE_BP}px)`).matches;

// --- utilities ---

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  if (Math.abs(v) >= 1e9) return (v / 1e9).toFixed(digits) + "B";
  if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(digits) + "M";
  if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(digits) + "k";
  return v.toFixed(digits);
}

function fmtAgo(ts) {
  if (!ts) return "-";
  const now = Math.floor(Date.now() / 1000);
  const diff = Math.max(0, now - ts);
  if (diff < 60) return diff + "s前";
  if (diff < 3600) return Math.floor(diff / 60) + "m前";
  if (diff < 86400) return Math.floor(diff / 3600) + "h前";
  return Math.floor(diff / 86400) + "d前";
}

function fmtTimeShort(ts) {
  if (!ts) return "-";
  const d = new Date(ts * 1000);
  const today = new Date();
  const sameDay = d.getFullYear() === today.getFullYear()
    && d.getMonth() === today.getMonth()
    && d.getDate() === today.getDate();
  if (sameDay) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleString([], {
    month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit",
  });
}

function shortAddr(a) {
  if (typeof a !== "string" || a.length < 10) return a || "";
  return a.slice(0, 4) + "…" + a.slice(-4);
}

function chartUrl(mint, source) {
  if (source === "birdeye") {
    return `https://birdeye.so/token/${encodeURIComponent(mint)}?chain=solana`;
  }
  return `https://dexscreener.com/solana/${encodeURIComponent(mint)}?embed=1&theme=dark&info=0&trades=0`;
}

function externalUrl(mint, source) {
  if (source === "birdeye") return `https://birdeye.so/token/${encodeURIComponent(mint)}?chain=solana`;
  return `https://dexscreener.com/solana/${encodeURIComponent(mint)}`;
}

// --- data loading ---

async function loadWindow(w) {
  if (state.payloads[w]) return state.payloads[w];
  try {
    const res = await fetch(`${DATA_BASE}/signals_${w}.json?cb=${Date.now()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    state.payloads[w] = json;
    return json;
  } catch (e) {
    console.warn("load failed", w, e);
    state.payloads[w] = { tokens: [], generated_at: null, total_events_in_window: 0 };
    return state.payloads[w];
  }
}

async function loadMeta() {
  try {
    const res = await fetch(`${DATA_BASE}/meta.json?cb=${Date.now()}`);
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}

// --- rendering ---

function applyFilters(tokens) {
  const q = state.search.trim().toLowerCase();
  const min = state.minBuyers;
  return tokens
    .filter((t) => (t.distinct_buyers || 0) >= min)
    .filter((t) => {
      if (!q) return true;
      const sym = (t.symbol || "").toLowerCase();
      const name = (t.name || "").toLowerCase();
      const mint = (t.mint || "").toLowerCase();
      return sym.includes(q) || name.includes(q) || mint.includes(q);
    });
}

function applySort(tokens) {
  const key = state.sort;
  const arr = tokens.slice();
  arr.sort((a, b) => (b[key] || 0) - (a[key] || 0));
  return arr;
}

function renderList() {
  const payload = state.payloads[state.window];
  if (!payload) return;
  const tokens = applySort(applyFilters(payload.tokens || []));

  els.list.innerHTML = "";
  if (tokens.length === 0) {
    els.emptyMsg.hidden = false;
    return;
  }
  els.emptyMsg.hidden = true;

  const frag = document.createDocumentFragment();
  for (const t of tokens) {
    const li = document.createElement("li");
    li.dataset.mint = t.mint;
    if (state.selectedMint === t.mint) li.classList.add("selected");

    const img = document.createElement("img");
    img.className = "icon";
    img.alt = "";
    img.src = t.image_url || "";
    img.onerror = () => { img.style.visibility = "hidden"; };

    const info = document.createElement("div");
    info.className = "info";
    const sym = t.symbol || shortAddr(t.mint);
    info.innerHTML = `
      <div class="symbol-row">
        <span class="sym">${escapeHtml(sym)}</span>
        <span class="name">${escapeHtml(t.name || "")}</span>
      </div>
      <div class="stats">
        <span class="buy">👥 ${t.distinct_buyers}</span>
        <span>SOL ${fmtNum(t.sum_buy_sol)}</span>
        <span>$${fmtNum(t.sum_buy_stable)}</span>
        ${t.n_large_buys ? `<span class="warn">🐋 ${t.n_large_buys}</span>` : ""}
      </div>
    `;

    const right = document.createElement("div");
    right.className = "right";
    right.innerHTML = `
      <span class="big">${t.buy_trades}txn</span>
      ${fmtAgo(t.last_seen_ts)}
    `;

    li.append(img, info, right);
    li.addEventListener("click", () => selectToken(t.mint));
    frag.append(li);
  }
  els.list.append(frag);
}

function renderHeader(payload, meta) {
  if (payload && payload.generated_at) {
    const d = new Date(payload.generated_at);
    els.updated.textContent = `Updated: ${d.toLocaleString()}`;
  } else {
    els.updated.textContent = "(no data)";
  }
  if (meta && meta.counts) {
    const parts = WINDOWS.map((w) => `${w}: ${meta.counts[w] ?? 0}`);
    els.counts.textContent = parts.join(" · ");
  }
}

function findToken(mint) {
  const payload = state.payloads[state.window];
  if (!payload) return null;
  return (payload.tokens || []).find((t) => t.mint === mint) || null;
}

function selectToken(mint) {
  state.selectedMint = mint;
  const t = findToken(mint);
  if (!t) return;

  // 行のハイライト更新
  els.list.querySelectorAll("li").forEach((li) => {
    li.classList.toggle("selected", li.dataset.mint === mint);
  });

  // モバイルでは詳細画面に切替
  if (isMobile()) document.body.classList.add("show-detail");

  // 右ペイン
  els.chartEmpty.hidden = true;
  els.chartDetail.hidden = false;

  els.detailImage.src = t.image_url || "";
  els.detailImage.style.visibility = t.image_url ? "visible" : "hidden";
  els.detailSymbol.textContent = t.symbol || shortAddr(t.mint);
  els.detailName.textContent = t.name || "";
  const stats = [
    `Mint: ${shortAddr(t.mint)}`,
    t.market_cap != null ? `MCap: $${fmtNum(t.market_cap)}` : null,
    t.price_usd != null ? `Px: $${fmtNum(t.price_usd, 6)}` : null,
    `BUY ${t.buy_trades}txn / ${t.distinct_buyers}wallets`,
    `SOL ${fmtNum(t.sum_buy_sol)} / $${fmtNum(t.sum_buy_stable)}`,
    `last ${fmtAgo(t.last_seen_ts)}`,
  ].filter(Boolean).join(" · ");
  els.detailStats.textContent = stats;

  updateChart();
  renderBuyers(t);
}

function updateChart() {
  if (!state.selectedMint) return;
  const url = chartUrl(state.selectedMint, state.source);
  if (els.chartFrame.src !== url) els.chartFrame.src = url;
  els.detailOpenLink.href = externalUrl(state.selectedMint, state.source);
  els.srcBtns.forEach((b) => {
    b.classList.toggle("active", b.dataset.source === state.source);
  });
}

function renderBuyers(t) {
  const buyers = t.buyers || [];
  els.buyersCount.textContent = buyers.length;
  els.buyersList.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const b of buyers) {
    const li = document.createElement("li");
    const amount = (b.sum_sol || 0) > 0
      ? `${fmtNum(b.sum_sol)} SOL`
      : `$${fmtNum(b.sum_stable)}`;
    const labelHtml = b.label
      ? `<span class="label-cell" title="${escapeHtml(b.label)}">${escapeHtml(b.label)}</span>`
      : `<span class="label-cell empty">-</span>`;
    li.innerHTML = `
      <span class="wallet" title="${escapeHtml(b.wallet || "")}">${shortAddr(b.wallet)}</span>
      ${labelHtml}
      <span class="trades">${b.trades || 0}</span>
      <span class="amount">${amount}</span>
      <span class="ts">
        <span class="abs">${fmtTimeShort(b.last_ts)}</span>
        <span class="ago">${fmtAgo(b.last_ts)}</span>
      </span>
    `;
    frag.append(li);
  }
  els.buyersList.append(frag);
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// --- events ---

els.tabs.forEach((btn) => {
  btn.addEventListener("click", async () => {
    els.tabs.forEach((b) => b.classList.toggle("active", b === btn));
    state.window = btn.dataset.window;
    state.selectedMint = null;
    els.chartDetail.hidden = true;
    els.chartEmpty.hidden = false;
    document.body.classList.remove("show-detail");
    await loadWindow(state.window);
    renderHeader(state.payloads[state.window], state._meta);
    renderList();
  });
});

if (els.backBtn) {
  els.backBtn.addEventListener("click", () => {
    document.body.classList.remove("show-detail");
  });
}

// --- Action buttons (copy CA / snapshot) ---

let _toastTimer = null;

function showToast(msg, kind = "ok") {
  if (!els.toast) return;
  els.toast.textContent = msg;
  els.toast.classList.toggle("error", kind === "error");
  els.toast.hidden = false;
  // 強制 reflow → transition で fade-in
  void els.toast.offsetWidth;
  els.toast.classList.add("show");
  if (_toastTimer) clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => {
    els.toast.classList.remove("show");
    setTimeout(() => { els.toast.hidden = true; }, 250);
  }, 1800);
}

async function copyText(text) {
  if (!text) return false;
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch (e) { /* fallthrough */ }
  // フォールバック (HTTPS / 古いブラウザ向け)
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

function buildSnapshotMarkdown(t) {
  const sym = t.symbol || shortAddr(t.mint);
  const name = t.name || "";
  const lines = [];
  lines.push(`**$${sym}** ${name ? `(${name})` : ""}`.trim());
  lines.push(`Mint: \`${t.mint}\``);
  const meta = [];
  if (t.market_cap != null) meta.push(`MCap: $${fmtNum(t.market_cap)}`);
  if (t.price_usd != null) meta.push(`Px: $${fmtNum(t.price_usd, 6)}`);
  meta.push(`last ${fmtAgo(t.last_seen_ts)}`);
  if (meta.length) lines.push(meta.join(" · "));
  lines.push(
    `BUY: ${t.buy_trades}txn / ${t.distinct_buyers}wallets · ` +
    `SOL ${fmtNum(t.sum_buy_sol)} / $${fmtNum(t.sum_buy_stable)}` +
    (t.n_large_buys ? ` · 🐋 ${t.n_large_buys}` : "")
  );
  lines.push("");
  lines.push(`https://dexscreener.com/solana/${t.mint}`);
  return lines.join("\n");
}

if (els.copyCaBtn) {
  els.copyCaBtn.addEventListener("click", async () => {
    if (!state.selectedMint) return;
    const ok = await copyText(state.selectedMint);
    showToast(ok ? "CA をコピーしました" : "コピーに失敗", ok ? "ok" : "error");
  });
}

if (els.snapshotBtn) {
  els.snapshotBtn.addEventListener("click", async () => {
    const t = state.selectedMint && findToken(state.selectedMint);
    if (!t) return;
    const md = buildSnapshotMarkdown(t);
    const ok = await copyText(md);
    showToast(
      ok ? "スナップショットを Markdown でコピー" : "コピーに失敗",
      ok ? "ok" : "error"
    );
  });
}

els.sort.addEventListener("change", () => {
  state.sort = els.sort.value;
  renderList();
});
els.minBuyers.addEventListener("input", () => {
  const v = parseInt(els.minBuyers.value, 10);
  state.minBuyers = isNaN(v) || v < 1 ? 1 : v;
  renderList();
});
els.search.addEventListener("input", () => {
  state.search = els.search.value;
  renderList();
});
els.srcBtns.forEach((b) => {
  b.addEventListener("click", () => {
    state.source = b.dataset.source;
    updateChart();
  });
});

// --- resizer (左ペイン幅をドラッグで変更、 localStorage 保存) ---

const LIST_W_KEY = "dashboard:list_w";
const LIST_W_MIN = 240;
const CHART_W_MIN = 360;

function setListW(px) {
  document.documentElement.style.setProperty("--list-w", px + "px");
}

function clampListW(px) {
  const total = document.querySelector(".layout").getBoundingClientRect().width;
  // resizer 6px ぶんも引く
  const max = Math.max(LIST_W_MIN, total - CHART_W_MIN - 6);
  return Math.max(LIST_W_MIN, Math.min(max, px));
}

(function initResizer() {
  if (!els.resizer) return;
  const saved = parseInt(localStorage.getItem(LIST_W_KEY), 10);
  if (!isNaN(saved)) setListW(clampListW(saved));

  let dragging = false;
  let pointerId = null;

  els.resizer.addEventListener("pointerdown", (ev) => {
    if (isMobile()) return; // モバイルではバー非表示なので来ないはずだが念のため
    dragging = true;
    pointerId = ev.pointerId;
    els.resizer.setPointerCapture(pointerId);
    els.resizer.classList.add("dragging");
    document.body.style.cursor = "col-resize";
    ev.preventDefault();
  });

  els.resizer.addEventListener("pointermove", (ev) => {
    if (!dragging) return;
    const layoutLeft = document.querySelector(".layout").getBoundingClientRect().left;
    const w = clampListW(ev.clientX - layoutLeft);
    setListW(w);
  });

  function endDrag() {
    if (!dragging) return;
    dragging = false;
    els.resizer.classList.remove("dragging");
    document.body.style.cursor = "";
    if (pointerId !== null) {
      try { els.resizer.releasePointerCapture(pointerId); } catch {}
      pointerId = null;
    }
    const cur = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--list-w"));
    if (!isNaN(cur)) localStorage.setItem(LIST_W_KEY, Math.round(cur));
  }
  els.resizer.addEventListener("pointerup", endDrag);
  els.resizer.addEventListener("pointercancel", endDrag);

  // ダブルクリックで初期幅にリセット
  els.resizer.addEventListener("dblclick", () => {
    document.documentElement.style.removeProperty("--list-w");
    localStorage.removeItem(LIST_W_KEY);
  });

  // ウィンドウリサイズ時に最小幅を再担保
  window.addEventListener("resize", () => {
    const cur = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--list-w"));
    if (!isNaN(cur)) setListW(clampListW(cur));
  });
})();

// --- deep link (?mint=...&window=...) ---

async function applyDeepLink() {
  const params = new URLSearchParams(location.search);
  const mint = params.get("mint");
  if (!mint) return;

  // URL で window 指定があれば優先、 なければ 24h → 7d → 6h → 1h の順で探す
  const requested = params.get("window");
  const order = requested && WINDOWS.includes(requested)
    ? [requested, ...WINDOWS.filter((w) => w !== requested)]
    : ["24h", "7d", "6h", "1h"];

  for (const w of order) {
    const payload = await loadWindow(w);
    const found = (payload.tokens || []).some((t) => t.mint === mint);
    if (found) {
      if (state.window !== w) {
        state.window = w;
        els.tabs.forEach((b) => b.classList.toggle("active", b.dataset.window === w));
        renderHeader(state.payloads[w], state._meta);
        renderList();
      }
      selectToken(mint);
      return;
    }
  }
  // 見つからなかった場合はそのまま (state.window は 24h)
  console.info(`deep link: mint=${mint} がどの window にも見つかりませんでした`);
}

// --- init ---

(async function init() {
  const [meta] = await Promise.all([loadMeta(), loadWindow(state.window)]);
  state._meta = meta;
  renderHeader(state.payloads[state.window], meta);
  renderList();
  await applyDeepLink();
})();
