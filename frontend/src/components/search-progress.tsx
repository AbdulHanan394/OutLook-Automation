"use client";

import { useEffect, useRef, useState } from "react";

export type ProgressStep = {
  id: string;
  label: string;
  detail: string;
  /** Elapsed milliseconds before this step becomes active. */
  at: number;
  /** True while this step is the one waiting on the model (shows the AI orb). */
  ai?: boolean;
};

function formatElapsed(ms: number) {
  return `${(ms / 1000).toFixed(1)}s`;
}

/**
 * Staged progress panel for long-running, multi-phase work. The phases come from
 * one blocking request, so the steps are paced against a real elapsed timer
 * rather than fake progress percentages.
 */
export default function SearchProgress({
  steps,
  running,
  heading,
  expectedMs = 4200,
  skeleton = 0,
}: {
  steps: ProgressStep[];
  running: boolean;
  heading: string;
  expectedMs?: number;
  skeleton?: number;
}) {
  const [elapsed, setElapsed] = useState(0);
  const startedAt = useRef<number | null>(null);

  useEffect(() => {
    if (!running) {
      startedAt.current = null;
      return;
    }
    startedAt.current = performance.now();
    setElapsed(0);
    const timer = window.setInterval(() => {
      if (startedAt.current === null) return;
      setElapsed(performance.now() - startedAt.current);
    }, 80);
    return () => window.clearInterval(timer);
  }, [running]);

  if (!running) return null;

  let activeIndex = 0;
  for (let index = 0; index < steps.length; index += 1) {
    if (elapsed >= steps[index].at) activeIndex = index;
  }
  const active = steps[activeIndex];
  const percent = Math.min(92, (elapsed / expectedMs) * 100);
  const doneCount = activeIndex;

  return (
    <section className="card progress-panel reveal" aria-busy="true">
      <span className="sr-only" role="status">
        {heading}. Step {activeIndex + 1} of {steps.length}: {active?.label}
      </span>
      <div className="progress-heading">
        <span className={`ai-orb${active?.ai ? "" : " is-quiet"}`} aria-hidden="true">
          <span className="ai-orb-halo" />
        </span>
        <div className="progress-heading-text">
          <h2>{heading}</h2>
          <p className="muted">
            {active?.label}
            {active?.ai ? <span className="typing-indicator" aria-hidden="true"><span /><span /><span /></span> : null}
          </p>
        </div>
        <span className="progress-timer" aria-hidden="true">{formatElapsed(elapsed)}</span>
      </div>

      <div
        className="progress-track"
        role="progressbar"
        aria-label={heading}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(percent)}
      >
        <div
          className="progress-fill"
          data-indeterminate={active?.ai ? "true" : "false"}
          style={{ width: `${active?.ai ? undefined : percent}%`, transform: active?.ai ? undefined : "scaleX(1)" }}
        />
      </div>

      <ol className="progress-steps" style={{ marginTop: "21px" }}>
        {steps.map((step, index) => (
          <li
            key={step.id}
            className="progress-step"
            data-state={index < doneCount ? "done" : index === activeIndex ? "active" : "pending"}
          >
            <span className="progress-step-indicator" aria-hidden="true">
              {index < doneCount ? "✓" : index + 1}
            </span>
            <div>
              <span className="progress-step-label">{step.label}</span>
              <p className="progress-step-detail">{step.detail}</p>
            </div>
          </li>
        ))}
      </ol>

      {skeleton > 0 ? (
        <div className="stagger" aria-hidden="true">
          {Array.from({ length: skeleton }, (_, index) => (
            <div className="skeleton-card" key={index}>
              <h3 className="skeleton skeleton-line w-60" />
              <div className="skeleton skeleton-line w-40" />
              <div style={{ marginTop: "14px" }}>
                <div className="skeleton skeleton-line w-95" />
                <div className="skeleton skeleton-line w-80" />
                <div className="skeleton skeleton-line w-60" />
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}
