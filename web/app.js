const STORAGE_KEY = "worldcup-score-ui-matches-20260616-live";
const SINGLE_STAKE = 100;
const PARLAY_STAKE = 10;
const TARGET_EDGE = 1.1;
const SPORTTERY_SCORE_WEIGHT = 0.45;

const state = {
  matches: [],
  selectedId: null,
  filter: "upcoming",
  upcomingListOpen: false,
  sourceName: "未载入真实数据",
  lastUpdated: "-",
};

const statusLabels = {
  live: "进行中",
  upcoming: "未开始",
  finished: "已结束",
};

const performanceFields = [
  { key: "form", label: "近期状态", weight: 0.24 },
  { key: "attack", label: "进攻表现", weight: 0.18 },
  { key: "defense", label: "防守表现", weight: 0.18 },
  { key: "playerHealth", label: "关键球员健康", weight: 0.16 },
  { key: "starPower", label: "核心球员影响", weight: 0.14 },
  { key: "goalkeeper", label: "门将/后防", weight: 0.06 },
  { key: "stamina", label: "体能赛程", weight: 0.04 },
];

bootstrap();

function bootstrap() {
  loadInitialData();

  document.querySelector("#statusFilter").addEventListener("change", (event) => {
    state.filter = event.target.value;
    ensureSelectedMatch();
    render();
  });

  document.querySelector("#dataFile").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      const payload = JSON.parse(await file.text());
      applyPayload(payload, file.name);
      localStorage.setItem(STORAGE_KEY, JSON.stringify(toPayload()));
      render();
    } catch (error) {
      renderError(`JSON 解析失败：${error.message}`);
    } finally {
      event.target.value = "";
    }
  });

  document.querySelector("#clearData").addEventListener("click", () => {
    localStorage.removeItem(STORAGE_KEY);
    if (window.WORLD_CUP_MATCHES) {
      applyPayload(window.WORLD_CUP_MATCHES, "web/data/matches.js");
    } else {
      state.matches = [];
      state.selectedId = null;
      state.sourceName = "未载入真实数据";
      state.lastUpdated = "-";
    }
    render();
  });

  render();
}

function loadInitialData() {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved) {
    try {
      const savedPayload = JSON.parse(saved);
      if (savedPayload.userImported) {
        const normalized = normalizePayload(savedPayload, "浏览器本地导入");
        state.matches = normalized.matches;
        state.sourceName = normalized.sourceName;
        state.lastUpdated = normalized.lastUpdated;
        state.selectedId = state.matches[0]?.id ?? null;
        ensureSelectedMatch();
        return;
      }
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      localStorage.removeItem(STORAGE_KEY);
    }
  }

  if (window.WORLD_CUP_MATCHES) {
    applyPayload(window.WORLD_CUP_MATCHES, "web/data/matches.js");
  }
}

function applyPayload(payload, fallbackSourceName) {
  const normalized = normalizePayload(payload, fallbackSourceName);
  state.matches = normalized.matches;
  state.sourceName = normalized.sourceName;
  state.lastUpdated = normalized.lastUpdated;
  state.selectedId = pickDefaultMatchId(state.matches);
  ensureSelectedMatch();
}

function toPayload() {
  return {
    userImported: true,
    sourceName: state.sourceName,
    lastUpdated: state.lastUpdated,
    matches: state.matches,
  };
}

function normalizePayload(payload, fallbackSourceName) {
  if (Array.isArray(payload)) {
    return {
      sourceName: fallbackSourceName,
      lastUpdated: new Date().toISOString(),
      matches: payload.map(normalizeMatch).filter(Boolean),
    };
  }

  if (payload?.matches) {
    return {
      sourceName: String(payload.sourceName || fallbackSourceName),
      lastUpdated: String(payload.lastUpdated || "-"),
      matches: payload.matches.map(normalizeMatch).filter(Boolean),
    };
  }

  if (payload?.events) {
    return {
      sourceName: String(payload.sourceName || fallbackSourceName),
      lastUpdated: String(payload.lastUpdated || "-"),
      matches: payload.events.map(eventToMatch).filter(Boolean),
    };
  }

  throw new Error("需要 matches 数组或 arbitrage events 数组");
}

function normalizeMatch(raw, index) {
  if (!raw) return null;
  const id = String(raw.id || `match-${index}`);
  const home = String(raw.home || raw.homeTeam || "待定");
  const away = String(raw.away || raw.awayTeam || "待定");
  const score = Array.isArray(raw.score) ? raw.score : [raw.homeScore ?? "-", raw.awayScore ?? "-"];
  return {
    id,
    status: normalizeStatus(raw.status),
    stage: String(raw.stage || raw.round || "-"),
    kickoff: String(raw.kickoff || raw.startTime || "-"),
    minute: String(raw.minute || statusLabels[normalizeStatus(raw.status)] || "-"),
    home,
    away,
    score,
    venue: String(raw.venue || "-"),
    possession: String(raw.possession || "-"),
    xg: String(raw.xg || "-"),
    odds: Array.isArray(raw.odds) ? raw.odds.map(normalizeOdd).filter(Boolean) : [],
    arbitrage: normalizeArbitrage(raw.arbitrage),
    timeline: Array.isArray(raw.timeline) ? raw.timeline.map(normalizeTimelineItem).filter(Boolean) : [],
    sources: Array.isArray(raw.sources) ? raw.sources.map(String) : [],
    marketNotes: Array.isArray(raw.marketNotes) ? raw.marketNotes.map(String) : [],
    sporttery: normalizeSporttery(raw.sporttery),
    hupu: normalizeHupu(raw.hupu),
    panewsAi: normalizePanewsAi(raw.panewsAi),
    performance: normalizePerformance(raw.performance),
    performanceNotes: Array.isArray(raw.performanceNotes) ? raw.performanceNotes.map(String) : [],
  };
}

function eventToMatch(event, index) {
  if (!event) return null;
  return normalizeMatch(
    {
      id: event.id || `event-${index}`,
      stage: event.title || event.id,
      home: event.home || "市场",
      away: event.away || "结果",
      status: event.status || "upcoming",
      kickoff: event.kickoff || "-",
      odds: eventToOdds(event),
      arbitrage: event.arbitrage,
    },
    index,
  );
}

function eventToOdds(event) {
  const outcomes = new Map((event.outcomes || []).map((outcome) => [outcome.id, outcome.label || outcome.id]));
  const grouped = new Map();

  for (const instrument of event.instruments || []) {
    const label = outcomes.get(instrument.outcome) || instrument.outcome;
    const row = grouped.get(instrument.outcome) || { outcome: label };
    if (instrument.type === "fixed_odds") row.sporttery = Number(instrument.decimal_odds);
    if (instrument.type === "prediction_share") {
      row.polymarket = Number(instrument.ask_price ?? instrument.ask_levels?.[0]?.price);
    }
    grouped.set(instrument.outcome, row);
  }

  return Array.from(grouped.values());
}

function normalizeOdd(raw) {
  if (!raw?.outcome) return null;
  return {
    outcome: String(raw.outcome),
    referenceOdds: toNumber(raw.referenceOdds),
    referenceMovement: raw.referenceMovement ? String(raw.referenceMovement) : "",
    sporttery: toNumber(raw.sporttery),
    polymarket: toNumber(raw.polymarket),
    polymarketBid: toNumber(raw.polymarketBid),
    polymarketAsk: toNumber(raw.polymarketAsk),
    polymarketSlug: raw.polymarketSlug ? String(raw.polymarketSlug) : "",
    referenceValid: raw.referenceValid === false ? false : true,
    referenceNote: raw.referenceNote ? String(raw.referenceNote) : "",
    bookmakers: Array.isArray(raw.bookmakers) ? raw.bookmakers.map(normalizeBookmaker).filter(Boolean) : [],
    movement: toNumber(raw.movement, 0),
  };
}

function normalizeBookmaker(raw) {
  if (!raw?.book) return null;
  const americanOdds = toNumber(raw.americanOdds);
  const decimalOdds = toNumber(raw.decimalOdds) ?? decimalFromAmerican(americanOdds);
  if (!Number.isFinite(decimalOdds)) return null;
  return {
    book: String(raw.book),
    americanOdds,
    decimalOdds,
    source: raw.source ? String(raw.source) : "",
    sourceUrl: raw.sourceUrl ? String(raw.sourceUrl) : "",
  };
}

function normalizeArbitrage(raw) {
  return {
    costRatio: toNumber(raw?.costRatio),
    profit: toNumber(raw?.profit),
    target: toNumber(raw?.target),
  };
}

function normalizeSporttery(raw) {
  return {
    matchId: raw?.matchId ? String(raw.matchId) : "",
    matchNumStr: raw?.matchNumStr ? String(raw.matchNumStr) : "",
    sourceUrl: raw?.sourceUrl ? String(raw.sourceUrl) : "",
    lastUpdated: raw?.lastUpdated ? String(raw.lastUpdated) : "",
    had: normalizeSportteryPool(raw?.had),
    hhad: normalizeSportteryPool(raw?.hhad),
    correctScore: normalizeScoreOdds(raw?.correctScore),
  };
}

function normalizeHupu(raw) {
  return {
    matchId: raw?.matchId ? String(raw.matchId) : "",
    status: raw?.status ? String(raw.status) : "",
    sourceUrl: raw?.sourceUrl ? String(raw.sourceUrl) : "",
    heat: toNumber(raw?.heat),
    ratingCount: toNumber(raw?.ratingCount),
    ratingText: raw?.ratingText ? String(raw.ratingText) : "",
  };
}

function normalizePanewsAi(raw) {
  if (!raw || typeof raw !== "object") return null;
  const models = Array.isArray(raw.models) ? raw.models.map(normalizePanewsModel).filter(Boolean) : [];
  return {
    sourceName: raw.sourceName ? String(raw.sourceName) : "PANews AI Arena",
    sourceUrl: raw.sourceUrl ? String(raw.sourceUrl) : "https://worldcup.panewslab.com/",
    matchUrl: raw.matchUrl ? String(raw.matchUrl) : "",
    arenaMatchId: raw.arenaMatchId ? String(raw.arenaMatchId) : "",
    lastUpdated: raw.lastUpdated ? String(raw.lastUpdated) : "",
    marketPrices: normalizeProbabilityObject(raw.marketPrices),
    consensus: normalizePanewsConsensus(raw.consensus),
    models,
    note: raw.note ? String(raw.note) : "外部 AI 交易观点，来自 PANews World Cup AI Arena 公开账本；不等同本站概率模型。",
  };
}

function normalizePanewsModel(raw) {
  if (!raw?.modelId) return null;
  return {
    modelId: String(raw.modelId),
    name: raw.name ? String(raw.name) : String(raw.modelId),
    short: raw.short ? String(raw.short) : String(raw.modelId),
    color: raw.color ? String(raw.color) : "#255fc7",
    latestAction: raw.latestAction ? String(raw.latestAction) : "hold",
    outcome: raw.outcome ? String(raw.outcome) : "",
    probabilities: normalizeProbabilityObject(raw.probabilities),
    reason: raw.reason ? String(raw.reason) : "",
    amount: toNumber(raw.amount),
    price: toNumber(raw.price),
    shares: toNumber(raw.shares),
    positionValue: toNumber(raw.positionValue),
    updatedAt: raw.updatedAt ? String(raw.updatedAt) : "",
  };
}

function normalizePanewsConsensus(raw) {
  return {
    modelCount: toNumber(raw?.modelCount, 0),
    topOutcome: raw?.topOutcome ? String(raw.topOutcome) : "",
    topProbability: toNumber(raw?.topProbability),
    averageProbabilities: normalizeProbabilityObject(raw?.averageProbabilities),
    agreement: toNumber(raw?.agreement),
  };
}

function normalizeProbabilityObject(raw) {
  return {
    home: toNumber(raw?.home),
    draw: toNumber(raw?.draw),
    away: toNumber(raw?.away),
  };
}

function normalizeSportteryPool(raw) {
  return {
    home: toNumber(raw?.home),
    draw: toNumber(raw?.draw),
    away: toNumber(raw?.away),
    goalLine: raw?.goalLine ? String(raw.goalLine) : "",
    lastUpdated: raw?.lastUpdated ? String(raw.lastUpdated) : "",
  };
}

function normalizeScoreOdds(raw) {
  if (!raw || typeof raw !== "object") return {};
  return Object.fromEntries(
    Object.entries(raw)
      .map(([score, odds]) => [String(score), toNumber(odds)])
      .filter(([, odds]) => Number.isFinite(odds)),
  );
}

function normalizeTimelineItem(raw) {
  if (!raw) return null;
  return {
    minute: String(raw.minute || "-"),
    title: String(raw.title || "-"),
    text: String(raw.text || ""),
  };
}

function normalizePerformance(raw) {
  return {
    home: normalizeTeamPerformance(raw?.home),
    away: normalizeTeamPerformance(raw?.away),
  };
}

function normalizeTeamPerformance(raw) {
  return Object.fromEntries(performanceFields.map((field) => [field.key, clampScore(raw?.[field.key] ?? 50)]));
}

function normalizeStatus(status) {
  if (status === "live" || status === "upcoming" || status === "finished") return status;
  return "upcoming";
}

function clampScore(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 50;
  return Math.max(0, Math.min(100, number));
}

function clampNumber(value, low, high) {
  if (!Number.isFinite(value)) return low;
  return Math.max(low, Math.min(high, value));
}

function toNumber(value, fallback = null) {
  if (value === null || value === undefined || value === "") return fallback;
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function render() {
  renderSource();
  renderSummary();
  renderMatchList();
  renderDetail();
}

function renderSource() {
  const sourceName = document.querySelector("#sourceName");
  const lastUpdated = document.querySelector("#lastUpdated");
  sourceName.textContent = compactSourceName(state.sourceName);
  sourceName.title = state.sourceName;
  lastUpdated.textContent = compactLastUpdated(state.lastUpdated);
  lastUpdated.title = state.lastUpdated;
}

function renderSummary() {
  const liveCount = state.matches.filter((match) => match.status === "live").length;
  const upcomingCount = state.matches.filter((match) => match.status === "upcoming").length;
  const finishedCount = state.matches.filter((match) => match.status === "finished").length;
  const referenceMatches = state.matches.filter(hasCompleteReferenceVector).length;
  const pmMatches = state.matches.filter(hasPolymarketPrices).length;
  const modelMatches = state.matches.filter(hasCompleteMarketVector).length;
  const sportteryMatches = state.matches.filter(hasSportteryData).length;
  const panewsMatches = state.matches.filter(hasPanewsAiData).length;

  document.querySelector("#summaryGrid").innerHTML = [
    summaryItem("未开始", upcomingCount, "upcoming", "场"),
    summaryItem("进行中", liveCount, "live", "场"),
    summaryItem("已结束", finishedCount, "finished", "场"),
    summaryItem("体彩覆盖", sportteryMatches || "-", "sporttery", "场"),
    summaryItem("AI 观点", panewsMatches || "-", "ai", "场"),
    summaryItem("可预测", modelMatches || "-", "model", "场"),
  ].join("");
}

function summaryItem(label, value, tone = "", suffix = "") {
  return `
    <div class="summary-item ${tone ? `summary-${tone}` : ""}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}${suffix && value !== "-" ? `<small>${escapeHtml(suffix)}</small>` : ""}</strong>
    </div>
  `;
}

function renderMatchList() {
  const visible = matchesForCurrentFilter();
  const list = document.querySelector("#matchList");
  document.querySelector("#statusFilter").value = state.filter;

  if (!state.matches.length) {
    list.innerHTML = emptyBlock("暂无真实比赛数据");
    return;
  }

  if (!visible.length) {
    list.innerHTML = emptyBlock("当前筛选下暂无比赛");
    return;
  }

  list.innerHTML = state.filter === "upcoming" ? renderUpcomingMatchList(visible) : visible.map(matchRow).join("");

  list.querySelectorAll("[data-match-id]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedId = button.dataset.matchId;
      render();
    });
  });

  const fold = list.querySelector("[data-upcoming-fold]");
  if (fold) {
    fold.addEventListener("toggle", (event) => {
      state.upcomingListOpen = event.currentTarget.open;
    });
  }
}

function renderUpcomingMatchList(matches) {
  const nearest = nextPredictableMatch(matches) ?? sortedUpcomingMatches(matches)[0];
  const remaining = matches.filter((match) => match.id !== nearest?.id);
  const shouldOpen = state.upcomingListOpen || remaining.some((match) => match.id === state.selectedId);

  return `
    ${
      nearest
        ? `
          <div class="pinned-list-block">
            <div class="pinned-list-label">
              <span>最近比赛预测</span>
              <strong>${escapeHtml(nearest.kickoff)}</strong>
            </div>
            ${matchRow(nearest, { pinned: true })}
          </div>
        `
        : ""
    }
    ${
      remaining.length
        ? `
          <details class="match-fold" data-upcoming-fold ${shouldOpen ? "open" : ""}>
            <summary>
              <span>未开赛列表</span>
              <strong>${remaining.length} 场</strong>
            </summary>
            <div class="match-fold-body">
              ${remaining.map(matchRow).join("")}
            </div>
          </details>
        `
        : ""
    }
  `;
}

function matchRow(match, options = {}) {
  const active = match.id === state.selectedId ? " active" : "";
  const pinned = options.pinned ? " pinned-prediction" : "";
  const status = marketStatus(match);
  const prediction = options.pinned && hasCompleteMarketVector(match) ? calculateScorePrediction(match) : null;
  const displayScore = formatScore(match.score, match.status);

  return `
    <button class="match-row${active}${pinned}" data-match-id="${escapeAttr(match.id)}" type="button">
      <span class="minute ${match.status}">${escapeHtml(match.minute)}</span>
      <span>
        <span class="row-title">
          <strong>${escapeHtml(match.stage)}</strong>
          <span class="tag ${match.status}">${statusLabels[match.status]}</span>
        </span>
        <span class="teams">
          <span>${escapeHtml(match.home)} vs ${escapeHtml(match.away)}</span>
          <span class="score-mini">${escapeHtml(displayScore)} · ${escapeHtml(match.kickoff)}</span>
          ${
            prediction
              ? `<span class="prediction-mini">预测 ${escapeHtml(prediction.primaryScore)} · ${escapeHtml(prediction.primaryLabel)} · ${formatPercent(prediction.primaryProbability)}</span>`
              : `<span class="odds-status ${status.tone}">${escapeHtml(status.label)}</span>`
          }
        </span>
      </span>
    </button>
  `;
}

function renderDetail() {
  const detail = document.querySelector("#matchDetail");
  const match = state.matches.find((item) => item.id === state.selectedId);

  if (!match) {
    detail.innerHTML = `
      <section class="empty-state">
        <h2>暂无真实比赛数据</h2>
        <p>导入包含真实赛程和市场胜率的 JSON 后，这里会显示比赛进程、市场对照和比分预测。</p>
        <code>${escapeHtml(sampleSchema())}</code>
      </section>
    `;
    return;
  }

  const bestOutcome = getBestOutcome(match);
  const scoreDisplay = formatScore(match.score, match.status);
  detail.innerHTML = `
    <article class="detail-card">
      <div class="detail-kicker">
        <span class="tag ${match.status}">${statusLabels[match.status]}</span>
        <strong>${escapeHtml(match.stage)}</strong>
        <span>${escapeHtml(match.kickoff)}</span>
      </div>
      <div class="detail-head">
        ${teamBlock("主队", match.home)}
        <div class="scorebox">
          <div class="score">${escapeHtml(scoreDisplay)}</div>
          <small>${statusLabels[match.status]} · ${escapeHtml(match.minute)}</small>
        </div>
        ${teamBlock("客队", match.away, "away")}
      </div>
      <div class="detail-meta">
        ${metaItem("阶段", match.stage)}
        ${metaItem("开赛", match.kickoff)}
        ${metaItem("场馆", match.venue)}
        ${metaItem("覆盖", coverageLabel(match))}
        ${metaItem("虎扑", hupuMetaLabel(match))}
        ${metaItem("AI观点", panewsMetaLabel(match))}
      </div>
    </article>

    ${renderCoverageStrip(match)}

    <div class="content-grid">
      <section class="odds-panel">
        <div class="section-title">
          <h2>比分预测模型</h2>
          <span class="arb-pill ${hasCompleteMarketVector(match) ? "" : "muted"}">${hasCompleteMarketVector(match) ? "可预测" : "数据不足"}</span>
        </div>
        ${renderScorePrediction(match)}
        ${renderPanewsAiPanel(match)}
        ${renderBettingRecommendations(match)}
        ${renderPredictionModel(match)}
        <div class="section-title compact odds-heading">
          <h2>市场胜率对照</h2>
          <span class="tag">赔率输入</span>
        </div>
        ${renderOddsTable(match, bestOutcome)}
        ${renderMarketNotes(match.marketNotes)}
      </section>

      <section class="timeline-panel">
        <div class="section-title">
          <h2>比赛动态</h2>
          <span class="tag ${match.status}">${escapeHtml(match.venue)}</span>
        </div>
        <div class="timeline">
          ${match.timeline.length ? match.timeline.map(timelineItem).join("") : emptyBlock("暂无比赛动态")}
        </div>
        ${renderSources(match.sources)}
      </section>
    </div>
  `;
}

function renderSources(sources) {
  if (!sources.length) return "";
  return `
    <div class="sources">
      <span>来源</span>
      ${sources
        .map((source) => `<a href="${escapeAttr(source)}" target="_blank" rel="noreferrer">${escapeHtml(source)}</a>`)
        .join("")}
    </div>
  `;
}

function hupuMetaLabel(match) {
  const hupu = match.hupu || {};
  const parts = [];
  if (Number.isFinite(hupu.heat)) parts.push(`热度 ${formatCompactCount(hupu.heat)}`);
  if (Number.isFinite(hupu.ratingCount)) parts.push(`评分 ${formatCompactCount(hupu.ratingCount)}`);
  if (!parts.length && hupu.status) parts.push(hupu.status);
  return parts.length ? parts.join(" · ") : "未公开";
}

function panewsMetaLabel(match) {
  if (!hasPanewsAiData(match)) return "未匹配";
  const consensus = match.panewsAi.consensus;
  const outcome = panewsOutcomeLabel(match, consensus.topOutcome);
  const parts = [`${consensus.modelCount}模型`];
  if (outcome && Number.isFinite(consensus.topProbability)) parts.push(`${outcome} ${formatPercent(consensus.topProbability)}`);
  return parts.join(" · ");
}

function renderCoverageStrip(match) {
  const items = [
    ["体彩官方", hasSportteryData(match) ? sportteryCoverageLabel(match) : "未匹配", hasSportteryData(match)],
    ["wc-2026 1X2", hasCompleteReferenceVector(match) ? "已抓取" : "缺失", hasCompleteReferenceVector(match)],
    ["Polymarket 单场", hasPolymarketPrices(match) ? "已抓取" : "未发现", hasPolymarketPrices(match)],
    ["PANews AI", hasPanewsAiData(match) ? `${match.panewsAi.consensus.modelCount}模型` : "未匹配", hasPanewsAiData(match)],
    ["预测模型", hasCompleteMarketVector(match) ? "可计算" : "等待赔率", hasCompleteMarketVector(match)],
  ];

  return `
    <section class="coverage-strip" aria-label="数据覆盖">
      ${items
        .map(
          ([label, value, ok]) => `
            <div class="coverage-item ${ok ? "ok" : "missing"}">
              <span>${escapeHtml(label)}</span>
              <strong>${escapeHtml(value)}</strong>
            </div>
          `,
        )
        .join("")}
    </section>
  `;
}

function renderMarketNotes(notes) {
  if (!notes?.length) return "";
  return `
    <div class="market-note-box">
      <strong>数据说明</strong>
      <ul>${notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}</ul>
    </div>
  `;
}

function renderOddsTable(match, bestOutcome) {
  if (!hasOddsData(match)) {
    return emptyBlock("暂无可核验赔率数据");
  }

  return `
    <div class="odds-scroll">
      <table class="odds-table">
        <thead>
          <tr>
            <th>结果</th>
              <th>体彩 HAD</th>
              <th>体彩隐含胜率</th>
              <th>wc-2026参考赔率</th>
              <th>海外最佳赔率</th>
              <th>海外隐含胜率</th>
              <th>Polymarket 胜率</th>
              <th>PM 等价赔率</th>
              <th>胜率差</th>
              <th>盘口变化</th>
          </tr>
        </thead>
        <tbody>
          ${match.odds.map((odd) => oddsRow(odd, bestOutcome)).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderScorePrediction(match) {
  if (!hasCompleteMarketVector(match)) {
    return `
      <section class="score-prediction empty-prediction">
        ${emptyBlock("缺少完整 1X2 赔率，暂不输出比分预测")}
      </section>
    `;
  }

  const prediction = calculateScorePrediction(match);
  const confidence = confidenceLabel(prediction.primaryProbability);
  return `
    <section class="score-prediction">
      <div class="score-forecast-main">
        <div>
          <span class="forecast-label">${escapeHtml(prediction.primarySourceLabel)}</span>
          <strong>${escapeHtml(prediction.primaryScore)}</strong>
          <small>${escapeHtml(prediction.primaryLabel)} · ${formatPercent(prediction.primaryProbability)}</small>
        </div>
        <div class="goal-expectation">
          ${metric("主队 xG", prediction.homeGoals.toFixed(2))}
          ${metric("客队 xG", prediction.awayGoals.toFixed(2))}
          ${metric("总进球", prediction.totalGoals.toFixed(2))}
          ${metric("信心", confidence)}
        </div>
      </div>
      <div class="probability-bars">
        ${prediction.outcomes.map(scoreOutcomeBar).join("")}
      </div>
      <div class="score-grid">
        ${prediction.topScores.map(scoreTile).join("")}
      </div>
      ${renderScoreBlendNote(prediction)}
    </section>
  `;
}

function renderPanewsAiPanel(match) {
  if (!hasPanewsAiData(match)) {
    return `
      <section class="panews-panel">
        <div class="section-title compact">
          <h2>外部 AI 预测</h2>
          <span class="tag">PANews</span>
        </div>
        ${emptyBlock("PANews AI Arena 暂未匹配到本场公开 AI 交易观点")}
      </section>
    `;
  }

  const ai = match.panewsAi;
  const consensus = ai.consensus;
  const topOutcome = panewsOutcomeLabel(match, consensus.topOutcome);
  const updated = formatExternalTime(ai.lastUpdated);
  return `
    <section class="panews-panel">
      <div class="section-title compact">
        <h2>外部 AI 预测</h2>
        <span class="tag">PANews AI Arena</span>
      </div>
      <div class="panews-summary">
        <div class="panews-consensus">
          <span>AI 共识方向</span>
          <strong>${escapeHtml(topOutcome || "-")}</strong>
          <small>${formatPercent(consensus.topProbability)} · 一致度 ${formatPercent(consensus.agreement)}</small>
        </div>
        <div class="panews-bars">
          ${panewsProbabilityBar(match, "home", match.home, consensus.averageProbabilities.home)}
          ${panewsProbabilityBar(match, "draw", "平局", consensus.averageProbabilities.draw)}
          ${panewsProbabilityBar(match, "away", match.away, consensus.averageProbabilities.away)}
        </div>
      </div>
      <div class="panews-market">
        ${panewsMarketPill(match, "home", match.home)}
        ${panewsMarketPill(match, "draw", "平局")}
        ${panewsMarketPill(match, "away", match.away)}
      </div>
      <div class="panews-model-grid">
        ${ai.models.map((model) => panewsModelCard(match, model)).join("")}
      </div>
      <div class="score-blend-note">
        <strong>${escapeHtml(ai.sourceName)} · ${escapeHtml(updated || "更新时间未知")}</strong>
        <span>${escapeHtml(ai.note)}</span>
        <a href="${escapeAttr(ai.sourceUrl)}" target="_blank" rel="noreferrer">打开 PANews AI Arena</a>
      </div>
    </section>
  `;
}

function panewsProbabilityBar(match, outcome, label, probability) {
  const width = Number.isFinite(probability) ? Math.round(probability * 100) : 0;
  return `
    <div class="prob-row">
      <span>${escapeHtml(label)}</span>
      <span class="prob-track panews-${escapeAttr(outcome)}"><span style="width: ${width}%"></span></span>
      <strong>${formatPercent(probability)}</strong>
    </div>
  `;
}

function panewsMarketPill(match, outcome, label) {
  const price = match.panewsAi.marketPrices[outcome];
  return `
    <div>
      <span>${escapeHtml(label)}</span>
      <strong>${formatPercent(price)}</strong>
      <small>PM 市场价</small>
    </div>
  `;
}

function panewsModelCard(match, model) {
  const actionClass = panewsActionClass(model.latestAction);
  const actionLabel = panewsActionLabel(model.latestAction);
  const outcome = panewsOutcomeLabel(match, model.outcome);
  const probability = model.probabilities?.[model.outcome];
  return `
    <article class="panews-model-card" style="--model-color:${escapeAttr(model.color)}">
      <div class="panews-model-head">
        <strong><span></span>${escapeHtml(model.short)}</strong>
        <em class="${actionClass}">${escapeHtml(actionLabel)}</em>
      </div>
      <div class="panews-model-pick">
        <span>${escapeHtml(outcome || "观察")}</span>
        <strong>${formatPercent(probability)}</strong>
      </div>
      <div class="panews-model-meta">
        ${panewsMiniStat("价格", formatDecimal(model.price))}
        ${panewsMiniStat("金额", formatArenaMoney(model.amount))}
        ${panewsMiniStat("持仓", formatArenaMoney(model.positionValue))}
      </div>
      ${model.reason ? `<p>${escapeHtml(model.reason)}</p>` : `<p>暂无公开理由。</p>`}
    </article>
  `;
}

function panewsMiniStat(label, value) {
  return `<span><small>${escapeHtml(label)}</small>${escapeHtml(value)}</span>`;
}

function scoreOutcomeBar(item) {
  return `
    <div class="prob-row">
      <span>${escapeHtml(item.label)}</span>
      <span class="prob-track"><span style="width: ${Math.round(item.probability * 100)}%"></span></span>
      <strong>${formatPercent(item.probability)}</strong>
    </div>
  `;
}

function scoreTile(item) {
  const edgeClass = item.valueEdge >= 0.04 ? "up" : item.valueEdge <= -0.04 ? "down" : "";
  return `
    <div class="score-tile ${item.isPrimary ? "primary" : ""}">
      <strong>${escapeHtml(item.score)}</strong>
      <span>${formatPercent(item.probability)}</span>
      <small>${escapeHtml(item.label)}</small>
      <div class="score-sources">
        <em>模型 ${formatPercent(item.modelProbability)}</em>
        <em>体彩 ${formatPercent(item.sportteryProbability)}</em>
        <em class="${edgeClass}">价值 ${formatPointGap(item.valueEdge)}</em>
      </div>
    </div>
  `;
}

function renderScoreBlendNote(prediction) {
  return `
    <div class="score-blend-note">
      <strong>${escapeHtml(prediction.blendLabel)}</strong>
      <span>${escapeHtml(prediction.blendNote)}</span>
    </div>
  `;
}

function renderBettingRecommendations(match) {
  if (!hasCompleteMarketVector(match)) {
    return `
      <section class="bet-panel">
        <div class="section-title compact">
          <h2>投注推荐</h2>
          <span class="tag">等待赔率</span>
        </div>
        ${emptyBlock("缺少完整 1X2 赔率，暂不生成比分投注推荐")}
      </section>
    `;
  }

  const singles = scoreBetRecommendations(match);
  const parlays = parlayRecommendations(match);
  const hasSportteryScoreOdds = hasSportteryCorrectScore(match);

  return `
    <section class="bet-panel">
      <div class="section-title compact">
        <h2>投注推荐</h2>
        <span class="tag">${hasSportteryScoreOdds ? "体彩 CRS" : "模型模拟"}</span>
      </div>
      <div class="bet-warning">
        ${escapeHtml(bettingDataNotice(match))}
      </div>
      <div class="bet-subtitle">
        <strong>单关比分</strong>
        <span>默认本金 ${formatCurrency(SINGLE_STAKE)}</span>
      </div>
      <div class="bet-grid">
        ${singles.map(betCard).join("")}
      </div>
      <div class="bet-subtitle">
        <strong>串关方案</strong>
        <span>默认本金 ${formatCurrency(PARLAY_STAKE)}</span>
      </div>
      <div class="parlay-grid">
        ${parlays.length ? parlays.map(parlayCard).join("") : emptyBlock("后续可预测比赛不足，暂不生成串关")}
      </div>
    </section>
  `;
}

function scoreBetRecommendations(match) {
  return calculateScorePrediction(match)
    .topScores.slice(0, 3)
    .map((score, index) => buildScoreBet(match, score, SINGLE_STAKE, index === 0 ? "主推" : "备选"));
}

function buildScoreBet(match, score, stake, label) {
  const fairOdds = decimalFromProbability(score.probability);
  const targetOdds = fairOdds * TARGET_EDGE;
  const sportteryScoreOdds = match.sporttery.correctScore?.[score.score] ?? null;
  const pricedOdds = sportteryScoreOdds ?? targetOdds;
  const grossReturn = stake * pricedOdds;
  const netProfit = grossReturn - stake;
  const expectedNet = score.probability * grossReturn - stake;

  return {
    label,
    match,
    score: score.score,
    resultLabel: score.label,
    probability: score.probability,
    fairOdds,
    targetOdds,
    sportteryScoreOdds,
    pricedOdds,
    modelProbability: score.modelProbability,
    sportteryProbability: score.sportteryProbability,
    valueEdge: score.valueEdge,
    stake,
    grossReturn,
    netProfit,
    expectedNet,
  };
}

function parlayRecommendations(match) {
  const legs = matchesAfterSelected(match, 4).map((item) => {
    const topScore = calculateScorePrediction(item).topScores[0];
    return buildScoreBet(item, topScore, PARLAY_STAKE, "串关腿");
  });

  return [2, 3, 4]
    .filter((legCount) => legs.length >= legCount)
    .map((legCount) => {
      const selectedLegs = legs.slice(0, legCount);
      const probability = selectedLegs.reduce((total, leg) => total * leg.probability, 1);
      const fairOdds = decimalFromProbability(probability);
      const targetOdds = selectedLegs.reduce((total, leg) => total * leg.targetOdds, 1);
      const grossReturn = PARLAY_STAKE * targetOdds;
      const netProfit = grossReturn - PARLAY_STAKE;
      const expectedNet = probability * grossReturn - PARLAY_STAKE;

      return {
        label: `${legCount}串1`,
        legCount,
        legs: selectedLegs,
        probability,
        fairOdds,
        targetOdds,
        stake: PARLAY_STAKE,
        grossReturn,
        netProfit,
        expectedNet,
      };
    });
}

function matchesAfterSelected(match, count) {
  const upcoming = state.matches.filter((item) => item.status === "upcoming" && hasCompleteMarketVector(item));
  const selectedIndex = upcoming.findIndex((item) => item.id === match.id);
  if (selectedIndex >= 0) return upcoming.slice(selectedIndex, selectedIndex + count);

  const allIndex = state.matches.findIndex((item) => item.id === match.id);
  const afterSelected = state.matches
    .slice(Math.max(allIndex, 0))
    .filter((item) => item.status === "upcoming" && hasCompleteMarketVector(item));
  return (afterSelected.length ? afterSelected : upcoming).slice(0, count);
}

function betCard(item, index) {
  const valueTone =
    Number.isFinite(item.sportteryScoreOdds) && item.sportteryScoreOdds >= item.targetOdds
      ? "up"
      : Number.isFinite(item.sportteryScoreOdds)
        ? "down"
        : "";
  return `
    <article class="bet-card ${index === 0 ? "primary" : ""}">
      <div class="bet-card-head">
        <span>${escapeHtml(item.label)}</span>
        <strong>${escapeHtml(item.score)}</strong>
      </div>
      <p>${escapeHtml(item.resultLabel)} · ${escapeHtml(item.match.home)} vs ${escapeHtml(item.match.away)}</p>
      <div class="bet-stats">
        ${betStat("命中概率", formatPercent(item.probability))}
        ${betStat("模型概率", formatPercent(item.modelProbability))}
        ${betStat("体彩隐含", formatPercent(item.sportteryProbability))}
        ${betStat("公平赔率", formatOdds(item.fairOdds))}
        ${betStat("最低可买赔率", formatOdds(item.targetOdds))}
        ${betStat("体彩比分赔率", formatOdds(item.sportteryScoreOdds), valueTone)}
        ${betStat("命中返还", formatCurrency(item.grossReturn))}
        ${betStat("净收益", formatSignedCurrency(item.netProfit))}
        ${betStat("理论期望", formatSignedCurrency(item.expectedNet))}
      </div>
    </article>
  `;
}

function parlayCard(item) {
  return `
    <article class="parlay-card">
      <div class="parlay-head">
        <h3>${escapeHtml(item.label)}</h3>
        <strong>${formatPercent(item.probability)}</strong>
      </div>
      <div class="parlay-legs">
        ${item.legs
          .map(
            (leg) => `
              <div class="parlay-leg">
                <span>${escapeHtml(leg.match.home)} vs ${escapeHtml(leg.match.away)}</span>
                <strong>${escapeHtml(leg.score)}</strong>
                <em>${formatPercent(leg.probability)}</em>
              </div>
            `,
          )
          .join("")}
      </div>
      <div class="bet-stats compact">
        ${betStat("最低组合赔率", formatOdds(item.targetOdds))}
        ${betStat("本金", formatCurrency(item.stake))}
        ${betStat("命中返还", formatCurrency(item.grossReturn))}
        ${betStat("净收益", formatSignedCurrency(item.netProfit))}
        ${betStat("理论期望", formatSignedCurrency(item.expectedNet))}
      </div>
    </article>
  `;
}

function betStat(label, value, tone = "") {
  return `
    <div class="${tone}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

function renderPredictionModel(match) {
  if (!hasCompleteMarketVector(match)) {
    return `
      <section class="model-panel">
        <div class="section-title compact">
          <h2>胜平负概率</h2>
          <span class="tag">待完整赔率</span>
        </div>
        ${emptyBlock("没有完整 1X2 市场向量，暂不计算胜平负概率")}
      </section>
    `;
  }

  const predictions = calculatePrediction(match);
  return `
    <section class="model-panel">
      <div class="section-title compact">
        <h2>胜平负概率</h2>
        <span class="tag">${match.status === "finished" ? "已完赛复盘" : "自动表现指标"}</span>
      </div>
      <div class="model-grid">
        ${renderPerformanceSnapshot(match, "home", match.home)}
        ${renderPerformanceSnapshot(match, "away", match.away)}
      </div>
      ${renderPerformanceNotes(match.performanceNotes)}
      <div class="odds-scroll">
        <table class="model-table">
          <thead>
            <tr>
              <th>结果</th>
              <th>模型胜率</th>
              <th>市场共识</th>
              <th>相对市场</th>
              <th>判断</th>
            </tr>
          </thead>
          <tbody>${predictions.map(predictionRow).join("")}</tbody>
        </table>
      </div>
    </section>
  `;
}

function renderPerformanceSnapshot(match, side, teamName) {
  const values = match.performance[side];
  const composite = compositeScore(values);
  return `
    <div class="model-side">
      <div class="model-side-head">
        <h3>${escapeHtml(teamName)}</h3>
        <strong>${Math.round(composite)}</strong>
      </div>
      ${performanceFields
        .map(
          (field) => `
            <div class="score-row">
              <span>${escapeHtml(field.label)}</span>
              <span class="score-track"><span style="width: ${values[field.key]}%"></span></span>
              <strong>${values[field.key]}</strong>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderPerformanceNotes(notes) {
  if (!notes.length) {
    return `
      <div class="model-note-box">
        <strong>模型说明</strong>
        <span>未取到可用的公开表现数据，当前表现项保持中性分。</span>
      </div>
    `;
  }

  return `
    <div class="model-note-box">
      <strong>公开数据整理</strong>
      <span>这些评分由公开赔率、Polymarket 冠军长期市场和赛程信息折算，前端只读；不是官方胜率，也不是投注建议。</span>
      <ul>${notes.map((note) => `<li>${escapeHtml(note)}</li>`).join("")}</ul>
    </div>
  `;
}

function predictionRow(row) {
  const signalClass = row.edgeVsMarket >= 0.04 ? "up" : row.edgeVsMarket <= -0.04 ? "down" : "";
  return `
    <tr>
      <td><strong>${escapeHtml(row.outcome)}</strong></td>
      <td>${formatPercent(row.modelProbability)}</td>
      <td>${formatPercent(row.marketConsensus)}</td>
      <td><span class="${signalClass}">${formatPointGap(row.edgeVsMarket)}</span></td>
      <td>${escapeHtml(row.signal)}</td>
    </tr>
  `;
}

function teamBlock(label, name, extraClass = "") {
  return `
    <div class="team ${extraClass}">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(name)}</strong>
    </div>
  `;
}

function metaItem(label, value) {
  return `<div class="meta-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function metric(label, value) {
  return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`;
}

function confidenceLabel(probability) {
  if (!Number.isFinite(probability)) return "-";
  if (probability >= 0.18) return "高";
  if (probability >= 0.12) return "中";
  return "低";
}

function oddsRow(odd, bestOutcome) {
  const movementClass = odd.referenceMovement === "down" ? "down" : odd.referenceMovement === "up" ? "up" : "";
  const movementLabel = odd.referenceMovement === "down" ? "下调" : odd.referenceMovement === "up" ? "上调" : "-";

  return `
    <tr>
      <td><strong>${escapeHtml(odd.outcome)}</strong></td>
      <td>
        <span class="odds-value">${formatDecimal(odd.sporttery)}</span>
        ${formatSportteryLine(odd)}
      </td>
      <td>
        <span class="odds-value">${formatPercent(sportteryProbability(odd))}</span>
      </td>
      <td>
        <span class="odds-value ${bestOutcome === odd.outcome ? "best" : ""} ${odd.referenceValid ? "" : "stale"}">${formatDecimal(odd.referenceOdds)}</span>
        ${formatReferenceMovement(odd.referenceMovement)}
        ${formatReferenceNote(odd)}
      </td>
      <td>
        <span class="odds-value">${formatDecimal(bestBookmaker(odd)?.decimalOdds)}</span>
        ${formatBookmakerLine(odd)}
      </td>
      <td>
        <span class="odds-value">${formatPercent(bestBookmakerProbability(odd))}</span>
      </td>
      <td>
        <span class="odds-value">${formatPercent(odd.polymarket)}</span>
        ${formatBook(odd)}
      </td>
      <td><span class="odds-value">${formatDecimal(decimalFromProbability(odd.polymarket))}</span></td>
      <td>${formatProbabilityGap(odd)}</td>
      <td><span class="movement ${movementClass}">${escapeHtml(movementLabel)}</span></td>
    </tr>
  `;
}

function timelineItem(item) {
  return `
    <div class="timeline-item">
      <span class="event-minute">${escapeHtml(item.minute)}</span>
      <span class="event-copy">
        <strong>${escapeHtml(item.title)}</strong>
        <span>${escapeHtml(item.text)}</span>
      </span>
    </div>
  `;
}

function getBestOutcome(match) {
  if (!match.odds.length) return null;
  return match.odds.reduce((best, current) => {
    const bestCost = favoriteScore(best);
    const currentCost = favoriteScore(current);
    return currentCost < bestCost ? current : best;
  }).outcome;
}

function calculateScorePrediction(match) {
  const rows = calculatePrediction(match);
  const homeRow = rows.find((row, index) => outcomeKind(match, row, index) === "home");
  const drawRow = rows.find((row, index) => outcomeKind(match, row, index) === "draw");
  const awayRow = rows.find((row, index) => outcomeKind(match, row, index) === "away");
  const homeProbability = homeRow?.modelProbability ?? 0.33;
  const drawProbability = drawRow?.modelProbability ?? 0.28;
  const awayProbability = awayRow?.modelProbability ?? 0.33;
  const totalGoals = estimateTotalGoals(match, drawProbability);
  const goalShare = clampNumber(0.5 + (homeProbability - awayProbability) * 0.58, 0.18, 0.82);
  const homeGoals = clampNumber(totalGoals * goalShare, 0.18, 4.75);
  const awayGoals = clampNumber(totalGoals - homeGoals, 0.12, 4.25);
  const sportteryScoreProbabilities = normalizeScoreProbabilityMap(match.sporttery.correctScore);
  const hasSportteryScores = Object.keys(sportteryScoreProbabilities).length > 0;
  const matrix = scoreMatrix(homeGoals, awayGoals, 5);
  const topScores = matrix
    .map((item) => ({
      ...item,
      score: `${item.home}-${item.away}`,
      label: scoreResultLabel(match, item.home, item.away),
      modelProbability: item.probability,
      sportteryProbability: sportteryScoreProbabilities[`${item.home}-${item.away}`] ?? null,
    }))
    .map((item) => ({
      ...item,
      probability: blendScoreProbability(item.modelProbability, item.sportteryProbability),
      valueEdge: Number.isFinite(item.sportteryProbability) ? item.modelProbability - item.sportteryProbability : null,
    }))
    .sort((a, b) => b.probability - a.probability)
    .slice(0, 6);
  const primary = topScores[0];

  return {
    homeGoals,
    awayGoals,
    totalGoals: homeGoals + awayGoals,
    primaryScore: primary.score,
    primaryLabel: primary.label,
    primaryProbability: primary.probability,
    primarySourceLabel: hasSportteryScores ? "融合推荐比分" : "模型推荐比分",
    blendLabel: hasSportteryScores ? "融合口径：模型 55% / 体彩比分盘 45%" : "模型口径：未发现体彩比分盘",
    blendNote: hasSportteryScores
      ? "体彩 CRS 已去水转换为比分市场概率，再与泊松模型概率融合；价值差仍用模型概率减体彩隐含概率观察。"
      : "当前场次没有体彩 CRS，比分概率仅来自胜平负市场、球队表现和泊松分布。",
    outcomes: [
      { label: `${match.home}胜`, probability: homeProbability },
      { label: "平局", probability: drawProbability },
      { label: `${match.away}胜`, probability: awayProbability },
    ],
    topScores: topScores.map((item, index) => ({ ...item, isPrimary: index === 0 })),
  };
}

function normalizeScoreProbabilityMap(scoreOdds) {
  const entries = Object.entries(scoreOdds || {})
    .map(([score, odds]) => [score, Number.isFinite(odds) && odds > 0 ? 1 / odds : null])
    .filter(([, probability]) => Number.isFinite(probability));
  const total = entries.reduce((sum, [, probability]) => sum + probability, 0);
  if (!total) return {};
  return Object.fromEntries(entries.map(([score, probability]) => [score, probability / total]));
}

function blendScoreProbability(modelProbability, sportteryProbability) {
  if (!Number.isFinite(sportteryProbability)) return modelProbability;
  return modelProbability * (1 - SPORTTERY_SCORE_WEIGHT) + sportteryProbability * SPORTTERY_SCORE_WEIGHT;
}

function estimateTotalGoals(match, drawProbability) {
  const totalLine = totalGoalsLine(match);
  if (Number.isFinite(totalLine)) {
    return clampNumber(totalLine + 0.06, 1.25, 4.25);
  }
  const favoriteGap = favoriteProbabilityGap(match);
  return clampNumber(2.78 - drawProbability * 1.35 + favoriteGap * 0.5, 1.65, 4.15);
}

function totalGoalsLine(match) {
  const source = [...match.marketNotes, ...match.timeline.map((item) => item.text)].join(" ");
  const found = source.match(/大小球:\s*O\s*(\d+(?:\.\d+)?)/);
  return found ? Number(found[1]) : null;
}

function favoriteProbabilityGap(match) {
  const rows = calculateMarketRows(match);
  if (rows.length < 3) return 0;
  const home = rows.find((row, index) => outcomeKind(match, row, index) === "home")?.marketConsensus ?? 0.33;
  const away = rows.find((row, index) => outcomeKind(match, row, index) === "away")?.marketConsensus ?? 0.33;
  return Math.abs(home - away);
}

function scoreMatrix(homeGoals, awayGoals, maxGoals) {
  const home = poissonVector(homeGoals, maxGoals);
  const away = poissonVector(awayGoals, maxGoals);
  const matrix = [];
  for (let h = 0; h <= maxGoals; h += 1) {
    for (let a = 0; a <= maxGoals; a += 1) {
      matrix.push({ home: h, away: a, probability: home[h] * away[a] });
    }
  }
  return matrix;
}

function poissonVector(lambda, maxGoals) {
  const values = [];
  for (let goals = 0; goals <= maxGoals; goals += 1) {
    values.push((Math.exp(-lambda) * lambda ** goals) / factorial(goals));
  }
  return values;
}

function factorial(value) {
  let result = 1;
  for (let item = 2; item <= value; item += 1) result *= item;
  return result;
}

function scoreResultLabel(match, homeGoals, awayGoals) {
  if (homeGoals > awayGoals) return `${match.home}胜`;
  if (homeGoals < awayGoals) return `${match.away}胜`;
  return "平局";
}

function calculatePrediction(match) {
  const marketRows = calculateMarketRows(match);
  const homeStrength = teamStrength(match.performance.home);
  const awayStrength = teamStrength(match.performance.away);
  const edge = homeStrength - awayStrength;

  const adjusted = marketRows.map((row, index) => {
    const kind = outcomeKind(match, row, index);
    let adjustment = 0;
    if (kind === "home") adjustment = edge * 1.25;
    if (kind === "away") adjustment = -edge * 1.25;
    if (kind === "draw") adjustment = -Math.abs(edge) * 0.55 + drawBalanceBoost(match.performance.home, match.performance.away);
    return {
      ...row,
      logit: Math.log(Math.max(row.marketConsensus, 0.001)) + adjustment,
    };
  });

  const maxLogit = Math.max(...adjusted.map((row) => row.logit));
  const exps = adjusted.map((row) => Math.exp(row.logit - maxLogit));
  const total = exps.reduce((sum, value) => sum + value, 0);

  return adjusted.map((row, index) => {
    const modelProbability = exps[index] / total;
    const edgeVsMarket = modelProbability - row.marketConsensus;
    return {
      ...row,
      modelProbability,
      edgeVsMarket,
      signal: signalLabel(edgeVsMarket),
    };
  });
}

function calculateMarketRows(match) {
  const referenceRaw = match.odds.map((odd) =>
    odd.referenceValid !== false && Number.isFinite(odd.referenceOdds) && odd.referenceOdds > 0 ? 1 / odd.referenceOdds : null,
  );
  const sportteryRaw = match.odds.map((odd) => (Number.isFinite(odd.sporttery) && odd.sporttery > 0 ? 1 / odd.sporttery : null));
  const polymarketRaw = match.odds.map((odd) => (Number.isFinite(odd.polymarket) ? odd.polymarket : null));
  const bookmakerRaw = match.odds.map((odd) => bestBookmakerProbability(odd));
  const reference = normalizeProbabilityVector(referenceRaw);
  const sporttery = normalizeProbabilityVector(sportteryRaw);
  const polymarket = normalizeProbabilityVector(polymarketRaw);
  const bookmakers = hasCompleteVector(bookmakerRaw) ? normalizeProbabilityVector(bookmakerRaw) : bookmakerRaw.map(() => null);

  return match.odds.map((odd, index) => {
    const probabilities = [sporttery[index], reference[index], bookmakers[index], polymarket[index]].filter(Number.isFinite);
    const marketConsensus = probabilities.length ? average(probabilities) : 1 / match.odds.length;
    return {
      outcome: odd.outcome,
      sportteryProbability: sporttery[index],
      referenceProbability: reference[index],
      bookmakerProbability: bookmakers[index],
      polymarketProbability: polymarket[index],
      marketConsensus,
    };
  });
}

function normalizeProbabilityVector(values) {
  const finite = values.map((value) => (Number.isFinite(value) && value > 0 ? value : null));
  const total = finite.reduce((sum, value) => sum + (value ?? 0), 0);
  if (!total) return values.map(() => null);
  return finite.map((value) => (value === null ? null : value / total));
}

function hasCompleteVector(values) {
  return values.length > 1 && values.every((value) => Number.isFinite(value) && value > 0);
}

function teamStrength(values) {
  return performanceFields.reduce((sum, field) => sum + ((values[field.key] - 50) / 50) * field.weight, 0);
}

function compositeScore(values) {
  return performanceFields.reduce((sum, field) => sum + values[field.key] * field.weight, 0);
}

function drawBalanceBoost(home, away) {
  const defensiveAverage = ((home.defense + home.goalkeeper + away.defense + away.goalkeeper) / 4 - 50) / 50;
  return defensiveAverage * 0.18;
}

function outcomeKind(match, row, index) {
  if (row.outcome.includes("平")) return "draw";
  if (row.outcome.includes(match.home)) return "home";
  if (row.outcome.includes(match.away)) return "away";
  if (index === 0) return "home";
  if (index === 1) return "draw";
  return "away";
}

function signalLabel(edge) {
  if (edge >= 0.04) return "模型高于市场";
  if (edge <= -0.04) return "模型低于市场";
  return "接近市场";
}

function favoriteScore(odd) {
  return odd.referenceValid !== false && Number.isFinite(odd.referenceOdds) && odd.referenceOdds > 0
    ? odd.referenceOdds
    : Number.isFinite(bestBookmaker(odd)?.decimalOdds)
      ? bestBookmaker(odd).decimalOdds
    : Number.isFinite(odd.polymarket) && odd.polymarket > 0
      ? 1 / odd.polymarket
      : Infinity;
}

function ensureSelectedMatch() {
  const visible = matchesForCurrentFilter();
  if (visible.length && !visible.some((match) => match.id === state.selectedId)) {
    state.selectedId = pickDefaultMatchId(visible);
  }
  if (!state.matches.length) {
    state.selectedId = null;
  }
}

function pickDefaultMatchId(matches) {
  return (
    nextPredictableMatch(matches)?.id ??
    sortedUpcomingMatches(matches)[0]?.id ??
    matches.find((match) => match.status === "live")?.id ??
    matches.find(hasCompleteReferenceVector)?.id ??
    matches[0]?.id ??
    null
  );
}

function matchesForCurrentFilter() {
  return state.matches.filter((match) => state.filter === "all" || match.status === state.filter);
}

function nextPredictableMatch(matches) {
  return sortedUpcomingMatches(matches).find(hasCompleteMarketVector) ?? null;
}

function sortedUpcomingMatches(matches) {
  return [...matches.filter((match) => match.status === "upcoming")].sort((a, b) => kickoffTime(a) - kickoffTime(b));
}

function kickoffTime(match) {
  const parsed = String(match.kickoff).match(/(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})/);
  if (!parsed) return Number.MAX_SAFE_INTEGER;
  const [, year, month, day, hour, minute] = parsed.map(Number);
  return new Date(year, month - 1, day, hour, minute).getTime();
}

function hasOddsData(match) {
  return match.odds.some(
    (odd) =>
      Number.isFinite(odd.sporttery) ||
      Number.isFinite(odd.referenceOdds) ||
      Number.isFinite(odd.polymarket) ||
      Number.isFinite(bestBookmaker(odd)?.decimalOdds),
  );
}

function hasCompleteReferenceVector(match) {
  const values = match.odds.map((odd) =>
    odd.referenceValid !== false && Number.isFinite(odd.referenceOdds) && odd.referenceOdds > 0 ? 1 / odd.referenceOdds : null,
  );
  return hasCompleteVector(values);
}

function hasCompleteSportteryVector(match) {
  return hasCompleteVector(match.odds.map((odd) => (Number.isFinite(odd.sporttery) && odd.sporttery > 0 ? 1 / odd.sporttery : null)));
}

function hasSportteryData(match) {
  return hasCompleteSportteryVector(match) || Boolean(Object.keys(match.sporttery.correctScore || {}).length) || hasCompleteSportteryPool(match.sporttery.hhad);
}

function hasCompleteSportteryPool(pool) {
  return [pool?.home, pool?.draw, pool?.away].every((value) => Number.isFinite(value) && value > 0);
}

function hasPolymarketPrices(match) {
  return match.odds.some((odd) => Number.isFinite(odd.polymarket));
}

function hasPanewsAiData(match) {
  return Boolean(match.panewsAi && Array.isArray(match.panewsAi.models) && match.panewsAi.models.length);
}

function hasCompleteMarketVector(match) {
  return hasCompleteSportteryVector(match) || hasCompleteReferenceVector(match) || hasCompletePolymarketVector(match) || hasCompleteBookmakerVector(match);
}

function hasCompletePolymarketVector(match) {
  return hasCompleteVector(match.odds.map((odd) => odd.polymarket));
}

function hasCompleteBookmakerVector(match) {
  return hasCompleteVector(match.odds.map((odd) => bestBookmakerProbability(odd)));
}

function bookmakerQuoteCount(match) {
  return match.odds.reduce((sum, odd) => sum + (odd.bookmakers?.length || 0), 0);
}

function marketStatus(match) {
  if (hasCompleteSportteryVector(match) || hasCompleteReferenceVector(match)) {
    const parts = [];
    if (hasCompleteSportteryVector(match)) parts.push("体彩");
    if (hasCompleteReferenceVector(match)) parts.push("wc-2026");
    if (hasPolymarketPrices(match)) parts.push("PM");
    if (bookmakerQuoteCount(match)) parts.push("海外");
    return { label: parts.join(" + "), tone: "ok" };
  }
  if (hasOddsData(match)) return { label: "盘口不完整", tone: "partial" };
  return { label: "暂无公开赔率", tone: "missing" };
}

function coverageLabel(match) {
  const status = marketStatus(match);
  return status.label;
}

function sportteryCoverageLabel(match) {
  const parts = [];
  if (hasCompleteSportteryVector(match)) parts.push("HAD");
  if (hasCompleteSportteryPool(match.sporttery.hhad)) parts.push(`HHAD ${match.sporttery.hhad.goalLine || ""}`.trim());
  if (Object.keys(match.sporttery.correctScore || {}).length) parts.push("比分");
  return parts.join(" + ") || "已匹配";
}

function hasSportteryCorrectScore(match) {
  return Boolean(Object.keys(match.sporttery.correctScore || {}).length);
}

function bettingDataNotice(match) {
  if (hasSportteryCorrectScore(match)) {
    return `已接入体彩官方比分盘 CRS，单关比分的返还和理论期望优先按体彩赔率计算；“最低可买赔率”仍是模型概率反推的价值线。`;
  }
  return `本场没有体彩官方比分盘 CRS，比分推荐用模型概率反推公平赔率，再加 ${Math.round((TARGET_EDGE - 1) * 100)}% 安全边际。实际赔率低于“最低可买赔率”就不建议买入。`;
}

function emptyBlock(text) {
  return `<div class="empty">${escapeHtml(text)}</div>`;
}

function sampleSchema() {
  return '{"sourceName":"真实数据源","lastUpdated":"2026-06-12T08:00:00Z","matches":[{"id":"...","status":"upcoming","home":"...","away":"...","odds":[{"outcome":"主胜","referenceOdds":2.1,"polymarket":0.45}],"performance":{"home":{"form":60},"away":{"form":55}}}]}';
}

function formatScore(score, status = "") {
  if (status === "upcoming") return "VS";
  if (!Array.isArray(score)) return "-";
  if (score[0] === "-" && score[1] === "-") return "VS";
  return `${score[0] ?? "-"} - ${score[1] ?? "-"}`;
}

function compactSourceName(value) {
  if (!value || value === "未载入真实数据") return value;
  const parts = [];
  if (value.includes("sporttery.cn")) parts.push("体彩官方");
  if (value.includes("wc-2026.com")) parts.push("wc-2026");
  if (value.includes("Polymarket")) parts.push("Polymarket");
  if (value.includes("虎扑") || value.includes("hupu")) parts.push("虎扑热度");
  if (value.includes("PANews")) parts.push("PANews AI");
  return parts.length ? parts.join(" + ") : value;
}

function compactLastUpdated(value) {
  if (!value || value === "-") return value;
  const cstMatch = String(value).match(/\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}\s+CST/);
  return cstMatch ? cstMatch[0] : value;
}

function formatDecimal(value) {
  return Number.isFinite(value) ? value.toFixed(2) : "-";
}

function formatExternalTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatOdds(value) {
  return Number.isFinite(value) ? value.toFixed(2) : "-";
}

function formatReferenceMovement(value) {
  if (value === "up") return `<small class="book-line up">wc-2026 ↑</small>`;
  if (value === "down") return `<small class="book-line down">wc-2026 ↓</small>`;
  return "";
}

function formatReferenceNote(odd) {
  if (odd.referenceValid !== false) return "";
  return `<small class="book-line down">${escapeHtml(odd.referenceNote || "待复核")}</small>`;
}

function decimalFromProbability(value) {
  return Number.isFinite(value) && value > 0 ? 1 / value : null;
}

function formatProbabilityGap(odd) {
  const marketProbability =
    sportteryProbability(odd) ??
    bestBookmakerProbability(odd) ??
    (odd.referenceValid !== false && Number.isFinite(odd.referenceOdds) ? 1 / odd.referenceOdds : null);
  if (!Number.isFinite(marketProbability) || !Number.isFinite(odd.polymarket)) return "-";
  const gap = odd.polymarket - marketProbability;
  return `${gap > 0 ? "+" : ""}${(gap * 100).toFixed(1)}pp`;
}

function formatSportteryLine(odd) {
  if (!Number.isFinite(odd.sporttery)) return "";
  return `<small class="book-line">HAD 官方</small>`;
}

function sportteryProbability(odd) {
  return Number.isFinite(odd.sporttery) && odd.sporttery > 0 ? 1 / odd.sporttery : null;
}

function formatPointGap(value) {
  if (!Number.isFinite(value)) return "-";
  return `${value > 0 ? "+" : ""}${(value * 100).toFixed(1)}pp`;
}

function formatBook(odd) {
  if (!Number.isFinite(odd.polymarketBid) && !Number.isFinite(odd.polymarketAsk)) return "";
  return `<small class="book-line">bid ${formatDecimal(odd.polymarketBid)} / ask ${formatDecimal(odd.polymarketAsk)}</small>`;
}

function formatBookmakerLine(odd) {
  const best = bestBookmaker(odd);
  if (!best) return "";
  const american = Number.isFinite(best.americanOdds) ? ` ${formatAmerican(best.americanOdds)}` : "";
  return `<small class="book-line">${escapeHtml(best.book)}${escapeHtml(american)}</small>`;
}

function bestBookmaker(odd) {
  if (!odd.bookmakers?.length) return null;
  return odd.bookmakers.reduce((best, current) => (current.decimalOdds > best.decimalOdds ? current : best));
}

function bestBookmakerProbability(odd) {
  const best = bestBookmaker(odd);
  return best?.decimalOdds > 0 ? 1 / best.decimalOdds : null;
}

function formatPercent(value) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : "-";
}

function formatCompactCount(value) {
  if (!Number.isFinite(value)) return "-";
  if (value >= 10000) {
    const compact = value / 10000;
    const fixed = compact >= 10 ? compact.toFixed(1) : compact.toFixed(1);
    return `${fixed.replace(/\.0$/, "")}万`;
  }
  return Math.round(value).toLocaleString("zh-CN");
}

function formatAmerican(value) {
  if (!Number.isFinite(value)) return "";
  return value > 0 ? `+${value}` : `${value}`;
}

function decimalFromAmerican(value) {
  if (!Number.isFinite(value) || value === 0) return null;
  return value > 0 ? 1 + value / 100 : 1 + 100 / Math.abs(value);
}

function formatMoney(value) {
  return Number.isFinite(value) ? `${value} CNY` : "-";
}

function formatArenaMoney(value) {
  if (!Number.isFinite(value)) return "-";
  const rounded = Math.round(value);
  return `${rounded.toLocaleString("zh-CN")} 点`;
}

function formatCurrency(value) {
  if (!Number.isFinite(value)) return "-";
  return `¥${Math.round(value).toLocaleString("zh-CN")}`;
}

function formatSignedCurrency(value) {
  if (!Number.isFinite(value)) return "-";
  const sign = value > 0 ? "+" : "";
  return `${sign}${formatCurrency(value)}`;
}

function formatProfit(value) {
  if (!Number.isFinite(value)) return "-";
  return `${value > 0 ? "+" : ""}${value} CNY`;
}

function panewsOutcomeLabel(match, outcome) {
  if (outcome === "home") return match.home;
  if (outcome === "draw") return "平局";
  if (outcome === "away") return match.away;
  return "";
}

function panewsActionLabel(action) {
  if (action === "buy") return "买入";
  if (action === "sell") return "卖出";
  return "观望";
}

function panewsActionClass(action) {
  if (action === "buy") return "up";
  if (action === "sell") return "down";
  return "";
}

function average(values) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function renderError(message) {
  document.querySelector("#matchDetail").innerHTML = `
    <section class="empty-state">
      <h2>数据载入失败</h2>
      <p>${escapeHtml(message)}</p>
    </section>
  `;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value);
}
