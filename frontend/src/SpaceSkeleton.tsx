import { Card } from "./ShellParts";
import "./SpaceSkeleton.css";

// Placeholders for screens that show spaces while the server's list has not answered yet.
// They reuse the real cards' classes, so each placeholder takes the size of the card it stands in for.

const Bar = ({ w, h = 14 }: { w: string; h?: number }) => <span className="sk-bar" style={{ width: w, height: h }} />;

/** Web "내 공간" list: three cards of .award-room-card. */
export function RoomGridSkeleton() {
  return (
    <div className="award-room-grid sk-grid" role="status" aria-label="공간을 불러오는 중">
      {[0, 1, 2].map((i) => (
        <article className="award-room-card sk-card" key={i} aria-hidden="true">
          <div className={`award-room-visual award-art-${i % 3}`} />
          <div className="award-room-copy sk-copy">
            <Bar w="42%" h={13} />
            <Bar w="64%" h={22} />
            <Bar w="30%" h={13} />
          </div>
        </article>
      ))}
    </div>
  );
}

/** Web home cards (.rp-room-grid). */
export function WorkspaceGridSkeleton() {
  return (
    <div className="rp-room-grid sk-grid" role="status" aria-label="공간을 불러오는 중">
      {[0, 1, 2].map((i) => (
        <div className="rp-room-wrap" key={i} aria-hidden="true">
          <div className="rp-room sk-card">
            <div className={`rp-room-art art-${i % 3}`} />
            <div className="rp-room-copy sk-copy">
              <Bar w="40%" h={13} />
              <Bar w="62%" h={20} />
              <Bar w="28%" h={13} />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

/** App "내 공간" list (.building-list): one building title and one card per section, like the real list. */
export function AppListSkeleton() {
  return (
    <div className="building-list sk-grid" role="status" aria-label="공간을 불러오는 중">
      {[0, 1, 2].map((i) => (
        <section key={i} aria-hidden="true">
          <h2 className="building-title"><Bar w="38%" h={20} /></h2>
          <div className="room-grid">
            <div className="room-entry">
              <div className="room-card sk-card">
                <span className="sk-copy">
                  <Bar w="40%" h={12} />
                  <Bar w="70%" h={20} />
                  <Bar w="55%" h={20} />
                </span>
              </div>
            </div>
          </div>
        </section>
      ))}
    </div>
  );
}

/** Heading text while the open space is not known yet. The line keeps the real heading's line height, so nothing below moves. */
const TextBar = ({ w }: { w: string }) => <span className="sk-text" style={{ width: w }}>{"\u00a0"}</span>;
export const HeadingSkeleton = () => <span role="status" aria-label="공간을 불러오는 중"><TextBar w="9em" /></span>;
export const SubtitleSkeleton = () => <p className="page-subtitle" aria-hidden="true"><TextBar w="9em" /></p>;

/** Web detail: the 3D stage's own empty frame, the same one the lazy 3D chunk shows. */
export const DetailPlanSkeleton = () => (
  <div className="detail-3d">
    <div className="s3 s3-loading" role="status" aria-label="도면을 불러오는 중"><div className="s3-head" /><div className="s3-body"><div className="s3-stage" /></div></div>
  </div>
);

/** App detail: the status card and the plan card, empty. */
export const AppDetailSkeleton = () => (
  <div className="workspace" role="status" aria-label="공간을 불러오는 중">
    <div className="workspace-main" aria-hidden="true">
      <Card className="status-card sk-status"><div className="sk-copy"><Bar w="8em" h={16} /><Bar w="12em" h={13} /></div></Card>
      <Card className="plan-card detail-plan-card"><div className="sk-bar sk-plan" /></Card>
    </div>
  </div>
);

/** App home: the same blocks as the dashboard (today, status, list, guide), empty. */
export const AppHomeSkeleton = () => (
  <div className="home-dashboard sk-grid" role="status" aria-label="공간을 불러오는 중">
    <section className="home-today sk-home-today" aria-hidden="true"><div className="sk-copy"><Bar w="30%" h={14} /><Bar w="100%" h={20} /><Bar w="100%" h={20} /><Bar w="100%" h={20} /></div></section>
    <div className="home-preview-head" aria-hidden="true"><h2 className="section-label">내 공간</h2></div>
    <section className="home-status sk-home-status" aria-hidden="true"><Bar w="60%" h={16} /></section>
    <div className="home-preview sk-home-preview" aria-hidden="true"><div className="sk-copy"><Bar w="45%" h={16} /><Bar w="45%" h={16} /><Bar w="45%" h={16} /></div></div>
    <div className="home-guide sk-home-guide" aria-hidden="true"><Bar w="40%" h={16} /></div>
  </div>
);
