// SM 速報 BUY ダッシュボード フロントエンド
// data/signals_<window>.json と data/meta.json を読み、 リスト + チャート iframe を描画。

const WINDOWS = ["1h", "6h", "24h", "7d"];
const DATA_BASE = "./data";

const state = {
  window: "1h",
  sort: "distinct_buyers",
  minBuyers: 1,
  search: "",
  source: "dexscreener",
  selectedMint: null,
  payloads: {}, // window -> payload
  buyersView: "buyers", // "buyers" | "events"
};

// window -> DexScreener bucket key
const WINDOW_TO_BUCKET = {
  "1h": "h1",
  "6h": "h6",
  "24h": "h24",
  "7d": "h24", // 7d は h24 で代替
};
const BUCKET_LABEL = {
  m5: "5m",
  h1: "1h",
  h6: "6h",
  h24: "24h",
};

const els = {
  updated: document.getElementById("updated"),
  counts: document.getElementById("counts"),
  reloadBtn: document.getElementById("reload-btn"),
  autoRefresh: document.getElementById("auto-refresh"),
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
  eventsCount: document.getElementById("events-count"),
  eventsList: document.getElementById("events-list"),
  buyersTabs: document.querySelectorAll(".buyers-tab"),
  buyersViews: document.querySelectorAll(".buyers-view"),
  tokenInfoGrid: document.getElementById("token-info-grid"),
  tokenInfoScore: document.getElementById("token-info-score"),
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
    // birdeye の埋め込みは ?chart_interval=15 を渡すと 15m がデフォルト
    return `https://birdeye.so/token/${encodeURIComponent(mint)}?chain=solana&chart_interval=15`;
  }
  // DexScreener iframe: interval=15 で 15m スケールをデフォルト指定
  return `https://dexscreener.com/solana/${encodeURIComponent(mint)}?embed=1&theme=dark&info=0&trades=0&interval=15`;
}

function fmtAgeFromMs(ms) {
  if (!ms || typeof ms !== "number") return null;
  const sec = Math.max(0, Math.floor((Date.now() - ms) / 1000));
  if (sec < 60) return sec + "s";
  if (sec < 3600) return Math.floor(sec / 60) + "m";
  if (sec < 86400) return Math.floor(sec / 3600) + "h";
  return Math.floor(sec / 86400) + "d";
}

// 現選択 window から DexScreener の bucket (m5/h1/h6/h24) を返す。
function currentBucket() {
  return WINDOW_TO_BUCKET[state.window] || "h24";
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
    const bucket = currentBucket();
    const vol = t.volume && t.volume[bucket];
    const tx = t.txns && t.txns[bucket];
    const txTotal = tx ? (tx.buys || 0) + (tx.sells || 0) : null;
    const age = fmtAgeFromMs(t.pair_created_at_ms);
    const bucketLabel = BUCKET_LABEL[bucket];
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
      <div class="dex-stats">
        ${t.market_cap != null ? `<span>MC $${fmtNum(t.market_cap)}</span>` : ""}
        ${vol != null ? `<span title="DexScreener ${bucketLabel} volume">Vol $${fmtNum(vol)}</span>` : ""}
        ${t.liquidity_usd != null ? `<span title="Liquidity (USD)">LIQ $${fmtNum(t.liquidity_usd)}</span>` : ""}
        ${txTotal != null ? `<span title="${bucketLabel} buys/sells">${txTotal}tx</span>` : ""}
        ${age ? `<span title="Pair age">⏱ ${age}</span>` : ""}
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
  renderEvents(t);
  renderTokenInfo(t);
  applyBuyersView();
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

function renderEvents(t) {
  if (!els.eventsList) return;
  const events = Array.isArray(t.events) ? t.events : [];
  if (els.eventsCount) els.eventsCount.textContent = events.length;
  els.eventsList.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const ev of events) {
    const li = document.createElement("li");
    const dir = (ev.direction || "").toUpperCase();
    const isBuy = dir === "BUY";
    const amount =
      ev.quote_label === "SOL"
        ? `${fmtNum(Math.abs(ev.quote_change || 0))} SOL`
        : `$${fmtNum(Math.abs(ev.quote_change || 0))}`;
    const labelHtml = ev.label
      ? `<span class="label-cell" title="${escapeHtml(ev.label)}">${escapeHtml(ev.label)}</span>`
      : `<span class="label-cell empty">-</span>`;
    li.innerHTML = `
      <span class="ts">
        <span class="abs">${fmtTimeShort(ev.ts)}</span>
        <span class="ago">${fmtAgo(ev.ts)}</span>
      </span>
      <span class="dir ${isBuy ? "buy" : "sell"}">${isBuy ? "BUY" : "SELL"}</span>
      <span class="wallet" title="${escapeHtml(ev.wallet || "")}">${shortAddr(ev.wallet)}</span>
      ${labelHtml}
      <span class="amount ${isBuy ? "buy" : "sell"}">${amount}${ev.is_large ? " 🐋" : ""}</span>
    `;
    frag.append(li);
  }
  els.eventsList.append(frag);
}

function applyBuyersView() {
  const view = state.buyersView;
  els.buyersTabs.forEach((b) =>
    b.classList.toggle("active", b.dataset.view === view)
  );
  els.buyersViews.forEach((v) => {
    v.hidden = v.dataset.view !== view;
  });
}

// --- Token Info panel ---

function pctClass(v) {
  if (v == null) return "";
  if (v > 0) return "pos";
  if (v < 0) return "neg";
  return "";
}
function fmtPct(v, digits = 2) {
  if (v == null || Number.isNaN(v)) return "-";
  const s = v.toFixed(digits) + "%";
  return v > 0 ? "+" + s : s;
}

function renderTokenInfo(t) {
  if (!els.tokenInfoGrid) return;

  const bucket = currentBucket();
  const bucketLabel = BUCKET_LABEL[bucket] || bucket;

  const vol = t.volume ? t.volume[bucket] : null;
  const tx = t.txns ? t.txns[bucket] : null;
  const pc = t.price_change ? t.price_change[bucket] : null;
  const age = fmtAgeFromMs(t.pair_created_at_ms);

  const liq = t.liquidity_usd;
  const mcap = t.market_cap;
  const volMcap = vol != null && mcap ? vol / mcap : null;
  const liqMcap = liq != null && mcap ? liq / mcap : null;

  // 銘柄スコア (簡易判定)
  const indicators = [];
  if (liq != null) {
    if (liq < 5_000) indicators.push({ cls: "bad", text: "LIQ 薄い" });
    else if (liq < 30_000) indicators.push({ cls: "warn", text: "LIQ やや薄" });
    else indicators.push({ cls: "good", text: "LIQ 厚い" });
  }
  if (volMcap != null) {
    if (volMcap >= 1) indicators.push({ cls: "good", text: `Vol/MC ${(volMcap * 100).toFixed(0)}%` });
    else if (volMcap < 0.05) indicators.push({ cls: "warn", text: "Vol/MC 低" });
  }
  if (tx) {
    const total = (tx.buys || 0) + (tx.sells || 0);
    if (total > 0) {
      const buyRatio = (tx.buys || 0) / total;
      if (buyRatio >= 0.65) indicators.push({ cls: "good", text: `買い優勢 ${(buyRatio * 100).toFixed(0)}%` });
      else if (buyRatio <= 0.35) indicators.push({ cls: "bad", text: `売り優勢 ${((1 - buyRatio) * 100).toFixed(0)}%` });
    }
  }
  if (pc != null) {
    if (pc >= 30) indicators.push({ cls: "good", text: `急騰 ${fmtPct(pc, 0)}` });
    else if (pc <= -20) indicators.push({ cls: "bad", text: `急落 ${fmtPct(pc, 0)}` });
  }
  if (t.pair_created_at_ms) {
    const ageMin = (Date.now() - t.pair_created_at_ms) / 60_000;
    if (ageMin < 60) indicators.push({ cls: "warn", text: "新規 (1h 未満)" });
    else if (ageMin < 24 * 60) indicators.push({ cls: "warn", text: "<24h" });
  }

  const cells = [
    { label: "MCap", value: mcap != null ? `$${fmtNum(mcap)}` : "-" },
    { label: "LIQ", value: liq != null ? `$${fmtNum(liq)}` : "-" },
    { label: `Vol ${bucketLabel}`, value: vol != null ? `$${fmtNum(vol)}` : "-" },
    {
      label: `Tx ${bucketLabel}`,
      value: tx
        ? `<span class="buy">${tx.buys || 0}</span> / <span class="sell">${tx.sells || 0}</span>`
        : "-",
      html: true,
    },
    {
      label: `Δ ${bucketLabel}`,
      value: pc != null ? `<span class="${pctClass(pc)}">${fmtPct(pc)}</span>` : "-",
      html: true,
    },
    { label: "Vol/MC", value: volMcap != null ? `${(volMcap * 100).toFixed(0)}%` : "-" },
    { label: "LIQ/MC", value: liqMcap != null ? `${(liqMcap * 100).toFixed(1)}%` : "-" },
    { label: "Age", value: age || "-" },
  ];

  els.tokenInfoGrid.innerHTML = cells
    .map(
      (c) => `
      <div class="ti-cell">
        <div class="ti-label">${escapeHtml(c.label)}</div>
        <div class="ti-value">${c.html ? c.value : escapeHtml(c.value)}</div>
      </div>`
    )
    .join("");

  els.tokenInfoScore.innerHTML = indicators.length
    ? indicators
        .map((i) => `<span class="ti-badge ${i.cls}">${escapeHtml(i.text)}</span>`)
        .join("")
    : `<span class="ti-empty">DexScreener 指標未取得</span>`;
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

els.buyersTabs.forEach((b) => {
  b.addEventListener("click", () => {
    state.buyersView = b.dataset.view;
    applyBuyersView();
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

// --- データ再読込 (手動 + オート) ---

const AUTO_REFRESH_KEY = "dashboard:auto_refresh";
const AUTO_REFRESH_INTERVAL_MS = 60 * 1000;
let _autoTimer = null;
let _reloading = false;

async function reloadData({ silent = false } = {}) {
  if (_reloading) return;
  _reloading = true;
  if (els.reloadBtn) els.reloadBtn.classList.add("spinning");
  try {
    // 現 window のキャッシュを捨てて再 fetch (他 window は遅延 fetch でいい)
    delete state.payloads[state.window];
    const [meta] = await Promise.all([loadMeta(), loadWindow(state.window)]);
    state._meta = meta;
    renderHeader(state.payloads[state.window], meta);
    renderList();
    // 選択中の銘柄があれば最新データで再描画 (state を保てる範囲で)
    if (state.selectedMint) {
      const t = findToken(state.selectedMint);
      if (t) {
        // selectToken だと list ハイライト等もまとめて更新できる
        selectToken(state.selectedMint);
      }
    }
    if (!silent) showToast("最新データに更新しました");
  } catch (e) {
    console.warn("reload failed", e);
    if (!silent) showToast("再読込に失敗", "error");
  } finally {
    _reloading = false;
    if (els.reloadBtn) els.reloadBtn.classList.remove("spinning");
  }
}

function setAutoRefresh(enabled, { persist = true } = {}) {
  if (els.autoRefresh) els.autoRefresh.checked = enabled;
  if (persist) {
    try { localStorage.setItem(AUTO_REFRESH_KEY, enabled ? "1" : "0"); } catch {}
  }
  if (_autoTimer) {
    clearInterval(_autoTimer);
    _autoTimer = null;
  }
  if (enabled) {
    _autoTimer = setInterval(() => {
      // ページが裏に隠れている間は無駄打ち回避
      if (document.hidden) return;
      reloadData({ silent: true });
    }, AUTO_REFRESH_INTERVAL_MS);
  }
}

if (els.reloadBtn) {
  els.reloadBtn.addEventListener("click", () => reloadData());
}
if (els.autoRefresh) {
  els.autoRefresh.addEventListener("change", () => {
    setAutoRefresh(els.autoRefresh.checked);
  });
}

// --- deep link (?mint=...&window=...) ---

async function applyDeepLink() {
  const params = new URLSearchParams(location.search);
  const mint = params.get("mint");
  if (!mint) return;

  // URL で window 指定があれば優先、 なければ 1h → 6h → 24h → 7d の順で探す
  const requested = params.get("window");
  const order = requested && WINDOWS.includes(requested)
    ? [requested, ...WINDOWS.filter((w) => w !== requested)]
    : ["1h", "6h", "24h", "7d"];

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
  // 自動更新は localStorage から復元 (デフォルト ON)
  const saved = localStorage.getItem(AUTO_REFRESH_KEY);
  const enabled = saved === null ? true : saved === "1";
  setAutoRefresh(enabled, { persist: false });
})();
