import { useState } from "react";
import { guideSteps } from "./guideContent";
import { GuideIllustration } from "./MoodIcons";

// Guide step opened in a sheet over the capture screen. Closing returns to the capture exactly as it was
// (photos, shape and current point stay), so reading help never ends or interrupts the measurement.
const CAPTURE_STEP = 2;

export function CaptureHelp({ onClose }: { onClose: () => void }) {
  const [index, setIndex] = useState(CAPTURE_STEP);
  const step = guideSteps[index];
  return (
    <section className="capture-help" aria-labelledby="capture-help-title">
      <p className="step-label">
        사용 가이드 {index + 1}/{guideSteps.length}
      </p>
      <h2 id="capture-help-title">{step.title}</h2>
      <div className="mobile-guide-visual" aria-hidden="true">
        <GuideIllustration step={index} />
      </div>
      <p>{step.text}</p>
      <p className="muted">{step.detail}</p>
      <div className="guide-pagination">
        <button type="button" className="secondary" disabled={index === 0} onClick={() => setIndex((i) => i - 1)}>
          이전 단계
        </button>
        <button type="button" className="secondary" disabled={index === guideSteps.length - 1} onClick={() => setIndex((i) => i + 1)}>
          다음 단계
        </button>
      </div>
      <button type="button" className="primary" onClick={onClose}>
        촬영으로 돌아가기
      </button>
    </section>
  );
}
