const apiBase = "";

const penceToGbp = (pence) => `GBP ${(Number(pence || 0) / 100).toFixed(2)}`;

const setBar = (id, percent) => {
  const el = document.getElementById(id);
  if (!el) return;
  const value = Math.max(0, Math.min(100, Number(percent || 0)));
  el.style.width = `${value}%`;
};

const safeDate = (value) => {
  if (!value) return "unknown";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return value;
  return dt.toLocaleString();
};

async function fetchJson(path) {
  const res = await fetch(`${apiBase}/api/${path}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

function renderTransactions(items) {
  const list = document.getElementById("transactions-list");
  list.innerHTML = "";
  if (!items.length) {
    const li = document.createElement("li");
    li.textContent = "No transactions available.";
    list.appendChild(li);
    return;
  }

  for (const tx of items) {
    const li = document.createElement("li");
    const left = document.createElement("div");
    const right = document.createElement("div");

    const merchant = document.createElement("div");
    merchant.textContent = tx.merchant || "Unknown";

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = `${tx.category || "uncategorised"} | ${safeDate(tx.occurred_at)}`;

    const amount = document.createElement("div");
    amount.className = `amount ${(tx.amount_pence || 0) < 0 ? "negative" : "positive"}`;
    amount.textContent = penceToGbp(tx.amount_pence || 0);

    left.appendChild(merchant);
    left.appendChild(meta);
    right.appendChild(amount);

    li.appendChild(left);
    li.appendChild(right);
    list.appendChild(li);
  }
}

async function refreshDashboard(category = "") {
  const [summary, txData] = await Promise.all([
    fetchJson("finance_summary"),
    fetchJson(`finance_transactions?limit=20${category ? `&category=${encodeURIComponent(category)}` : ""}`),
  ]);

  document.getElementById("weekly-label").textContent = `${penceToGbp(summary.weekly.spend_pence)} / ${penceToGbp(summary.weekly.target_pence)}`;
  document.getElementById("debt-label").textContent = `${summary.debt.months_remaining} months remaining | ${summary.debt.on_track ? "on track" : "behind"}`;
  document.getElementById("fund-label").textContent = `${penceToGbp(summary.emergency_fund.current_balance_pence)} / ${penceToGbp(summary.emergency_fund.target_pence)}`;
  document.getElementById("advice-text").textContent = summary.advice.text || "No advice generated yet.";
  document.getElementById("natwest-upload").textContent = `NatWest: ${safeDate(summary.uploads.natwest)}`;
  document.getElementById("paypal-upload").textContent = `PayPal: ${safeDate(summary.uploads.paypal)}`;

  setBar("weekly-bar", summary.weekly.progress_percent);
  setBar("debt-bar", summary.debt.progress_percent);
  setBar("fund-bar", summary.emergency_fund.progress_percent);

  document.getElementById("last-updated").textContent = `Last refreshed: ${new Date().toLocaleString()}`;
  renderTransactions(txData.transactions || []);
}

document.getElementById("category-filter").addEventListener("change", (event) => {
  refreshDashboard(event.target.value).catch((err) => {
    console.error(err);
  });
});

refreshDashboard().catch((err) => {
  console.error(err);
  document.getElementById("last-updated").textContent = "Failed to load dashboard data.";
});
