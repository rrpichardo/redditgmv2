// app.js — bootstrap entry point: global events, initial data load, run summary
// refresh, download/export controllers. Wires the modular UI together.
import { state, $, $$, fmt, fileSize, esc } from "./state.js";
import { apiUrl, request } from "./api.js";
import { setNotice, setBusy } from "./components.js";
import { render, setView, handleSaveKindClick } from "./nav.js";
import { bindResize } from "./charts.js";
import { buildFilterParams, hasActiveFilters } from "./views/explore.js";
import { collectProgressText, updateCollectUi, startCollectPolling } from "./views/collect.js";

// ---------------------------------------------------------------------------
// Run load + summary refresh
// ---------------------------------------------------------------------------

export async function loadRun() {
  state.tag = $("#tagInput").value.trim() || "gm_vehicle_on_demand";
  setNotice("Loading run...");
  try {
    const params = buildFilterParams();
    state.data = await request(apiUrl("/api/run", { tag: state.tag, ...params }));

    const collectStatus = await request(
      apiUrl("/api/collect/status", { tag: state.tag })
    ).catch(() => null);

    if (collectStatus?.status === "running") {
      $("#collectorStatus").textContent = `running ${collectProgressText(collectStatus)}`;
    } else {
      $("#collectorStatus").textContent = state.data.status.legacy_collector_found
        ? "ready"
        : "missing";
    }

    const analyzed = state.data.summary.metrics.analyzed_rows ?? 0;
    const total = state.data.summary.metrics.total_rows ?? 0;
    const filterNote = hasActiveFilters() ? " [filtered]" : "";
    $("#runSubtitle").textContent =
      `${state.data.tag} / ${fmt.format(total)} rows / ${fmt.format(analyzed)} analyzed / ` +
      `${state.data.status.has_classified ? "classified" : "source only"}${filterNote}`;
    $("#railRows").textContent = `${fmt.format(total)} total`;
    $("#railExports").textContent = state.data.status.has_source ? "ready" : "empty";

    updateDownloads();
    render();

    if (collectStatus?.status === "running") {
      updateCollectUi(collectStatus);
      startCollectPolling(collectStatus.job_id);
    } else {
      setNotice("");
    }
  } catch (error) {
    setNotice(error.message, "error");
  }
}

function updateDownloads() {
  const classified = $("#classifiedDownload");
  const report = $("#reportDownload");
  const sourceZip = $("#sourceZipDownload");
  const combined = $("#combinedDownload");
  const saveZip = $("#saveZipBtn");
  const hasSource = Boolean(state.data?.status.has_source);

  if (saveZip) saveZip.disabled = !hasSource;
  if (sourceZip) {
    sourceZip.href = `/api/download/source?tag=${encodeURIComponent(state.tag)}&kind=all`;
    sourceZip.setAttribute("aria-disabled", hasSource ? "false" : "true");
  }
  if (combined) {
    combined.href = `/api/download/source?tag=${encodeURIComponent(state.tag)}&kind=combined`;
    combined.setAttribute("aria-disabled", hasSource ? "false" : "true");
  }
  if (classified) {
    classified.href = `/api/download/classified?tag=${encodeURIComponent(state.tag)}`;
    classified.setAttribute("aria-disabled", state.data?.status.has_classified ? "false" : "true");
  }
  if (report) {
    report.href = `/api/download/report?tag=${encodeURIComponent(state.tag)}`;
    report.setAttribute("aria-disabled", state.data?.status.has_report ? "false" : "true");
  }
}

// Exports panel used inside the collect view. Exported so collect.js can embed it.
export function sourceDownloadsPanel() {
  const files = state.data?.status.source_files || [];
  const hasSource = Boolean(state.data?.status.has_source);
  const fileMap = Object.fromEntries(files.map((file) => [file.kind, file]));
  const items = [
    ["all", "All data ZIP", hasSource, "raw + classified + report"],
    ["combined", "Posts + comments CSV", fileMap.combined?.exists, fileSize(fileMap.combined?.bytes)],
    ["posts", "Posts CSV", fileMap.posts?.exists, fileSize(fileMap.posts?.bytes)],
    ["comments", "Comments CSV", fileMap.comments?.exists, fileSize(fileMap.comments?.bytes)],
  ];
  return `<section class="panel download-panel">
    <div class="panel-head">
      <h2>Exports</h2>
      <small>${hasSource ? `${fmt.format(state.data.summary.metrics.total_rows)} rows loaded` : "no source rows"}</small>
    </div>
    <div class="field-row flush">
      <button id="saveZipPanelBtn" class="button primary" type="button" ${hasSource ? "" : "disabled"}>Save ZIP to Downloads</button>
    </div>
    <div class="download-grid">${items.map(([kind, label, enabled, note]) =>
      `<a class="download-tile" href="/api/download/source?tag=${encodeURIComponent(state.tag)}&kind=${kind}"
          aria-disabled="${enabled ? "false" : "true"}" data-save-kind="${kind}">
        <strong>${esc(label)}</strong>
        <span>${esc(note)}</span>
      </a>`
    ).join("")}</div>
  </section>`;
}

// ---------------------------------------------------------------------------
// Save export to Downloads
// ---------------------------------------------------------------------------

export async function saveExport(kind = "all") {
  const labels = {
    all: "ZIP",
    combined: "posts + comments CSV",
    posts: "posts CSV",
    comments: "comments CSV",
    classified: "classified CSV",
    report: "briefing",
  };
  const buttons = kind === "all"
    ? [$("#saveZipBtn"), $("#saveZipPanelBtn")].filter(Boolean)
    : [];
  buttons.forEach((button) => setBusy(button, true, "Save ZIP"));
  setNotice(`Saving ${labels[kind] || "export"}...`);
  try {
    const result = await request(apiUrl("/api/export/save", { tag: state.tag, kind }), {
      method: "POST",
    });
    setNotice(`Saved ${result.filename} to Downloads.`, "success");
  } catch (error) {
    setNotice(error.message, "error");
  } finally {
    buttons.forEach((button) =>
      setBusy(button, false, button.id === "saveZipBtn" ? "Save ZIP" : "Save ZIP to Downloads")
    );
  }
}

// ---------------------------------------------------------------------------
// Export job polling (PDF / charts ZIP)
// ---------------------------------------------------------------------------

function clearExportPolling() {
  if (state.exportPollTimer) {
    window.clearInterval(state.exportPollTimer);
    state.exportPollTimer = null;
  }
}

function startExportPolling(jobId = "") {
  clearExportPolling();
  state.exportPollTimer = window.setInterval(() => pollExportJobStatus(jobId), 2000);
}

async function pollExportJobStatus(jobId = "") {
  try {
    const params = jobId ? { tag: state.tag, job_id: jobId } : { tag: state.tag };
    const status = await request(apiUrl("/api/export/status", params));
    state.exportJobStatus = status;
    if (state.view === "briefing") render();
    const done = ["completed", "failed", "interrupted"].includes(status.state);
    if (done) {
      clearExportPolling();
      if (status.state === "completed") {
        setNotice("Export completed. Download links are now active.", "success");
      } else {
        setNotice(`Export ${status.state}.`, "error");
      }
    }
  } catch {
    clearExportPolling();
  }
}

export async function startExportJob(kind) {
  const btnId = kind === "charts" ? "#chartsZipBtn" : "#briefingPdfBtn";
  const btn = $(btnId);
  setBusy(btn, true, kind === "charts" ? "Export charts ZIP" : "Export briefing PDF");
  try {
    const status = await request("/api/export/pdf-job", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tag: state.tag, kind }),
    });
    state.exportJobStatus = status;
    render();
    if (status.state === "running") {
      setNotice(`${kind === "charts" ? "Charts" : "Briefing"} export job started.`);
      startExportPolling(status.job_id);
    }
  } catch (error) {
    setNotice(error.message, "error");
    setBusy(btn, false, kind === "charts" ? "Export charts ZIP" : "Export briefing PDF");
  }
}

export async function refreshExportJobStatus() {
  try {
    const status = await request(apiUrl("/api/export/status", { tag: state.tag }));
    state.exportJobStatus = status;
    render();
    if (status.state === "running") startExportPolling(status.job_id);
  } catch (error) {
    setNotice(error.message, "error");
  }
}

// ---------------------------------------------------------------------------
// Global bindings + init
// ---------------------------------------------------------------------------

function bindGlobalEvents() {
  $("#refreshBtn").addEventListener("click", loadRun);
  $$(".top-actions [data-save-kind]").forEach((target) =>
    target.addEventListener("click", handleSaveKindClick)
  );
  $("#tagInput").addEventListener("change", loadRun);
  $("#providerSelect").addEventListener("change", () => {
    $("#modelInput").value =
      $("#providerSelect").value === "openai" ? "gpt-4o-mini" : "gpt-oss-120b";
  });
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => setView(tab.dataset.view)));
}

bindGlobalEvents();
bindResize();
loadRun();
