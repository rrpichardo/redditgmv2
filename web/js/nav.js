// nav.js — view registry, tab switching, render + ECharts mount pass, event binding.
// Primary IA: Dashboard, Q&A, Data Explorer, Data Gathering, Settings.
import { state, $, $$ } from "./state.js";
import { mountViewCharts, disposeAll } from "./charts.js";
import { saveExport, startExportJob, refreshExportJobStatus, loadDetailCharts } from "./app.js";
import { dashboard, mountDashboardCharts } from "./views/dashboard.js";
import { explorerView, bindExplorerEvents } from "./views/explore.js";
import { gatheringView, bindGatheringEvents } from "./views/gathering.js";
import { bindPipelineEvents, stopPipelinePolling } from "./views/pipeline.js";
import { settingsView, bindSettingsEvents } from "./views/settings.js";
import { qaView, bindQaEvents, stopQaPolling } from "./views/qa.js";

export function setView(view) {
  stopQaPolling();
  stopPipelinePolling();
  state.view = view;
  // Sync visual and accessibility state for the single active tab.
  $$(".tab").forEach((tab) => {
    const active = tab.dataset.view === view;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
  const panel = $("#viewRoot");
  panel?.setAttribute("aria-labelledby", `tab-${view}`);
  render();
  if (view === "explorer") loadDetailCharts();
}

export function handleTabKeydown(event) {
  const tabs = $$(".tab");
  const current = tabs.indexOf(event.currentTarget);
  if (current < 0) return;
  let next = current;
  if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
  else if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = tabs.length - 1;
  else return;
  event.preventDefault();
  const target = tabs[next];
  setView(target.dataset.view);
  target.focus();
}

// Map data-view keys to render functions.
const views = {
  dashboard,
  qa: qaView,
  explorer: explorerView,
  gathering: gatheringView,
  settings: settingsView,
};

export function render() {
  const root = $("#viewRoot");
  root.getAnimations().forEach((animation) => animation.cancel());
  disposeAll();  // release ECharts instances before wiping DOM
  root.innerHTML = (views[state.view] || (() => ""))();
  bindViewEvents();
  // Mount any [data-chart] panels via ECharts after HTML is in the DOM.
  mountViewCharts(root, state.data);
  mountDashboardCharts();
  if (!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
    root.animate(
      [
        { opacity: 0.72, transform: "translateY(6px)" },
        { opacity: 1, transform: "translateY(0)" },
      ],
      { duration: 200, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" },
    );
  }
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
  bindQaEvents();
}
