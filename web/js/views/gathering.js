// gathering.js — data collection and one-click full-pipeline analysis.
// Combines the upload + Reddit collect actions from collect.js with a single
// "Analyze data" button that fires the full pipeline in one API call.
import { state, esc, $, fmt } from "../state.js";
import { apiUrl, request } from "../api.js"; // apiUrl needed for raw fetch in analyzeData
import { setNotice, setBusy } from "../components.js";
import { loadRun } from "../app.js";
import {
  collect,
  createSubredditListDraft,
  saveSubredditList,
  selectSubredditList,
} from "./collect.js";

export function gatheringView() {
  const selected = state.subredditLists.find((item) => item.id === state.selectedSubredditListId)
    || state.subredditLists[0] || null;
  const options = state.subredditLists.map((item) =>
    `<option value="${esc(item.id)}" ${item.id === selected?.id ? "selected" : ""}>${esc(item.display_name)} · ${esc(item.type)}</option>`
  ).join("");
  return `
    <div class="gathering-layout">
      <section class="panel">
        <div class="panel-head"><h2>1 · Get data</h2></div>
        <div class="form-grid">
          <div class="control">
            <label for="uploadFile">Upload CSV</label>
            <input id="uploadFile" name="file" type="file" accept=".csv" />
            <small>Load a Reddit collector CSV from your machine.</small>
          </div>
        </div>
        <div class="actions">
          <button id="uploadBtn" class="button">Upload CSV</button>
        </div>
        <hr class="divider" />
        <div class="subreddit-list-editor">
          <div class="form-grid">
            <div class="control wide">
              <label for="subredditListSelect">Named subreddit list</label>
              <select id="subredditListSelect" name="subreddit_list_id" autocomplete="off">
                ${options || '<option value="">No saved lists yet</option>'}
              </select>
              <small>The selected version is snapshotted when collection starts.</small>
            </div>
            <div class="control">
              <label for="subredditListName">Display name</label>
              <input id="subredditListName" name="display_name" value="${esc(selected?.display_name || "")}" maxlength="80" />
            </div>
            <div class="control">
              <label for="subredditListType">List type</label>
              <select id="subredditListType" name="list_type">
                ${["gm", "competitor", "custom"].map((type) => `<option value="${type}" ${selected?.type === type ? "selected" : ""}>${type}</option>`).join("")}
              </select>
            </div>
            <div class="control wide">
              <label for="subredditListValues">Subreddits</label>
              <textarea id="subredditListValues" name="subreddits" rows="6" placeholder="Silverado&#10;GMC">${esc((selected?.subreddits || []).join("\n"))}</textarea>
              <small>One subreddit per line. r/ prefixes and duplicates are normalized on save.</small>
            </div>
          </div>
          <div class="actions compact">
            <button id="createSubredditListBtn" class="button secondary" type="button">Create new list</button>
            <button id="saveSubredditListBtn" class="button" type="button">Save list</button>
          </div>
        </div>
        <div class="form-grid" style="margin-top:1rem">
          <div class="control">
            <label for="listingLimit">Posts per subreddit</label>
            <input id="listingLimit" name="listing_limit" type="number" inputmode="numeric" autocomplete="off" min="1" max="500" value="100" />
          </div>
          <div class="control">
            <label for="commentsLimit">Comments per post</label>
            <input id="commentsLimit" name="comments_limit" type="number" inputmode="numeric" autocomplete="off" min="0" max="25" value="5" />
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
    await loadRun({ silent: true });  // don't overwrite the success notice
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(button, false, "Upload CSV");
  }
}

// POST to /api/analyze to run the full pipeline in one call.
// Uses raw fetch() instead of request() so that non-2xx responses (like 409)
// are returned as a Response object rather than thrown — allowing us to inspect
// the status and show the user a meaningful message.
export async function analyzeData() {
  const btn = $("#analyzeBtn");
  setBusy(btn, true, "Analyze data");
  try {
    // Raw fetch so we get the Response back on 409 instead of an unhandled throw.
    const res = await fetch(apiUrl("/api/analyze"), {
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
        const conflict = err.detail || err;
        setNotice(`A run is already active (run ${conflict.active_run_id || "unknown"}). Check Settings → Pipeline runs.`, "warn");
      } else {
        setNotice("Failed to start analysis. Check Settings → Pipeline runs for details.", "error");
      }
      return;
    }
    const data = await res.json();
    state.pipelineRunId = data.run_id;
    setNotice(`Analysis started (run ${data.run_id}). Watch progress in Settings → Pipeline runs.`, "success");
    // Navigate to the nested pipeline monitor so the user can watch progress.
    const { setView } = await import("../nav.js");
    state.settingsSection = "pipeline";
    setView("settings");
  } finally {
    setBusy(btn, false, "Analyze data");
  }
}

export function bindGatheringEvents() {
  // Upload button uses the local wrapper that reads #uploadFile.
  $("#uploadBtn")?.addEventListener("click", uploadFromGathering);
  // Collect button reuses the existing collect() action from collect.js.
  $("#collectBtn")?.addEventListener("click", collect);
  $("#subredditListSelect")?.addEventListener("change", (event) => selectSubredditList(event.currentTarget.value));
  $("#createSubredditListBtn")?.addEventListener("click", createSubredditListDraft);
  $("#saveSubredditListBtn")?.addEventListener("click", saveSubredditList);
  // Analyze button fires the full pipeline.
  $("#analyzeBtn")?.addEventListener("click", analyzeData);
}
