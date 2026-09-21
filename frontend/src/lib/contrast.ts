/**
 * WCAG 2.1 relative luminance and contrast ratio, so the token palette can be audited by a
 * test rather than by eye (docs/design.md, "Contrast audit"). Nothing here renders; it
 * exists so a later change to a token cannot quietly drop text below the legal floor.
 */

/** The floor for body text. Text at 21 px and up may go down to `LARGE_TEXT_MIN`. */
export const BODY_TEXT_MIN = 4.5;
export const LARGE_TEXT_MIN = 3;

function channels(hex: string): [number, number, number] {
  const value = hex.trim().replace("#", "");
  const full =
    value.length === 3
      ? value
          .split("")
          .map((character) => character + character)
          .join("")
      : value;
  if (!/^[0-9a-fA-F]{6}$/.test(full)) throw new Error(`not a hex colour: ${hex}`);
  return [0, 2, 4].map((index) => parseInt(full.slice(index, index + 2), 16) / 255) as [number, number, number];
}

/** WCAG's relative luminance: sRGB channels linearised, then weighted. */
export function relativeLuminance(hex: string): number {
  const [r, g, b] = channels(hex).map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** 1 (identical) to 21 (black on white). Order does not matter. */
export function contrastRatio(foreground: string, background: string): number {
  const [lighter, darker] = [relativeLuminance(foreground), relativeLuminance(background)].sort(
    (a, b) => b - a,
  );
  return (lighter + 0.05) / (darker + 0.05);
}
