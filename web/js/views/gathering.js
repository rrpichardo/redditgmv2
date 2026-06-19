// gathering.js — data collection and one-click full-pipeline analysis.
// Combines the upload + Reddit collect actions from collect.js with a single
// "Analyze data" button that fires the full pipeline in one API call.
import { state, esc, $, fmt } from "../state.js";
import { apiUrl, request } from "../api.js";
import { setNotice, setBusy } from "../components.js";
import { loadRun } from "../app.js";
import { collect } from "./collect.js";   // reuse existing collect action

export function gatheringView() {
  return `
    <div class="gathering-layout">
      <section class="panel">
        <div class="panel-head"><h2>1 · Get data</h2></div>
        <div class="form-grid">
          <div class="control">
            <label for="uploadFile">Upload CSV</label>
            <input id="uploadFile" type="file" accept=".csv" />
            <small>Load a Reddit collector CSV from your machine.</small>
          </div>
        </div>
        <div class="actions">
          <button id="uploadBtn" class="button">Upload CSV</button>
        </div>
        <hr class="divider" />
        <div class="form-grid">
          <div class="control">
            <label for="collectSource">Reddit source</label>
            <select id="collectSource">
              <option value="gm">GM vehicle list</option>
              <option value="competitor">Competitor list</option>
              <option value="custom">Custom list</option>
            </select>
            <small>Download fresh posts from Reddit into your workspace.</small>
          </div>
          <div class="control">
            <label for="listingLimit">Posts per subreddit</label>
            <input id="listingLimit" type="number" min="1" max="500" value="100" />
          </div>
        </div>
        <div class="actions">
          <button id="collectBtn" class="button">Download from Reddit</button>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head"><h2>2 · Analyze</h2></div>
        <p class="helper-text">Classify posts, extract insights, detect trends, and build the Q&amp;A index — the full pipeline in one step.</p>
        <div class="actions">
          <button id="analyzeBtn" class="button primary large">Analyze data</button>
        </div>
        <div id="analyzeStatus" aria-live="polite"></div>
      </section>
    </div>
  `;
}

// Upload wrapper that reads from #uploadFile (gathering view) instead of #uploadInput (collect view).
async function uploadFromGathering() {
  const input = $("#uploadFile");
  const button = $("#uploadBtn");
  if (!input?.files?.length) {
    setNotice("Choose a CSV first.", "error");
    return;
  }
  setBusy(button, true, "Upload CSV");
  const form = new FormData();
  form.append("file", input.files[0]);
  try {
    const result = await request(apiUrl("/api/upload", { tag: state.tag }), {
      method: "POST",
      body: form,
    });
    setNotice(`Loaded ${fmt.format(result.rows)} rows as ${result.kind}.`, "success");
    await loadRun();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(button, false, "Upload CSV");
  }
}

// POST to /api/analyze to run the full pipeline in one call.
export async function analyzeData() {
  const btn = $("#analyzeBtn");
  setBusy(btn, true, "Analyze data");
  try {
    const res = await request("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        provider: state.provider,
        model: state.model,
        api_key: state.apiKey,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      if (res.status === 409) {
        setNotice(`A run is already active (run ${err.active_run_id || "unknown"}). Check the Pipeline tab.`, "warn");
      } else {
        setNotice("Failed to start analysis. Check the Pipeline tab for details.", "error");
      }
      return;
    }
    const data = await res.json();
    state.pipelineRunId = data.run_id;
    setNotice(`Analysis started (run ${data.run_id}). Watch progress in the Pipeline tab.`, "ok");
    // Navigate to Pipeline so the user can watch progress.
    const { setView } = await import("../nav.js");
    setView("pipeline");
  } finally {
    setBusy(btn, false, "Analyze data");
  }
}

export function bindGatheringEvents() {
  // Upload button uses the local wrapper that reads #uploadFile.
  $("#uploadBtn")?.addEventListener("click", uploadFromGathering);
  // Collect button reuses the existing collect() action from collect.js.
  $("#collectBtn")?.addEventListener("click", collect);
  // Analyze button fires the full pipeline.
  $("#analyzeBtn")?.addEventListener("click", analyzeData);
}
