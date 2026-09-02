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

const RARE_META = {
  godly: { label: "Godly", cls: "rare-godly", tint: "rgba(242,201,76,0.16)" },
  ancient: { label: "Ancient", cls: "rare-ancient", tint: "rgba(79,140,255,0.16)" },
  unique: { label: "Unique", cls: "rare-unique", tint: "rgba(155,124,245,0.16)" },
};
function rareMeta(rare) {
  return RARE_META[(rare || "").toLowerCase()] || { label: rare || "?", cls: "", tint: "rgba(255,255,255,0.05)" };
}

const CATEGORY_ICONS = {
  godly: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M12 2l2.6 6.2L21 9l-5 4.6L17.4 21 12 17.3 6.6 21 8 13.6 3 9l6.4-0.8L12 2z" stroke="#f2c94c" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  ancient: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><path d="M12 3c-3 2-6 2.5-6 2.5v6c0 4.5 3 7.5 6 8.5 3-1 6-4 6-8.5v-6S15 5 12 3z" stroke="#4f8cff" stroke-width="1.6" stroke-linejoin="round"/></svg>',
  unique: '<svg width="17" height="17" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="3.2" stroke="#9b7cf5" stroke-width="1.6"/><path d="M12 2v3.2M12 18.8V22M2 12h3.2M18.8 12H22" stroke="#9b7cf5" stroke-width="1.6" stroke-linecap="round"/></svg>',
};

const ARROW_UP = '<svg width="9" height="9" viewBox="0 0 24 24" fill="none"><path d="M12 20V4M12 4l-6 6M12 4l6 6" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const ARROW_DOWN = '<svg width="9" height="9" viewBox="0 0 24 24" fill="none"><path d="M12 4v16M12 20l-6-6M12 20l6-6" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

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
function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function fmtPrice(p) { return p != null ? p.toFixed(2) + "₽" : "—"; }
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
  const hasDeal = item.cheaper_source === "legacy" && item.legacy_price != null;
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
    <div class="price-line"><div class="item-price num">${fmtPrice(mainPrice)}</div></div>
    <div class="change-row">
      ${item.prev_price != null ? `<div class="item-prev num">${fmtPrice(item.prev_price)}</div>` : ""}
      ${changePill(change)}
    </div>
    ${hasDeal ? `<div class="item-deal">⚡ в обычном дороже: ${fmtPrice(item.price)}</div>` : ""}
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

  renderWindowRow("#home-win-row", () => loadHome());

  const res = await fetch(`/api/menu?window=${currentWindow}`);
  const data = await res.json();

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

function withMinCandleBody(candles) {
  // Свечи без изменения цены (open === close) иначе рисуются нулевой высоты —
  // на глаз это невидимая тонкая полоска ("посошек"), а не прямоугольник.
  // Растягиваем тело таких свечей до минимальной заметной высоты — только
  // для отрисовки; реальная цена (текст над графиком, бейдж %) не трогается.
  let min = Infinity, max = -Infinity;
  for (const c of candles) {
    if (c.low < min) min = c.low;
    if (c.high > max) max = c.high;
  }
  const range = (max - min) || Math.abs(candles[0].open) * 0.02 || 1;
  const minBody = range * 0.035;
  return candles.map(c => {
    if (Math.abs(c.close - c.open) >= minBody) return c;
    const up = c.close >= c.open;
    const close = up ? c.open + minBody : c.open - minBody;
    return { time: c.time, open: c.open, close, high: Math.max(c.high, close), low: Math.min(c.low, close) };
  });
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
    crosshair: { vertLine: { color: "rgba(255,255,255,0.18)", labelBackgroundColor: "#242832" }, horzLine: { color: "rgba(255,255,255,0.18)", labelBackgroundColor: "#242832" } },
    localization: { priceFormatter: (p) => p.toLocaleString("ru-RU", { maximumFractionDigits: 0 }) + "₽" },
  });
  const series = priceChart.addCandlestickSeries({
    upColor: "#33c266", downColor: "#ef5350",
    borderVisible: false, wickUpColor: "#33c266", wickDownColor: "#ef5350",
    priceLineVisible: false,
    // Точную последнюю цену и так крупно показывает текст над графиком — а
    // тут после "растяжки" плоских свечей цифра была бы чуть неточной.
    lastValueVisible: false,
  });
  series.setData(withMinCandleBody(candles));
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
    ${item.chroma ? '<span class="chip">Chroma</span>' : ""}
  `;
  const mainPrice = item.best_price != null ? item.best_price : item.price;
  el("#item-price").textContent = fmtPrice(mainPrice);
  el("#item-prev").textContent = item.prev_price != null ? fmtPrice(item.prev_price) : "";
  el("#item-change").innerHTML = changePill(item.change_percent);

  const dealNote = el("#deal-note");
  const buyGroup = el("#buy-group");
  buyGroup.innerHTML = "";

  const hasLegacy = item.legacy_price != null;
  const legacyCheaper = item.cheaper_source === "legacy";

  if (hasLegacy && legacyCheaper) {
    dealNote.style.display = "flex";
    el("#deal-note-text").innerHTML = `<b>В обычном каталоге дороже</b> — ${fmtPrice(item.price)} там же`;
  } else {
    dealNote.style.display = "none";
  }

  // Кнопка покупки там, где дешевле — первой и выделенной; вторая ссылка — как альтернатива.
  const currentBtn = `<a class="buy-btn" href="${item.buy_url}" target="_blank" rel="noopener">Купить за ${fmtPrice(item.price)}<span class="tag">Текущий каталог</span></a>`;
  const legacyBtn = hasLegacy
    ? `<a class="buy-btn secondary" href="${item.legacy_buy_url}" target="_blank" rel="noopener">Купить за ${fmtPrice(item.legacy_price)}<span class="tag">Legacy-каталог</span></a>`
    : "";

  buyGroup.innerHTML = legacyCheaper ? (legacyBtn.replace('secondary', '') + currentBtn.replace('buy-btn', 'buy-btn secondary')) : (currentBtn + legacyBtn);

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
