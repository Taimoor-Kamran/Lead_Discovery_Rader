import { cx } from "./cx";

/** A surface: one line, one radius, never a shadow — elevation is for overlays only. */
export function Card({
  className,
  padded = true,
  as: Tag = "section",
  ...rest
}: React.HTMLAttributes<HTMLElement> & { padded?: boolean; as?: "section" | "div" | "article" | "aside" }) {
  return <Tag className={cx("rounded-lg border border-line bg-surface", padded && "p-4", className)} {...rest} />;
}
