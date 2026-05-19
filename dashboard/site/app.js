// SM 速報 BUY ダッシュボード フロントエンド
// data/signals_<window>.json と data/meta.json を読み、 リスト + チャート iframe を描画。

const WINDOWS = ["1h", "6h", "24h", "7d"];
const DATA_BASE = "./data";

const state = {
  window: "1h", // "1h"|"6h"|"24h"|"7d"|"fav"|"watch"
  sort: "distinct_buyers",
  minBuyers: 1,
  search: "",
  source: "dexscreener",
  selectedMint: null,
  payloads: {}, // window -> payload
  buyersView: "buyers", // "buyers" | "events" | "token-info"
  favorites: new Set(), // mint の集合 (localStorage)
  watchlist: [], // 任意登録 mint の配列 (localStorage, 追加順)
  watchTokens: {}, // mint -> DexScreener から組んだ token (watch タブ用キャッシュ)
};

const TIME_WINDOWS = ["1h", "6h", "24h", "7d"];
const FAV_KEY = "dashboard:favorites";
const WATCH_KEY = "dashboard:watchlist";

function loadFavWatch() {
  try {
    const f = JSON.parse(localStorage.getItem(FAV_KEY) || "[]");
    if (Array.isArray(f)) state.favorites = new Set(f.filter((x) => typeof x === "string"));
  } catch {}
  try {
    const w = JSON.parse(localStorage.getItem(WATCH_KEY) || "[]");
    if (Array.isArray(w)) state.watchlist = w.filter((x) => typeof x === "string");
  } catch {}
}
function saveFavorites() {
  try { localStorage.setItem(FAV_KEY, JSON.stringify([...state.favorites])); } catch {}
}
function saveWatchlist() {
  try { localStorage.setItem(WATCH_KEY, JSON.stringify(state.watchlist)); } catch {}
}
function isFav(mint) { return state.favorites.has(mint); }
function toggleFav(mint) {
  if (state.favorites.has(mint)) state.favorites.delete(mint);
  else state.favorites.add(mint);
  saveFavorites();
  updateFavWatchCounts();
}
function updateFavWatchCounts() {
  if (els.favCount) els.favCount.textContent = state.favorites.size;
  if (els.watchCount) els.watchCount.textContent = state.watchlist.length;
}

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
  grokBtn: document.getElementById("grok-btn"),
  deepnetsBtn: document.getElementById("deepnets-btn"),
  favCount: document.getElementById("fav-count"),
  watchCount: document.getElementById("watch-count"),
  watchAdd: document.getElementById("watch-add"),
  watchInput: document.getElementById("watch-input"),
  watchAddBtn: document.getElementById("watch-add-btn"),
  listToolbar: document.getElementById("list-toolbar"),
  listToolbarCount: document.getElementById("list-toolbar-count"),
  clearAllBtn: document.getElementById("clear-all-btn"),
  exportBtn: document.getElementById("export-btn"),
  importBtn: document.getElementById("import-btn"),
  importFile: document.getElementById("import-file"),
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

// bot/links.py の make_grok_narrative_url と同じクエリ生成
function grokNarrativeUrl(symbol, mint) {
  const parts = [];
  if (symbol) parts.push(`$${symbol}`);
  if (mint) parts.push(`(CA: ${mint})`);
  parts.push("のナラティブと最新動向を教えて");
  return `https://grok.com/?q=${encodeURIComponent(parts.join(" "))}`;
}

function deepnetsUrl(mint) {
  return `https://deepnets.ai/token/${encodeURIComponent(mint)}`;
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

// 1 銘柄ぶんの <li> を生成。 isWatch=true は DexScreener 由来 (bot 集計なし)。
function createTokenRow(t, { isWatch = false } = {}) {
  const li = document.createElement("li");
  li.dataset.mint = t.mint;
  if (state.selectedMint === t.mint) li.classList.add("selected");

  const star = document.createElement("button");
  star.className = "fav-star" + (isFav(t.mint) ? " on" : "");
  star.textContent = isFav(t.mint) ? "⭐" : "☆";
  star.title = "お気に入り";
  star.addEventListener("click", (ev) => {
    ev.stopPropagation();
    toggleFav(t.mint);
    star.classList.toggle("on", isFav(t.mint));
    star.textContent = isFav(t.mint) ? "⭐" : "☆";
    if (state.window === "fav") renderList();
  });

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
  const statsRow = isWatch
    ? `<div class="stats"><span class="muted-tag">📌 監視 (bot 集計外)</span></div>`
    : `<div class="stats">
        <span class="buy">👥 ${t.distinct_buyers}</span>
        <span>SOL ${fmtNum(t.sum_buy_sol)}</span>
        <span>$${fmtNum(t.sum_buy_stable)}</span>
        ${t.n_large_buys ? `<span class="warn">🐋 ${t.n_large_buys}</span>` : ""}
      </div>`;
  info.innerHTML = `
    <div class="symbol-row">
      <span class="sym">${escapeHtml(sym)}</span>
      <span class="name">${escapeHtml(t.name || "")}</span>
    </div>
    ${statsRow}
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
  if (isWatch) {
    const del = document.createElement("button");
    del.className = "watch-del";
    del.textContent = "×";
    del.title = "監視リストから削除";
    del.addEventListener("click", (ev) => {
      ev.stopPropagation();
      state.watchlist = state.watchlist.filter((m) => m !== t.mint);
      saveWatchlist();
      updateFavWatchCounts();
      renderList();
    });
    right.append(del);
  } else {
    right.innerHTML = `
      <span class="big">${t.buy_trades}txn</span>
      ${fmtAgo(t.last_seen_ts)}
    `;
  }

  li.append(star, img, info, right);
  li.addEventListener("click", () => selectToken(t.mint));
  return li;
}

function renderRows(tokens, { isWatch = false } = {}) {
  els.list.innerHTML = "";
  if (!tokens.length) {
    els.emptyMsg.hidden = false;
    return;
  }
  els.emptyMsg.hidden = true;
  const frag = document.createDocumentFragment();
  for (const t of tokens) frag.append(createTokenRow(t, { isWatch }));
  els.list.append(frag);
}

// fav タブ: 全 time window から favorites の token を集約 (1h 優先で最初に見つかったもの)
function collectFavoriteTokens() {
  const seen = new Map();
  for (const w of TIME_WINDOWS) {
    const p = state.payloads[w];
    if (!p) continue;
    for (const t of p.tokens || []) {
      if (state.favorites.has(t.mint) && !seen.has(t.mint)) seen.set(t.mint, t);
    }
  }
  return [...seen.values()];
}

function updateListToolbar() {
  const w = state.window;
  const show = w === "fav" || w === "watch";
  els.listToolbar.hidden = !show;
  if (!show) return;
  if (w === "fav") {
    els.listToolbarCount.textContent = `⭐ お気に入り ${state.favorites.size} 件`;
    els.clearAllBtn.textContent = "🗑 全解除";
  } else {
    els.listToolbarCount.textContent = `📌 監視 ${state.watchlist.length} 件`;
    els.clearAllBtn.textContent = "🗑 全削除";
  }
}

function renderList() {
  els.watchAdd.hidden = state.window !== "watch";
  updateListToolbar();

  if (state.window === "watch") {
    renderWatchlist();
    return;
  }
  if (state.window === "fav") {
    renderRows(applySort(applyFilters(collectFavoriteTokens())));
    return;
  }
  const payload = state.payloads[state.window];
  if (!payload) return;
  renderRows(applySort(applyFilters(payload.tokens || [])));
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
  // watch タブ / watch キャッシュ優先
  if (state.watchTokens[mint]) return state.watchTokens[mint];
  // 現 window
  const payload = state.payloads[state.window];
  const hit = payload && (payload.tokens || []).find((t) => t.mint === mint);
  if (hit) return hit;
  // fav タブ等は全 window を横断
  for (const w of TIME_WINDOWS) {
    const p = state.payloads[w];
    const h = p && (p.tokens || []).find((t) => t.mint === mint);
    if (h) return h;
  }
  return null;
}

// DexScreener pair → ダッシュボード token 形式 (token_info.py の抽出と等価)
function dexPairToToken(mint, pair) {
  const base = pair && pair.baseToken;
  const info = pair && pair.info;
  const num = (v) => {
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : null;
  };
  const bucket = (obj) => {
    if (!obj || typeof obj !== "object") return null;
    const out = {};
    for (const k of ["m5", "h1", "h6", "h24"]) {
      const v = num(obj[k]);
      if (v != null) out[k] = v;
    }
    return Object.keys(out).length ? out : null;
  };
  const txnsObj = (obj) => {
    if (!obj || typeof obj !== "object") return null;
    const out = {};
    for (const k of ["m5", "h1", "h6", "h24"]) {
      const s = obj[k];
      if (s && typeof s === "object") {
        out[k] = { buys: parseInt(s.buys || 0, 10), sells: parseInt(s.sells || 0, 10) };
      }
    }
    return Object.keys(out).length ? out : null;
  };
  const img = info && typeof info.imageUrl === "string" && info.imageUrl.startsWith("http")
    ? info.imageUrl : "";
  return {
    mint,
    symbol: base && base.symbol ? base.symbol : null,
    name: base && base.name ? base.name : null,
    image_url: img,
    market_cap: num(pair && (pair.marketCap ?? pair.fdv)),
    price_usd: num(pair && pair.priceUsd),
    liquidity_usd: num(pair && pair.liquidity && pair.liquidity.usd),
    volume: bucket(pair && pair.volume),
    txns: txnsObj(pair && pair.txns),
    price_change: bucket(pair && pair.priceChange),
    pair_created_at_ms: pair && pair.pairCreatedAt ? Number(pair.pairCreatedAt) : null,
    // bot 集計フィールドは無し (watch は監視外)
    buyers: [],
    events: [],
    _watch: true,
  };
}

async function fetchDexToken(mint) {
  const res = await fetch(
    `https://api.dexscreener.com/latest/dex/tokens/${encodeURIComponent(mint)}`
  );
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const json = await res.json();
  const pairs = json && Array.isArray(json.pairs) ? json.pairs : [];
  if (!pairs.length) return null;
  return dexPairToToken(mint, pairs[0]);
}

async function renderWatchlist() {
  if (!state.watchlist.length) {
    els.list.innerHTML = "";
    els.emptyMsg.hidden = false;
    els.emptyMsg.textContent = "監視 CA がありません。 上の入力欄から追加してください";
    return;
  }
  els.emptyMsg.hidden = true;
  // 取得済みは即描画、 未取得は並列 fetch
  const missing = state.watchlist.filter((m) => !state.watchTokens[m]);
  const tokens0 = state.watchlist
    .map((m) => state.watchTokens[m])
    .filter(Boolean);
  if (tokens0.length) renderRows(applySort(tokens0), { isWatch: true });

  if (missing.length) {
    const results = await Promise.allSettled(missing.map((m) => fetchDexToken(m)));
    results.forEach((r, i) => {
      const mint = missing[i];
      if (r.status === "fulfilled" && r.value) {
        state.watchTokens[mint] = r.value;
      } else {
        // 取得失敗でも行は出す (CA だけ表示)
        state.watchTokens[mint] = {
          mint, symbol: null, name: "(取得失敗)", image_url: "",
          buyers: [], events: [], _watch: true,
        };
      }
    });
    if (state.window === "watch") {
      const tokens = state.watchlist.map((m) => state.watchTokens[m]).filter(Boolean);
      renderRows(applySort(tokens), { isWatch: true });
    }
  }
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

  const symForLink = t.symbol && t.symbol !== "?" ? t.symbol : "";
  if (els.grokBtn) els.grokBtn.href = grokNarrativeUrl(symForLink, t.mint);
  if (els.deepnetsBtn) els.deepnetsBtn.href = deepnetsUrl(t.mint);

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

function starsStr(r) {
  const n = parseInt(r, 10);
  return Number.isFinite(n) && n > 0 ? "⭐".repeat(n) : "";
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
    const star = starsStr(b.rating);
    li.innerHTML = `
      <span class="wallet" title="${escapeHtml(b.wallet || "")}">${star ? `<span class="rating">${star}</span> ` : ""}${shortAddr(b.wallet)}</span>
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
      <span class="wallet" title="${escapeHtml(ev.wallet || "")}">${(() => { const s = starsStr(ev.rating); return s ? `<span class="rating">${s}</span> ` : ""; })()}${shortAddr(ev.wallet)}</span>
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
    if (state.window === "fav") {
      // fav は全 time window を横断するので全部ロード
      await Promise.all(TIME_WINDOWS.map((w) => loadWindow(w)));
    } else if (state.window === "watch") {
      // watch は DexScreener 直 fetch (renderList 内)
    } else {
      await loadWindow(state.window);
      renderHeader(state.payloads[state.window], state._meta);
    }
    renderList();
  });
});

if (els.watchAddBtn) {
  const addWatch = () => {
    const v = (els.watchInput.value || "").trim();
    if (!v) return;
    // CA らしき文字列のみ (Solana base58, ざっくり 32-44 文字)
    if (!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(v)) {
      showToast("CA 形式が不正です", "error");
      return;
    }
    if (state.watchlist.includes(v)) {
      showToast("既に登録済みです");
    } else {
      state.watchlist.push(v);
      saveWatchlist();
      updateFavWatchCounts();
    }
    els.watchInput.value = "";
    if (state.window === "watch") renderList();
    else {
      els.tabs.forEach((b) => b.classList.toggle("active", b.dataset.window === "watch"));
      state.window = "watch";
      renderList();
    }
  };
  els.watchAddBtn.addEventListener("click", addWatch);
  els.watchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") addWatch();
  });
}

if (els.clearAllBtn) {
  els.clearAllBtn.addEventListener("click", () => {
    if (state.window === "fav") {
      if (!state.favorites.size) return;
      if (!confirm(`お気に入り ${state.favorites.size} 件をすべて解除しますか?`)) return;
      state.favorites.clear();
      saveFavorites();
      showToast("お気に入りを全解除しました");
    } else if (state.window === "watch") {
      if (!state.watchlist.length) return;
      if (!confirm(`監視 CA ${state.watchlist.length} 件をすべて削除しますか?`)) return;
      state.watchlist = [];
      state.watchTokens = {};
      saveWatchlist();
      showToast("監視リストを全削除しました");
    } else {
      return;
    }
    updateFavWatchCounts();
    renderList();
  });
}

// --- お気に入り/監視リストのローカル書き出し・読み込み ---

function exportLists() {
  const payload = {
    app: "nansen-sm-dashboard",
    kind: "fav-watch-backup",
    version: 1,
    exportedAt: new Date().toISOString(),
    favorites: [...state.favorites],
    watchlist: [...state.watchlist],
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  const ts = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  const name =
    `dashboard-list-${ts.getFullYear()}${pad(ts.getMonth() + 1)}${pad(ts.getDate())}` +
    `-${pad(ts.getHours())}${pad(ts.getMinutes())}.json`;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  showToast(
    `書き出しました（⭐${payload.favorites.length} / 📌${payload.watchlist.length}）`,
  );
}

function importListsFromText(text) {
  let data;
  try {
    data = JSON.parse(text);
  } catch {
    showToast("JSON の読み込みに失敗しました", "error");
    return;
  }
  const fav = Array.isArray(data && data.favorites)
    ? data.favorites.filter((x) => typeof x === "string")
    : null;
  const watch = Array.isArray(data && data.watchlist)
    ? data.watchlist.filter((x) => typeof x === "string")
    : null;
  if (!fav && !watch) {
    showToast("バックアップ形式ではありません", "error");
    return;
  }
  if (
    !confirm(
      `読み込むと現在のリストを置き換えます。\n` +
        `⭐ お気に入り ${state.favorites.size} → ${fav ? fav.length : state.favorites.size} 件\n` +
        `📌 監視 ${state.watchlist.length} → ${watch ? watch.length : state.watchlist.length} 件\n` +
        `続行しますか?`,
    )
  ) {
    return;
  }
  if (fav) {
    state.favorites = new Set(fav);
    saveFavorites();
  }
  if (watch) {
    state.watchlist = watch;
    state.watchTokens = {};
    saveWatchlist();
  }
  updateFavWatchCounts();
  renderList();
  showToast("リストを復元しました");
}

if (els.exportBtn) {
  els.exportBtn.addEventListener("click", exportLists);
}
if (els.importBtn && els.importFile) {
  els.importBtn.addEventListener("click", () => els.importFile.click());
  els.importFile.addEventListener("change", () => {
    const file = els.importFile.files && els.importFile.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => importListsFromText(String(reader.result || ""));
    reader.onerror = () => showToast("ファイル読み込みエラー", "error");
    reader.readAsText(file);
    els.importFile.value = ""; // 同じファイルを再選択できるようリセット
  });
}

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
    if (state.window === "watch") {
      // watch は DexScreener 直 fetch なのでキャッシュを捨てて再取得
      state.watchTokens = {};
      renderList();
    } else if (state.window === "fav") {
      for (const w of TIME_WINDOWS) delete state.payloads[w];
      const [meta] = await Promise.all([
        loadMeta(),
        ...TIME_WINDOWS.map((w) => loadWindow(w)),
      ]);
      state._meta = meta;
      renderList();
    } else {
      // 現 window のキャッシュを捨てて再 fetch (他 window は遅延 fetch でいい)
      delete state.payloads[state.window];
      const [meta] = await Promise.all([loadMeta(), loadWindow(state.window)]);
      state._meta = meta;
      renderHeader(state.payloads[state.window], meta);
      renderList();
    }
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
  loadFavWatch();
  updateFavWatchCounts();
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
