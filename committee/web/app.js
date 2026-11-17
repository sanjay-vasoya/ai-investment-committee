"use strict";

const $ = (id) => document.getElementById(id);

const form = $("analyze-form");
const tickersInput = $("tickers-input");
const usePortfolio = $("use-portfolio");
const useSa = $("use-sa");
const runBtn = $("run-btn");
const runPanel = $("run-panel");
const runTitle = $("run-title");
const runStatus = $("run-status");
const runLog = $("run-log");
const reportList = $("report-list");
const reportView = $("report-view");
const portfolioView = $("portfolio-view");
const portfolioBody = $("portfolio-body");
const verdictChips = $("verdict-chips");
const journalView = $("journal-view");
const journalBody = $("journal-body");
const welcome = $("welcome");

let pollTimer = null;
let activeReportName = null;
let portfolioRows = [];
let activeVerdictFilter = "all";

async function api(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
    throw new Error(detail);
  }
  return res.json();
}

function show(view) {
  welcome.classList.toggle("hidden", view !== "welcome");
  reportView.classList.toggle("hidden", view !== "report");
  portfolioView.classList.toggle("hidden", view !== "portfolio");
  journalView.classList.toggle("hidden", view !== "journal");
}

/* ------------------------------------------------------------- meta */

async function loadMeta() {
  try {
    const meta = await api("/api/meta");
    $("disclaimer").textContent = meta.disclaimer;
    $("portfolio-hint").textContent = meta.portfolio_configured
      ? "Portfolio file found: data/portfolio.json"
      : "No data/portfolio.json — portfolio review will be skipped.";
  } catch (e) { /* non-fatal */ }
}

/* -------------------------------------------------- seeking alpha */

async function loadSaStatus() {
  try {
    const s = await api("/api/sa-status");
    if (s.ticker_count === 0) {
      $("sa-status").textContent = "No data uploaded yet.";
      useSa.checked = false;
    } else {
      $("sa-status").textContent =
        `${s.ticker_count} tickers · latest export ${s.latest_as_of}` +
        (s.tickers.length <= 8 ? ` (${s.tickers.join(", ")})` : "");
    }
  } catch (e) { /* non-fatal */ }
}

$("sa-file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const msg = $("sa-upload-msg");
  msg.textContent = "Uploading…";
  try {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch("/api/sa-upload", { method: "POST", body });
    if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
    const summary = await res.json();
    msg.textContent =
      `Imported ${summary.tickers_in_file.length} tickers from ${summary.file} ` +
      `(as of ${summary.as_of}). Store now has ${summary.tickers_in_store}.`;
    useSa.checked = true;
    await loadSaStatus();
  } catch (e) {
    msg.textContent = `Upload failed: ${e.message}`;
  } finally {
    ev.target.value = "";
  }
});

/* ---------------------------------------------------------- reports */

async function loadReports(selectName) {
  const reports = await api("/api/reports");
  reportList.innerHTML = "";
  if (!reports.length) {
    const li = document.createElement("li");
    li.className = "empty";
    li.textContent = "No reports yet";
    reportList.appendChild(li);
    return;
  }
  for (const r of reports) {
    const li = document.createElement("li");
    li.dataset.name = r.name;
    li.innerHTML = `<span class="ticker">${r.ticker}</span><span class="date">${r.date}</span>`;
    li.addEventListener("click", () => openReport(r.name));
    if (r.name === (selectName || activeReportName)) li.classList.add("active");
    reportList.appendChild(li);
  }
  if (selectName) openReport(selectName);
}

function makeChips(container, values, onSelect) {
  container.innerHTML = "";
  for (const value of values) {
    const btn = document.createElement("button");
    btn.className = "chip" + (value === "all" ? " active" : "");
    btn.textContent = value;
    btn.addEventListener("click", () => {
      for (const c of container.children) c.classList.remove("active");
      btn.classList.add("active");
      onSelect(value);
    });
    container.appendChild(btn);
  }
}

/* Add verdict filter chips above the Portfolio Manager table in a report. */
function enhancePmTable() {
  const heading = [...reportView.querySelectorAll("h2")]
    .find((h) => h.textContent.trim().startsWith("Portfolio Manager"));
  if (!heading) return;
  let table = heading.nextElementSibling;
  while (table && table.tagName !== "TABLE") table = table.nextElementSibling;
  if (!table) return;
  const headers = [...table.querySelectorAll("th")].map((t) => t.textContent.trim());
  const vIdx = headers.indexOf("Verdict");
  if (vIdx === -1) return;

  const rows = [...table.querySelectorAll("tbody tr")];
  const verdicts = [...new Set(rows.map((r) => r.cells[vIdx].textContent.trim()))].sort();
  if (verdicts.length < 2) return;

  const chips = document.createElement("div");
  chips.className = "chips";
  table.parentNode.insertBefore(chips, table);
  makeChips(chips, ["all", ...verdicts], (v) => {
    for (const row of rows) {
      row.classList.toggle("hidden", v !== "all" && row.cells[vIdx].textContent.trim() !== v);
    }
  });
}

async function openReport(name) {
  try {
    const data = await api(`/api/reports/${encodeURIComponent(name)}`);
    activeReportName = name;
    reportView.innerHTML = data.html;
    enhancePmTable();
    show("report");
    for (const li of reportList.children) {
      li.classList.toggle("active", li.dataset.name === name);
    }
    reportView.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    alert(`Could not open report: ${e.message}`);
  }
}

/* -------------------------------------------------------- portfolio */

function renderPortfolioRows() {
  portfolioBody.innerHTML = "";
  const rows = portfolioRows.filter(
    (r) => activeVerdictFilter === "all" || r.verdict === activeVerdictFilter,
  );
  if (!rows.length) {
    portfolioBody.innerHTML =
      `<tr><td colspan="8" style="color:#66727f">No holdings match this filter.</td></tr>`;
    return;
  }
  for (const r of rows) {
    const tr = document.createElement("tr");
    const qty = typeof r.quantity === "number" ? r.quantity : (r.quantity || "");
    const reviewed = r.report
      ? `<span class="report-link" data-name="${r.report}">${r.reviewed_in} · ${r.date}</span>`
      : "—";
    tr.innerHTML = `
      <td><strong>${r.ticker}</strong>${r.detail ? ` <span class="hint-dark">${r.detail}</span>` : ""}</td>
      <td>${r.type === "leaps_call" ? "LEAPS" : "equity"}</td>
      <td>${qty}</td>
      <td>${r.cost_basis ?? ""}</td>
      <td>${r.sa_quant_rating ?? "—"}</td>
      <td class="decision verdict-${r.verdict}">${r.verdict}</td>
      <td class="rationale">${r.rationale || ""}</td>
      <td>${reviewed}</td>`;
    const link = tr.querySelector(".report-link");
    if (link) link.addEventListener("click", () => openReport(link.dataset.name));
    portfolioBody.appendChild(tr);
  }
}

async function openPortfolio() {
  const data = await api("/api/portfolio-view");
  if (!data.portfolio_configured) {
    $("portfolio-meta").textContent =
      "No data/portfolio.json found — import or create one to use this screen.";
    portfolioRows = [];
    verdictChips.innerHTML = "";
    renderPortfolioRows();
    show("portfolio");
    return;
  }
  portfolioRows = data.rows;
  const cash = typeof data.cash === "number"
    ? `cash ${data.cash < 0 ? "−" : ""}$${Math.abs(data.cash).toLocaleString()}` : "";
  $("portfolio-meta").textContent =
    `${data.rows.length} holdings · ${cash} · imported ${(data.as_of || "").slice(0, 10)}`;
  const verdicts = [...new Set(data.rows.map((r) => r.verdict))].sort();
  activeVerdictFilter = "all";
  makeChips(verdictChips, ["all", ...verdicts], (v) => {
    activeVerdictFilter = v;
    renderPortfolioRows();
  });
  renderPortfolioRows();
  show("portfolio");
}

$("portfolio-btn").addEventListener("click", () => {
  openPortfolio().catch((e) => alert(`Could not load portfolio: ${e.message}`));
});

/* ------------------------------------------------------------- runs */

function setRunBadge(status) {
  runStatus.textContent = status.replaceAll("_", " ");
  runStatus.className = "badge " + (
    status === "running" ? "badge-running"
    : status === "failed" ? "badge-failed"
    : "badge-done");
}

async function pollRun(runId) {
  let run;
  try {
    run = await api(`/api/runs/${runId}`);
  } catch (e) {
    clearInterval(pollTimer);
    runBtn.disabled = false;
    return;
  }
  runLog.textContent = run.log.join("\n");
  runLog.scrollTop = runLog.scrollHeight;
  setRunBadge(run.status);

  if (run.status !== "running") {
    clearInterval(pollTimer);
    pollTimer = null;
    runBtn.disabled = false;
    const newest = run.reports[run.reports.length - 1];
    await loadReports(newest || undefined);
  }
}

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const tickers = tickersInput.value.split(/[\s,]+/).filter(Boolean);
  if (!tickers.length) return;

  runBtn.disabled = true;
  try {
    const run = await api("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tickers,
        use_portfolio: usePortfolio.checked,
        use_seeking_alpha: useSa.checked,
      }),
    });
    runTitle.textContent = `Committee in session — ${run.tickers.join(", ")}`;
    runPanel.classList.remove("hidden");
    setRunBadge("running");
    runLog.textContent = run.log.join("\n");
    pollTimer = setInterval(() => pollRun(run.id), 1500);
  } catch (e) {
    runBtn.disabled = false;
    alert(`Could not start run: ${e.message}`);
  }
});

/* ---------------------------------------------------------- journal */

$("journal-btn").addEventListener("click", async () => {
  const entries = await api("/api/journal");
  journalBody.innerHTML = "";
  if (!entries.length) {
    journalBody.innerHTML = `<tr><td colspan="6" style="color:#66727f">No entries yet.</td></tr>`;
  }
  for (const e of entries) {
    const tr = document.createElement("tr");
    const reportName = (e.report_path || "").split("/").pop() || "";
    tr.innerHTML = `
      <td>${(e.timestamp || "").slice(0, 10)}</td>
      <td>${e.ticker}</td>
      <td class="decision decision-${e.decision}">${e.decision}</td>
      <td>${Math.round(e.conviction_total)}/100</td>
      <td>${e.confidence}</td>
      <td><span class="report-link" data-name="${reportName}">${reportName}</span></td>`;
    tr.querySelector(".report-link").addEventListener("click", () => openReport(reportName));
    journalBody.appendChild(tr);
  }
  show("journal");
});

/* ------------------------------------------------------------ boot */

async function resumeActiveRun() {
  try {
    const runs = await api("/api/runs");
    const active = runs.find((r) => r.status === "running");
    if (active) {
      runTitle.textContent = `Committee in session — ${active.tickers.join(", ")}`;
      runPanel.classList.remove("hidden");
      runBtn.disabled = true;
      pollTimer = setInterval(() => pollRun(active.id), 1500);
    }
  } catch (e) { /* ignore */ }
}

loadMeta();
loadSaStatus();
loadReports();
resumeActiveRun();
show("welcome");
