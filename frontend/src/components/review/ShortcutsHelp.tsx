"use client";

import { Button, Dialog } from "@/components/ui";

const SHORTCUTS: [string, string][] = [
  ["j", "Next business"],
  ["k", "Previous business"],
  ["a", "Approve the focused opportunity"],
  ["r", "Reject the focused opportunity (asks for a reason)"],
  ["?", "Show this help"],
  ["Esc", "Close a dialog"],
];

export function ShortcutsHelp({ onClose }: { onClose: () => void }) {
  return (
    <Dialog
      title="Keyboard shortcuts"
      size="sm"
      onClose={onClose}
      footer={<Button onClick={onClose}>Close</Button>}
    >
      <dl className="grid grid-cols-[3.5rem_1fr] gap-y-1.5 text-base">
        {SHORTCUTS.map(([key, what]) => (
          <div key={key} className="contents">
            <dt>
              <kbd className="rounded-sm border border-line bg-surface-sunken px-1.5 font-mono text-sm">{key}</kbd>
            </dt>
            <dd>{what}</dd>
          </div>
        ))}
      </dl>
      <p className="text-sm text-ink-soft">Shortcuts never fire while you are typing in a field.</p>
    </Dialog>
  );
}
