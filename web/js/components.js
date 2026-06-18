// components.js — shared render helpers + small DOM-status mutators.
import { $, esc, fmt } from "./state.js";

// Show or clear the status bar notice.
export function setNotice(message = "", type = "") {
  const bar = $("#statusBar");
  if (!message) {
    bar.innerHTML = "";
    return;
  }
  bar.innerHTML = `<div class="notice ${type}" role="status">${esc(message)}</div>`;
}

// Toggle a button's disabled state and optional working/idle label.
export function setBusy(button, busy, label) {
  if (!button) return;
  button.disabled = busy;
  if (label) button.textContent = busy ? "Working..." : label;
}

// Standard panel wrapper: title head + body + optional note + extra class.
export function panel(title, body, note = "", className = "") {
  return `<section class="panel ${esc(className)}">
    <div class="panel-head">
      <h3>${esc(title)}</h3>
      ${note ? `<small>${esc(note)}</small>` : ""}
    </div>
    ${body}
  </section>`;
}

// Five-card top metric grid for the run summary.
export function metricGrid(metrics) {
  const cards = [
    ["Analyzed rows", fmt.format(metrics.analyzed_rows), `${fmt.format(metrics.skipped_rows)} skipped`, "volume"],
    ["Complaint rate", `${Number(metrics.complaint_rate || 0).toFixed(1)}%`, `${fmt.format(metrics.complaints)} complaint rows`, "alert"],
    ["Negative rate", `${Number(metrics.negative_rate || 0).toFixed(1)}%`, "share of analyzed rows", "negative"],
    ["Competitor signal", `${Number(metrics.competitor_rate || 0).toFixed(1)}%`, "mentions outside GM", "rival"],
    ["EV topic mix", `${Number(metrics.ev_rate || 0).toFixed(1)}%`, "EV-related rows", "ev"],
  ];
  return `<div class="metric-grid">${cards.map(([label, value, note, tone]) =>
    `<article class="metric-card tone-${tone}">
      <span>${label}</span>
      <strong>${value}</strong>
      <small>${note}</small>
    </article>`
  ).join("")}</div>`;
}

// Render the "no run data" empty-state template and wire its jump button.
export function emptyState() {
  const template = $("#emptyTemplate").content.cloneNode(true);
  const root = document.createElement("div");
  root.appendChild(template);
  return root.innerHTML;
}

// Durable-job status card with progress bar and artifact chips.
export function jobStatusCard(status) {
  if (!status) return `<div class="notice">No active job.</div>`;
  const st = status.state || "unknown";
  const total = Number(status.total || 0);
  const processed = Number(status.processed || 0);
  const pctDone = total > 0 ? Math.round((processed / total) * 100) : 0;
  const stateClass = st === "completed" ? "success" : st === "failed" ? "error" : "running";
  const progressBar = total > 0
    ? `<div class="job-progress-track"><div class="job-progress-fill" style="width:${pctDone}%"></div></div>`
    : "";
  const errSpan = status.errors ? `<span class="error-chip">${status.errors} errors</span>` : "";
  const artifacts = (status.artifact_paths || []).map((p) => {
    const name = p.split("/").pop();
    return `<span class="artifact-chip" title="${esc(p)}">${esc(name)}</span>`;
  }).join("");

  return `<div class="job-status-card ${stateClass}">
    <div class="job-status-header">
      <span class="job-state-badge">${esc(st)}</span>
      <span class="job-kind-badge">${esc(status.kind || "")}</span>
      ${errSpan}
    </div>
    ${progressBar}
    ${total > 0 ? `<div class="job-meta">${processed} / ${total} rows${pctDone > 0 ? ` (${pctDone}%)` : ""}</div>` : ""}
    ${artifacts ? `<div class="job-artifacts">${artifacts}</div>` : ""}
    ${status.error ? `<div class="notice error" style="margin-top:0.5rem">${esc(status.error)}</div>` : ""}
  </div>`;
}

// chartPanel emits an empty ECharts mount point; charts.js fills it after render.
export function chartPanel(id, title, note = "") {
  const body = `<div class="echart" data-chart="${esc(id)}" role="img" aria-label="${esc(title)} chart" style="height:320px"></div>`;
  return panel(title, body, note, "chart-panel");
}
