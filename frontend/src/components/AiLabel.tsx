import { Badge } from "@/components/ui";
import { AI_LABEL } from "@/lib/safe";

/** The label every AI-generated field carries. Never omitted, never reworded. */
export function AiLabel({ className }: { className?: string }) {
  return (
    <Badge tone="warn" className={className} data-testid="ai-label">
      <span aria-hidden="true">✦</span> {AI_LABEL}
    </Badge>
  );
}
