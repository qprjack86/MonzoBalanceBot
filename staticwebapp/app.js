const fmtMoney = (pence) => `GBP ${(Number(pence || 0) / 100).toFixed(2)}`;
const API_BASE = window.FINANCE_API_BASE || (
  window.location.hostname.endsWith(".azurestaticapps.net")
    ? "https://monzowatchdog-js.azurewebsites.net/api"
    : "/api"
);

async function fetchJson(url) {
  const response = await fetch(url, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

function setBar(id, ratio) {
  const pct = Math.max(0, Math.min(1, Number(ratio || 0))) * 100;
  document.getElementById(id).style.width = `${pct}%`;
}

function renderTransactions(rows) {
  const list = document.getElementById("txList");
  list.innerHTML = "";

  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "meta";
    empty.textContent = "No transactions for this filter yet.";
    list.appendChild(empty);
    return;
  }

  rows.forEach((row) => {
    const node = document.createElement("article");
    node.className = "tx";
    node.innerHTML = `
      <div class="tx-top">
        <strong>${row.merchant || "Unknown"}</strong>
        <strong>${fmtMoney(row.amount_pence)}</strong>
      </div>
      <p class="tx-meta">${new Date(row.date_iso).toLocaleString()} • ${row.category} • ${row.source}</p>
    `;
    list.appendChild(node);
  });
}

async function loadSummary() {
  const summary = await fetchJson(`${API_BASE}/dashboard/summary`);

  document.getElementById("weeklySpendMetric").textContent = `${fmtMoney(summary.weekly_spend_pence)} / ${fmtMoney(summary.weekly_target_pence)}`;
  document.getElementById("weeklySpendMeta").textContent = "Resets every Monday";
  setBar("weeklySpendBar", summary.weekly_progress);

  document.getElementById("debtMetric").textContent = fmtMoney(summary.debt.current_balance_pence);
  document.getElementById("debtMeta").textContent = `${summary.debt.months_remaining} months remaining (${summary.debt.on_track ? "on track" : "behind"})`;
  setBar("debtBar", summary.debt.progress);

  document.getElementById("fundMetric").textContent = fmtMoney(summary.emergency_fund.current_balance_pence);
  document.getElementById("fundMeta").textContent = `Target ${fmtMoney(summary.emergency_fund.target_balance_pence)}`;
  setBar("fundBar", summary.emergency_fund.progress);

  document.getElementById("adviceText").textContent = summary.advice.text || "No advice yet.";
  document.getElementById("natwestUpload").textContent = summary.last_uploads.natwest ? new Date(summary.last_uploads.natwest).toLocaleString() : "No upload yet";
  document.getElementById("paypalUpload").textContent = summary.last_uploads.paypal ? new Date(summary.last_uploads.paypal).toLocaleString() : "No upload yet";
  document.getElementById("potBalance").textContent = summary.pot_balance_pence == null ? "Not configured" : fmtMoney(summary.pot_balance_pence);
}

async function loadTransactions() {
  const category = document.getElementById("categoryFilter").value;
  const url = category
    ? `${API_BASE}/dashboard/transactions?category=${encodeURIComponent(category)}`
    : `${API_BASE}/dashboard/transactions`;

  const result = await fetchJson(url);
  renderTransactions(result.transactions || []);
}

async function refreshAll() {
  try {
    await Promise.all([loadSummary(), loadTransactions()]);
  } catch (err) {
    console.error(err);
    document.getElementById("adviceText").textContent = "Dashboard refresh failed. Check Function App logs.";
  }
}

document.getElementById("refreshBtn").addEventListener("click", refreshAll);
document.getElementById("categoryFilter").addEventListener("change", loadTransactions);

refreshAll();
