// settings.js — full config editor: API key, provider, collection, analysis, prompts.
import { state, esc } from "../state.js";
import { request, apiUrl } from "../api.js";
import { setNotice } from "../components.js";

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------
export function settingsView() {
  const cfg = state.config || {};
  const p   = cfg.provider   || {};
  const col = cfg.collection || {};
  const an  = cfg.analysis   || {};
  const pr  = cfg.prompts    || {};

  return `
    <div class="settings-layout">

      <!-- ── API & Provider ──────────────────────────── -->
      <section class="panel config-section">
        <div class="panel-head">
          <h2>API &amp; Provider</h2>
          <small>Controls which LLM is used for classify, briefing, and Q&amp;A</small>
        </div>
        <div class="config-grid">
          <div class="control">
            <label for="cfg-api-key">API key</label>
            <input id="cfg-api-key" name="api_key" type="password"
              placeholder="${cfg._api_key_set ? "●●●●●●●● (saved)" : "Paste your OpenRouter key"}"
              autocomplete="off" />
            <small>Saved to <code>runtime/secrets.json</code> on your machine — never committed to git.</small>
          </div>
          <div class="control">
            <label for="cfg-provider">Provider</label>
            <select id="cfg-provider" name="provider">
              <option value="openrouter"${p.name === "openrouter" ? " selected" : ""}>OpenRouter</option>
              <option value="openai"${p.name === "openai" ? " selected" : ""}>OpenAI</option>
            </select>
          </div>
          <div class="control">
            <label for="cfg-model">Model</label>
            <input id="cfg-model" name="model" value="${esc(p.model || "")}"
              placeholder="gpt-oss-120b" autocomplete="off" />
            <small>Model identifier passed to the provider API.</small>
          </div>
          <div class="control">
            <label for="cfg-embedding">Embedding model</label>
            <input id="cfg-embedding" name="embedding_model" value="${esc(p.embedding_model || "")}"
              placeholder="text-embedding-3-small" autocomplete="off" />
            <small>Used for FAISS index building and Q&amp;A retrieval.</small>
          </div>
          <div class="control wide">
            <label for="cfg-base-url">API base URL</label>
            <input id="cfg-base-url" name="base_url" value="${esc(p.base_url || "")}"
              autocomplete="off" />
          </div>
        </div>
      </section>

      <!-- ── Collection ─────────────────────────────── -->
      <section class="panel config-section">
        <div class="panel-head">
          <h2>Collection</h2>
          <small>Default values pre-filled in the Data Gathering form</small>
        </div>
        <div class="config-grid">
          <div class="control">
            <label for="cfg-tag">Default run tag</label>
            <input id="cfg-tag" name="default_tag" value="${esc(col.default_tag || "")}"
              autocomplete="off" />
            <small>Workspace name used when no tag is specified.</small>
          </div>
          <div class="control">
            <label for="cfg-listing-limit">Posts per subreddit</label>
            <input id="cfg-listing-limit" name="listing_limit" type="number" min="1" max="1000"
              value="${col.listing_limit ?? 100}" />
            <small>How many posts to scan in each subreddit.</small>
          </div>
          <div class="control">
            <label for="cfg-comments-limit">Comments per post</label>
            <input id="cfg-comments-limit" name="comments_limit" type="number" min="0" max="500"
              value="${col.comments_limit ?? 5}" />
            <small>Top comments pulled per post (0 = posts only).</small>
          </div>
        </div>
      </section>

      <!-- ── Analysis ───────────────────────────────── -->
      <section class="panel config-section">
        <div class="panel-head">
          <h2>Analysis</h2>
          <small>Pipeline tuning parameters</small>
        </div>
        <div class="config-grid">
          <div class="control">
            <label for="cfg-clusters">K-means clusters</label>
            <input id="cfg-clusters" name="n_clusters" type="number" min="2" max="50"
              value="${an.n_clusters ?? 10}" />
            <small>Number of topic clusters for trend detection. Higher = finer-grained signals.</small>
          </div>
          <div class="control">
            <label for="cfg-classify-limit">Classify limit</label>
            <input id="cfg-classify-limit" name="classify_limit" type="number" min="0"
              value="${an.classify_limit ?? 0}" />
            <small>Max rows to classify per run. 0 = all pending rows.</small>
          </div>
          <div class="control">
            <label for="cfg-qa-k">Q&amp;A top-k</label>
            <input id="cfg-qa-k" name="qa_k" type="number" min="1" max="50"
              value="${an.qa_k ?? 8}" />
            <small>Evidence chunks retrieved per Q&amp;A query.</small>
          </div>
        </div>
      </section>

      <!-- ── Prompts ─────────────────────────────────── -->
      <section class="panel config-section">
        <div class="panel-head">
          <h2>Prompts</h2>
          <small>System prompts sent to the LLM. Changes take effect immediately on next run.</small>
        </div>
        <div class="control" style="margin-bottom:1rem">
          <label for="cfg-prompt-classify">Classification prompt</label>
          <textarea id="cfg-prompt-classify" name="prompt_classification"
            rows="10">${esc(pr.classification || "")}</textarea>
        </div>
        <div class="control">
          <label for="cfg-prompt-synthesis">Synthesis / briefing prompt</label>
          <textarea id="cfg-prompt-synthesis" name="prompt_synthesis"
            rows="10">${esc(pr.synthesis || "")}</textarea>
        </div>
      </section>

      <div class="config-actions">
        <button id="saveConfigBtn" class="button primary">Save all settings</button>
        <span id="configSaveStatus" class="config-save-status"></span>
      </div>

    </div>
  `;
}

// ---------------------------------------------------------------------------
// Load config from server into state
// ---------------------------------------------------------------------------
export async function loadConfig() {
  try {
    const cfg = await request(apiUrl("/api/config"));
    state.config = cfg;
    // Mirror key fields back into legacy state fields for existing pipeline code
    state.tag      = cfg.collection?.default_tag || state.tag;
    state.provider = cfg.provider?.name          || state.provider;
    state.model    = cfg.provider?.model         || state.model;
  } catch (_) {
    // non-fatal — pipeline will use server defaults
  }
}

// ---------------------------------------------------------------------------
// Bind events
// ---------------------------------------------------------------------------
export function bindSettingsEvents() {
  document.getElementById("saveConfigBtn")?.addEventListener("click", saveConfig);
}

async function saveConfig() {
  const btn    = document.getElementById("saveConfigBtn");
  const status = document.getElementById("configSaveStatus");
  if (btn) btn.disabled = true;
  if (status) status.textContent = "Saving…";

  const apiKey       = document.getElementById("cfg-api-key")?.value ?? "";
  const providerName = document.getElementById("cfg-provider")?.value;
  const model        = document.getElementById("cfg-model")?.value?.trim();
  const embedding    = document.getElementById("cfg-embedding")?.value?.trim();
  const baseUrl      = document.getElementById("cfg-base-url")?.value?.trim();
  const defaultTag   = document.getElementById("cfg-tag")?.value?.trim();
  const listingLimit = parseInt(document.getElementById("cfg-listing-limit")?.value, 10);
  const commentsLimit= parseInt(document.getElementById("cfg-comments-limit")?.value, 10);
  const nClusters    = parseInt(document.getElementById("cfg-clusters")?.value, 10);
  const classifyLimit= parseInt(document.getElementById("cfg-classify-limit")?.value, 10);
  const qaK          = parseInt(document.getElementById("cfg-qa-k")?.value, 10);
  const promptClassify = document.getElementById("cfg-prompt-classify")?.value ?? "";
  const promptSynth    = document.getElementById("cfg-prompt-synthesis")?.value ?? "";

  const body = {
    provider: {
      name: providerName,
      model,
      embedding_model: embedding,
      base_url: baseUrl,
    },
    collection: {
      default_tag: defaultTag,
      listing_limit: listingLimit,
      comments_limit: commentsLimit,
    },
    analysis: {
      n_clusters: nClusters,
      classify_limit: classifyLimit,
      qa_k: qaK,
    },
    prompts: {
      classification: promptClassify,
      synthesis: promptSynth,
    },
  };
  if (apiKey) body.api_key = apiKey;

  try {
    await request(apiUrl("/api/config"), { method: "PATCH", body: JSON.stringify(body),
      headers: { "Content-Type": "application/json" } });
    // Update state so in-flight pipeline calls use new values
    await loadConfig();
    if (status) {
      status.textContent = "Saved.";
      setTimeout(() => { if (status) status.textContent = ""; }, 2500);
    }
  } catch (err) {
    if (status) status.textContent = `Error: ${err.message}`;
  } finally {
    if (btn) btn.disabled = false;
  }
}
