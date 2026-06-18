// briefing.js — briefing/export view and template/LLM report generation.
import { state, $, esc } from "../state.js";
import { request } from "../api.js";
import { setNotice, setBusy, jobStatusCard } from "../components.js";
import { loadRun } from "../app.js";
import { setView } from "../nav.js";

export function briefingView() {
  const exportJob = state.exportJobStatus;
  const isExporting = exportJob?.state === "running";
  const tag = state.tag;
  const hasClassified = Boolean(state.data?.status.has_classified);

  return `
    <section class="panel briefing-actions">
      <div class="panel-head"><h2>Narrative briefing</h2><small>markdown export</small></div>
      <div class="field-row">
        <button id="templateBriefBtn" class="button primary">Template briefing</button>
        <button id="llmBriefBtn" class="button secondary">LLM briefing</button>
      </div>
    </section>

    <section class="panel spaced">
      <div class="panel-head">
        <h2>PDF exports</h2>
        <small>charts ZIP or full briefing PDF</small>
      </div>
      <p class="panel-desc">Render all charts as PNGs and package as a ZIP, or combine with the narrative to produce a full briefing PDF. Requires classified data.</p>
      <div class="field-row">
        <button id="chartsZipBtn" class="button secondary" ${isExporting || !hasClassified ? "disabled" : ""}>
          Export charts ZIP
        </button>
        <button id="briefingPdfBtn" class="button secondary" ${isExporting || !hasClassified ? "disabled" : ""}>
          Export briefing PDF
        </button>
        <button id="refreshExportBtn" class="button secondary">Refresh status</button>
      </div>
      ${jobStatusCard(exportJob)}
      <div class="field-row" style="margin-top:0.85rem">
        <a id="downloadChartsLink" class="export-chip"
           href="/api/download/charts?tag=${encodeURIComponent(tag)}"
           aria-disabled="${hasClassified ? "false" : "true"}">Download charts ZIP</a>
        <a id="downloadPdfLink" class="export-chip"
           href="/api/download/briefing-pdf?tag=${encodeURIComponent(tag)}"
           aria-disabled="${hasClassified ? "false" : "true"}">Download briefing PDF</a>
      </div>
      ${!hasClassified ? `<div class="notice" style="margin-top:0.5rem">Classify data first to enable PDF exports.</div>` : ""}
    </section>

    <section class="panel spaced">
      <pre id="briefingText" class="briefing">${esc(state.report || "No briefing generated yet.")}</pre>
    </section>`;
}

// Generate the narrative briefing (template or LLM), then jump to this view.
export async function briefing(useLlm) {
  const button = useLlm ? $("#llmBriefBtn") : $("#templateBriefBtn");
  setBusy(button, true, useLlm ? "LLM briefing" : "Template briefing");
  try {
    const result = await request("/api/briefing", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        tag: state.tag,
        provider: $("#providerSelect").value,
        model: $("#modelInput").value,
        api_key: $("#apiKeyInput").value,
        use_llm: useLlm,
      }),
    });
    state.report = result.report;
    setNotice("Briefing saved.", "success");
    await loadRun();
    setView("briefing");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    setBusy(button, false, useLlm ? "LLM briefing" : "Template briefing");
  }
}
