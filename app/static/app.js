const STRATEGY_LABELS = {
  bull_put_credit: "Bull Put Credit Spread",
  bear_call_credit: "Bear Call Credit Spread",
  bull_call_debit: "Bull Call Debit Spread",
  bear_put_debit: "Bear Put Debit Spread",
  iron_condor: "Iron Condor",
};

const PREDICTION_ORDER = ["big_down", "small_down", "flat", "small_up", "big_up"];
const PREDICTION_LABELS = {
  big_down: "Big Down",
  small_down: "Small Down",
  flat: "Flat",
  small_up: "Small Up",
  big_up: "Big Up",
};

function fmtMoney(x) {
  if (x === null || x === undefined) return "--";
  return "$" + Number(x).toFixed(2);
}

function fmtTime(iso) {
  if (!iso) return "--";
  const d = new Date(iso);
  return d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function renderStrikes(card) {
  if (card.strategy === "iron_condor") {
    return `Put ${card.put_long_strike}/${card.put_short_strike} &nbsp;|&nbsp; Call ${card.call_short_strike}/${card.call_long_strike}`;
  }
  return `Short ${card.short_strike} / Long ${card.long_strike}`;
}

// Same shape as renderStrikes, but for a tracked-trade row -- the trades
// table stores strikes as flat columns (short_strike/long_strike or the
// four condor legs), not nested under a "card" like a live suggestion.
function renderTradePosition(t) {
  if (t.strategy === "iron_condor") {
    return `Put ${t.put_long_strike}/${t.put_short_strike} &nbsp;|&nbsp; Call ${t.call_short_strike}/${t.call_long_strike}`;
  }
  return `Short ${t.short_strike} / Long ${t.long_strike}`;
}

function predictionHtml(prediction, hindsight) {
  if (!prediction) {
    return `<div class="sub">Predicted movement not available yet.</div>`;
  }
  const segments = PREDICTION_ORDER.map((b) => {
    const active = b === prediction.bucket;
    const side = b === "flat" ? "flat" : b.split("_")[1];
    return `<div class="pred-segment ${side}${active ? " active" : ""}">${PREDICTION_LABELS[b]}</div>`;
  }).join("");

  let hindsightHtml = "";
  if (hindsight) {
    const resultClass = hindsight.correct ? "pnl-pos" : hindsight.direction_correct ? "" : "pnl-neg";
    const resultLabel = hindsight.correct ? "Exact match" : hindsight.direction_correct ? "Right direction, wrong size" : "Missed";
    const sign = hindsight.realized_pct_change > 0 ? "+" : "";
    hindsightHtml = `
      <div class="status-sub" style="margin-top:8px">
        Actual by ${hindsight.checked_at}: <strong>${hindsight.label}</strong> (${sign}${hindsight.realized_pct_change.toFixed(2)}%)
        &middot; <span class="${resultClass}">${resultLabel}</span>
      </div>`;
  }

  const sourceLabel = { iv: "options-implied (IV)", atr: "recent realized volatility (ATR)", none: "unavailable" }[
    prediction.magnitude_source
  ] || prediction.magnitude_source;
  const moveHtml =
    prediction.expected_move_pct != null
      ? `<div class="status-sub" style="margin-top:2px">Expected move: <strong>&plusmn;${prediction.expected_move_pct.toFixed(2)}%</strong> <span class="sub">(${sourceLabel})</span></div>`
      : "";

  return `
    <div class="section-title" style="margin-top:16px">Predicted movement</div>
    <div class="pred-row">${segments}</div>
    <div class="status-sub" style="margin-top:6px">Direction: <strong>${prediction.label}</strong> &middot; confidence ${prediction.confidence}%</div>
    ${moveHtml}
    <div class="sub" style="margin-top:2px">Direction from technical signals; size (small/big) from ${sourceLabel === "unavailable" ? "defaults to “small” (no volatility read available)" : sourceLabel} -- two independent reads, not the same number twice.</div>
    ${hindsightHtml}
  `;
}

function renderStatus(data, elId = "statusCard", banner = "", hindsight = null) {
  const el = document.getElementById(elId);
  const direction = data.direction || "none";
  const bannerHtml = banner ? `<div class="demo-banner">${banner}</div><br>` : "";
  const predictionBlock = predictionHtml(data.prediction, hindsight);

  if (!data.tradeable || !data.card) {
    el.className = "card status-card";
    const reasons = (data.reasons || []).map((r) => `<li>${r}</li>`).join("");
    el.innerHTML = `
      ${bannerHtml}
      <div class="status-headline neutral">No confident trade -- standing aside</div>
      <div class="status-sub">Confidence score: ${data.score ?? 0}/100</div>
      <div class="score-bar"><div class="score-fill" style="width:${data.score ?? 0}%"></div></div>
      <ul class="rationale">${reasons || "<li>Waiting on more signal.</li>"}</ul>
      ${predictionBlock}
    `;
    return;
  }

  const card = data.card;
  el.className = "card status-card tradeable";
  const dirClass = direction === "bullish" ? "bullish" : direction === "bearish" ? "bearish" : "neutral";
  const rationale = (card.rationale || []).map((r) => `<li>${r}</li>`).join("");

  el.innerHTML = `
    ${bannerHtml}
    <span class="pill ${dirClass}">${direction}</span>
    <div class="status-headline ${dirClass}" style="margin-top:10px">${STRATEGY_LABELS[card.strategy] || card.strategy}</div>
    <div class="status-sub">Confidence score: ${card.confidence_score}/100 &middot; expires ${card.expiration}</div>
    <div class="score-bar"><div class="score-fill" style="width:${card.confidence_score}%"></div></div>

    <div class="grid">
      <div class="stat"><div class="label">Strikes (SPX est.)</div><div class="value">${renderStrikes(card)}</div></div>
      <div class="stat"><div class="label">Width</div><div class="value">${card.width} pts</div></div>
      <div class="stat"><div class="label">Est. ${card.strategy.includes("debit") ? "Debit" : "Credit"}</div><div class="value">${fmtMoney(card.est_credit_debit)}</div></div>
      <div class="stat"><div class="label">Max Profit</div><div class="value">${fmtMoney(card.max_profit)}</div></div>
      <div class="stat"><div class="label">Max Loss</div><div class="value">${fmtMoney(card.max_loss)}</div></div>
      <div class="stat"><div class="label">Profit Target</div><div class="value">${fmtMoney(card.profit_target_price)}</div></div>
      <div class="stat"><div class="label">Stop Loss</div><div class="value">${fmtMoney(card.stop_loss_price)}</div></div>
      <div class="stat"><div class="label">Force Close By</div><div class="value">${card.force_close_by}</div></div>
    </div>

    <ul class="rationale">${rationale}</ul>
    <div class="status-sub" style="margin-top:12px">${card.note}</div>
    ${predictionBlock}
  `;
}

async function renderEcon() {
  const el = document.getElementById("econList");
  try {
    const res = await fetch("/api/econ-today");
    const events = await res.json();
    if (!events.length) {
      el.innerHTML = "No high-impact events flagged for today.";
      return;
    }
    el.innerHTML = events
      .map((e) => `<div class="econ-row"><span>${e.name}</span><span class="impact-${e.impact}">${e.time_et} ET</span></div>`)
      .join("");
  } catch (e) {
    el.innerHTML = "Could not load economic calendar.";
  }
}

async function renderStats() {
  const summaryEl = document.getElementById("statsSummary");
  const byStrategyEl = document.getElementById("statsByStrategy");
  try {
    const res = await fetch("/api/stats");
    const s = await res.json();
    if (!s.total_resolved) {
      summaryEl.textContent = "No resolved trades yet -- stats will appear once a suggested trade hits its target, stop, or cutoff.";
      byStrategyEl.innerHTML = "";
      return;
    }
    summaryEl.innerHTML = `<strong>${s.win_rate}%</strong> win rate over ${s.total_resolved} resolved trade${s.total_resolved === 1 ? "" : "s"} (${s.wins}W / ${s.losses}L) &middot; avg P&amp;L ${fmtMoney(s.avg_pnl)}`;
    byStrategyEl.innerHTML = Object.entries(s.by_strategy)
      .map(
        ([strategy, st]) => `
        <div class="stat">
          <div class="label">${STRATEGY_LABELS[strategy] || strategy}</div>
          <div class="value">${st.win_rate ?? "--"}% <span class="sub">(${st.wins}/${st.total})</span></div>
        </div>`
      )
      .join("");
  } catch (e) {
    summaryEl.textContent = "Could not load stats.";
  }
}

async function renderPredictionStats() {
  const summaryEl = document.getElementById("predictionStatsSummary");
  const byBucketEl = document.getElementById("predictionStatsByBucket");
  try {
    const res = await fetch("/api/prediction-stats");
    const s = await res.json();
    if (!s.total_resolved) {
      summaryEl.textContent = `No resolved predictions yet -- each one checks itself against actual price action a while later.`;
      byBucketEl.innerHTML = "";
      return;
    }
    summaryEl.innerHTML = `<strong>${s.exact_accuracy}%</strong> exact-bucket accuracy &middot; <strong>${s.direction_accuracy}%</strong> right-direction accuracy over ${s.total_resolved} resolved prediction${s.total_resolved === 1 ? "" : "s"}`;
    byBucketEl.innerHTML = PREDICTION_ORDER.filter((b) => s.by_bucket[b])
      .map((b) => {
        const st = s.by_bucket[b];
        return `
        <div class="stat">
          <div class="label">${PREDICTION_LABELS[b]}</div>
          <div class="value">${st.accuracy ?? "--"}% <span class="sub">(${st.total} made)</span></div>
        </div>`;
      })
      .join("");
  } catch (e) {
    summaryEl.textContent = "Could not load prediction stats.";
  }
}

async function renderTrades() {
  const tbody = document.querySelector("#tradesTable tbody");
  try {
    const res = await fetch("/api/trades?limit=200");
    const rows = await res.json();
    if (!rows.length) {
      tbody.innerHTML = `<tr><td colspan="11">No tracked trades yet.</td></tr>`;
      return;
    }
    tbody.innerHTML = rows
      .map((t) => {
        const pnlClass = t.pnl == null ? "" : t.pnl >= 0 ? "pnl-pos" : "pnl-neg";
        const notes = (t.reason_tags || []).map((r) => `<li>${r}</li>`).join("");
        return `<tr>
          <td>${fmtTime(t.opened_at)}</td>
          <td>${t.resolved_at ? fmtTime(t.resolved_at) : "--"}</td>
          <td>${STRATEGY_LABELS[t.strategy] || t.strategy}</td>
          <td>${renderTradePosition(t)}</td>
          <td>${fmtMoney(t.entry_price)}</td>
          <td>${t.exit_price != null ? fmtMoney(t.exit_price) : "--"}</td>
          <td><span class="pill ${t.status}">${t.status}</span></td>
          <td>${t.exit_reason_code || "--"}</td>
          <td class="${pnlClass}">${t.pnl != null ? fmtMoney(t.pnl) : "--"}</td>
          <td>${t.confidence_score != null ? t.confidence_score.toFixed ? t.confidence_score.toFixed(1) : t.confidence_score : "--"}</td>
          <td>${notes ? `<ul class="notes-list">${notes}</ul>` : "--"}</td>
        </tr>`;
      })
      .join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="11">Could not load tracked trades.</td></tr>`;
  }
}

async function renderHistory() {
  const tbody = document.querySelector("#historyTable tbody");
  try {
    const res = await fetch("/api/history?limit=200");
    const rows = await res.json();
    tbody.innerHTML = rows
      .map((r) => {
        const verdict = r.tradeable
          ? `<span class="pill ${r.direction}">${r.direction}</span>`
          : `<span class="pill neutral">no trade</span>`;
        return `<tr>
          <td>${fmtTime(r.timestamp)}</td>
          <td>${verdict}</td>
          <td>${STRATEGY_LABELS[r.strategy] || r.strategy || "--"}</td>
          <td>${r.score?.toFixed ? r.score.toFixed(1) : r.score}</td>
          <td>${r.spx_estimate ? r.spx_estimate.toFixed(2) : "--"}</td>
        </tr>`;
      })
      .join("");
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5">Could not load history.</td></tr>`;
  }
}

async function refreshSignal() {
  try {
    const res = await fetch("/api/signal");
    const data = await res.json();
    renderStatus(data);
  } catch (e) {
    document.getElementById("statusCard").innerHTML = "Could not load signal.";
  }
}

async function forceRefresh() {
  const btn = document.getElementById("refreshBtn");
  btn.disabled = true;
  btn.textContent = "Refreshing...";
  try {
    await fetch("/api/refresh", { method: "POST" });
  } catch (e) {
    // ignore -- next poll will pick it up
  }
  await Promise.all([refreshSignal(), renderHistory(), renderStats(), renderTrades(), renderPredictionStats()]);
  btn.disabled = false;
  btn.textContent = "Refresh now";
}

async function runDemo() {
  const btn = document.getElementById("demoBtn");
  const demoCard = document.getElementById("demoCard");
  btn.disabled = true;
  btn.textContent = "Loading last session...";
  demoCard.style.display = "block";
  demoCard.innerHTML = "Loading...";
  try {
    const res = await fetch("/api/demo", { method: "POST" });
    const data = await res.json();
    const label = data.session_date
      ? `Demo -- ${data.session_date} session @ ${data.evaluated_at || "?"}, chain: ${data.chain_expiration || "n/a"} (not live)`
      : "Demo -- not live";
    renderStatus(data, "demoCard", label, data.prediction_hindsight || null);
  } catch (e) {
    demoCard.innerHTML = "Could not load demo preview.";
  }
  btn.disabled = false;
  btn.textContent = "Preview last session";
}

document.getElementById("refreshBtn").addEventListener("click", forceRefresh);
document.getElementById("demoBtn").addEventListener("click", runDemo);

refreshSignal();
renderEcon();
renderHistory();
renderStats();
renderTrades();
renderPredictionStats();
setInterval(refreshSignal, 30000);
setInterval(renderHistory, 60000);
setInterval(renderStats, 60000);
setInterval(renderTrades, 60000);
setInterval(renderPredictionStats, 60000);
