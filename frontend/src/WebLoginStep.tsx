import { GoogleButton } from "./GoogleMark";

export function WebLoginGoogleStep({ notice, onGoogle, onCancel }: { notice?: string; onGoogle: () => void; onCancel: () => void }) {
  return (
    <div className="web-login-start">
      <h2>SunSync 시작하기</h2>
      <p className="muted">Google 계정으로 로그인하거나 새로 가입하세요.</p>
      {notice && <p className="auth-notice" role="status">{notice}</p>}
      <GoogleButton autoFocus onClick={onGoogle} />
      <button className="secondary" type="button" onClick={onCancel}>
        취소
      </button>
    </div>
  );
}
