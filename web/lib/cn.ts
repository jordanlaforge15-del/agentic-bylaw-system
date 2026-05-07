// Conditional className join. We don't need clsx/tailwind-merge for the small
// surface this project covers — falsy values are dropped, the rest joined.
export function cn(
  ...parts: Array<string | false | null | undefined>
): string {
  return parts.filter(Boolean).join(" ");
}
