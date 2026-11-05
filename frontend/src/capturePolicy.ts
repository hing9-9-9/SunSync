/** The mobile screen stands in for the SunSync app until a native app ships (team decision 2026-11-05),
 *  so it captures in every build; the desktop web captures only in an opted-in development build. */
export function allowBrowserCapture(development: boolean, testFlag: string | undefined, mobile = false): boolean {
  return mobile || (development && testFlag === "true");
}
