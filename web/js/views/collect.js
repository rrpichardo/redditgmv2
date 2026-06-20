// collect.js — collector controls, CSV upload, and collect-job polling.
import { state, $, fmt } from "../state.js";
import { apiUrl, request } from "../api.js";
import { setNotice, setBusy } from "../components.js";
import { sourceDownloadsPanel, loadRun } from "../app.js";

export function collectView() {
  return `
    <div class="collect-layout">
      <section class="panel collector-panel">
        <div class="panel-head"><h2>Collector</h2><small>append-only</small></div>
        <div class="form-grid">
          <div class="control"><label for="collectSource">Source</label>
            <select id="collectSource">
              <option value="gm">GM vehicle list</option>
              <option value="competitor">Competitor list</option>
              <option value="custom">Custom list</option>
            </select>
          </div>
          <div class="control"><label for="listingLimit">Posts per subreddit</label>
            <input id="listingLimit" type="number" min="1" max="500" value="100">
          </div>
          <div class="control"><label for="commentsLimit">Comments per post</label>
            <input id="commentsLimit" type="number" min="0" max="25" value="5">
          </div>
          <div class="control wide"><label for="customSubs">Custom subreddits</label>
            <textarea id="customSubs" placeholder="Silverado&#10;Chevy"></textarea>
          </div>
          <div class="control"><label for="dryRun">Dry run</label>
            <select id="dryRun"><option value="false">No</option><option value="true">Yes</option></select>
          </div>
        </div>
        <div class="field-row">
          <button id="collectBtn" class="button primary">Run collector</button>
        </div>
        <div id="collectProgress" class="collector-progress" hidden></div>
        <pre id="collectLog" class="log-box" hidden></pre>
      </section>
      ${sourceDownloadsPanel()}
    </div>
    <section class="panel upload-panel">
      <div class="panel-head"><h2>Upload CSV</h2><small>collector or classified output</small></div>
      <div class="field-row">
        <input id="uploadInput" type="file" accept=".csv">
        <button id="uploadBtn" class="button secondary">Load CSV</button>
      </div>
    </section>`;
}

// ---------------------------------------------------------------------------
// Collect UI + polling
// ---------------------------------------------------------------------------

// Human-readable progress line for the collector status box.
export function collectProgressText(status) {
  const done = Number(status?.completed_subreddits || 0);
  const total = Number(status?.total_subreddits || 0);
  const count = total ? `${done}/${total}` : `${done}`;
  const last = status?.last_subreddit ? ` | last: r/${status.last_subreddit}` : "";
  const elapsed = status?.elapsed_seconds ? ` | ${Math.round(status.elapsed_seconds)}s` : "";
  return `${count} subreddits${last}${elapsed}`;
}

export function clearCollectPolling() {
  if (state.collectPollTimer) {
    window.clearInterval(state.collectPollTimer);
    state.collectPollTimer = null;
  }
}

// Reflect collector status into the progress box, log, and notice bar.
export function updateCollectUi(status) {
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

export function startCollectPolling(jobId = "") {
  clearCollectPolling();
  state.collectPollTimer = window.setInterval(() => pollCollectStatus(jobId), 1500);
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

// Kick off a collector run, then poll if it goes async.
export async function collect() {
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

// Upload a CSV file to the current tag.
export async function upload() {
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
    const result = await request(apiUrl("/api/upload", { tag: state.tag }), {
      method: "POST",
      body: form,
    });
    setNotice(`Loaded ${fmt.format(result.rows)} rows as ${result.kind}.`, "success");
    await loadRun({ silent: true });
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(button, false, "Load CSV");
  }
}
