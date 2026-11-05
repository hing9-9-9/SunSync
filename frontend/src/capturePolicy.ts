/** The mobile screen stands in for the SunSync app until a native app ships (team decision 2026-11-05),
 *  so it captures in every build. The desktop web never captures: photos come from the phone. */
export function allowBrowserCapture(mobile: boolean): boolean {
  return mobile;
}
