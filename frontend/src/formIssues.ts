// Marks wrong fields in place: the label carries the message (shown under the field by CSS) and the
// input gets aria-invalid. Returns the labels and the first input so the caller can summarise and focus.
// `message` is a form-level check (validateSpace); fields whose label text it names are marked too.
// checkValidity=false skips the browser's number checks, for paths where sizes like the window are optional.
export function markInvalidFields(form: HTMLFormElement, message?: string, checkValidity = true) {
  const issues: { label: string; input: HTMLInputElement | HTMLSelectElement }[] = [];
  for (const label of form.querySelectorAll<HTMLLabelElement>("label.field")) {
    const input = label.querySelector<HTMLInputElement | HTMLSelectElement>("input, select");
    const name = label.querySelector("span")?.textContent?.trim() ?? "";
    if (!input || input.disabled || !name) continue;
    const v = input.validity;
    const text = (checkValidity || input.type !== "number") && !v.valid
      ? v.valueMissing
        ? "입력해 주세요."
        : v.rangeUnderflow
          ? `${input.getAttribute("min")} 이상으로 입력해 주세요.`
          : v.rangeOverflow
            ? `${input.getAttribute("max")} 이하로 입력해 주세요.`
            : "형식을 확인해 주세요."
      : message?.includes(name)
        ? message
        : "";
    // NumberField already shows its own message under the field.
    const own = !!label.querySelector(".field-warning");
    if (text && !own) label.dataset.issue = text;
    else delete label.dataset.issue;
    input.setAttribute("aria-invalid", String(!!text));
    if (text) issues.push({ label: name, input });
  }
  return issues;
}

// Typing in a marked field clears its mark.
export function clearFieldIssue(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return;
  const label = target.closest<HTMLLabelElement>("label.field");
  if (!label?.dataset.issue) return;
  delete label.dataset.issue;
  target.setAttribute("aria-invalid", "false");
}
