// nav.js — view registry, tab switching, render + ECharts mount pass, event binding.
import { state, $, $$ } from "./state.js";
import { mountViewCharts } from "./charts.js";
import { saveExport, startExportJob, refreshExportJobStatus } from "./app.js";
import { dashboard } from "./views/dashboard.js";
import { collectView, collect, upload } from "./views/collect.js";
import {
  classifyView,
  previewClassify,
  llmClassify,
  startClassifyJob,
  refreshClassifyJobStatus,
} from "./views/classify.js";
import { exploreView, bindFilterEvents } from "./views/explore.js";
import { briefingView, briefing } from "./views/briefing.js";
import {
  trendsView,
  loadTrends,
  startTrendsJob,
  refreshTrendsStatus,
  startTrendBriefingJob,
  refreshTrendBriefingStatus,
} from "./views/trends.js";
import {
  qaView,
  startQaBuildIndex,
  refreshQaStatus,
  submitQaQuestion,
  submitQaSearch,
} from "./views/qa.js";

export function setView(view) {
  state.view = view;
  $$(".tab").forEach((tab) => tab.classList.toggle("is-active", tab.dataset.view === view));
  // Load trends data on first visit or tab switch
  if (view === "trends") {
    loadTrends().then(() => render());
    // Also load job status if not already tracking
    if (!state.trendPollTimer) refreshTrendsStatus();
    if (!state.trendBriefingPollTimer) refreshTrendBriefingStatus();
    return;
  }
  render();
}

const views = {
  dashboard,
  collect: collectView,
  classify: classifyView,
  explore: exploreView,
  briefing: briefingView,
  trends: trendsView,
  qa: qaView,
};

export function render() {
  const root = $("#viewRoot");
  root.innerHTML = (views[state.view] || (() => ""))();
  bindViewEvents();
  // After HTML is in the DOM, mount any [data-chart] panels via ECharts.
  mountViewCharts(root, state.data);
}

export function handleSaveKindClick(event) {
  const target = event.currentTarget;
  event.preventDefault();
  if (target.getAttribute("aria-disabled") === "true" || target.disabled) return;
  saveExport(target.dataset.saveKind || "all");
}

export function bindViewEvents() {
  $$("[data-jump]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.jump)));
  $$("[data-save-export]").forEach((button) => button.addEventListener("click", () => saveExport("all")));
  $$("#viewRoot [data-save-kind]").forEach((target) => target.addEventListener("click", handleSaveKindClick));

  // Collect
  $("#collectBtn")?.addEventListener("click", collect);
  $("#saveZipPanelBtn")?.addEventListener("click", () => saveExport("all"));
  $("#uploadBtn")?.addEventListener("click", upload);

  // Classify
  $("#previewClassifyBtn")?.addEventListener("click", previewClassify);
  $("#llmClassifyBtn")?.addEventListener("click", llmClassify);
  $("#startClassifyJobBtn")?.addEventListener("click", startClassifyJob);
  $("#refreshClassifyJobBtn")?.addEventListener("click", refreshClassifyJobStatus);

  // Briefing / exports
  $("#templateBriefBtn")?.addEventListener("click", () => briefing(false));
  $("#llmBriefBtn")?.addEventListener("click", () => briefing(true));
  $("#chartsZipBtn")?.addEventListener("click", () => startExportJob("charts"));
  $("#briefingPdfBtn")?.addEventListener("click", () => startExportJob("briefing"));
  $("#refreshExportBtn")?.addEventListener("click", refreshExportJobStatus);

  // Trends (Phase 5)
  $("#startTrendsBtn")?.addEventListener("click", startTrendsJob);
  $("#refreshTrendsBtn")?.addEventListener("click", refreshTrendsStatus);
  $("#trendBriefingBtn")?.addEventListener("click", startTrendBriefingJob);
  $("#refreshTrendBriefingBtn")?.addEventListener("click", refreshTrendBriefingStatus);

  // Q&A (Phase 6)
  $("#qaBuildIndexBtn")?.addEventListener("click", startQaBuildIndex);
  $("#qaRefreshBtn")?.addEventListener("click", refreshQaStatus);
  $("#qaSubmitBtn")?.addEventListener("click", submitQaQuestion);
  $("#qaSearchBtn")?.addEventListener("click", submitQaSearch);

  // Filters (explore view)
  bindFilterEvents();
}
