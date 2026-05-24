import type { Metadata } from "next";
import { JetBrains_Mono, IBM_Plex_Mono } from "next/font/google";
import { NmShell } from "./components/nm-shell";
import "./nm-styles.css";

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-nm-jetbrains",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  display: "swap",
});

const ibmPlexMono = IBM_Plex_Mono({
  variable: "--font-nm-plex",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Night Manager — Mission Console",
};

export default function NmLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className={`${jetbrainsMono.variable} ${ibmPlexMono.variable}`}>
      <NmShell>{children}</NmShell>
    </div>
  );
}
