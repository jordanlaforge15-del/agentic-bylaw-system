// /signup owns its metadata here rather than in page.tsx: the page is a
// client component ("use client") and Next only reads a `metadata` export
// from server components. This layout is the thinnest possible server
// wrapper that carries it (ABS-509).

import type { Metadata } from "next";
import { pageMetadata } from "@/lib/page-metadata";

export const metadata: Metadata = pageMetadata({
  path: "/signup",
  title: "Request an Invite — ABS°",
  description:
    "ABS is in private beta. Request an invite to ask questions about the Halifax Regional Centre Land Use By-law and get sourced, cited readings back.",
});

export default function SignupLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
