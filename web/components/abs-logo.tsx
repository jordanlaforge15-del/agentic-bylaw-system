// The ABS wordmark: literal "abs" in Inter Tight 800 followed by a small
// accent-coloured rectangle the height of the lowercase x-height. Mark
// proportions (18% width × 78% height of font size, 8% left margin) are
// part of the brand spec — change with care.

type Props = {
  size?: number;
};

export function ABSLogo({ size = 24 }: Props) {
  return (
    <span
      className="inline-flex items-center font-sans font-extrabold leading-none text-text"
      style={{ fontSize: size, letterSpacing: "-0.06em" }}
    >
      <span>abs</span>
      <span
        aria-hidden
        className="inline-block bg-accent"
        style={{
          width: size * 0.18,
          height: size * 0.78,
          marginLeft: size * 0.08,
        }}
      />
    </span>
  );
}
