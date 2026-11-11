import { useEffect, useState } from "react";
import { effectBadges } from "./adaptiveEffect.ts";
import { planAdaptiveShading, userMessage } from "./spaceApi.ts";
import type { AdaptiveMetrics } from "./spaceApi.ts";
import type { Space } from "./model.ts";

type State = { key: string; metrics?: AdaptiveMetrics; error?: string };

/** What the AI film control changes for today, next to the plan it smooths. Asks the server once per saved revision. */
export default function AdaptiveEffect({ space, start }: { space: Space; start?: string }) {
  const sceneId = space.serverSceneId, revision = space.serverRevision;
  const key = sceneId && start ? `${sceneId}:${revision}:${start}` : "";
  const [state, setState] = useState<State | null>(null);
  const [run, setRun] = useState(0);

  useEffect(() => {
    if (!key || !sceneId || !start) return;
    const stop = new AbortController();
    planAdaptiveShading(sceneId, start, stop.signal).then(
      (result) => setState({ key: `${key}:${run}`, metrics: result.metrics }),
      (error) => {
        if (!stop.signal.aborted) setState({ key: `${key}:${run}`, error: userMessage(error, "효과를 계산하지 못했어요.") });
      },
    );
    return () => stop.abort();
  }, [key, run, sceneId, start]);

  if (!key) return null;
  const current = state?.key === `${key}:${run}` ? state : null;
  return (
    <div className="effect-card" role="group" aria-label="AI 필름 제어 효과" aria-live="polite">
      <div className="effect-head">
        <h4>AI 제어 효과</h4>
        <button type="button" className="effect-refresh" aria-label="다시 계산" title="다시 계산" disabled={!current} onClick={() => setRun((n) => n + 1)}>
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.5-5.8M20 4v4.5h-4.5" /></svg>
        </button>
      </div>
      <p className="muted small">기본 계획과 비교한 오늘 하루치예요.</p>
      {current?.metrics ? (
        <div className="effect-badges">
          {effectBadges(current.metrics).map((b) => (
            <div key={b.label} className={`effect-badge${b.better ? " better" : ""}`}>
              <span>{b.label}</span>
              <strong>{b.value}</strong>
            </div>
          ))}
        </div>
      ) : current?.error ? (
        <p className="muted small">{current.error}</p>
      ) : (
        <p className="muted small">계산하는 중이에요. 몇 초 걸려요.</p>
      )}
    </div>
  );
}
