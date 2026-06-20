// qa.js — Q&A / RAG view, evidence cards, and index-build job polling.
// Provider/model/api_key now come from state (set in Settings tab) rather than DOM inputs.
import { state, $, esc, fmt } from "../state.js";
import { apiUrl, request } from "../api.js";
import { setNotice, setBusy } from "../components.js";
import { render } from "../nav.js";

// ---------------------------------------------------------------------------
// Q&A rendering helpers
// ---------------------------------------------------------------------------

function qaJobBar(status) {
  if (!status || ["ready", "missing", "blocked_no_api_key"].includes(status.state)) return "";
  const stateLabel = { building: "Building index…", stale: "Index is stale", failed: "Build failed" }[status.state] || status.state;
  const color = status.state === "building" ? "" : "error";
  const pct = status.total > 0 ? Math.round((status.processed / status.total) * 100) : 0;
  return `<div class="notice ${color}" style="margin-bottom:0.5rem">
    <strong>Index build:</strong> ${stateLabel}
    ${status.state === "building" && status.total > 0
      ? ` — ${status.processed}/${status.total} docs (${pct}%)`
      : ""}
    ${status.error ? ` — ${esc(status.error)}` : ""}
  </div>`;
}

function qaEvidenceCard(hit, index) {
  const vehicle = hit.vehicle || "unknown";
  const sentiment = hit.sentiment || "";
  const category = (hit.category || "").replaceAll("_", " ");
  const subreddit = hit.subreddit ? `r/${esc(hit.subreddit)}` : "";
  const permalink = hit.permalink || "";
  const score = (hit.retrieval_score || 0).toFixed(3);
  const body = (hit.body || "").slice(0, 400);
  return `<article class="evidence-item" style="margin-bottom:1rem">
    <div class="evidence-meta">
      <strong>[${index}]</strong>
      <span>${esc(vehicle)}</span>
      ${sentiment ? `<span class="tag">${esc(sentiment)}</span>` : ""}
      ${category ? `<span class="tag">${esc(category)}</span>` : ""}
      ${subreddit ? `<span>${esc(subreddit)}</span>` : ""}
      <span style="color:var(--muted);font-size:0.8rem">sim ${score}</span>
    </div>
    <p style="margin:0.25rem 0 0;font-size:0.875rem">${esc(body)}</p>
    ${permalink ? `<a href="https://reddit.com${esc(permalink)}" target="_blank" rel="noopener" style="font-size:0.8rem">View on Reddit</a>` : ""}
  </article>`;
}

export function qaView() {
  const qjs = state.qaJobStatus;
  const hits = state.qaHits;
  const answer = state.qaAnswer;
  const hasClassified = !!(state.data && state.data.summary && state.data.summary.metrics?.analyzed_rows > 0);
  const qaState = qjs?.state || "missing";
  const indexReady = qaState === "ready";
  const jobRunning = qaState === "building";
  const recovery = {
    missing: { label: "Build Q&A index", help: qjs?.detail || "No index exists for this analysis." },
    failed: { label: "Retry index build", help: qjs?.detail || "The previous build failed." },
    stale: { label: "Rebuild for current data", help: qjs?.detail || "This index belongs to older data." },
  }[qaState];

  return `<div class="qa-workbench">
    ${qaJobBar(qjs)}

    <section class="panel" style="margin-bottom:1rem">
      <div class="panel-head"><h3>Ask your evidence</h3><small>${esc(qaState)}</small></div>
      <p style="font-size:0.875rem;margin:0 0 0.5rem">
        Ask natural-language questions and get answers grounded in the current classified Reddit evidence.
      </p>
      ${!hasClassified
        ? `<div class="notice" style="margin-top:0.5rem">Classify data first before building the Q&amp;A index.</div>`
        : recovery
          ? `<div class="notice" style="margin-top:0.5rem">${esc(recovery.help)}</div>
             <button id="qaBuildIndexBtn" class="button primary" type="button">${esc(recovery.label)}</button>`
          : qaState === "blocked_no_api_key"
            ? `<div class="notice error" style="margin-top:0.5rem">${esc(qjs?.detail || "Add an API key before using Q&A.")}</div>
               <button class="button" type="button" data-jump="settings">Open Settings</button>`
          : ""
      }
      ${indexReady && qjs?.doc_count != null
        ? `<div class="notice success" style="margin-top:0.5rem">Ready · ${fmt.format(qjs.doc_count)} docs indexed for this generation.</div>`
        : ""
      }
    </section>

    <!-- Question box -->
    ${indexReady ? `
    <section class="panel" style="margin-bottom:1rem">
      <div class="panel-head"><h3>Ask a Question</h3></div>
      <div style="display:flex;gap:0.5rem;flex-wrap:wrap;margin-bottom:0.5rem">
        <input id="qaQuestionInput" name="question" type="search" autocomplete="off" aria-label="Question" placeholder="e.g. What are the most common Silverado transmission complaints…"
          style="flex:1;min-width:200px" />
        <button id="qaSubmitBtn" class="button primary">Ask (with answer)</button>
        <button id="qaSearchBtn" class="button">Search only</button>
      </div>
      <p style="font-size:0.8rem;color:var(--muted);margin:0">
        <em>Ask</em> retrieves evidence and generates an LLM answer. <em>Search only</em> returns evidence cards without an LLM call.
      </p>
    </section>
    ` : ""}

    <!-- Answer -->
    ${answer ? `
    <section class="panel" style="margin-bottom:1rem">
      <div class="panel-head"><h3>Answer</h3></div>
      <p style="font-size:0.9rem;white-space:pre-wrap">${esc(answer)}</p>
    </section>
    ` : ""}

    <!-- Evidence -->
    ${hits && hits.length > 0 ? `
    <section>
      <div style="display:flex;align-items:baseline;gap:0.5rem;margin-bottom:0.5rem">
        <h3 style="margin:0">Evidence</h3>
        <small style="color:var(--muted)">${hits.length} result${hits.length === 1 ? "" : "s"} · sorted by similarity</small>
      </div>
      <div class="evidence-feed">
        ${hits.map((h, i) => qaEvidenceCard(h, i + 1)).join("")}
      </div>
    </section>
    ` : hits && hits.length === 0 ? `<div class="notice">No relevant evidence found for that query.</div>` : ""}
  </div>`;
}

// ---------------------------------------------------------------------------
// Q&A polling
// ---------------------------------------------------------------------------

export function stopQaPolling() {
  if (state.qaPollTimer) {
    window.clearInterval(state.qaPollTimer);
    state.qaPollTimer = null;
  }
}

function startQaPolling(jobId = "") {
  stopQaPolling();
  state.qaPollTimer = window.setInterval(() => pollQaStatus(jobId), 2000);
}

async function pollQaStatus(jobId = "") {
  try {
    const params = jobId ? { tag: state.tag, job_id: jobId } : { tag: state.tag };
    const status = await request(apiUrl("/api/qa/status", params));
    state.qaJobStatus = status;
    if (state.view === "dashboard") render();
    const done = ["ready", "failed", "stale", "missing", "blocked_no_api_key"].includes(status.state);
    if (done) {
      stopQaPolling();
      if (status.state === "ready") {
        setNotice("Q&A index built. You can now ask questions.", "success");
      } else {
        setNotice(`Index build ${status.state}.`, "error");
      }
    }
  } catch {
    stopQaPolling();
  }
}

export async function refreshQaStatus() {
  try {
    const status = await request(apiUrl("/api/qa/status", { tag: state.tag }));
    state.qaJobStatus = status;
    if (state.view === "dashboard") render();
    if (status.state === "building") startQaPolling(status.job_id);
  } catch (error) {
    setNotice(error.message, "error");
  }
}

// ---------------------------------------------------------------------------
// Q&A actions
// ---------------------------------------------------------------------------

export async function startQaBuildIndex() {
  const btn = $("#qaBuildIndexBtn");
  setBusy(btn, true, "Build index");
  try {
    const status = await request("/api/qa/build-index", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        provider: state.provider,
        model: state.model,
        api_key: state.apiKey,
      }),
    });
    state.qaJobStatus = { ...status, state: "building" };
    state.qaHits = null;
    state.qaAnswer = null;
    if (state.view === "dashboard") render();
    if (["running", "pending"].includes(status.state)) {
      setNotice("Index build started.");
      startQaPolling(status.job_id);
    }
  } catch (error) {
    setNotice(error.message, "error");
    setBusy(btn, false, "Build index");
  }
}

export async function submitQaQuestion() {
  const question = ($("#qaQuestionInput")?.value || "").trim();
  if (!question) { setNotice("Enter a question first.", "error"); return; }
  const btn = $("#qaSubmitBtn");
  setBusy(btn, true, "Ask (with answer)");
  state.qaHits = null;
  state.qaAnswer = null;
  try {
    const result = await request("/api/qa/answer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        question,
        provider: state.provider,
        model: state.model,
        api_key: state.apiKey,
        k: 8,
      }),
    });
    state.qaHits = result.hits || [];
    state.qaAnswer = result.answer || "";
    if (state.view === "dashboard") render();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(btn, false, "Ask (with answer)");
  }
}

export async function submitQaSearch() {
  const question = ($("#qaQuestionInput")?.value || "").trim();
  if (!question) { setNotice("Enter a query first.", "error"); return; }
  const btn = $("#qaSearchBtn");
  setBusy(btn, true, "Search only");
  state.qaHits = null;
  state.qaAnswer = null;
  try {
    const result = await request("/api/qa/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        query: question,
        provider: state.provider,
        model: state.model,
        api_key: state.apiKey,
        k: 8,
      }),
    });
    state.qaHits = result.hits || [];
    state.qaAnswer = null;
    if (state.view === "dashboard") render();
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(btn, false, "Search only");
  }
}

export function bindQaEvents() {
  $("#qaBuildIndexBtn")?.addEventListener("click", startQaBuildIndex);
  $("#qaSubmitBtn")?.addEventListener("click", submitQaQuestion);
  $("#qaSearchBtn")?.addEventListener("click", submitQaSearch);
}
