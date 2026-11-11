/** The build id shown on the account screens, so a person can tell which version is running. */
export function buildLabel(id: string | undefined) {
  const value = (id ?? "").trim();
  return value ? `버전 ${value}` : "버전 개발용";
}
