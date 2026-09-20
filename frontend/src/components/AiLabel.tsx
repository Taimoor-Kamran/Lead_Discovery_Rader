import { AI_LABEL } from "@/lib/safe";

/** The label every AI-generated field carries. Never omitted, never reworded. */
export function AiLabel({ className }: { className?: string }) {
  return (
    <span
      className={`chip border-amber-300 bg-amber-50 text-amber-900 ${className ?? ""}`}
      data-testid="ai-label"
    >
      <span aria-hidden="true">✦</span> {AI_LABEL}
    </span>
  );
}
