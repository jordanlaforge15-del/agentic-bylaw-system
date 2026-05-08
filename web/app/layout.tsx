import type { Metadata } from "next";
import { Inter_Tight, JetBrains_Mono } from "next/font/google";
import { ClerkProvider } from "@clerk/nextjs";
import "./globals.css";

const interTight = Inter_Tight({
  variable: "--font-inter-tight",
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700", "800"],
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "ABS° — Agentic Bylaw System",
  description:
    "An expert planner integrated into your workflow. ABS° reads the Halifax Regional Municipality Land Use By-law, applied to your specific parcel.",
};

// Inline pre-paint script: read the saved theme from localStorage and stamp
// data-mode on <html> before the browser computes styles. Without this the
// page would flash in the default mode for one frame whenever a returning
// visitor's saved mode differs from the default.
const themeBootScript = `(function(){try{var m=localStorage.getItem('abs:theme');if(m!=='light'&&m!=='dark'){m='light';}document.documentElement.setAttribute('data-mode',m);}catch(e){document.documentElement.setAttribute('data-mode','light');}})();`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <ClerkProvider
      signInUrl="/sign-in"
      signUpUrl="/sign-up"
      signInFallbackRedirectUrl="/app"
      signUpFallbackRedirectUrl="/app"
    >
      <html
        lang="en"
        data-mode="light"
        className={`${interTight.variable} ${jetbrainsMono.variable}`}
        suppressHydrationWarning
      >
        <head>
          <script dangerouslySetInnerHTML={{ __html: themeBootScript }} />
        </head>
        <body className="bg-surface text-text font-sans">{children}</body>
      </html>
    </ClerkProvider>
  );
}
