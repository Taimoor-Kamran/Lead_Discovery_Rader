import { cx } from "@/components/ui";
import { hostOf, safeHttpUrl } from "@/lib/safe";

/**
 * An external link, but only for a plain http(s) URL. Anything else is printed as text,
 * so a `javascript:` or `data:` URL from a website or a model can never become a click.
 * A URL is evidence, so it is set in the evidence face (see docs/design.md).
 */
export function SafeLink({
  href,
  children,
  className,
}: {
  href: string | null | undefined;
  children?: React.ReactNode;
  className?: string;
}) {
  const url = safeHttpUrl(href);
  if (!url) {
    return href ? <span className={cx("font-mono", className)}>{href}</span> : null;
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className={cx(
        "rounded font-mono text-accent underline underline-offset-2 hover:text-accent-strong",
        className,
      )}
    >
      {children ?? hostOf(url) ?? url}
    </a>
  );
}
