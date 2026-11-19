import type { GuideMethod } from "./guideContent";

// Two-way explanation used by the guide (web, app) and the capture help sheet.
export function GuideMethods({ methods }: { methods?: GuideMethod[] }) {
  if (!methods?.length) return null;
  return (
    <ul className="guide-methods">
      {methods.map((method) => (
        <li key={method.title}>
          <strong>{method.title}</strong>
          <span>{method.text}</span>
        </li>
      ))}
    </ul>
  );
}
