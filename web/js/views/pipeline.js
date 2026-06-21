// pipeline.js — Pipeline run audit: step cards, polling, log toggle, cancel, retry.
import { state, esc, $ } from "../state.js";
import { apiUrl, request } from "../api.js";
import { setNotice, setBusy } from "../components.js";
import { loadRun } from "../app.js";

// Ordered list of pipeline steps (skipped steps are still shown in order).
const STEP_ORDER = ["prepare", "classify", "briefing", "synthesis_pdf", "trends", "trend_pdf", "qa_index"];

// Human-readable step labels.
const STEP_LABELS = {
  prepare:    "Prepare working set",
  classify:   "Classify posts",
  briefing:   "Generate briefing",
  synthesis_pdf: "Render synthesis PDF",
  trends:     "Cluster & trend signals",
  trend_pdf:  "Trend report PDF",
  qa_index:   "Build Q&A index",
};

// Terminal states — stop polling once reached.
const TERMINAL = new Set(["completed", "completed_with_warnings", "blocked", "failed", "cancelled"]);
// Terminal states that produced fresh artifacts worth showing in the other tabs.
const SUCCESS_TERMINAL = new Set(["completed", "completed_with_warnings"]);
let statusErrorVisible = false;
let retryInFlight = false;
// Run ids we've observed mid-flight (running/pending) during this session, and
// run ids whose results we've already pushed to the rest of the app. Together
// they ensure we refresh the Dashboard/Explorer/Trends/Q&A exactly once when a
// watched run finishes — without re-firing when loadRun() re-renders this view
// or when the user merely browses an already-finished run.
const activeRunsSeen = new Set();
const refreshedRuns = new Set();
const openLogKeys = new Set();
const logSessions = new Map();

function logKey(runId, stepName) {
  return `${runId}:${stepName}`;
}

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
  const expanded = openLogKeys.has(logKey(runId, name));
  const logBtn = step.log_available
    ? `<button class="button small log-toggle-btn" data-step="${esc(name)}" data-run="${esc(runId)}"
         aria-label="Toggle log for ${esc(label)}" aria-controls="log-${esc(name)}"
         aria-expanded="${expanded ? "true" : "false"}">Log ${expanded ? "▴" : "▾"}</button>`
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
      <div class="log-panel${expanded ? " is-open" : ""}" id="log-${esc(name)}"><pre tabindex="0"></pre></div>
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
    await renderStatus(status);
    managePoll(status);
  } catch (err) {
    statusErrorVisible = true;
    setNotice(`Failed to load run status: ${err.message}`, "error");
  }
}

// Update the steps list and cancel button visibility.
async function renderStatus(status) {
  const stepsEl = $("#pipelineSteps");
  if (!stepsEl) return; // view unmounted

  const scrollState = captureOpenLogScroll(status.run_id);
  stepsEl.innerHTML = stepsHTML(status.steps, status.run_id);

  // Show cancel button only while the run is active.
  const cancelBtn = $("#pipelineCancelBtn");
  if (cancelBtn) {
    cancelBtn.style.display = status.state === "running" ? "" : "none";
  }

  // Re-bind per-card buttons after innerHTML replacement.
  bindCardButtons(status.run_id);
  await refreshOpenLogs(status.run_id, scrollState);
}

function captureOpenLogScroll(runId) {
  const captured = new Map();
  for (const key of openLogKeys) {
    if (!key.startsWith(`${runId}:`)) continue;
    const stepName = key.slice(runId.length + 1);
    const pre = document.querySelector(`#log-${CSS.escape(stepName)} pre`);
    if (!pre) continue;
    captured.set(key, {
      scrollTop: pre.scrollTop,
      atBottom: pre.scrollHeight - pre.clientHeight - pre.scrollTop < 28,
    });
  }
  return captured;
}

async function refreshOpenLogs(runId, scrollState = new Map()) {
  const keys = [...openLogKeys].filter((key) => key.startsWith(`${runId}:`));
  await Promise.all(keys.map((key) => refreshLog(key, scrollState.get(key))));
}

async function refreshLog(key, savedScroll = null) {
  if (!openLogKeys.has(key)) return;
  const separator = key.indexOf(":");
  const runId = key.slice(0, separator);
  const stepName = key.slice(separator + 1);
  const session = logSessions.get(key) || { attemptNo: 0, offset: 0, text: "" };

  try {
    let eof = false;
    while (!eof && openLogKeys.has(key)) {
      const previousOffset = session.offset;
      const params = {
        tag: state.tag,
        run_id: runId,
        step: stepName,
        offset: session.offset,
        max_bytes: 262144,
      };
      if (session.attemptNo) params.attempt_no = session.attemptNo;
      const chunk = await request(apiUrl("/api/pipeline/log/chunk", params));
      const didReset = chunk.reset || (session.attemptNo && session.attemptNo !== chunk.attempt_no);
      if (didReset) {
        session.text = "";
        session.offset = 0;
      }
      session.attemptNo = chunk.attempt_no;
      session.offset = chunk.next_offset;
      session.text += chunk.text || "";
      eof = Boolean(chunk.eof);
      if (!eof && session.offset <= (didReset ? 0 : previousOffset)) break;
    }
    logSessions.set(key, session);
  } catch (error) {
    if (!session.text) session.text = `Error: ${error.message}`;
  }

  const panel = document.getElementById(`log-${stepName}`);
  const pre = panel?.querySelector("pre");
  const button = document.querySelector(`.log-toggle-btn[data-step="${CSS.escape(stepName)}"]`);
  if (!panel || !pre || !openLogKeys.has(key)) return;
  const wasAtBottom = savedScroll?.atBottom ?? (pre.scrollHeight - pre.clientHeight - pre.scrollTop < 28);
  const priorTop = savedScroll?.scrollTop ?? pre.scrollTop;
  panel.classList.add("is-open");
  pre.textContent = session.text;
  if (wasAtBottom) pre.scrollTop = pre.scrollHeight;
  else pre.scrollTop = priorTop;
  if (button) {
    button.textContent = "Log ▴";
    button.setAttribute("aria-expanded", "true");
  }
}

// ------------------------------------------------------------------
// Polling
// ------------------------------------------------------------------

// Start or stop the 2s polling loop based on run state.
function managePoll(status) {
  const isTerminal = TERMINAL.has(status.state);

  // Remember runs we've watched while still in flight so we can tell a real
  // active→done transition apart from simply opening an old finished run.
  if (!isTerminal) activeRunsSeen.add(status.run_id);

  if (isTerminal) {
    stopPipelinePolling();
    maybeRefreshOnComplete(status);
    return;
  }

  // Clear any existing poll before starting a fresh one (e.g. after switching runs).
  stopPipelinePolling();

  state.pipelinePollTimer = setInterval(async () => {
    // Guard: stop polling if the Settings pipeline panel has been unmounted.
    if (!document.getElementById("pipelineRunSelect")) {
      stopPipelinePolling();
      return;
    }
    await loadStatus();
  }, 2000);
}

export function stopPipelinePolling() {
  if (state.pipelinePollTimer) {
    clearInterval(state.pipelinePollTimer);
    state.pipelinePollTimer = null;
  }
}

// When a watched run finishes successfully, reload the run snapshot so the
// Dashboard, Explorer, Trends, and Q&A tabs pick up the freshly published
// artifacts. Without this, those tabs keep showing the pre-analysis (empty)
// state until a full page reload. loadRun() re-renders the current view, which
// re-enters managePoll → here; the refreshedRuns guard makes that a no-op.
async function maybeRefreshOnComplete(status) {
  if (!SUCCESS_TERMINAL.has(status.state)) return;   // failed/blocked/cancelled → nothing new to show
  if (!activeRunsSeen.has(status.run_id)) return;     // only react to a run we watched go active→done
  if (refreshedRuns.has(status.run_id)) return;       // already pushed this run's results once
  refreshedRuns.add(status.run_id);
  await loadRun({ silent: true });                    // silent: keep our completion notice below
  setNotice("Analysis complete — results are ready in the Dashboard, Explorer, Trends, and Q&A tabs.", "success");
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

// Toggle a step log. Open logs are refreshed during each status poll.
async function toggleLog(stepName, runId, btn) {
  const panel = document.getElementById(`log-${stepName}`);
  if (!panel) return;
  const key = logKey(runId, stepName);

  if (openLogKeys.has(key)) {
    openLogKeys.delete(key);
    panel.classList.remove("is-open");
    btn.textContent = "Log ▾";
    btn.setAttribute("aria-expanded", "false");
    return;
  }

  openLogKeys.add(key);
  panel.classList.add("is-open");
  btn.setAttribute("aria-expanded", "true");
  btn.textContent = "Loading…";
  await refreshLog(key);
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
        stopPipelinePolling();
        await loadPipelineRuns();
        return;
      }
      throw new Error(body.detail || body.message || `Request failed with ${response.status}`);
    }
    // Re-fetch status and restart polling if needed.
    stopPipelinePolling();
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
// Event binding — called whenever the Settings pipeline panel renders.
// ------------------------------------------------------------------
export function bindPipelineEvents() {
  // Guard: only run if the pipeline view is actually in the DOM.
  if (!document.getElementById("pipelineRunSelect")) return;

  // Clear any stale polling timer from a previous render cycle.
  stopPipelinePolling();

  // Load runs and auto-select the active one.
  loadPipelineRuns();

  // Run selector — update selection and re-fetch status.
  $("#pipelineRunSelect")?.addEventListener("change", async (e) => {
    state.pipelineRunId = e.target.value;
    stopPipelinePolling();
    await loadStatus();
  });

  // Refresh button — manual re-fetch.
  $("#pipelineRefreshBtn")?.addEventListener("click", async () => {
    stopPipelinePolling();
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
      stopPipelinePolling();
      await loadStatus();
    } catch (err) {
      setNotice(`Cancel failed: ${err.message}`, "error");
    } finally {
      setBusy(btn, false, "Cancel");
    }
  });
}
