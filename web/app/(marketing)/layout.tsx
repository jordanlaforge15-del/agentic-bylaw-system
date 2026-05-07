// Marketing shell: sticky top nav + footer with content in between. The
// product route lives in a sibling group so it bypasses this chrome
// entirely — see app/(product)/app/layout.tsx.

import { TopNav } from "@/components/top-nav";
import { Footer } from "@/components/footer";

export default function MarketingLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-screen flex flex-col bg-surface text-text">
      <TopNav />
      <main className="flex-1">{children}</main>
      <Footer />
    </div>
  );
}
