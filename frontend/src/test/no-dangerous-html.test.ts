import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? walk(path) : [path];
  });
}

describe("safety rule: page text, evidence and AI output are only ever rendered as text", () => {
  it("no source file uses dangerouslySetInnerHTML or innerHTML", () => {
    const offenders = walk("src")
      .filter((path) => /\.(ts|tsx)$/.test(path) && !path.endsWith(".test.ts"))
      .filter((path) => {
        const text = readFileSync(path, "utf8");
        return text.includes("dangerouslySetInnerHTML") || /\.innerHTML\s*=/.test(text);
      });
    expect(offenders).toEqual([]);
  });
});
