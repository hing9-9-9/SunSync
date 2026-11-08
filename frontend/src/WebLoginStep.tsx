import { GoogleMark } from "./GoogleMark";

export function WebLoginGoogleStep({ notice, onGoogle, onCancel }: { notice?: string; onGoogle: () => void; onCancel: () => void }) {
  return (
    <div className="web-login-start">
      <h2>SunSync 시작하기</h2>
      <p className="muted">Google 계정으로 로그인하거나 새로 가입하세요.</p>
      {notice && <p className="auth-notice" role="status">{notice}</p>}
      <button type="button" className="gsi-button" autoFocus onClick={onGoogle}>
        <GoogleMark />
        <span>Google로 계속하기</span>
      </button>
      <button className="secondary" type="button" onClick={onCancel}>
        취소
      </button>
    </div>
  );
}
