// classify.js — classify view, preview/LLM actions, and durable-job polling.
import { state, $, fmt } from "../state.js";
import { apiUrl, request } from "../api.js";
import { setNotice, setBusy, metricGrid, jobStatusCard } from "../components.js";
import { evidenceFeed } from "./explore.js";
import { loadRun } from "../app.js";
import { render } from "../nav.js";

export function classifyView() {
  const metrics = state.data?.summary.metrics || {};
  const jobStatus = state.classifyJobStatus;
  const isRunning = jobStatus?.state === "running";

  return `
    <section class="panel classify-panel">
      <div class="panel-head">
        <h2>Quick classify</h2>
        <small>${fmt.format(metrics.total_rows || 0)} source rows</small>
      </div>
      ${metricGrid(metrics)}
      <div class="field-row">
        <label class="control" style="max-width:180px">
          <span>Rows</span>
          <input id="classifyLimit" type="number" min="1" value="50">
        </label>
        <button id="previewClassifyBtn" class="button primary">Preview classify</button>
        <button id="llmClassifyBtn" class="button secondary">LLM classify (inline)</button>
      </div>
    </section>

    <section class="panel spaced">
      <div class="panel-head">
        <h2>Full-run LLM job</h2>
        <small>durable background subprocess</small>
      </div>
      <p class="panel-desc">Classifies all pending rows via the configured LLM. Runs as a background job — safe to close the browser and return later.</p>
      <div class="field-row">
        <label class="control" style="max-width:130px">
          <span>Row limit (0 = all)</span>
          <input id="jobLimitInput" type="number" min="0" value="0">
        </label>
        <button id="startClassifyJobBtn" class="button primary" ${isRunning ? "disabled" : ""}>
          ${isRunning ? "Job running…" : "Start classify job"}
        </button>
        <button id="refreshClassifyJobBtn" class="button secondary">Refresh status</button>
      </div>
      ${jobStatusCard(jobStatus)}
    </section>

    <section class="panel spaced">
      <div class="panel-head"><h2>Evidence sample</h2><small>classification context</small></div>
      ${evidenceFeed((state.data?.evidence || []).slice(0, 10))}
    </section>`;
}

// ---------------------------------------------------------------------------
// Classify job polling
// ---------------------------------------------------------------------------

function clearClassifyPolling() {
  if (state.classifyPollTimer) {
    window.clearInterval(state.classifyPollTimer);
    state.classifyPollTimer = null;
  }
}

function startClassifyPolling(jobId = "") {
  clearClassifyPolling();
  state.classifyPollTimer = window.setInterval(() => pollClassifyJobStatus(jobId), 2000);
}

async function pollClassifyJobStatus(jobId = "") {
  try {
    const params = jobId ? { tag: state.tag, job_id: jobId } : { tag: state.tag };
    const status = await request(apiUrl("/api/classify/status", params));
    state.classifyJobStatus = status;
    if (state.view === "classify") render();
    const done = ["completed", "failed", "interrupted"].includes(status.state);
    if (done) {
      clearClassifyPolling();
      if (status.state === "completed") {
        setNotice("Classify job completed.", "success");
        await loadRun();
      } else {
        setNotice(`Classify job ${status.state}.`, "error");
      }
    }
  } catch {
    clearClassifyPolling();
  }
}

export async function startClassifyJob() {
  const btn = $("#startClassifyJobBtn");
  setBusy(btn, true, "Start classify job");
  try {
    const limit = Number($("#jobLimitInput")?.value || 0);
    const status = await request("/api/classify/job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        provider: $("#providerSelect").value,
        model: $("#modelInput").value,
        api_key: $("#apiKeyInput").value,
        limit,
      }),
    });
    state.classifyJobStatus = status;
    render();
    if (status.state === "running") {
      setNotice("Classify job started.");
      startClassifyPolling(status.job_id);
    }
  } catch (error) {
    setNotice(error.message, "error");
    setBusy(btn, false, "Start classify job");
  }
}

export async function refreshClassifyJobStatus() {
  try {
    const status = await request(apiUrl("/api/classify/status", { tag: state.tag }));
    state.classifyJobStatus = status;
    render();
    if (status.state === "running") startClassifyPolling(status.job_id);
  } catch (error) {
    setNotice(error.message, "error");
  }
}

// ---------------------------------------------------------------------------
// Inline classify actions
// ---------------------------------------------------------------------------

export async function previewClassify() {
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

export async function llmClassify() {
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
