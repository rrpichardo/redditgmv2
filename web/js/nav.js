// nav.js — view registry, tab switching, render + ECharts mount pass, event binding.
// Restructured for 5-tab IA: Dashboard, Data Explorer, Data Gathering, Pipeline, Settings.
import { state, $, $$ } from "./state.js";
import { mountViewCharts } from "./charts.js";
import { saveExport, startExportJob, refreshExportJobStatus } from "./app.js";
import { dashboard, mountDashboardCharts } from "./views/dashboard.js";
import { explorerView, bindExplorerEvents } from "./views/explore.js";
import { gatheringView, bindGatheringEvents } from "./views/gathering.js";
import { pipelineView, bindPipelineEvents } from "./views/pipeline.js";
import { settingsView, bindSettingsEvents } from "./views/settings.js";

export function setView(view) {
  state.view = view;
  // Sync active class on tab buttons.
  $$(".tab").forEach((tab) => tab.classList.toggle("is-active", tab.dataset.view === view));
  render();
}

// Map data-view keys to render functions.
const views = {
  dashboard,
  explorer: explorerView,
  gathering: gatheringView,
  pipeline: pipelineView,
  settings: settingsView,
};

export function render() {
  const root = $("#viewRoot");
  root.innerHTML = (views[state.view] || (() => ""))();
  bindViewEvents();
  // Mount any [data-chart] panels via ECharts after HTML is in the DOM.
  mountViewCharts(root, state.data);
  mountDashboardCharts();
}

export function handleSaveKindClick(event) {
  const target = event.currentTarget;
  event.preventDefault();
  if (target.getAttribute("aria-disabled") === "true" || target.disabled) return;
  saveExport(target.dataset.saveKind || "all");
}

export function bindViewEvents() {
  // data-jump buttons (e.g. empty-state "Open collection" now points to "gathering").
  $$("[data-jump]").forEach((button) => button.addEventListener("click", () => setView(button.dataset.jump)));
  $$("[data-save-export]").forEach((button) => button.addEventListener("click", () => saveExport("all")));
  $$("#viewRoot [data-save-kind]").forEach((target) => target.addEventListener("click", handleSaveKindClick));

  // Per-view event binding — only the currently rendered view's elements exist in DOM.
  bindSettingsEvents();
  bindGatheringEvents();
  bindPipelineEvents();
  bindExplorerEvents();  // covers filters + Q&A buttons
}
