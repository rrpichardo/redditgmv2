const state = {
  tag: "gm_vehicle_on_demand",
  view: "dashboard",
  data: null,
  report: "",
  collectPollTimer: null,
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

const fmt = new Intl.NumberFormat("en-US");
const pct = (value) => `${Number(value || 0).toFixed(1)}%`;
const fileSize = (bytes) => {
  const value = Number(bytes || 0);
  if (!value) return "0 KB";
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(value / 1024))} KB`;
};
const esc = (value) =>
  String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");

function apiUrl(path, params = {}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (Array.isArray(value)) {
      value.forEach((item) => item && url.searchParams.append(key, item));
    } else if (value !== undefined && value !== null && value !== "") {
      url.searchParams.set(key, value);
    }
  });
  return url;
}

async function request(path, options = {}) {
  const response = await fetch(path, options);
  const text = await response.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {
    body = { detail: text };
  }
  if (!response.ok) {
    throw new Error(body.detail || `Request failed with ${response.status}`);
  }
  return body;
}

function setNotice(message = "", type = "") {
  const bar = $("#statusBar");
  if (!message) {
    bar.innerHTML = "";
    return;
  }
  bar.innerHTML = `<div class="notice ${type}" role="status">${esc(message)}</div>`;
}

function setBusy(button, busy, label) {
  if (!button) return;
  button.disabled = busy;
  if (label) button.textContent = busy ? "Working..." : label;
}

function compactText(value, fallback = "unknown") {
  const text = String(value ?? "").trim();
  return text || fallback;
}

function humanLabel(value) {
  return compactText(value).replaceAll("_", " ");
}

function topItem(rows, labelKey, valueKey = "count") {
  if (!rows?.length) return null;
  return [...rows].sort((a, b) => Number(b[valueKey] || 0) - Number(a[valueKey] || 0))[0];
}

function collectProgressText(status) {
  const done = Number(status?.completed_subreddits || 0);
  const total = Number(status?.total_subreddits || 0);
  const count = total ? `${done}/${total}` : `${done}`;
  const last = status?.last_subreddit ? ` | last: r/${status.last_subreddit}` : "";
  const elapsed = status?.elapsed_seconds ? ` | ${Math.round(status.elapsed_seconds)}s` : "";
  return `${count} subreddits${last}${elapsed}`;
}

function clearCollectPolling() {
  if (state.collectPollTimer) {
    window.clearInterval(state.collectPollTimer);
    state.collectPollTimer = null;
  }
}

function updateCollectUi(status) {
  const running = status?.status === "running";
  const failed = status?.status === "failed";
  const progress = collectProgressText(status);
  const button = $("#collectBtn");
  setBusy(button, running, "Run collector");

  const progressBox = $("#collectProgress");
  if (progressBox) {
    progressBox.hidden = false;
    progressBox.textContent = `${status.status}: ${progress}`;
    progressBox.classList.toggle("is-running", running);
    progressBox.classList.toggle("is-failed", failed);
  }

  const log = $("#collectLog");
  if (log) {
    log.hidden = false;
    log.textContent = status?.log || `${status.status}: ${progress}`;
    log.scrollTop = log.scrollHeight;
  }

  if (running) {
    setNotice(`Collector running: ${progress}`);
  } else if (failed) {
    setNotice("Collector failed. The log has the details.", "error");
  } else if (status?.status === "completed") {
    setNotice(`Collector completed: ${progress}`, "success");
  }
}

async function loadRun() {
  state.tag = $("#tagInput").value.trim() || "gm_vehicle_on_demand";
  setNotice("Loading run...");
  try {
    state.data = await request(apiUrl("/api/run", { tag: state.tag }));
    const collectStatus = await request(apiUrl("/api/collect/status", { tag: state.tag })).catch(() => null);
    if (collectStatus?.status === "running") {
      $("#collectorStatus").textContent = `running ${collectProgressText(collectStatus)}`;
    } else {
      $("#collectorStatus").textContent = state.data.status.legacy_collector_found ? "ready" : "missing";
    }
    $("#runSubtitle").textContent = `${state.data.tag} / ${fmt.format(state.data.summary.metrics.total_rows)} rows / ${fmt.format(state.data.summary.metrics.analyzed_rows)} analyzed / ${state.data.status.has_classified ? "classified" : "source only"}`;
    $("#railRows").textContent = `${fmt.format(state.data.summary.metrics.total_rows)} total`;
    $("#railExports").textContent = state.data.status.has_source ? "ready" : "empty";
    updateDownloads();
    render();
    if (collectStatus?.status === "running") {
      updateCollectUi(collectStatus);
      startCollectPolling(collectStatus.job_id);
    } else {
      setNotice("");
    }
  } catch (error) {
    setNotice(error.message, "error");
  }
}

function updateDownloads() {
  const classified = $("#classifiedDownload");
  const report = $("#reportDownload");
  const sourceZip = $("#sourceZipDownload");
  const combined = $("#combinedDownload");
  const saveZip = $("#saveZipBtn");
  const hasSource = Boolean(state.data?.status.has_source);

  if (saveZip) {
    saveZip.disabled = !hasSource;
  }
  if (sourceZip) {
    sourceZip.href = `/api/download/source?tag=${encodeURIComponent(state.tag)}&kind=all`;
    sourceZip.setAttribute("aria-disabled", hasSource ? "false" : "true");
  }
  if (combined) {
    combined.href = `/api/download/source?tag=${encodeURIComponent(state.tag)}&kind=combined`;
    combined.setAttribute("aria-disabled", hasSource ? "false" : "true");
  }
  classified.href = `/api/download/classified?tag=${encodeURIComponent(state.tag)}`;
  report.href = `/api/download/report?tag=${encodeURIComponent(state.tag)}`;
  classified.setAttribute("aria-disabled", state.data?.status.has_classified ? "false" : "true");
  report.setAttribute("aria-disabled", state.data?.status.has_report ? "false" : "true");
}

function setView(view) {
  state.view = view;
  $$(".tab").forEach((tab) => tab.classList.toggle("is-active", tab.dataset.view === view));
  render();
}

function emptyState() {
  const template = $("#emptyTemplate").content.cloneNode(true);
  const root = document.createElement("div");
  root.appendChild(template);
  $(".button", root).addEventListener("click", () => setView("collect"));
  return root.innerHTML;
}

function metricGrid(metrics) {
  const cards = [
    ["Analyzed rows", fmt.format(metrics.analyzed_rows), `${fmt.format(metrics.skipped_rows)} skipped`, "volume"],
    ["Complaint rate", pct(metrics.complaint_rate), `${fmt.format(metrics.complaints)} complaint rows`, "alert"],
    ["Negative rate", pct(metrics.negative_rate), "share of analyzed rows", "negative"],
    ["Competitor signal", pct(metrics.competitor_rate), "mentions outside GM", "rival"],
    ["EV topic mix", pct(metrics.ev_rate), "EV-related rows", "ev"],
  ];
  return `<div class="metric-grid">${cards
    .map(([label, value, note, tone]) => `<article class="metric-card tone-${tone}">
      <span>${label}</span>
      <strong>${value}</strong>
      <small>${note}</small>
    </article>`)
    .join("")}</div>`;
}

function bars(rows, labelKey, valueKey = "count", color = "var(--blue)") {
  if (!rows?.length) return `<div class="notice">No data for this view.</div>`;
  const max = Math.max(...rows.map((row) => Number(row[valueKey] || 0)), 1);
  return `<div class="bar-list">${rows
    .map((row) => {
      const value = Number(row[valueKey] || 0);
      return `<div class="bar-row">
        <strong>${esc(humanLabel(row[labelKey]))}</strong>
        <div class="bar-track" aria-hidden="true"><div class="bar-fill" style="width:${(value / max) * 100}%;background:${color}"></div></div>
        <span class="bar-value">${fmt.format(value)}</span>
      </div>`;
    })
    .join("")}</div>`;
}

function panel(title, body, note = "", className = "") {
  return `<section class="panel ${esc(className)}"><div class="panel-head"><h3>${esc(title)}</h3>${note ? `<small>${esc(note)}</small>` : ""}</div>${body}</section>`;
}

function dashboard() {
  const data = state.data;
  if (!data?.summary.metrics.total_rows) return emptyState();
  const charts = data.charts;
  const topTheme = topItem(charts.complaints, "theme");
  const topVehicle = topItem(charts.vehicles, "vehicle_mentioned", "total_rows");
  return `
    <section class="readout-deck">
      <div>
        ${metricGrid(data.summary.metrics)}
      </div>
      <aside class="run-card">
        <span class="micro-label">Run focus</span>
        <strong>${esc(topTheme ? humanLabel(topTheme.theme) : "No dominant complaint")}</strong>
        <p>${esc(topVehicle ? `${humanLabel(topVehicle.vehicle_mentioned)} has the largest visible sample.` : "Collect or classify more rows to build a stronger issue map.")}</p>
        <div class="run-card-actions">
          <button class="button primary" type="button" data-save-export>Save ZIP</button>
          <button class="button secondary" type="button" data-jump="collect">Collect</button>
        </div>
      </aside>
    </section>
    <div class="panel-grid">
      ${panel("Sentiment distribution", bars(charts.sentiment, "sentiment", "count", "var(--green)"))}
      ${panel("Signal flags", bars(charts.flags.slice(0, 8), "flag", "count", "var(--blue)"))}
      ${panel("Complaint themes", bars(charts.complaints.slice(0, 8), "theme", "count", "var(--amber)"))}
    </div>
    <div class="panel-grid two">
      ${panel("Priority map", scatter(charts.priority), "volume x negativity", "priority-panel")}
      ${panel("Evidence feed", evidenceFeed(data.evidence.slice(0, 8)), "latest matched rows", "evidence-panel")}
    </div>`;
}

function scatter(rows) {
  if (!rows?.length) return `<div class="notice">No complaint priorities yet.</div>`;
  const width = 720;
  const height = 360;
  const pad = 44;
  const plotted = rows.slice(0, 12);
  const maxX = Math.max(...plotted.map((row) => Number(row.volume || 0)), 1);
  const maxY = Math.max(...plotted.map((row) => Number(row.pct_negative || 0)), 100);
  const points = plotted
    .map((row) => {
      const x = pad + (Number(row.volume || 0) / maxX) * (width - pad * 2);
      const y = height - pad - (Number(row.pct_negative || 0) / maxY) * (height - pad * 2);
      const radius = Math.max(7, Math.min(24, Number(row.volume || 1) * 4));
      const fill = row.priority === "Fix now" ? "#c2413b" : row.priority === "Monitor" ? "#d97706" : "#1e40af";
      return `<g tabindex="0" aria-label="${esc(row.theme)} ${row.volume} complaints ${row.pct_negative} percent negative">
        <circle cx="${x}" cy="${y}" r="${radius}" fill="${fill}" opacity="0.78"></circle>
        <title>${esc(humanLabel(row.theme))}: ${fmt.format(row.volume || 0)} complaints, ${pct(row.pct_negative)}</title>
      </g>`;
    })
    .join("");
  const legend = plotted
    .slice(0, 6)
    .map((row) => `<li><strong>${esc(humanLabel(row.theme))}</strong><span>${fmt.format(row.volume || 0)} / ${pct(row.pct_negative)}</span></li>`)
    .join("");
  return `<div class="priority-map">
    <svg class="chart-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Complaint priority matrix">
      <rect x="${pad}" y="${pad}" width="${width - pad * 2}" height="${height - pad * 2}" rx="10"></rect>
      <line x1="${pad}" y1="${height - pad}" x2="${width - pad}" y2="${height - pad}"></line>
      <line x1="${pad}" y1="${pad}" x2="${pad}" y2="${height - pad}"></line>
      <line class="axis-mid" x1="${pad}" y1="${height / 2}" x2="${width - pad}" y2="${height / 2}"></line>
      <line class="axis-mid" x1="${width / 2}" y1="${pad}" x2="${width / 2}" y2="${height - pad}"></line>
      <text x="${width / 2}" y="${height - 10}" text-anchor="middle">Complaint volume</text>
      <text x="16" y="${height / 2}" transform="rotate(-90 16 ${height / 2})" text-anchor="middle">% negative</text>
      ${points}
    </svg>
    <ol class="priority-legend">${legend}</ol>
  </div>`;
}

function evidenceFeed(rows) {
  if (!rows?.length) return `<div class="notice">No evidence rows for this view.</div>`;
  return `<div class="evidence-feed">${rows
    .map((row) => `<article class="evidence-item">
      <div class="evidence-meta">
        <span>${esc(row.created_at_norm || "")}</span>
        <span>r/${esc(row.subreddit_norm || "unknown")}</span>
        <span>${esc(row.sentiment || "unlabeled")}</span>
      </div>
      <a href="${esc(row.permalink_norm || "#")}" target="_blank" rel="noreferrer">${esc(row.description || row.title_norm || "No text available")}</a>
      <div class="evidence-tags">
        <span>${esc(humanLabel(row.vehicle_mentioned))}</span>
        <span>${esc(humanLabel(row.top_complaint_category))}</span>
      </div>
    </article>`)
    .join("")}</div>`;
}

function evidenceTable(rows) {
  if (!rows?.length) return `<div class="notice">No evidence rows for this view.</div>`;
  const headers = ["date", "subreddit", "vehicle", "sentiment", "theme", "score", "description"];
  return `<div class="table-wrap"><table>
    <thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead>
    <tbody>${rows
      .map(
        (row) => `<tr>
          <td>${esc(row.created_at_norm || "")}</td>
          <td>${esc(row.subreddit_norm || "")}</td>
          <td>${esc(row.vehicle_mentioned || "")}</td>
          <td>${esc(row.sentiment || "")}</td>
          <td>${esc(row.top_complaint_category || "")}</td>
          <td>${esc(row.score_norm || "")}</td>
          <td>${row.permalink_norm ? `<a href="${esc(row.permalink_norm)}" target="_blank" rel="noreferrer">${esc(row.description || row.title_norm || "")}</a>` : esc(row.description || row.title_norm || "")}</td>
        </tr>`
      )
      .join("")}</tbody>
  </table></div>`;
}

function sourceDownloadsPanel() {
  const files = state.data?.status.source_files || [];
  const hasSource = Boolean(state.data?.status.has_source);
  const fileMap = Object.fromEntries(files.map((file) => [file.kind, file]));
  const items = [
    ["all", "All data ZIP", hasSource, "raw + classified + report"],
    ["combined", "Posts + comments CSV", fileMap.combined?.exists, fileSize(fileMap.combined?.bytes)],
    ["posts", "Posts CSV", fileMap.posts?.exists, fileSize(fileMap.posts?.bytes)],
    ["comments", "Comments CSV", fileMap.comments?.exists, fileSize(fileMap.comments?.bytes)],
  ];
  return `<section class="panel download-panel">
    <div class="panel-head"><h2>Exports</h2><small>${hasSource ? `${fmt.format(state.data.summary.metrics.total_rows)} rows loaded` : "no source rows"}</small></div>
    <div class="field-row flush"><button id="saveZipPanelBtn" class="button primary" type="button" ${hasSource ? "" : "disabled"}>Save ZIP to Downloads</button></div>
    <div class="download-grid">${items
      .map(([kind, label, enabled, note]) => `<a class="download-tile" href="/api/download/source?tag=${encodeURIComponent(state.tag)}&kind=${kind}" aria-disabled="${enabled ? "false" : "true"}" data-save-kind="${kind}">
        <strong>${esc(label)}</strong>
        <span>${esc(note)}</span>
      </a>`)
      .join("")}</div>
  </section>`;
}

function collectView() {
  return `
    <div class="collect-layout">
      <section class="panel collector-panel">
        <div class="panel-head"><h2>Collector</h2><small>append-only</small></div>
        <div class="form-grid">
          <div class="control"><label for="collectSource">Source</label><select id="collectSource"><option value="gm">GM vehicle list</option><option value="competitor">Competitor list</option><option value="custom">Custom list</option></select></div>
          <div class="control"><label for="listingLimit">Posts per subreddit</label><input id="listingLimit" type="number" min="1" max="500" value="100"></div>
          <div class="control"><label for="commentsLimit">Comments per post</label><input id="commentsLimit" type="number" min="0" max="25" value="5"></div>
          <div class="control"><label for="sinceDays">Since days</label><input id="sinceDays" type="number" min="0" max="3650" value="0"></div>
          <div class="control wide"><label for="customSubs">Custom subreddits</label><textarea id="customSubs" placeholder="Silverado&#10;Chevy"></textarea></div>
          <div class="control"><label for="dryRun">Dry run</label><select id="dryRun"><option value="false">No</option><option value="true">Yes</option></select></div>
        </div>
        <div class="field-row"><button id="collectBtn" class="button primary">Run collector</button></div>
        <div id="collectProgress" class="collector-progress" hidden></div>
        <pre id="collectLog" class="log-box" hidden></pre>
      </section>
      ${sourceDownloadsPanel()}
    </div>
    <section class="panel upload-panel">
      <div class="panel-head"><h2>Upload CSV</h2><small>collector or classified output</small></div>
      <div class="field-row"><input id="uploadInput" type="file" accept=".csv"><button id="uploadBtn" class="button secondary">Load CSV</button></div>
    </section>`;
}

function classifyView() {
  const metrics = state.data?.summary.metrics || {};
  return `
    <section class="panel classify-panel">
      <div class="panel-head"><h2>Classify</h2><small>${fmt.format(metrics.total_rows || 0)} source rows</small></div>
      ${metricGrid(metrics)}
      <div class="field-row">
        <label class="control" style="max-width:180px"><span>Rows</span><input id="classifyLimit" type="number" min="1" value="50"></label>
        <button id="previewClassifyBtn" class="button primary">Preview classify</button>
        <button id="llmClassifyBtn" class="button secondary">LLM classify</button>
      </div>
    </section>
    <section class="panel spaced">
      <div class="panel-head"><h2>Evidence sample</h2><small>classification context</small></div>
      ${evidenceFeed((state.data?.evidence || []).slice(0, 10))}
    </section>`;
}

function exploreView() {
  if (!state.data?.summary.metrics.total_rows) return emptyState();
  const charts = state.data.charts;
  return `
    ${metricGrid(state.data.summary.metrics)}
    <div class="panel-grid two">
      ${panel("Complaint priority", scatter(charts.priority))}
      ${panel("EV comparison", bars(charts.ev, "powertrain", "complaint_rate_pct", "var(--teal)"), "complaint rate")}
    </div>
    <div class="panel-grid two">
      ${panel("Vehicles", bars(charts.vehicles.slice(0, 12), "vehicle_mentioned", "complaint_rate_pct", "var(--blue)"), "complaint rate")}
      ${panel("Competitors", bars(charts.competitors, "brand", "count", "var(--red)"))}
    </div>
    <section class="panel spaced">
      <div class="panel-head"><h2>Matched evidence</h2><small>latest 200 rows</small></div>
      ${evidenceTable(state.data.evidence)}
    </section>`;
}

function briefingView() {
  return `
    <section class="panel briefing-actions">
      <div class="panel-head"><h2>Briefing</h2><small>markdown export</small></div>
      <div class="field-row">
        <button id="templateBriefBtn" class="button primary">Template briefing</button>
        <button id="llmBriefBtn" class="button secondary">LLM briefing</button>
      </div>
    </section>
    <section class="panel spaced">
      <pre id="briefingText" class="briefing">${esc(state.report || "No briefing generated yet.")}</pre>
    </section>`;
}

function render() {
  const root = $("#viewRoot");
  const views = {
    dashboard,
    collect: collectView,
    classify: classifyView,
    explore: exploreView,
    briefing: briefingView,
  };
  root.innerHTML = views[state.view]();
  bindViewEvents();
}

function handleSaveKindClick(event) {
  const target = event.currentTarget;
  event.preventDefault();
  if (target.getAttribute("aria-disabled") === "true" || target.disabled) return;
  saveExport(target.dataset.saveKind || "all");
}

function bindViewEvents() {
  $$("[data-jump]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.jump)));
  $$("[data-save-export]").forEach((button) => button.addEventListener("click", () => saveExport("all")));
  $$("#viewRoot [data-save-kind]").forEach((target) => target.addEventListener("click", handleSaveKindClick));
  $("#collectBtn")?.addEventListener("click", collect);
  $("#saveZipPanelBtn")?.addEventListener("click", () => saveExport("all"));
  $("#uploadBtn")?.addEventListener("click", upload);
  $("#previewClassifyBtn")?.addEventListener("click", previewClassify);
  $("#llmClassifyBtn")?.addEventListener("click", llmClassify);
  $("#templateBriefBtn")?.addEventListener("click", () => briefing(false));
  $("#llmBriefBtn")?.addEventListener("click", () => briefing(true));
}

function startCollectPolling(jobId = "") {
  clearCollectPolling();
  state.collectPollTimer = window.setInterval(() => {
    pollCollectStatus(jobId);
  }, 1500);
}

async function pollCollectStatus(jobId = "") {
  try {
    const status = await request(apiUrl("/api/collect/status", { tag: state.tag, job_id: jobId }));
    updateCollectUi(status);
    if (status.status !== "running") {
      clearCollectPolling();
      setBusy($("#collectBtn"), false, "Run collector");
      await loadRun();
      updateCollectUi(status);
    }
  } catch (error) {
    clearCollectPolling();
    setBusy($("#collectBtn"), false, "Run collector");
    setNotice(error.message, "error");
  }
}

async function collect() {
  const button = $("#collectBtn");
  clearCollectPolling();
  setBusy(button, true, "Run collector");
  setNotice("Collector starting...");
  const body = {
    tag: state.tag,
    source: $("#collectSource").value,
    subreddits: $("#customSubs").value,
    listing_limit: Number($("#listingLimit").value || 100),
    comments_limit: Number($("#commentsLimit").value || 5),
    since_days: Number($("#sinceDays").value || 0),
    dry_run: $("#dryRun").value === "true",
  };
  try {
    const result = await request("/api/collect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    updateCollectUi(result);
    if (result.status === "running") {
      startCollectPolling(result.job_id);
    } else {
      setBusy(button, false, "Run collector");
      await loadRun();
      updateCollectUi(result);
    }
  } catch (error) {
    setNotice(error.message, "error");
    setBusy(button, false, "Run collector");
  }
}

async function saveExport(kind = "all") {
  const labels = {
    all: "ZIP",
    combined: "posts + comments CSV",
    posts: "posts CSV",
    comments: "comments CSV",
    classified: "classified CSV",
    report: "briefing",
  };
  const buttons = kind === "all" ? [$("#saveZipBtn"), $("#saveZipPanelBtn")].filter(Boolean) : [];
  buttons.forEach((button) => setBusy(button, true, "Save ZIP"));
  setNotice(`Saving ${labels[kind] || "export"}...`);
  try {
    const result = await request(apiUrl("/api/export/save", { tag: state.tag, kind }), { method: "POST" });
    setNotice(`Saved ${result.filename} to Downloads.`, "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    buttons.forEach((button) => setBusy(button, false, button.id === "saveZipBtn" ? "Save ZIP" : "Save ZIP to Downloads"));
  }
}

async function upload() {
  const input = $("#uploadInput");
  const button = $("#uploadBtn");
  if (!input.files?.length) {
    setNotice("Choose a CSV first.", "error");
    return;
  }
  setBusy(button, true, "Load CSV");
  const form = new FormData();
  form.append("file", input.files[0]);
  try {
    const result = await request(apiUrl("/api/upload", { tag: state.tag }), { method: "POST", body: form });
    setNotice(`Loaded ${fmt.format(result.rows)} rows as ${result.kind}.`, "success");
    await loadRun();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(button, false, "Load CSV");
  }
}

async function previewClassify() {
  const button = $("#previewClassifyBtn");
  setBusy(button, true, "Preview classify");
  try {
    const result = await request("/api/classify/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tag: state.tag, limit: Number($("#classifyLimit").value || 50) }),
    });
    setNotice(`Saved preview labels for ${fmt.format(result.rows)} rows.`, "success");
    await loadRun();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(button, false, "Preview classify");
  }
}

async function llmClassify() {
  const button = $("#llmClassifyBtn");
  setBusy(button, true, "LLM classify");
  try {
    const result = await request("/api/classify/llm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        provider: $("#providerSelect").value,
        model: $("#modelInput").value,
        api_key: $("#apiKeyInput").value,
        limit: Number($("#classifyLimit").value || 25),
      }),
    });
    setNotice(`Saved ${fmt.format(result.rows)} LLM labels.`, "success");
    await loadRun();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(button, false, "LLM classify");
  }
}

async function briefing(useLlm) {
  const button = useLlm ? $("#llmBriefBtn") : $("#templateBriefBtn");
  setBusy(button, true, useLlm ? "LLM briefing" : "Template briefing");
  try {
    const result = await request("/api/briefing", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        provider: $("#providerSelect").value,
        model: $("#modelInput").value,
        api_key: $("#apiKeyInput").value,
        use_llm: useLlm,
      }),
    });
    state.report = result.report;
    setNotice("Briefing saved.", "success");
    await loadRun();
    setView("briefing");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(button, false, useLlm ? "LLM briefing" : "Template briefing");
  }
}

function bindGlobalEvents() {
  $("#refreshBtn").addEventListener("click", loadRun);
  $$(".top-actions [data-save-kind]").forEach((target) => target.addEventListener("click", handleSaveKindClick));
  $("#tagInput").addEventListener("change", loadRun);
  $("#providerSelect").addEventListener("change", () => {
    $("#modelInput").value = $("#providerSelect").value === "openai" ? "gpt-4o-mini" : "gpt-oss-120b";
  });
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => setView(tab.dataset.view)));
}

bindGlobalEvents();
loadRun();
