// /changelog — beta-era placeholder.
//
// Visible release notes will start at GA. During private beta the source
// of truth is the git history and the Linear board (linked below). We do
// not want to publish a polished "v0.x" release log that doesn't match
// the commit log a reader can verify.

import type { Metadata } from "next";
import { pageMetadata } from "@/lib/page-metadata";
import Link from "next/link";
import { Mono } from "@/components/mono";
import { Btn } from "@/components/btn";
import { Page, PageHead } from "@/components/marketing/page-shell";

export const metadata: Metadata = pageMetadata({
  path: "/changelog",
  title: "Changelog — ABS°",
  description:
    "What's changed in ABS. Public release notes begin at general availability; during private beta the product changes daily and the log lives in git.",
});

export default function ChangelogPage() {
  return (
    <Page>
      <PageHead
        kicker="REGISTER · CHANGELOG"
        title="What's changed."
        sub="Public release notes start at GA. ABS is in private beta and changes daily — the authoritative log lives in git."
      />

      <div
        className="border-[1.5px] border-text bg-surface-alt flex flex-col gap-5"
        style={{ padding: "28px 30px" }}
      >
        <Mono accent size={11}>
          STATUS · PRIVATE BETA
        </Mono>
        <h2
          className="m-0 font-sans"
          style={{
            fontSize: 32,
            fontWeight: 700,
            letterSpacing: "-0.03em",
            lineHeight: 1.1,
          }}
        >
          No versioned releases yet.
        </h2>
        <p
          className="m-0 text-text-muted max-w-[640px]"
          style={{ fontSize: 15, lineHeight: 1.55 }}
        >
          ABS is being built against the Halifax Regional Centre Land Use
          By-law. Until the first GA cut we are deliberately not publishing
          a polished release log — too much changes day to day for a
          curated changelog to stay honest.
        </p>
        <div className="flex flex-wrap gap-2.5 pt-2">
          <Link href="/coverage">
            <Btn variant="primary" size="sm">
              What's covered today
            </Btn>
          </Link>
          <Link href="/support">
            <Btn variant="ghost" size="sm">
              Ask what's shipping next
            </Btn>
          </Link>
        </div>
      </div>

      <div
        className="mt-8 border border-dashed border-hair text-text-muted"
        style={{
          padding: "20px 22px",
          fontSize: 13,
          lineHeight: 1.55,
        }}
      >
        <Mono muted size={9.5} className="block mb-2">
          WHAT THIS PAGE WILL BECOME
        </Mono>
        When ABS hits GA this page will turn into an amendment register —
        one entry per release, dated, with the same kind of citation
        discipline we apply to bylaw readings.
      </div>
    </Page>
  );
}
