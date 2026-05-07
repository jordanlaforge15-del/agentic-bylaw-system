// Marketing section shell. Top-bordered container with kicker label, an
// optional H2, and arbitrary children. Two widths — wide (1340px default)
// and narrow (980px for text-heavy sections).

import { Mono } from "./mono";

type Props = {
  kicker: string;
  title?: React.ReactNode;
  narrow?: boolean;
  children: React.ReactNode;
};

export function Section({ kicker, title, narrow, children }: Props) {
  return (
    <section
      className="border-t border-hair px-8 py-14 mx-auto"
      style={{ maxWidth: narrow ? 980 : 1340 }}
    >
      <div className="flex items-center gap-3.5 mb-[22px]">
        <Mono muted>{kicker}</Mono>
        <div className="flex-1 h-px bg-hair" />
      </div>
      {title && (
        <h2
          className="font-sans font-bold text-[48px] leading-[1.05] m-0 mb-7 max-w-[720px]"
          style={{ letterSpacing: "-0.035em" }}
        >
          {title}
        </h2>
      )}
      {children}
    </section>
  );
}
