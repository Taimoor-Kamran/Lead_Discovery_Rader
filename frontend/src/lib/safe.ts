/**
 * Safety rules for anything that came from a website or a model.
 *
 * Text is always rendered as text (React escapes it; nothing here ever builds HTML).
 * A link is only a link when its URL is plain http or https; anything else — `javascript:`,
 * `data:`, a bare word — is shown as text so a page can never smuggle a click through us.
 */

export function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  const trimmed = value.trim();
  let parsed: URL;
  try {
    parsed = new URL(trimmed);
  } catch {
    return null;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return null;
  return parsed.href;
}

export function hostOf(value: string | null | undefined): string | null {
  const url = safeHttpUrl(value);
  if (!url) return null;
  try {
    return new URL(url).host;
  } catch {
    return null;
  }
}

export const AI_LABEL = "AI-generated — verify before use";
