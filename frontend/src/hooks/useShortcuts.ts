"use client";

import { useEffect } from "react";

export type ShortcutMap = Record<string, () => void>;

const EDITABLE = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** Whether a key press happened while typing: shortcuts never fire then. */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (EDITABLE.has(target.tagName)) return true;
  return target.isContentEditable;
}

/**
 * Single-key shortcuts (`j`, `k`, `a`, `r`, `?`). Ignored while typing in a field, while a
 * modifier is held, and while a dialog is open (the caller passes `enabled=false`).
 */
export function useShortcuts(map: ShortcutMap, enabled = true): void {
  useEffect(() => {
    if (!enabled) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (isTypingTarget(event.target)) return;
      const handler = map[event.key];
      if (!handler) return;
      event.preventDefault();
      handler();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [map, enabled]);
}
