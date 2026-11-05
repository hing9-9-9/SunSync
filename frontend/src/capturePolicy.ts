/** A query string or mobile layout is never evidence of a native camera. */
export function allowBrowserCapture(development: boolean, testFlag: string | undefined): boolean {
  return development && testFlag === "true";
}
