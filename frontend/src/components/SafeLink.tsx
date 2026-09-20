import { hostOf, safeHttpUrl } from "@/lib/safe";

/**
 * An external link, but only for a plain http(s) URL. Anything else is printed as text,
 * so a `javascript:` or `data:` URL from a website or a model can never become a click.
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
    return href ? <span className={className}>{href}</span> : null;
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className={className ?? "text-teal-700 underline underline-offset-2 hover:text-teal-600"}
    >
      {children ?? hostOf(url) ?? url}
    </a>
  );
}
