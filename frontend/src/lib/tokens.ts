/**
 * The design tokens, mirrored from `src/app/globals.css` so tests can reason about them.
 * `src/test/tokens.test.ts` fails if the two ever disagree, so this is not a second source
 * of truth — it is the same one, checked.
 */

export const TOKENS = {
  ink: "#101826",
  "ink-soft": "#475467",
  "ink-raised": "#1c2534",
  "on-ink-soft": "#c8cdd6",
  paper: "#fbfaf8",
  surface: "#ffffff",
  "surface-sunken": "#f4f3f1",
  line: "#e4e7ec",
  accent: "#0f766e",
  "accent-strong": "#0b5c55",
  "accent-tint": "#e9f3f2",
  warn: "#b54708",
  "warn-tint": "#fef0e6",
  risk: "#b42318",
  "risk-tint": "#fef0ef",
  ok: "#027a48",
  "ok-tint": "#e8f7ef",
} as const;

export type TokenName = keyof typeof TOKENS;

/**
 * Every foreground/background pair the UI actually puts text on. `large` marks a pair that
 * is only ever used at 21 px or more, where WCAG allows 3:1 — there are none today, and
 * the flag exists so that adding one is a deliberate, visible decision.
 */
export const TEXT_PAIRS: readonly { fg: TokenName; bg: TokenName; where: string; large?: boolean }[] = [
  { fg: "ink", bg: "paper", where: "body text on the app background" },
  { fg: "ink", bg: "surface", where: "body text on a card or a table row" },
  { fg: "ink", bg: "surface-sunken", where: "text on a table header or a sunken panel" },
  { fg: "ink-soft", bg: "paper", where: "secondary text on the app background" },
  { fg: "ink-soft", bg: "surface", where: "labels and meta on a card" },
  { fg: "ink-soft", bg: "surface-sunken", where: "table header labels" },
  { fg: "accent", bg: "surface", where: "links and ghost actions on a card" },
  { fg: "accent", bg: "paper", where: "links on the app background" },
  { fg: "accent", bg: "surface-sunken", where: "a link inside a sunken panel" },
  { fg: "accent", bg: "accent-tint", where: "an accent badge" },
  { fg: "surface", bg: "accent", where: "the label of a primary button" },
  { fg: "surface", bg: "accent-strong", where: "a primary button, hovered" },
  { fg: "surface", bg: "ink", where: "the top bar and the skip link" },
  { fg: "surface", bg: "ink-raised", where: "the active navigation item" },
  { fg: "on-ink-soft", bg: "ink", where: "the role under the account email" },
  { fg: "on-ink-soft", bg: "ink-raised", where: "a hovered navigation item" },
  { fg: "ink", bg: "accent-tint", where: "a selected table row" },
  { fg: "warn", bg: "surface", where: "a medium-severity word on a card" },
  { fg: "warn", bg: "warn-tint", where: "a warning badge or banner" },
  { fg: "ink", bg: "warn-tint", where: "the message of a warning banner" },
  { fg: "risk", bg: "surface", where: "an error message or a danger button" },
  { fg: "risk", bg: "risk-tint", where: "a risk badge or an error banner" },
  { fg: "ink", bg: "risk-tint", where: "the message of an error banner" },
  { fg: "ok", bg: "surface", where: "a success word on a card" },
  { fg: "ok", bg: "ok-tint", where: "a success badge" },
  { fg: "ink", bg: "ok-tint", where: "the message of a success toast" },
];
