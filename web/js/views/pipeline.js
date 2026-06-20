// pipeline.js — Pipeline run audit: step cards, polling, log toggle, cancel, retry.
import { state, esc, $ } from "../state.js";
import { apiUrl, request } from "../api.js";
import { setNotice, setBusy } from "../components.js";

// Ordered list of pipeline steps (skipped steps are still shown in order).
const STEP_ORDER = ["prepare", "classify", "briefing", "trends", "trend_pdf", "qa_index"];

// Human-readable step labels.
const STEP_LABELS = {
  prepare:    "Prepare working set",
  classify:   "Classify posts",
  briefing:   "Generate briefing",
  trends:     "Cluster & trend signals",
  trend_pdf:  "Trend report PDF",
  qa_index:   "Build Q&A index",
};

// Terminal states — stop polling once reached.
const TERMINAL = new Set(["completed", "completed_with_warnings", "blocked", "failed", "cancelled"]);
let statusErrorVisible = false;
let retryInFlight = false;

const STATE_META = {
  pending: ["○", "Pending"],
  running: ["◌", "Running"],
  completed: ["✓", "Completed"],
  completed_with_warnings: ["!", "Completed with warnings"],
  blocked: ["⊘", "Blocked"],
  skipped: ["–", "Skipped"],
  failed: ["×", "Failed"],
  cancelled: ["■", "Cancelled"],
};

// ------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------

// Convert Unix timestamp (seconds) to a human time string.
function humanTime(iso) {
  if (!iso) return "—";
  return new Date(iso * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

// Format bytes into KB or MB.
function humanBytes(bytes) {
  const n = Number(bytes || 0);
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(n / 1024))} KB`;
}

// Format elapsed duration between two Unix timestamps.
function elapsed(start, end) {
  if (!start) return "";
  const ms = ((end || Date.now() / 1000) - start) * 1000;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.floor(ms / 60000)}m ${Math.round((ms % 60000) / 1000)}s`;
}

// ------------------------------------------------------------------
// Render helpers
// ------------------------------------------------------------------

// Render a single step card as an HTML string.
function stepCardHTML(step, runId) {
  const name = step.name;
  const label = STEP_LABELS[name] || name;
  // Map state name to CSS class (replace underscores with hyphens).
  const stateClass = `state-${(step.state || "pending").replaceAll("_", "-")}`;
  const [stateIcon, stateLabel] = STATE_META[step.state] || ["?", step.state || "Pending"];

  // Timing and throughput.
  const timing = step.started_at
    ? `${humanTime(step.started_at)} → ${humanTime(step.ended_at)}  (${elapsed(step.started_at, step.ended_at)})`
    : "";
  const progress = step.total > 0
    ? `${step.processed ?? 0}/${step.total}` + (step.error_rate > 0 ? ` · ${(step.error_rate * 100).toFixed(1)}% err` : "")
    : "";

  // Artifact chips.
  const artifactChips = (step.artifacts || []).map((a) =>
    `<a href="${esc(a.url)}" download="${esc(a.name)}" class="artifact-chip" title="${esc(a.name)}">${esc(a.name)} (${humanBytes(a.bytes)})</a>`
  ).join("");

  // Log toggle button — only if the backend says log exists.
  const logBtn = step.log_available
    ? `<button class="button small log-toggle-btn" data-step="${esc(name)}" data-run="${esc(runId)}" aria-label="Toggle log for ${esc(label)}">Log ▾</button>`
    : "";

  // Retry button — show for terminal failure/cancel/blocked states.
  const canRetry = ["failed", "cancelled", "blocked"].includes(step.state);
  const retryBtn = canRetry
    ? `<button class="button small retry-btn" data-step="${esc(name)}" aria-label="Retry ${esc(label)}">Retry</button>`
    : "";

  return `
    <div class="step-card" data-step-card="${esc(name)}">
      <div class="step-card-head">
        <span class="step-state-badge ${stateClass}"><span class="step-state-icon" aria-hidden="true">${esc(stateIcon)}</span><span>${esc(stateLabel)}</span></span>
        <span class="step-name">${esc(label)}</span>
        ${timing ? `<span class="step-meta">${esc(timing)}</span>` : ""}
        ${progress ? `<span class="step-meta">${esc(progress)}</span>` : ""}
      </div>
      ${step.warning ? `<div class="step-warning">${esc(step.warning)}</div>` : ""}
      <div class="step-actions">
        ${artifactChips}
        ${logBtn}
        ${retryBtn}
      </div>
      <div class="log-panel" id="log-${esc(name)}"><pre></pre></div>
    </div>`;
}

// Build the full step list HTML using STEP_ORDER (show placeholder for missing steps).
function stepsHTML(steps, runId) {
  // Index steps by name for O(1) lookup.
  const byName = Object.fromEntries((steps || []).map((s) => [s.name, s]));
  return STEP_ORDER.map((name) => {
    const step = byName[name] || { name, state: "pending" };
    return stepCardHTML(step, runId);
  }).join("");
}

// Format a run option label for the dropdown.
function runOptionLabel(run) {
  const short = (run.run_id || "").slice(0, 8);
  const startedHuman = humanTime(run.started_at);
  return `${short} · ${run.state} · ${startedHuman}`;
}

function syncRunSelect() {
  const select = $("#pipelineRunSelect");
  if (!select) return;
  select.innerHTML = state.pipelineRuns.length
    ? state.pipelineRuns.map((run) =>
        `<option value="${esc(run.run_id)}" ${run.run_id === state.pipelineRunId ? "selected" : ""}>${esc(runOptionLabel(run))}</option>`
      ).join("")
    : '<option value="">No runs found</option>';
  select.value = state.pipelineRunId || "";
}

function updateCachedRun(status) {
  const index = state.pipelineRuns.findIndex((run) => run.run_id === status.run_id);
  const summary = {
    run_id: status.run_id,
    state: status.state,
    started_at: status.started_at,
    ended_at: status.ended_at,
  };
  if (index >= 0) {
    state.pipelineRuns[index] = { ...state.pipelineRuns[index], ...summary };
  } else {
    state.pipelineRuns.unshift(summary);
  }
  syncRunSelect();
}

// ------------------------------------------------------------------
// View template
// ------------------------------------------------------------------
export function pipelineView() {
  // Build dropdown options from cached runs.
  const runsOptions = (state.pipelineRuns || []).map((run) =>
    `<option value="${esc(run.run_id)}" ${run.run_id === state.pipelineRunId ? "selected" : ""}>${esc(runOptionLabel(run))}</option>`
  ).join("");

  return `
    <div class="pipeline-layout">
      <section class="panel">
        <div class="panel-head"><h2>Pipeline</h2></div>

        <div class="pipeline-toolbar">
          <select id="pipelineRunSelect" name="pipeline_run" autocomplete="off" aria-label="Select pipeline run">
            ${runsOptions || '<option value="">Loading runs…</option>'}
          </select>
          <button id="pipelineRefreshBtn" class="button" aria-label="Refresh pipeline status">Refresh</button>
          <button id="pipelineCancelBtn" class="button danger" aria-label="Cancel running pipeline" style="display:none">Cancel</button>
        </div>

        <div id="pipelineSteps" aria-live="polite">
          <p class="helper-text">Select a run above to see step details.</p>
        </div>
      </section>
    </div>`;
}

// ------------------------------------------------------------------
// Data fetching
// ------------------------------------------------------------------

// Fetch recent runs and populate the dropdown + state.pipelineRuns.
async function loadPipelineRuns() {
  try {
    const runs = await request(apiUrl("/api/pipeline/runs", { tag: state.tag, limit: 20 }));
    state.pipelineRuns = runs || [];

    const select = $("#pipelineRunSelect");
    if (!select) return; // view may have unmounted

    // Auto-select the first run if nothing is selected yet.
    if (!state.pipelineRunId && state.pipelineRuns.length) {
      state.pipelineRunId = state.pipelineRuns[0].run_id;
    }
    syncRunSelect();

    if (state.pipelineRunId) await loadStatus();
  } catch (err) {
    setNotice(`Failed to load pipeline runs: ${err.message}`, "error");
  }
}

// Fetch status for the current run and re-render steps.
async function loadStatus() {
  if (!state.pipelineRunId) return;
  try {
    const status = await request(apiUrl("/api/pipeline/status", { tag: state.tag, run_id: state.pipelineRunId }));
    if (statusErrorVisible) {
      setNotice();
      statusErrorVisible = false;
    }
    updateCachedRun(status);
    renderStatus(status);
    managePoll(status);
  } catch (err) {
    statusErrorVisible = true;
    setNotice(`Failed to load run status: ${err.message}`, "error");
  }
}

// Update the steps list and cancel button visibility.
function renderStatus(status) {
  const stepsEl = $("#pipelineSteps");
  if (!stepsEl) return; // view unmounted

  stepsEl.innerHTML = stepsHTML(status.steps, status.run_id);

  // Show cancel button only while the run is active.
  const cancelBtn = $("#pipelineCancelBtn");
  if (cancelBtn) {
    cancelBtn.style.display = status.state === "running" ? "" : "none";
  }

  // Re-bind per-card buttons after innerHTML replacement.
  bindCardButtons(status.run_id);
}

// ------------------------------------------------------------------
// Polling
// ------------------------------------------------------------------

// Start or stop the 2s polling loop based on run state.
function managePoll(status) {
  const isTerminal = TERMINAL.has(status.state);

  if (isTerminal) {
    clearPoll();
    return;
  }

  // Clear any existing poll before starting a fresh one (e.g. after switching runs).
  clearPoll();

  state.pipelinePollTimer = setInterval(async () => {
    // Guard: stop polling if the Pipeline tab has been unmounted.
    if (!document.getElementById("pipelineRunSelect")) {
      clearPoll();
      return;
    }
    await loadStatus();
  }, 2000);
}

function clearPoll() {
  if (state.pipelinePollTimer) {
    clearInterval(state.pipelinePollTimer);
    state.pipelinePollTimer = null;
  }
}

// ------------------------------------------------------------------
// Card-level button handlers (bound after each render).
// ------------------------------------------------------------------
function bindCardButtons(runId) {
  // Log toggle buttons.
  document.querySelectorAll(".log-toggle-btn").forEach((btn) => {
    btn.addEventListener("click", () => toggleLog(btn.dataset.step, runId, btn));
  });

  // Retry buttons.
  document.querySelectorAll(".retry-btn").forEach((btn) => {
    btn.addEventListener("click", () => retryStep(btn.dataset.step, btn));
  });
}

// Toggle a step's log panel — fetch once, then collapse/expand on subsequent clicks.
async function toggleLog(stepName, runId, btn) {
  const panel = document.getElementById(`log-${stepName}`);
  if (!panel) return;

  // If already open, just collapse it.
  if (panel.classList.contains("is-open")) {
    panel.classList.remove("is-open");
    btn.textContent = "Log ▾";
    return;
  }

  // Fetch the log if the pre is still empty.
  const pre = panel.querySelector("pre");
  if (!pre.textContent) {
    btn.disabled = true;
    btn.textContent = "Loading…";
    try {
      // Log endpoint returns text/plain — use raw fetch.
      const res = await fetch(apiUrl("/api/pipeline/log", { tag: state.tag, run_id: runId, step: stepName }));
      pre.textContent = res.ok ? await res.text() : `Error ${res.status}`;
    } catch (err) {
      pre.textContent = `Error: ${err.message}`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Log ▴";
    }
  } else {
    btn.textContent = "Log ▴";
  }

  panel.classList.add("is-open");
}

// POST /api/pipeline/retry for a single step.
async function retryStep(stepName, btn) {
  if (retryInFlight) return;
  retryInFlight = true;
  const retryButtons = Array.from(document.querySelectorAll(".retry-btn"));
  retryButtons.forEach((button) => { button.disabled = true; });
  setBusy(btn, true, "Retry");
  btn.textContent = "Retrying…";
  try {
    const response = await fetch(apiUrl("/api/pipeline/retry"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        run_id: state.pipelineRunId,
        step: stepName,
        api_key: state.apiKey || undefined,
      }),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      if (response.status === 409 && body.active_run_id) {
        state.pipelineRunId = body.active_run_id;
        setNotice(`A run is already active (run ${body.active_run_id}).`, "warn");
        clearPoll();
        await loadPipelineRuns();
        return;
      }
      throw new Error(body.detail || body.message || `Request failed with ${response.status}`);
    }
    // Re-fetch status and restart polling if needed.
    clearPoll();
    await loadStatus();
  } catch (err) {
    setNotice(`Retry failed: ${err.message}`, "error");
  } finally {
    retryInFlight = false;
    retryButtons.forEach((button) => {
      if (button.isConnected) button.disabled = false;
    });
    if (btn.isConnected) setBusy(btn, false, "Retry");
  }
}

// ------------------------------------------------------------------
// Event binding — called once each time the Pipeline tab renders.
// ------------------------------------------------------------------
export function bindPipelineEvents() {
  // Guard: only run if the pipeline view is actually in the DOM.
  if (!document.getElementById("pipelineRunSelect")) return;

  // Clear any stale polling timer from a previous render cycle.
  clearPoll();

  // Load runs and auto-select the active one.
  loadPipelineRuns();

  // Run selector — update selection and re-fetch status.
  $("#pipelineRunSelect")?.addEventListener("change", async (e) => {
    state.pipelineRunId = e.target.value;
    clearPoll();
    await loadStatus();
  });

  // Refresh button — manual re-fetch.
  $("#pipelineRefreshBtn")?.addEventListener("click", async () => {
    clearPoll();
    await loadPipelineRuns();
  });

  // Cancel button — POST cancel then re-fetch.
  $("#pipelineCancelBtn")?.addEventListener("click", async () => {
    const btn = $("#pipelineCancelBtn");
    setBusy(btn, true, "Cancel");
    try {
      await request(apiUrl("/api/pipeline/cancel"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tag: state.tag, run_id: state.pipelineRunId }),
      });
      clearPoll();
      await loadStatus();
    } catch (err) {
      setNotice(`Cancel failed: ${err.message}`, "error");
    } finally {
      setBusy(btn, false, "Cancel");
    }
  });
}
