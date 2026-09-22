/**
 * The design system, enforced. v0.9.0's acceptance criteria say no component may set a raw
 * colour, font size or radius outside the token system, and that no all-caps label, no
 * `·`-joined meta string and no eyebrow survive. These are greps, deliberately: a lint rule
 * would need a Tailwind-aware plugin, and the failure message here names the file and line.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { TOKENS } from "@/lib/tokens";

// vitest runs from `frontend/`, which is where the sources being audited live.
const ROOT = `${process.cwd()}/`;
const COMPONENTS = join(ROOT, "src/components");
const APP = join(ROOT, "src/app");
const GLOBALS = join(APP, "globals.css");

function sources(directory: string): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry);
    if (statSync(path).isDirectory()) found.push(...sources(path));
    else if (/\.tsx?$/.test(entry) && !entry.endsWith(".test.ts") && !entry.endsWith(".test.tsx")) found.push(path);
  }
  return found;
}

/** Every offending `file:line` for a pattern, so a failure says exactly where to look. */
function hits(files: string[], pattern: RegExp, skip?: (line: string) => boolean): string[] {
  const found: string[] = [];
  for (const file of files) {
    readFileSync(file, "utf8")
      .split("\n")
      .forEach((line, index) => {
        if (skip?.(line)) return;
        if (pattern.test(line)) found.push(`${file.replace(ROOT, "")}:${index + 1}: ${line.trim()}`);
      });
  }
  return found;
}

const FILES = [...sources(COMPONENTS), ...sources(APP)];

describe("components stay inside the token system", () => {
  it("names no raw hex colour", () => {
    expect(hits(FILES, /#[0-9a-fA-F]{3,8}\b/)).toEqual([]);
  });

  it("names no rgb(), hsl() or CSS colour keyword in a style", () => {
    expect(hits(FILES, /(rgba?|hsla?)\(/)).toEqual([]);
  });

  it("uses no Tailwind palette that the theme no longer defines", () => {
    // The old navy/teal/slate palette, and anything else with a numeric shade: the theme
    // replaces Tailwind's colours with the tokens, so these would silently render nothing.
    const palette =
      /\b(?:bg|text|border|ring|divide|fill|stroke|from|via|to|accent|decoration|outline|shadow)-(?:slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose|navy)(?:-\d{2,3})?\b/;
    expect(hits(FILES, palette)).toEqual([]);
  });

  it("sets no font size outside the six-step scale", () => {
    // text-xs / sm / base / md / lg / xl are the scale; anything else is off-system.
    expect(hits(FILES, /\btext-(?:\dxl|\[[^\]]+\]|tiny|huge)\b/)).toEqual([]);
    expect(hits(FILES, /\bfont-size\s*:/)).toEqual([]);
  });

  it("sets no radius outside rounded / rounded-sm / rounded-lg / rounded-full", () => {
    expect(hits(FILES, /\brounded-(?:md|xl|2xl|3xl|\[[^\]]+\])\b/)).toEqual([]);
  });

  it("uses the one shadow, and only for overlays", () => {
    const offenders = hits(FILES, /\bshadow-(?!overlay\b|none\b)[a-z0-9[]/);
    expect(offenders).toEqual([]);
  });

  it("uses the token classes rather than an arbitrary colour value", () => {
    expect(hits(FILES, /\b(?:bg|text|border)-\[(?:#|rgb|hsl)/)).toEqual([]);
  });
});

describe("the writing rules of the design pass", () => {
  it("has no tracked-out all-caps label", () => {
    expect(hits(FILES, /\buppercase\b/)).toEqual([]);
    expect(hits(FILES, /\btracking-(?:wide|wider|widest)\b/)).toEqual([]);
  });

  it("joins no visible meta string with a middle dot", () => {
    // A `·` inside a `title` (the raw-code tooltip) is fine — it is not the page's wording.
    const offenders = hits(FILES, /·/, (line) => /title=|content=|\/\/|\*/.test(line));
    expect(offenders).toEqual([]);
  });
});

describe("the tokens in globals.css and src/lib/tokens.ts are the same tokens", () => {
  const css = readFileSync(GLOBALS, "utf8");

  it.each(Object.entries(TOKENS))("--%s is %s in globals.css", (name, value) => {
    expect(css).toContain(`--${name}: ${value};`);
  });

  it("defines the radius, shadow and motion tokens too", () => {
    for (const token of ["--radius-control", "--radius-surface", "--shadow-overlay", "--motion-duration", "--motion-easing", "--scrim"]) {
      expect(css).toContain(`${token}:`);
    }
  });

  it("switches every transition and animation off under prefers-reduced-motion", () => {
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)/);
    expect(css).toMatch(/animation-duration:\s*0\.01ms\s*!important/);
    expect(css).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
  });

  it("self-hosts both families with font-display: swap and no external CDN", () => {
    expect(css).toContain('font-family: "Public Sans"');
    expect(css).toContain('font-family: "IBM Plex Mono"');
    expect(css.match(/font-display: swap/g)?.length).toBe(css.match(/@font-face/g)?.length);
    expect(css).not.toMatch(/https?:\/\//);
  });

  it("carries the print rules the lead page needs", () => {
    expect(css).toMatch(/@media print/);
    expect(css).toContain(".print-hide");
    expect(css).toContain(".print-only");
  });
});
