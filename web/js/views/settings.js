// settings.js — tag, provider, model, api_key config for all run operations.
// These fields were previously in the left rail; now they live in their own tab.
import { state, esc } from "../state.js";
import { setNotice } from "../components.js";

export function settingsView() {
  return `
    <div class="settings-layout">
      <section class="panel">
        <div class="panel-head"><h2>Run settings</h2></div>
        <div class="form-grid">
          <div class="control">
            <label for="settingsTag">Run tag</label>
            <input id="settingsTag" name="tag" value="${esc(state.tag)}" autocomplete="off" />
            <small>Groups all data and runs under one workspace.</small>
          </div>
          <div class="control">
            <label for="settingsProvider">LLM provider</label>
            <select id="settingsProvider" name="provider" autocomplete="off">
              <option value="openrouter"${state.provider === "openrouter" ? " selected" : ""}>OpenRouter</option>
              <option value="openai"${state.provider === "openai" ? " selected" : ""}>OpenAI</option>
            </select>
            <small>Used for classification and Q&amp;A index building.</small>
          </div>
          <div class="control">
            <label for="settingsModel">Model</label>
            <input id="settingsModel" name="model" value="${esc(state.model)}" autocomplete="off" />
            <small>Model name passed to the provider API.</small>
          </div>
          <div class="control">
            <label for="settingsApiKey">API key override</label>
            <input id="settingsApiKey" name="api_key" type="password" value="${esc(state.apiKey)}" autocomplete="off" />
            <small>Overrides the server environment key. Never saved to disk.</small>
          </div>
        </div>
        <div class="actions">
          <button id="saveSettingsBtn" class="button primary">Save settings</button>
        </div>
      </section>
    </div>
  `;
}

export function bindSettingsEvents() {
  // Read all four inputs and write them back to state on save.
  document.getElementById("saveSettingsBtn")?.addEventListener("click", () => {
    state.tag      = document.getElementById("settingsTag")?.value.trim()    || state.tag;
    state.provider = document.getElementById("settingsProvider")?.value       || state.provider;
    state.model    = document.getElementById("settingsModel")?.value.trim()   || state.model;
    state.apiKey   = document.getElementById("settingsApiKey")?.value         || "";
    setNotice("Settings saved for this session.", "success");
  });
}
