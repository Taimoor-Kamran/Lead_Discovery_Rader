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
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-navy-900/50 p-4" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="shortcuts-title"
        className="w-full max-w-sm rounded-lg border border-slate-200 bg-white p-5 shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <h2 id="shortcuts-title" className="text-lg font-semibold text-navy">Keyboard shortcuts</h2>
        <dl className="mt-3 grid grid-cols-[3rem_1fr] gap-y-1 text-sm">
          {SHORTCUTS.map(([key, what]) => (
            <div key={key} className="contents">
              <dt>
                <kbd className="rounded border border-slate-300 bg-slate-50 px-1.5 font-mono text-xs">{key}</kbd>
              </dt>
              <dd>{what}</dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-xs text-slate-500">Shortcuts never fire while you are typing in a field.</p>
        <button type="button" className="btn-secondary mt-4" onClick={onClose} autoFocus>
          Close
        </button>
      </div>
    </div>
  );
}
