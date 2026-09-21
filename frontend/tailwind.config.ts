import type { Config } from "tailwindcss";

/**
 * The design tokens of docs/design.md, and nothing else. The default palette, type scale,
 * radii and shadows are **replaced** rather than extended, so `bg-slate-50`, `text-2xl` or
 * `rounded-xl` simply do not exist any more and a component cannot quietly reach past the
 * system. src/test/tokens.test.ts fails the build if one names a raw value instead.
 */
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      inherit: "inherit",
      ink: { DEFAULT: "var(--ink)", soft: "var(--ink-soft)" },
      paper: "var(--paper)",
      surface: { DEFAULT: "var(--surface)", sunken: "var(--surface-sunken)" },
      line: "var(--line)",
      scrim: "var(--scrim)",
      accent: { DEFAULT: "var(--accent)", strong: "var(--accent-strong)", tint: "var(--accent-tint)" },
      warn: { DEFAULT: "var(--warn)", tint: "var(--warn-tint)" },
      risk: { DEFAULT: "var(--risk)", tint: "var(--risk-tint)" },
      ok: { DEFAULT: "var(--ok)", tint: "var(--ok-tint)" },
    },
    // 12 / 13 / 15 / 17 / 21 / 27 px; 1.5 for body, 1.2 for headings.
    fontSize: {
      xs: ["12px", { lineHeight: "1.5" }],
      sm: ["13px", { lineHeight: "1.5" }],
      base: ["15px", { lineHeight: "1.5" }],
      md: ["17px", { lineHeight: "1.4" }],
      lg: ["21px", { lineHeight: "1.2" }],
      xl: ["27px", { lineHeight: "1.2" }],
    },
    fontFamily: {
      sans: ["Public Sans", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "sans-serif"],
      // Evidence, URLs, scores and IDs only — so a reviewer can tell at a glance what came
      // from the website and what is our own wording.
      mono: ["IBM Plex Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
    },
    borderRadius: {
      none: "0",
      sm: "4px",
      DEFAULT: "var(--radius-control)",
      lg: "var(--radius-surface)",
      full: "9999px",
    },
    boxShadow: {
      none: "none",
      // The only elevation in the app, and only overlays may use it.
      overlay: "var(--shadow-overlay)",
    },
    transitionDuration: {
      DEFAULT: "var(--motion-duration)",
      0: "0ms",
    },
    transitionTimingFunction: {
      DEFAULT: "var(--motion-easing)",
    },
    extend: {
      maxWidth: {
        // Running prose never exceeds a comfortable measure; the shell caps the content.
        measure: "72ch",
        shell: "1440px",
      },
      keyframes: {
        // The skeleton's only motion; `prefers-reduced-motion` flattens it to the base tint.
        pulse: { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0.55" } },
      },
      animation: {
        pulse: "pulse 1.6s cubic-bezier(0.4, 0, 0.6, 1) infinite",
      },
    },
  },
  plugins: [],
};

export default config;
