// pipeline.js — pipeline run audit (skeleton; full implementation in M2-frontend task).
// Real polling, step status, logs, and retry controls are added in the next task.

export function pipelineView() {
  return `
    <div class="pipeline-layout">
      <section class="panel">
        <div class="panel-head"><h2>Pipeline</h2></div>
        <p class="helper-text">Run audit, step status, logs, and retry controls will appear here once a run is active.</p>
        <p id="pipelinePlaceholder" class="empty-hint">Start an analysis from the <strong>Data Gathering</strong> tab to see run details.</p>
      </section>
    </div>
  `;
}

// No events to bind yet — placeholder for M2-frontend.
export function bindPipelineEvents() {}
