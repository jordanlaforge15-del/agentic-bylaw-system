// Display-type highlighter — paints an accent bar behind the wrapped text
// at the bottom of the line. Used in hero / section headlines.

type Props = {
  children: React.ReactNode;
  height?: number; // em units of the bar height; 0.18 by default.
};

export function HighlightWord({ children, height = 0.18 }: Props) {
  return (
    <span className="relative inline-block">
      {children}
      <span
        aria-hidden
        className="absolute left-0 right-0 bg-accent"
        style={{ bottom: "14%", height: `${height}em`, zIndex: -1 }}
      />
    </span>
  );
}
