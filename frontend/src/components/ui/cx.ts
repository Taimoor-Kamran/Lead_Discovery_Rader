/** Join class names, dropping anything falsy. The whole of our "classnames" need. */
export function cx(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(" ");
}
