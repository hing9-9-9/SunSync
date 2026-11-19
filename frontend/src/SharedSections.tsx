import type { SpaceApp } from "./useSpaceApp";
import "./ReferencePreview.css";
import "./WorkspaceDesign.css";
import { DeviceHandoff } from "./DeviceHandoff";
import { Card } from "./ShellParts";

/** Sections that are the same in the web and the app layouts. */
export function TempDraftsSection({ app }: { app: SpaceApp }) {
  const { askDeleteTempDraft, resumeTempDraft, tempDrafts } = app;
  return (
      <section className="temp-drafts" aria-labelledby="temp-drafts-title">
        <h2 id="temp-drafts-title">작성 중인 공간</h2>
        {tempDrafts.map((t) => (
          <div className="row" key={t.space.id}>
            <button type="button" className="temp-draft-open" onClick={() => resumeTempDraft(t)}>
              <span>
                {t.space.name || "이름 없는 공간"}
                <small>
                  {[t.space.building, t.space.floor].filter(Boolean).join(" ") || "건물 정보 없음"} · {new Date(t.savedAt).toLocaleDateString("ko-KR", { month: "long", day: "numeric" })} 임시 저장
                </small>
              </span>
              <span className="status-pill todo">작성 중</span>
            </button>
            <button type="button" className="text-button destructive-text" onClick={() => askDeleteTempDraft(t.space.id)}>
              삭제
            </button>
          </div>
        ))}
      </section>
  );
}

export function QrWebGuide({ app }: { app: SpaceApp }) {
  const { deviceSession, handoffLink, measurementReady, receiveMeasurement, setPage, transferSpace } = app;
  return (
      <Card className="qr-web-guide">
        <h2>앱에서 공간 인식</h2>
        <ol className="instruction-list">
          <li>창문 규격·위치 입력 후 필름에 QR 마커 표시</li>
          <li>앱에서 가구 이름과 상판 형태 선택</li>
          <li>형태별 촬영 지점에서 창문 QR 마커를 지점마다 촬영 버튼을 한 번 누르기</li>
          <li>마커가 다 보이지 않거나 흔들리면 앱이 재촬영 안내</li>
          <li>촬영이 끝나면 웹에서 도면 확인 후 가구·보호 영역 편집</li>
        </ol>
        <button
          className="primary"
          onClick={() => transferSpace("app")}
        >
          휴대폰에서 촬영
        </button>
        {deviceSession && (
          <DeviceHandoff
            session={deviceSession}
            onComplete={receiveMeasurement}
          />
        )}
        {measurementReady && (
          <button
            className="primary"
            onClick={() => setPage("protect")}
          >
            측정 완료, 도면 확인
          </button>
        )}
        {handoffLink && (
          <>
            <a className="secondary handoff-link" href={handoffLink}>
              앱 촬영 화면 열기
            </a>
            <label className="field">
              <span>휴대폰에서 열 링크</span>
              <input
                readOnly
                value={handoffLink}
                onFocus={(e) => e.target.select()}
              />
            </label>
          </>
        )}
        <button
          className="secondary"
          onClick={() => setPage("protect")}
        >
          직접 가구 추가·도면 편집
        </button>
      </Card>
  );
}

export function ProcessingCard({ app }: { app: SpaceApp }) {
  const { progress, setPage, setProgress } = app;
  return (
      <Card className="processing">
        <p className="step-label">2/3단계, 휴대폰 사진 분석</p>
        <h2>측정 도면 생성 중</h2>
        <strong className="progress-number">
          {progress}
          <small>%</small>
        </strong>
        <progress
          aria-label="도면 생성 진행률"
          value={progress}
          max={100}
        />
        <p className="muted small">촬영한 가구를 도면에 배치하는 중</p>
        <button
          className="secondary"
          onClick={() => {
            setProgress(0);
            setPage("capture");
          }}
        >
          분석 취소
        </button>
      </Card>
  );
}

