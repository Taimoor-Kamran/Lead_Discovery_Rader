/**
 * The accessibility floor, as a test helper. v0.9.0 asks for zero **serious or critical**
 * axe violations on the main screens; anything milder is reported in the failure message
 * when a serious one is found, but does not fail on its own.
 */

import axe, { type Result } from "axe-core";

const BLOCKING = new Set(["serious", "critical"]);

/** Rules that cannot be judged inside jsdom, where nothing has a layout or a real colour. */
const NOT_MEANINGFUL_IN_JSDOM = [
  // jsdom computes no colours, so axe cannot measure contrast here. The palette is audited
  // instead, by the numbers, in src/lib/contrast.test.ts.
  "color-contrast",
  // A component rendered on its own has no landmark parent; the page tests cover that.
  "region",
];

export type AxeFinding = { id: string; impact: string; help: string; nodes: string[] };

function describe(results: Result[]): AxeFinding[] {
  return results.map((result) => ({
    id: result.id,
    impact: result.impact ?? "unknown",
    help: result.help,
    nodes: result.nodes.map((node) => node.html),
  }));
}

/**
 * Run axe over a container and return the serious/critical findings. The caller asserts
 * the list is empty, so a failure prints the rule, the impact and the offending markup.
 */
export async function axeViolations(container: Element): Promise<AxeFinding[]> {
  const results = await axe.run(container, {
    rules: Object.fromEntries(NOT_MEANINGFUL_IN_JSDOM.map((id) => [id, { enabled: false }])),
    resultTypes: ["violations"],
  });
  return describe(results.violations.filter((violation) => BLOCKING.has(violation.impact ?? "")));
}
