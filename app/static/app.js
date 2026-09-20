const STRATEGY_LABELS = {
  bull_put_credit: "Bull Put Credit Spread",
  bear_call_credit: "Bear Call Credit Spread",
  bull_call_debit: "Bull Call Debit Spread",
  bear_put_debit: "Bear Put Debit Spread",
  iron_condor: "Iron Condor",
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

function renderStatus(data) {
  const el = document.getElementById("statusCard");
  const direction = data.direction || "none";

  if (!data.tradeable || !data.card) {
    el.className = "card status-card";
    const reasons = (data.reasons || []).map((r) => `<li>${r}</li>`).join("");
    el.innerHTML = `
      <div class="status-headline neutral">No confident trade -- standing aside</div>
      <div class="status-sub">Confidence score: ${data.score ?? 0}/100</div>
      <div class="score-bar"><div class="score-fill" style="width:${data.score ?? 0}%"></div></div>
      <ul class="rationale">${reasons || "<li>Waiting on more signal.</li>"}</ul>
    `;
    return;
  }

  const card = data.card;
  el.className = "card status-card tradeable";
  const dirClass = direction === "bullish" ? "bullish" : direction === "bearish" ? "bearish" : "neutral";
  const rationale = (card.rationale || []).map((r) => `<li>${r}</li>`).join("");

  el.innerHTML = `
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

async function renderHistory() {
  const tbody = document.querySelector("#historyTable tbody");
  try {
    const res = await fetch("/api/history?limit=25");
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
  await Promise.all([refreshSignal(), renderHistory()]);
  btn.disabled = false;
  btn.textContent = "Refresh now";
}

document.getElementById("refreshBtn").addEventListener("click", forceRefresh);

refreshSignal();
renderEcon();
renderHistory();
setInterval(refreshSignal, 30000);
setInterval(renderHistory, 60000);
