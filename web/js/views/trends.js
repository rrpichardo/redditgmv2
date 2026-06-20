// trends.js — trends view, cluster rendering, clustering + briefing job polling.
import { state, $, esc, fmt } from "../state.js";
import { apiUrl, request } from "../api.js";
import { setNotice, setBusy } from "../components.js";
import { render } from "../nav.js";

// ---------------------------------------------------------------------------
// Trend rendering helpers
// ---------------------------------------------------------------------------

function trendConfidenceBadge(level) {
  const colors = { high: "var(--green, #16a34a)", medium: "var(--amber, #d97706)", low: "var(--red, #c2413b)" };
  const color = colors[level] || "var(--muted)";
  return `<span class="confidence-badge" style="background:${color};color:#fff;padding:2px 7px;border-radius:4px;font-size:0.75rem;font-weight:600">${esc(level?.toUpperCase() || "—")}</span>`;
}

function trendDirectionIcon(direction) {
  if (direction === "rising") return "↑";
  if (direction === "falling") return "↓";
  if (direction === "stable") return "→";
  return "—";
}

function renderTrendCluster(cluster) {
  const label = cluster.label || {};
  const sig = cluster.trend_signal || {};
  const vel = sig.velocity || {};
  const zsc = sig.zscore || {};
  const conf = sig.confidence_banner || "";
  const confNote = sig.confidence_note || "";

  const hasSignal = conf && conf !== "";
  const velDir = vel.direction || "";
  const zscDir = zsc.direction || "";

  return `<article class="panel" style="margin-bottom:0.75rem">
    <div class="panel-head" style="display:flex;align-items:center;gap:0.5rem">
      <h4 style="margin:0;flex:1">${esc(label.short_label || `Cluster ${cluster.cluster_id}`)}</h4>
      ${hasSignal ? trendConfidenceBadge(conf) : ""}
      <span style="color:var(--muted);font-size:0.75rem">${esc(label.theme_type || "")}</span>
    </div>
    ${hasSignal && confNote ? `<div class="notice" style="margin:0.5rem 0;font-size:0.85rem">${esc(confNote)}</div>` : ""}
    <div style="display:flex;gap:1.5rem;flex-wrap:wrap;font-size:0.875rem;margin-top:0.25rem">
      ${hasSignal ? `
        <span><strong>Velocity:</strong> ${trendDirectionIcon(velDir)} ${esc(velDir || "—")}
          <small style="color:var(--muted)"> (${vel.recent_count ?? "?"}↑ recent / ${vel.baseline_count ?? "?"}↑ baseline)</small>
        </span>
        <span><strong>Z-score:</strong> ${trendDirectionIcon(zscDir)} ${esc(zscDir || "—")}
          <small style="color:var(--muted)"> (${zsc.n_periods ?? "?"} periods)</small>
        </span>
        <span><strong>Agreement:</strong> ${esc(sig.agreement || "—")}</span>
      ` : `<span style="color:var(--muted)">Run trend analysis to see signals.</span>`}
      <span><strong>Size:</strong> ${fmt.format(cluster.cluster_size)}</span>
      ${cluster.top_vehicles?.length ? `<span><strong>Vehicles:</strong> ${cluster.top_vehicles.slice(0, 3).map(esc).join(", ")}</span>` : ""}
    </div>
    ${label.detailed_label ? `<p style="margin:0.5rem 0 0;font-size:0.85rem;color:var(--muted)">${esc(label.detailed_label)}</p>` : ""}
  </article>`;
}

function trendJobBar(status) {
  if (!status || status.state === "idle") return "";
  const state_label = status.state || "idle";
  const is_running = state_label === "running";
  const color = { completed: "success", failed: "error", interrupted: "error" }[state_label] || "";
  const warning = status.trend_warning ? `<small style="color:var(--amber)"> ⚠ Trend signals: ${esc(status.trend_warning)}</small>` : "";
  return `<div class="notice ${color}" style="margin-bottom:0.75rem">
    Clustering job: <strong>${esc(state_label)}</strong>
    ${is_running ? ` — ${fmt.format(status.processed || 0)} / ${fmt.format(status.total || 0)} clusters` : ""}
    ${warning}
  </div>`;
}

function trendBriefingBar(status) {
  if (!status || status.state === "idle") return "";
  const state_label = status.state || "idle";
  const color = { completed: "success", completed_with_warnings: "", failed: "error", interrupted: "error" }[state_label] || "";
  const markdownReady = status.formats?.markdown === "ready";
  const pdfReady = status.formats?.pdf === "ready" || (state_label === "completed" && !status.formats);
  const tag = state.tag;
  return `<div class="notice ${color}" style="margin-bottom:0.5rem">
    Trend report: <strong>${esc(state_label)}</strong>
    ${markdownReady ? `<a href="/api/download/trend-md?tag=${encodeURIComponent(tag)}" style="margin-left:0.75rem" class="export-chip">Download Markdown</a>` : ""}
    ${pdfReady ? `<a href="/api/download/trend-pdf?tag=${encodeURIComponent(tag)}" style="margin-left:0.75rem" class="export-chip">Download PDF</a>` : ""}
    ${status.warning ? ` — ${esc(status.warning)}` : status.error ? ` — ${esc(status.error)}` : ""}
  </div>`;
}

export function trendsView() {
  const tag = state.tag;
  const td = state.trendsData;
  const hasClassified = !!(state.data && state.data.summary && state.data.summary.metrics?.analyzed_rows > 0);
  const hasClusters = !!(td && td.ok && td.clusters?.length);
  const trendSummary = td?.trend_summary;
  const hasTimestamps = trendSummary?.has_timestamps;
  const dataSpanDays = trendSummary?.data_span_days || 0;
  const tjs = state.trendJobStatus;
  const bjs = state.trendBriefingJobStatus;

  const jobRunning = tjs?.state === "running";

  return `<div style="padding:1rem">
    ${trendJobBar(tjs)}
    ${trendBriefingBar(bjs)}

    <!-- Data confidence gate -->
    <section class="panel" style="margin-bottom:1rem">
      <div class="panel-head"><h3>⚠ Data Confidence Notice</h3></div>
      <p style="font-size:0.875rem">
        Trend signals require sufficient data across time.
        ${!hasTimestamps && hasClusters
          ? "<strong>No timestamps detected — velocity and z-score signals are unavailable. Cluster labels show thematic content only.</strong>"
          : hasTimestamps
            ? `Dataset spans <strong>${dataSpanDays} days</strong>. Signals marked <em>directional only</em> lack enough data for statistical reliability.`
            : "Run clustering first to see trend signals."
        }
      </p>
      <p style="font-size:0.875rem;color:var(--muted)">
        Trend claims are estimates based on post timestamps and cluster assignment.
        Small clusters (&lt;5 posts) or short spans (&lt;14 days) produce low-confidence signals.
        Always check the confidence badge before acting on a trend.
      </p>
    </section>

    <!-- Clustering controls -->
    <section class="panel" style="margin-bottom:1rem">
      <div class="panel-head"><h3>Clustering & Trend Analysis</h3></div>
      <div style="display:flex;gap:0.5rem;flex-wrap:wrap;align-items:center">
        <label for="nClustersInput">Clusters (k):</label>
        <input id="nClustersInput" type="number" min="2" max="30" value="10" style="width:60px" />
        <button id="startTrendsBtn" class="button primary" ${!hasClassified || jobRunning ? "disabled" : ""}>
          ${hasClusters ? "Re-run clustering" : "Run clustering"}
        </button>
        <button id="refreshTrendsBtn" class="button">Refresh status</button>
        ${hasClusters ? `<button id="trendBriefingBtn" class="button" ${bjs?.state === "running" ? "disabled" : ""}>Generate trend report</button>` : ""}
        ${bjs?.state === "running" ? `<button id="refreshTrendBriefingBtn" class="button">Refresh briefing</button>` : ""}
      </div>
      ${!hasClassified ? `<div class="notice" style="margin-top:0.5rem">Classify data first before running trend analysis.</div>` : ""}
    </section>

    <!-- Results -->
    ${hasClusters ? `
      <section>
        <div style="display:flex;align-items:baseline;gap:0.75rem;margin-bottom:0.5rem">
          <h3 style="margin:0">Cluster Trends</h3>
          <small style="color:var(--muted)">${td.clusters.length} clusters · ${esc(td.metadata?.model || "")}</small>
        </div>
        ${hasTimestamps
          ? `<div class="notice" style="margin-bottom:0.75rem;font-size:0.85rem">
              Clusters sorted by rising activity first. ${dataSpanDays >= 14 ? "" : "Short data span — treat directional signals as exploratory."}
            </div>`
          : `<div class="notice" style="margin-bottom:0.75rem;font-size:0.85rem">No timestamps in dataset — trend directions are unavailable.</div>`
        }
        ${[...td.clusters]
          .sort((a, b) => {
            const dirOrder = { rising: 0, falling: 1, stable: 2, insufficient_data: 3 };
            const confOrder = { high: 0, medium: 1, low: 2 };
            const aDir = a.trend_signal?.velocity?.direction || "insufficient_data";
            const bDir = b.trend_signal?.velocity?.direction || "insufficient_data";
            const aC = a.trend_signal?.confidence_banner || "low";
            const bC = b.trend_signal?.confidence_banner || "low";
            return (dirOrder[aDir] ?? 3) - (dirOrder[bDir] ?? 3)
              || (confOrder[aC] ?? 2) - (confOrder[bC] ?? 2);
          })
          .map(renderTrendCluster).join("")}
      </section>
    ` : !tjs || tjs.state === "idle"
      ? `<div class="notice">No clustering results yet. Click <em>Run clustering</em> to start.</div>`
      : ""
    }
  </div>`;
}

export async function loadTrends() {
  try {
    const td = await request(apiUrl("/api/trends", { tag: state.tag }));
    state.trendsData = td;
  } catch {
    // Non-fatal — trends section shows "no data" state
    state.trendsData = null;
  }
}

// ---------------------------------------------------------------------------
// Trend job polling
// ---------------------------------------------------------------------------

function clearTrendPolling() {
  if (state.trendPollTimer) {
    window.clearInterval(state.trendPollTimer);
    state.trendPollTimer = null;
  }
}

function startTrendPolling(jobId = "") {
  clearTrendPolling();
  state.trendPollTimer = window.setInterval(() => pollTrendStatus(jobId), 2000);
}

async function pollTrendStatus(jobId = "") {
  try {
    const params = jobId ? { tag: state.tag, job_id: jobId } : { tag: state.tag };
    const status = await request(apiUrl("/api/trends/status", params));
    state.trendJobStatus = status;
    if (state.view === "trends") render();
    const done = ["completed", "completed_with_warnings", "failed", "interrupted"].includes(status.state);
    if (done) {
      clearTrendPolling();
      if (status.state === "completed") {
        await loadTrends();
        setNotice("Clustering completed. Trend signals ready.", "success");
      } else {
        setNotice(`Clustering ${status.state}.`, "error");
      }
      if (state.view === "trends") render();
    }
  } catch {
    clearTrendPolling();
  }
}

export async function startTrendsJob() {
  const btn = $("#startTrendsBtn");
  setBusy(btn, true, hasClustersNow() ? "Re-run clustering" : "Run clustering");
  try {
    const status = await request("/api/trends/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        provider: $("#providerSelect").value,
        model: $("#modelInput").value,
        api_key: $("#apiKeyInput").value,
        n_clusters: Number($("#nClustersInput")?.value || 10),
      }),
    });
    state.trendJobStatus = status;
    if (state.view === "trends") render();
    if (status.state === "running") {
      setNotice("Clustering job started.");
      startTrendPolling(status.job_id);
    }
  } catch (error) {
    setNotice(error.message, "error");
    setBusy(btn, false, "Run clustering");
  }
}

export async function refreshTrendsStatus() {
  try {
    const status = await request(apiUrl("/api/trends/status", { tag: state.tag }));
    state.trendJobStatus = status;
    if (status.state === "completed") await loadTrends();
    if (state.view === "trends") render();
    if (status.state === "running") startTrendPolling(status.job_id);
  } catch (error) {
    setNotice(error.message, "error");
  }
}

function hasClustersNow() {
  return !!(state.trendsData?.ok && state.trendsData?.clusters?.length);
}

// ---------------------------------------------------------------------------
// Trend briefing polling
// ---------------------------------------------------------------------------

function clearTrendBriefingPolling() {
  if (state.trendBriefingPollTimer) {
    window.clearInterval(state.trendBriefingPollTimer);
    state.trendBriefingPollTimer = null;
  }
}

function startTrendBriefingPolling(jobId = "") {
  clearTrendBriefingPolling();
  state.trendBriefingPollTimer = window.setInterval(() => pollTrendBriefingStatus(jobId), 2000);
}

async function pollTrendBriefingStatus(jobId = "") {
  try {
    const params = jobId ? { tag: state.tag, job_id: jobId } : { tag: state.tag };
    const status = await request(apiUrl("/api/trends/briefing/status", params));
    state.trendBriefingJobStatus = status;
    if (state.view === "trends") render();
    const done = ["completed", "failed", "interrupted"].includes(status.state);
    if (done) {
      clearTrendBriefingPolling();
      if (["completed", "completed_with_warnings"].includes(status.state)) {
        setNotice(status.warning || "Trend report ready for download.", status.state === "completed" ? "success" : "");
      } else {
        setNotice(`Trend briefing ${status.state}.`, "error");
      }
    }
  } catch {
    clearTrendBriefingPolling();
  }
}

export async function startTrendBriefingJob() {
  const btn = $("#trendBriefingBtn");
  setBusy(btn, true, "Generate trend report");
  try {
    const status = await request("/api/trends/briefing", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        provider: $("#providerSelect").value,
        model: $("#modelInput").value,
        api_key: $("#apiKeyInput").value,
      }),
    });
    state.trendBriefingJobStatus = status;
    if (state.view === "trends") render();
    if (status.state === "running") {
      setNotice("Trend report job started.");
      startTrendBriefingPolling(status.job_id);
    }
  } catch (error) {
    setNotice(error.message, "error");
    setBusy(btn, false, "Generate trend report");
  }
}

export async function refreshTrendBriefingStatus() {
  try {
    const status = await request(apiUrl("/api/trends/briefing/status", { tag: state.tag }));
    state.trendBriefingJobStatus = status;
    if (state.view === "trends") render();
    if (status.state === "running") startTrendBriefingPolling(status.job_id);
  } catch (error) {
    setNotice(error.message, "error");
  }
}
