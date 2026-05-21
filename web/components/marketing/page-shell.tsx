// Shared Page wrapper + PageHead used by the secondary marketing routes
// (changelog, coverage, support, about, privacy, terms). Matches the
// design_handoff_marketing_pages spec: max-width 1200, 56px vertical /
// 32px horizontal at desktop, condensed at mobile. PageHead = mono
// kicker + 56px display H1 + muted subtitle, separated from the body by
// a hair underline.

import { Mono } from "@/components/mono";

export function Page({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-5 sm:px-8 py-10 sm:py-12 lg:py-14 mx-auto max-w-[1200px]">
      {children}
    </div>
  );
}

type PageHeadProps = {
  kicker: string;
  title: React.ReactNode;
  sub?: React.ReactNode;
};

export function PageHead({ kicker, title, sub }: PageHeadProps) {
  return (
    <header className="border-b border-hair pb-6 sm:pb-7 mb-8 sm:mb-10 flex flex-col gap-3 sm:gap-3.5">
      <Mono muted size={11}>
        {kicker}
      </Mono>
      <h1
        className="font-sans font-extrabold m-0 text-[36px] sm:text-[48px] lg:text-[56px] leading-[0.98]"
        style={{ letterSpacing: "-0.04em" }}
      >
        {title}
      </h1>
      {sub && (
        <p className="text-text-muted text-[15px] sm:text-[17px] leading-[1.45] m-0 max-w-[620px]">
          {sub}
        </p>
      )}
    </header>
  );
}
