const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
  // Полноэкранный режим (Bot API 8.0+) — убирает верхнюю "шторку" Telegram
  // с заголовком бота. На старых клиентах метода просто нет — тихо пропускаем.
  if (typeof tg.requestFullscreen === "function") {
    try { tg.requestFullscreen(); } catch (e) {}
  }
  if (typeof tg.disableVerticalSwipes === "function") {
    try { tg.disableVerticalSwipes(); } catch (e) {}
  }
}

const PLACEHOLDER = "data:image/svg+xml;utf8," + encodeURIComponent(
  '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">' +
  '<rect width="100" height="100" fill="#2a2d36"/>' +
  '<text x="50" y="55" font-size="12" fill="#666" text-anchor="middle">нет фото</text></svg>'
);

function timeAgoRu(isoString) {
  const then = new Date(isoString).getTime();
  if (isNaN(then)) return null;
  const diffSec = Math.max(0, Math.floor((Date.now() - then) / 1000));
  const units = [
    [31536000, "год", "года", "лет"],
    [2592000, "месяц", "месяца", "месяцев"],
    [86400, "день", "дня", "дней"],
    [3600, "час", "часа", "часов"],
    [60, "минуту", "минуты", "минут"],
  ];
  for (const [sec, one, few, many] of units) {
    const n = Math.floor(diffSec / sec);
    if (n >= 1) {
      const mod10 = n % 10, mod100 = n % 100;
      let word = many;
      if (mod10 === 1 && mod100 !== 11) word = one;
      else if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) word = few;
      return `${n} ${word} назад`;
    }
  }
  return "только что";
}

function renderGameUpdate(gameUpdate) {
  const box = el("#game-update-info");
  if (!gameUpdate || !gameUpdate.updated) {
    box.innerHTML = "";
    return;
  }
  const ago = timeAgoRu(gameUpdate.updated);
  box.innerHTML = `<span class="dot"></span>MM2 в Roblox обновлялась: <b>${ago}</b>`;
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function truncate(s, n) {
  s = s || "";
  return s.length > n ? s.slice(0, n).trim() + "…" : s;
}

// Новости о самой игре MM2 (не о ценах) — сторонние источники (см. mm2_api.py:
// Nikilis/официальный Discord недоступны без браузера или платного API),
// поэтому подписываем каждую карточку именем источника, а не выдаём за
// официальные патчноуты.
function renderMm2News(news) {
  const box = el("#mm2-news");
  if (!news || (!news.colbe && !news.mmoexp)) {
    box.innerHTML = "";
    return;
  }
  let html = "";
  if (news.colbe && news.colbe.url) {
    const ago = timeAgoRu(news.colbe.published);
    html += `
      <a class="news-card" href="${news.colbe.url}" target="_blank" rel="noopener">
        <span class="news-src"><svg viewBox="0 0 24 24" fill="currentColor"><path d="M6 4v16a1 1 0 0 0 1.524 .852l13 -8a1 1 0 0 0 0 -1.704l-13 -8a1 1 0 0 0 -1.524 .852z" /></svg> Colbe · YouTube${ago ? " · " + ago : ""}</span>
        <span class="news-title">${escapeHtml(news.colbe.title)}</span>
        <span class="news-body">${escapeHtml(truncate(news.colbe.description, 200))}</span>
      </a>`;
  }
  if (news.mmoexp && news.mmoexp.url) {
    html += `
      <a class="news-card" href="${news.mmoexp.url}" target="_blank" rel="noopener">
        <span class="news-src"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 6h3a1 1 0 0 1 1 1v11a2 2 0 0 1 -4 0v-13a1 1 0 0 0 -1 -1h-10a1 1 0 0 0 -1 1v12a3 3 0 0 0 3 3h11" /><path d="M8 8l4 0" /><path d="M8 12l4 0" /><path d="M8 16l4 0" /></svg> MMOexp.com${news.mmoexp.published ? " · " + news.mmoexp.published : ""}</span>
        <span class="news-title">${escapeHtml(news.mmoexp.title)}</span>
        <span class="news-body">${escapeHtml(truncate(news.mmoexp.summary, 200))}</span>
      </a>`;
  }
  box.innerHTML = html;
}

const RARE_META = {
  godly: { label: "Godly", cls: "rare-godly", tint: "rgba(242,201,76,0.16)" },
  ancient: { label: "Ancient", cls: "rare-ancient", tint: "rgba(79,140,255,0.16)" },
  unique: { label: "Unique", cls: "rare-unique", tint: "rgba(155,124,245,0.16)" },
};
function rareMeta(rare) {
  return RARE_META[(rare || "").toLowerCase()] || { label: rare || "?", cls: "", tint: "rgba(255,255,255,0.05)" };
}

const CATEGORY_ICONS = {
  godly: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 17.75l-6.172 3.245l1.179 -6.873l-5 -4.867l6.9 -1l3.086 -6.253l3.086 6.253l6.9 1l-5 4.867l1.179 6.873l-6.158 -3.245" /></svg>',
  ancient: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a12 12 0 0 0 8.5 3a12 12 0 0 1 -8.5 15a12 12 0 0 1 -8.5 -15a12 12 0 0 0 8.5 -3" /></svg>',
  unique: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 12a1 1 0 1 0 2 0a1 1 0 1 0 -2 0" /><path d="M7 12a5 5 0 1 0 10 0a5 5 0 1 0 -10 0" /><path d="M3 12a9 9 0 1 0 18 0a9 9 0 1 0 -18 0" /></svg>',
};

const ARROW_UP = '<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5l0 14" /><path d="M16 9l-4 -4" /><path d="M8 9l4 -4" /></svg>';
const ARROW_DOWN = '<svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5l0 14" /><path d="M16 15l-4 4" /><path d="M8 15l4 4" /></svg>';

// Период сравнения "было -> стало" — общий для главного экрана, категории и
// карточки предмета: сколько выбрали, столько и используется в /api/... до
// следующей смены. Список синхронизирован с WINDOW_OPTIONS в webapp/server.py.
const WINDOW_OPTIONS = [
  ["1m", "1 мин"], ["5m", "5 мин"], ["1h", "1 час"], ["3h", "3 часа"],
  ["1d", "Сутки"], ["1w", "Неделя"], ["1mo", "Месяц"], ["1q", "Квартал"], ["1y", "Год"],
];
let currentWindow = "5m";
// График всегда показывает минимум сутки истории, даже если для "было/стало"
// выбран более короткий период (см. CHART_WINDOW_SECONDS на бэкенде) — иначе
// график почти всегда был бы пустым при выборе "1 мин"/"5 мин"/"1 час"/"3 часа".
const CHART_BUCKET_LABELS = {
  "1m": "часовые свечи · сутки", "5m": "часовые свечи · сутки",
  "1h": "часовые свечи · сутки", "3h": "часовые свечи · сутки",
  "1d": "часовые свечи · сутки", "1w": "4-часовые свечи · неделя",
  "1mo": "дневные свечи · месяц", "1q": "дневные свечи · квартал",
  "1y": "недельные свечи · год",
};

function renderWindowRow(containerSel, onChange) {
  const container = el(containerSel);
  container.innerHTML = WINDOW_OPTIONS.map(([key, label]) =>
    `<button class="win-chip${key === currentWindow ? " active" : ""}" data-win="${key}">${label}</button>`
  ).join("");
  container.querySelectorAll(".win-chip").forEach(btn => {
    btn.onclick = () => {
      const key = btn.dataset.win;
      if (key === currentWindow) return;
      currentWindow = key;
      document.querySelectorAll(".win-chip").forEach(b => b.classList.toggle("active", b.dataset.win === key));
      onChange(key);
    };
  });
}

function el(sel) { return document.querySelector(sel); }
function fmtPrice(p) { return p != null ? p.toFixed(2) + "₽" : "—"; }

// Value без Demand/Rarity/Stability и без %-пилюль — в пикере/инвентаре/
// трейде нужна только сама цифра ценности, а не полная сводка как на
// карточке предмета.
function valueText(item) {
  const cv = (item.community_values || []).find(c => c.source === "mm2values");
  return cv && cv.value_raw != null ? `Value: ${cv.value_raw}` : "";
}
function changePill(change) {
  if (change == null) return "";
  const up = change > 0;
  return `<span class="pill ${up ? "up" : "down"}">${up ? ARROW_UP : ARROW_DOWN}${up ? "+" : ""}${change.toFixed(1)}%</span>`;
}

let backAction = null;
let priceChart = null;
let valueChart = null;

function showScreen(name) {
  document.querySelectorAll(".screen").forEach(s => s.classList.remove("active"));
  el("#screen-" + name).classList.add("active");
  el("#back-btn").style.display = name === "home" ? "none" : "flex";
  if (tg) {
    if (name === "home") tg.BackButton.hide();
    else { tg.BackButton.show(); }
  }
}

el("#back-btn").onclick = () => { if (backAction) backAction(); };
if (tg) tg.BackButton.onClick(() => { if (backAction) backAction(); });

function itemCard(item, onOpen) {
  const card = document.createElement("div");
  card.className = "item-card";
  const change = item.change_percent;
  const isLegacyOnly = item.cheaper_source === "legacy" && item.legacy_price != null && item.price == null;
  const hasDeal = item.cheaper_source === "legacy" && item.legacy_price != null && item.price != null;
  const mainPrice = item.best_price != null ? item.best_price : item.price;
  const meta = rareMeta(item.rare);
  const shortLabel = { mm2values: "MM2V" };
  const valuesLine = (item.community_values || [])
    .map(v => `${shortLabel[v.source] || v.label}: ${v.value_raw}`)
    .join(" · ");
  card.innerHTML = `
    <div class="thumb" style="background:linear-gradient(145deg, ${meta.tint}, rgba(20,21,28,0.9));">
      <img src="${item.image}" loading="lazy" alt="">
    </div>
    <div class="item-name">${escapeHtml(item.name)}</div>
    ${item.chroma ? '<div class="item-chroma"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 17c0 -5.523 -4.477 -10 -10 -10c-5.523 0 -10 4.477 -10 10" /><path d="M18 17a6 6 0 1 0 -12 0" /><path d="M14 17a2 2 0 1 0 -4 0" /></svg> Разноцветная хрома</div>' : ""}
    <div class="price-line"><div class="item-price num">${fmtPrice(mainPrice)}</div></div>
    <div class="change-row">
      ${item.prev_price != null ? `<div class="item-prev num">${fmtPrice(item.prev_price)}</div>` : ""}
      ${changePill(change)}
    </div>
    ${hasDeal ? `<div class="item-deal"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 3l0 7l6 0l-8 11l0 -7l-6 0l8 -11" /></svg> в обычном дороже: ${fmtPrice(item.price)}</div>` : ""}
    ${isLegacyOnly ? `<div class="item-deal"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 3l0 7l6 0l-8 11l0 -7l-6 0l8 -11" /></svg> только в Legacy-каталоге</div>` : ""}
    ${valuesLine ? `<div class="item-values"><span class="dot"></span>${escapeHtml(valuesLine)}</div>` : ""}
  `;
  card.querySelector("img").onerror = function() { this.src = PLACEHOLDER; };
  card.onclick = onOpen;
  return card;
}

async function loadHome() {
  showScreen("home");
  el("#header-title").textContent = "MM2 Каталог";
  backAction = null;

  el("#open-inventory-btn").onclick = loadInventory;
  el("#open-trade-btn").onclick = loadTrade;
  el("#open-fees-btn").onclick = loadFees;

  renderWindowRow("#home-win-row", () => loadHome());

  const res = await fetch(`/api/menu?window=${currentWindow}`);
  const data = await res.json();

  renderGameUpdate(data.game_update);
  renderMm2News(data.news);

  const catWrap = el("#categories");
  catWrap.innerHTML = "";
  for (const c of data.categories) {
    const meta = rareMeta(c.key);
    const btn = document.createElement("button");
    btn.className = `cat-btn ${meta.cls}`;
    btn.innerHTML = `
      <div class="icon">${CATEGORY_ICONS[c.key] || ""}</div>
      <div class="label">${meta.label}</div>
      <div class="count num">${c.count}</div>
    `;
    btn.onclick = () => loadCategory(c.key);
    catWrap.appendChild(btn);
  }

  const moversWrap = el("#movers");
  moversWrap.innerHTML = "";
  if (!data.movers.length) {
    moversWrap.innerHTML = '<div class="empty">Пока нет заметных изменений цен — они появятся, как только накопится история.</div>';
  } else {
    for (const item of data.movers) {
      moversWrap.appendChild(itemCard(item, () => loadItem(item.id, loadHome)));
    }
  }
}

let categoryItems = [];
let categorySearch = "";
let categorySort = "price_desc";
let categoryKeyLoaded = null;
let categoryChangeFilter = "all"; // "all" | "up" | "down"
let categoryThreshold = 10;

const SORTERS = {
  price_desc: (a, b) => (b.best_price ?? b.price ?? 0) - (a.best_price ?? a.price ?? 0),
  price_asc: (a, b) => (a.best_price ?? a.price ?? 0) - (b.best_price ?? b.price ?? 0),
  change_desc: (a, b) => (b.change_percent ?? -Infinity) - (a.change_percent ?? -Infinity),
  change_asc: (a, b) => (a.change_percent ?? Infinity) - (b.change_percent ?? Infinity),
};

function renderCategoryGrid(key) {
  const grid = el("#category-grid");
  grid.innerHTML = "";
  const query = categorySearch.trim().toLowerCase();
  let filtered = query ? categoryItems.filter(it => (it.name || "").toLowerCase().includes(query)) : categoryItems.slice();

  if (categoryChangeFilter !== "all") {
    filtered = filtered.filter(it => {
      if (it.change_percent == null) return false;
      return categoryChangeFilter === "up"
        ? it.change_percent >= categoryThreshold
        : it.change_percent <= -categoryThreshold;
    });
  }

  filtered.sort(SORTERS[categorySort] || SORTERS.price_desc);

  if (!filtered.length) {
    let msg = "Нет предметов с активными лотами в этой категории.";
    if (query) msg = `Ничего не найдено по запросу «${escapeHtml(categorySearch)}».`;
    else if (categoryChangeFilter !== "all") msg = `Нет предметов, которые ${categoryChangeFilter === "up" ? "выросли" : "упали"} на ${categoryThreshold}% и больше за выбранный период.`;
    grid.innerHTML = `<div class="empty">${msg}</div>`;
    return;
  }
  for (const item of filtered) {
    grid.appendChild(itemCard(item, () => loadItem(item.id, () => loadCategory(key))));
  }
}

async function loadCategory(key) {
  showScreen("category");
  el("#header-title").textContent = key;
  backAction = loadHome;

  if (categoryKeyLoaded !== key) { categorySearch = ""; categoryChangeFilter = "all"; }
  categoryKeyLoaded = key;

  renderWindowRow("#category-win-row", () => loadCategory(key));

  const searchInput = el("#category-search");
  searchInput.value = categorySearch;
  searchInput.oninput = () => { categorySearch = searchInput.value; renderCategoryGrid(key); };

  const sortSelect = el("#category-sort");
  sortSelect.value = categorySort;
  sortSelect.onchange = () => { categorySort = sortSelect.value; renderCategoryGrid(key); };

  const thresholdBox = el("#category-threshold-box");
  const thresholdInput = el("#category-threshold");
  thresholdInput.value = categoryThreshold;
  thresholdBox.style.display = categoryChangeFilter === "all" ? "none" : "flex";
  thresholdInput.oninput = () => {
    categoryThreshold = Math.max(0, Number(thresholdInput.value) || 0);
    renderCategoryGrid(key);
  };

  const filterChips = el("#category-change-filter");
  filterChips.querySelectorAll(".filter-chip").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.filter === categoryChangeFilter);
    btn.onclick = () => {
      categoryChangeFilter = btn.dataset.filter;
      filterChips.querySelectorAll(".filter-chip").forEach(b => b.classList.toggle("active", b.dataset.filter === categoryChangeFilter));
      thresholdBox.style.display = categoryChangeFilter === "all" ? "none" : "flex";
      renderCategoryGrid(key);
    };
  });

  const grid = el("#category-grid");
  grid.innerHTML = '<div class="loading">Загрузка…</div>';

  const res = await fetch(`/api/category/${key}?window=${currentWindow}`);
  const data = await res.json();
  el("#header-title").textContent = data.label;
  categoryItems = data.items;
  renderCategoryGrid(key);
}

function renderHistBadge(item) {
  const badge = el("#item-hist-badge");
  const price = item.best_price;
  // hist_max === hist_min значит, что по предмету пока только одна точка
  // истории (только начали отслеживать) — сравнивать не с чем, значок не
  // показываем, чтобы не выдавать это за настоящий рекорд.
  if (price == null || item.hist_min == null || item.hist_max == null || item.hist_max <= item.hist_min) {
    badge.style.display = "none";
    return;
  }
  const EPS = 0.001;
  if (price >= item.hist_max * (1 - EPS)) {
    badge.style.display = "flex";
    badge.className = "hist-badge high";
    badge.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6 -6l4 4l8 -8" /><path d="M14 7l7 0l0 7" /></svg> Дороже, чем когда-либо за всё время наблюдений';
  } else if (price <= item.hist_min * (1 + EPS)) {
    badge.style.display = "flex";
    badge.className = "hist-badge low";
    badge.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7l6 6l4 -4l8 8" /><path d="M21 10l0 7l-7 0" /></svg> Дешевле, чем когда-либо за всё время наблюдений';
  } else {
    badge.style.display = "none";
  }
}

function renderPriceChart(candles) {
  const chartEl = el("#chart");
  chartEl.innerHTML = "";
  if (!candles || !candles.length || !window.LightweightCharts) {
    chartEl.innerHTML = '<div class="empty">Пока недостаточно истории для графика — она копится каждые несколько минут.</div>';
    return;
  }
  priceChart = LightweightCharts.createChart(chartEl, {
    width: chartEl.clientWidth,
    height: 220,
    layout: { background: { color: "transparent" }, textColor: "#c8c8cc", fontFamily: "'Space Grotesk','Manrope',sans-serif" },
    grid: { vertLines: { visible: false }, horzLines: { color: "rgba(255,255,255,0.06)" } },
    timeScale: { timeVisible: true, secondsVisible: false, borderVisible: false, fixLeftEdge: true, fixRightEdge: true },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.18, bottom: 0.12 } },
    crosshair: { vertLine: { color: "rgba(79,140,255,0.35)", labelBackgroundColor: "#1c2942" }, horzLine: { color: "rgba(79,140,255,0.35)", labelBackgroundColor: "#1c2942" } },
    localization: { priceFormatter: (p) => p.toLocaleString("ru-RU", { maximumFractionDigits: 0 }) + "₽" },
  });
  const series = priceChart.addAreaSeries({
    lineColor: "#4f8cff", topColor: "rgba(79,140,255,0.28)", bottomColor: "rgba(79,140,255,0)",
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: candles.length > 1,
  });
  series.setData(candles.map(c => ({ time: c.time, value: c.close })));
  priceChart.timeScale().fitContent();
}

function renderValueChart(history, sourceLabel) {
  const block = el("#value-chart-block");
  const chartEl = el("#value-chart");
  chartEl.innerHTML = "";
  if (!history || !history.length || !window.LightweightCharts) {
    block.style.display = "none";
    return;
  }
  block.style.display = "block";
  el("#value-chart-source").textContent = sourceLabel || "";
  valueChart = LightweightCharts.createChart(chartEl, {
    width: chartEl.clientWidth,
    height: 160,
    layout: { background: { color: "transparent" }, textColor: "#c8c8cc", fontFamily: "'Space Grotesk','Manrope',sans-serif" },
    grid: { vertLines: { visible: false }, horzLines: { color: "rgba(255,255,255,0.06)" } },
    timeScale: { timeVisible: true, secondsVisible: false, borderVisible: false, fixLeftEdge: true, fixRightEdge: true },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.2, bottom: 0.12 } },
    crosshair: { vertLine: { color: "rgba(45,212,191,0.35)", labelBackgroundColor: "#1c3532" }, horzLine: { color: "rgba(45,212,191,0.35)", labelBackgroundColor: "#1c3532" } },
    localization: { priceFormatter: (v) => v.toLocaleString("ru-RU", { maximumFractionDigits: 0 }) },
  });
  const series = valueChart.addAreaSeries({
    lineColor: "#2dd4bf", topColor: "rgba(45,212,191,0.28)", bottomColor: "rgba(45,212,191,0)",
    lineWidth: 2,
    priceLineVisible: false,
    lastValueVisible: history.length > 1,
  });
  series.setData(history.map(p => ({ time: p.time, value: p.value })));
  valueChart.timeScale().fitContent();
}

window.addEventListener("resize", () => {
  const chartEl = el("#chart");
  if (priceChart && chartEl) priceChart.applyOptions({ width: chartEl.clientWidth });
  const valueChartEl = el("#value-chart");
  if (valueChart && valueChartEl) valueChart.applyOptions({ width: valueChartEl.clientWidth });
});

// ---------- Общий пикер предмета (для инвентаря и калькулятора трейда) ----------
// Пустой запрос сразу показывает список для "пролистать" (самые дорогие
// сначала) — не обязательно печатать название, можно просто проскроллить.

let pickerCallback = null;
let pickerDebounce = null;

function openPicker(onSelect) {
  pickerCallback = onSelect;
  el("#picker-modal").style.display = "flex";
  const input = el("#picker-search");
  input.value = "";
  el("#picker-results").innerHTML = '<div class="loading">Загрузка…</div>';
  loadPickerResults("");
  input.focus();
}

function closePicker() {
  el("#picker-modal").style.display = "none";
  pickerCallback = null;
}

el("#picker-close").onclick = closePicker;
el("#picker-modal").onclick = (e) => { if (e.target.id === "picker-modal") closePicker(); };

el("#picker-search").oninput = () => {
  clearTimeout(pickerDebounce);
  pickerDebounce = setTimeout(() => loadPickerResults(el("#picker-search").value.trim()), 250);
};

async function loadPickerResults(q) {
  const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
  const data = await res.json();
  renderPickerResults(data.items);
}

function renderPickerResults(items) {
  const wrap = el("#picker-results");
  if (!items.length) {
    wrap.innerHTML = '<div class="empty">Ничего не найдено.</div>';
    return;
  }
  wrap.innerHTML = "";
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "picker-row";
    row.innerHTML = `
      <img src="${item.image}" loading="lazy" alt="">
      <div class="picker-row-info">
        <div class="picker-row-name">${escapeHtml(item.name)}</div>
        <div class="picker-row-meta">${fmtPrice(item.best_price)}${valueText(item) ? " · " + valueText(item) : ""}</div>
      </div>
    `;
    row.querySelector("img").onerror = function() { this.src = PLACEHOLDER; };
    row.onclick = () => {
      const cb = pickerCallback;
      closePicker();
      if (cb) cb(item);
    };
    wrap.appendChild(row);
  }
}

// ---------- Сетка слотов-иконок (общая для инвентаря и калькулятора трейда) ----------
// Пустой слот с "+" открывает пикер; заполненный — иконка, цена, Value и
// свой степпер количества. Отдельная кнопка "Добавить" не нужна — сам слот
// ей и служит.

function renderSlotGrid(containerSel, items, { onAdd, onIncrement, onDecrement, onRemove }) {
  const wrap = el(containerSel);
  wrap.innerHTML = "";
  for (const item of items) {
    const tile = document.createElement("div");
    tile.className = "slot-tile";
    tile.innerHTML = `
      <div class="slot slot-filled">
        <img src="${item.image}" loading="lazy" alt="">
        <button class="slot-remove" aria-label="Убрать"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6l-12 12" /><path d="M6 6l12 12" /></svg></button>
      </div>
      <div class="slot-meta">
        <div class="slot-price">${fmtPrice(item.best_price)}</div>
        ${valueText(item) ? `<div class="slot-value">${valueText(item)}</div>` : ""}
        <div class="slot-stepper">
          <button class="slot-stepper-btn" data-act="minus">−</button>
          <span class="num">${item.quantity}</span>
          <button class="slot-stepper-btn" data-act="plus">+</button>
        </div>
      </div>
    `;
    tile.querySelector("img").onerror = function() { this.src = PLACEHOLDER; };
    tile.querySelector(".slot-remove").onclick = () => onRemove(item);
    tile.querySelector('[data-act="minus"]').onclick = () => onDecrement(item);
    tile.querySelector('[data-act="plus"]').onclick = () => onIncrement(item);
    wrap.appendChild(tile);
  }

  const addTile = document.createElement("div");
  addTile.className = "slot-tile";
  addTile.innerHTML = `<div class="slot slot-add">+</div><div class="slot-meta slot-add-label">Добавить</div>`;
  addTile.querySelector(".slot-add").onclick = onAdd;
  wrap.appendChild(addTile);
}

// ---------- Мой инвентарь ----------

let inventoryItems = [];

async function setInventoryQuantity(pid, quantity) {
  await fetch(`/api/inventory/${pid}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ quantity: Math.max(0, quantity) }),
  });
}

async function loadInventory() {
  showScreen("inventory");
  el("#header-title").textContent = "Мой инвентарь";
  backAction = loadHome;

  el("#inventory-list").innerHTML = '<div class="loading">Загрузка…</div>';
  const res = await fetch("/api/inventory");
  const data = await res.json();
  inventoryItems = data.items;
  renderInventoryList(data.items, data.total);
}

function renderInventoryList(items, total) {
  el("#inventory-total").textContent = fmtPrice(total);
  renderSlotGrid("#inventory-list", items, {
    onAdd: () => openPicker(async (item) => {
      const existing = inventoryItems.find(x => x.id === item.id);
      await setInventoryQuantity(item.id, (existing ? existing.quantity : 0) + 1);
      loadInventory();
    }),
    onIncrement: async (item) => { await setInventoryQuantity(item.id, item.quantity + 1); loadInventory(); },
    onDecrement: async (item) => { await setInventoryQuantity(item.id, item.quantity - 1); loadInventory(); },
    onRemove: async (item) => { await setInventoryQuantity(item.id, 0); loadInventory(); },
  });
}

// ---------- Калькулятор трейда ----------
// Живёт только в памяти вкладки (не сохраняется на сервере) — это быстрая
// прикидка "честный ли обмен", а не постоянные данные вроде инвентаря.

const trade = { a: [], b: [] };

function loadTrade() {
  showScreen("trade");
  el("#header-title").textContent = "Калькулятор трейда";
  backAction = loadHome;
  renderTrade();
}

function addToTradeSide(side, item) {
  const existing = side.find(x => x.id === item.id);
  if (existing) existing.quantity += 1;
  else side.push({ ...item, quantity: 1 });
}

function renderTradeSide(side, containerSel) {
  renderSlotGrid(containerSel, side, {
    onAdd: () => openPicker(item => { addToTradeSide(side, item); renderTrade(); }),
    onIncrement: (item) => { item.quantity += 1; renderTrade(); },
    onDecrement: (item) => { item.quantity = Math.max(1, item.quantity - 1); renderTrade(); },
    onRemove: (item) => {
      const idx = side.findIndex(x => x.id === item.id);
      if (idx >= 0) side.splice(idx, 1);
      renderTrade();
    },
  });
}

function tradeSideTotals(side) {
  let priceSum = 0, valueSum = 0, valueCount = 0;
  for (const it of side) {
    priceSum += (it.best_price || 0) * it.quantity;
    const cv = (it.community_values || []).find(c => c.source === "mm2values");
    if (cv && cv.value != null) {
      valueSum += cv.value * it.quantity;
      valueCount++;
    }
  }
  return { priceSum, valueSum, valueCount };
}

function renderTrade() {
  renderTradeSide(trade.a, "#trade-list-a");
  renderTradeSide(trade.b, "#trade-list-b");

  const a = tradeSideTotals(trade.a);
  const b = tradeSideTotals(trade.b);
  el("#trade-a-total").textContent = fmtPrice(a.priceSum);
  el("#trade-b-total").textContent = fmtPrice(b.priceSum);

  const verdictEl = el("#trade-verdict");
  if (!trade.a.length && !trade.b.length) {
    verdictEl.innerHTML = "";
    return;
  }

  // Честность трейда обычно смотрят по Community Value, а не по цене
  // каталога (обмен ведь идёт не за деньги) — но Value есть не у всех
  // предметов (mm2values покрывает только Godly/Ancient/Unique), поэтому
  // сравниваем по Value, только если она известна для предметов ОБЕИХ сторон.
  const useValue = a.valueCount === trade.a.length && b.valueCount === trade.b.length && trade.a.length && trade.b.length;
  const sumA = useValue ? a.valueSum : a.priceSum;
  const sumB = useValue ? b.valueSum : b.priceSum;
  const basisLabel = useValue ? "Community Value" : "цене каталога";
  const diff = sumA - sumB;
  const bigger = Math.max(sumA, sumB) || 1;
  const diffPercent = Math.abs(diff) / bigger * 100;

  let verdictText;
  if (diffPercent < 5) {
    verdictText = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 20l10 0" /><path d="M6 6l6 -1l6 1" /><path d="M12 3l0 17" /><path d="M9 12l-3 -6l-3 6a3 3 0 0 0 6 0" /><path d="M21 12l-3 -6l-3 6a3 3 0 0 0 6 0" /></svg> Обмен примерно честный по ${basisLabel} (разница ${diffPercent.toFixed(0)}%)`;
  } else if (diff > 0) {
    verdictText = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6 -6l4 4l8 -8" /><path d="M14 7l7 0l0 7" /></svg> Ваша сторона дороже по ${basisLabel} на ${diffPercent.toFixed(0)}% — обмен невыгоден вам`;
  } else {
    verdictText = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7l6 6l4 -4l8 8" /><path d="M21 10l0 7l-7 0" /></svg> Их сторона дороже по ${basisLabel} на ${diffPercent.toFixed(0)}% — обмен выгоден вам`;
  }
  const note = !useValue && (a.valueCount > 0 || b.valueCount > 0)
    ? '<div class="trade-verdict-note">Не у всех предметов есть Community Value — сравниваем по цене каталога.</div>'
    : "";
  verdictEl.innerHTML = `<div class="trade-verdict-text">${verdictText}</div>${note}`;
}

// ---------- Калькулятор комиссий DreamPets (пополнение/вывод) ----------
// Комиссии — реальные, из открытого API самого DreamPets (см.
// /api/dreampets_fees на бэкенде), а не выдуманные проценты. Направление
// расчёта — стандартная для платёжных агрегаторов модель "комиссия берётся
// от суммы платежа/вывода" (см. заметку под калькулятором в разметке) —
// если точный процент на сайте будет отличаться, поправим формулу.

let dreampetsFees = null;

function methodLabel(m) {
  const parts = [m.system];
  if (m.method) parts.push(m.method);
  return parts.join(" · ");
}

function calcTopupTotal(price, method) {
  const rate = (method.commission_rate || 0) / 100;
  return price / (1 - rate);
}

function calcWithdrawalNet(price, method) {
  const rate = (method.commission_rate || 0) / 100;
  return price * (1 - rate) - (method.fixed_commission || 0);
}

function populateFeesSelect(selectEl, methods) {
  selectEl.innerHTML = methods.map((m, i) => {
    const extra = m.fixed_commission ? ` +${m.fixed_commission}₽` : "";
    return `<option value="${i}">${escapeHtml(methodLabel(m))} — ${m.commission_rate}%${extra}</option>`;
  }).join("");
}

function renderFeesBuyResult() {
  const price = parseFloat(el("#fees-buy-price").value);
  const methods = dreampetsFees.topup_methods;
  const resultEl = el("#fees-buy-result");
  if (!price || price <= 0 || !methods.length) { resultEl.innerHTML = ""; return; }
  const method = methods[Number(el("#fees-buy-method").value)];
  const total = calcTopupTotal(price, method);
  const feeAmount = total - price;
  resultEl.innerHTML = `
    <div class="fees-result-main">К оплате: <b>${fmtPrice(total)}</b></div>
    <div class="fees-result-sub">Из них комиссия за пополнение: ${fmtPrice(feeAmount)} (${method.commission_rate}%)</div>
  `;
}

function renderFeesSellResult() {
  const price = parseFloat(el("#fees-sell-price").value);
  const methods = dreampetsFees.withdrawal_methods;
  const resultEl = el("#fees-sell-result");
  if (!price || price <= 0 || !methods.length) { resultEl.innerHTML = ""; return; }
  const method = methods[Number(el("#fees-sell-method").value)];
  const net = Math.max(0, calcWithdrawalNet(price, method));
  const feeAmount = price - net;
  resultEl.innerHTML = `
    <div class="fees-result-main">Получите на карту: <b>${fmtPrice(net)}</b></div>
    <div class="fees-result-sub">Комиссия за вывод: ${fmtPrice(feeAmount)} (${method.commission_rate}%${method.fixed_commission ? ` + ${method.fixed_commission}₽` : ""})</div>
  `;
}

async function loadFees() {
  showScreen("fees");
  el("#header-title").textContent = "Комиссии DreamPets";
  backAction = loadHome;

  if (!dreampetsFees) {
    const res = await fetch("/api/dreampets_fees");
    dreampetsFees = await res.json();
  }
  populateFeesSelect(el("#fees-buy-method"), dreampetsFees.topup_methods);
  populateFeesSelect(el("#fees-sell-method"), dreampetsFees.withdrawal_methods);

  el("#fees-buy-price").oninput = renderFeesBuyResult;
  el("#fees-buy-method").onchange = renderFeesBuyResult;
  el("#fees-sell-price").oninput = renderFeesSellResult;
  el("#fees-sell-method").onchange = renderFeesSellResult;

  el("#fees-buy-pick").onclick = () => openPicker(item => {
    el("#fees-buy-price").value = item.best_price != null ? item.best_price.toFixed(2) : "";
    renderFeesBuyResult();
  });
  el("#fees-sell-pick").onclick = () => openPicker(item => {
    el("#fees-sell-price").value = item.best_price != null ? item.best_price.toFixed(2) : "";
    renderFeesSellResult();
  });

  el(".fees-tabs").querySelectorAll(".fees-tab").forEach(btn => {
    btn.onclick = () => {
      el(".fees-tabs").querySelectorAll(".fees-tab").forEach(b => b.classList.toggle("active", b === btn));
      el("#fees-buy").style.display = btn.dataset.tab === "buy" ? "block" : "none";
      el("#fees-sell").style.display = btn.dataset.tab === "sell" ? "block" : "none";
    };
  });

  renderFeesBuyResult();
  renderFeesSellResult();
}

async function loadItem(id, backFn) {
  showScreen("item");
  backAction = backFn || loadHome;
  el("#header-title").textContent = "…";

  renderWindowRow("#item-win-row", () => loadItem(id, backFn));

  const res = await fetch(`/api/item/${id}?window=${currentWindow}`);
  if (!res.ok) {
    el("#header-title").textContent = "Не найдено";
    return;
  }
  const item = await res.json();
  const meta = rareMeta(item.rare);

  el("#header-title").textContent = item.name;
  el("#item-image").src = item.image;
  el("#item-image").onerror = function() { this.src = PLACEHOLDER; };
  el("#item-name").textContent = item.name;
  el("#item-sub").innerHTML = `
    <span class="chip ${meta.cls}">${meta.label}</span>
    <span class="chip">${escapeHtml(item.category || "")}</span>
    ${item.chroma ? '<span class="chip chroma"><svg viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 17c0 -5.523 -4.477 -10 -10 -10c-5.523 0 -10 4.477 -10 10" /><path d="M18 17a6 6 0 1 0 -12 0" /><path d="M14 17a2 2 0 1 0 -4 0" /></svg> Разноцветная хрома</span>' : ""}
  `;
  const mainPrice = item.best_price != null ? item.best_price : item.price;
  el("#item-price").textContent = fmtPrice(mainPrice);
  el("#item-prev").textContent = item.prev_price != null ? fmtPrice(item.prev_price) : "";
  el("#item-change").innerHTML = changePill(item.change_percent);
  renderHistBadge(item);

  const invQtyEl = el("#item-inv-qty");
  let invQty = item.inventory_quantity || 0;
  invQtyEl.textContent = invQty;
  el("#item-inv-minus").onclick = async () => {
    invQty = Math.max(0, invQty - 1);
    invQtyEl.textContent = invQty;
    await setInventoryQuantity(item.id, invQty);
  };
  el("#item-inv-plus").onclick = async () => {
    invQty += 1;
    invQtyEl.textContent = invQty;
    await setInventoryQuantity(item.id, invQty);
  };

  const dealNote = el("#deal-note");
  const buyGroup = el("#buy-group");
  buyGroup.innerHTML = "";

  const hasCurrent = item.price != null;
  const hasLegacy = item.legacy_price != null;
  const legacyCheaper = item.cheaper_source === "legacy";

  // Предмет мог сейчас быть распродан в текущем каталоге, но всё ещё
  // продаваться в Legacy (см. price_history.has_any_price) — тогда сравнивать
  // "дороже/дешевле" не с чем, просто уточняем, где именно есть в наличии.
  if (hasLegacy && legacyCheaper && hasCurrent) {
    dealNote.style.display = "flex";
    el("#deal-note-text").innerHTML = `<b>В обычном каталоге дороже</b> — ${fmtPrice(item.price)} там же`;
  } else if (hasLegacy && !hasCurrent) {
    dealNote.style.display = "flex";
    el("#deal-note-text").innerHTML = `<b>Сейчас есть только в Legacy-каталоге</b> — в текущем распродано`;
  } else {
    dealNote.style.display = "none";
  }

  // Кнопка покупки там, где дешевле — первой и выделенной; вторая ссылка — как альтернатива.
  // Если предмета сейчас нет в одном из каталогов вообще — кнопки для него нет,
  // а не "Купить за —" в никуда.
  const currentBtn = hasCurrent
    ? `<a class="buy-btn" href="${item.buy_url}" target="_blank" rel="noopener">Купить за ${fmtPrice(item.price)}<span class="tag">Текущий каталог</span></a>`
    : "";
  const legacyBtn = hasLegacy
    ? `<a class="buy-btn secondary" href="${item.legacy_buy_url}" target="_blank" rel="noopener">Купить за ${fmtPrice(item.legacy_price)}<span class="tag">Legacy-каталог</span></a>`
    : "";

  // FunPay сопоставлен по неточному текстовому совпадению названия в
  // объявлении продавца (см. price_history.update_funpay) — поэтому всегда
  // третья, второстепенная кнопка, никогда не главная/выделенная, даже если
  // там формально дешевле всего.
  const funpayBtn = item.funpay_price != null
    ? `<a class="buy-btn secondary" href="${item.funpay_url}" target="_blank" rel="noopener">Купить за ${fmtPrice(item.funpay_price)}<span class="tag">FunPay</span></a>`
    : "";

  buyGroup.innerHTML = (legacyCheaper ? (legacyBtn.replace('secondary', '') + currentBtn.replace('buy-btn', 'buy-btn secondary')) : (currentBtn + legacyBtn)) + funpayBtn;
  el("#funpay-note").style.display = item.funpay_price != null ? "block" : "none";

  // Community value (mm2values.com) — справочно, не цена покупки.
  const cvWrap = el("#community-values");
  const communityValues = item.community_values || [];
  if (communityValues.length) {
    cvWrap.style.display = "flex";
    cvWrap.innerHTML = communityValues.map(v => `
      <div class="cv-panel">
        <div class="cv-head">
          <a class="src" href="${v.url}" target="_blank" rel="noopener"><span class="dot"></span>Community value · ${escapeHtml(v.label)}</a>
          <span class="tag">не цена покупки</span>
        </div>
        <div class="cv-stats">
          <div class="cv-stat"><div class="k">Value</div><div class="v num">${escapeHtml(String(v.value_raw ?? "—"))}</div></div>
          <div class="cv-stat"><div class="k">Demand</div><div class="v num">${escapeHtml(String(v.demand ?? "—"))}</div></div>
          <div class="cv-stat"><div class="k">Rarity</div><div class="v num">${escapeHtml(String(v.rarity ?? "—"))}</div></div>
          <div class="cv-stat"><div class="k">Stability</div><div class="v">${escapeHtml(String(v.stability ?? "—"))}</div></div>
        </div>
      </div>
    `).join("");
  } else {
    cvWrap.style.display = "none";
    cvWrap.innerHTML = "";
  }

  // Уточняем, из какого каталога история цены на графике — она берётся из
  // того же источника, что и крупная цена/% над ним (см. cheaper_source),
  // а не всегда из "текущего", чтобы цифры и линия на графике не расходились.
  const chartSourceLabel = legacyCheaper ? "Legacy" : "Текущий";
  const bucketLabel = CHART_BUCKET_LABELS[item.window] || "";
  el("#chart-bucket-hint").textContent = bucketLabel ? `${bucketLabel} · ${chartSourceLabel}` : chartSourceLabel;
  renderPriceChart(item.candles);

  const firstWithHistory = communityValues.find(v => v.history && v.history.length);
  renderValueChart(firstWithHistory ? firstWithHistory.history : null, firstWithHistory ? firstWithHistory.label : null);
}

loadHome();
