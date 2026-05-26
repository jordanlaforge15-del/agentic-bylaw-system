import Link from "next/link";
import { ABSLogo } from "@/components/abs-logo";

export default function NotFound() {
  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-surface px-6">
      <div className="max-w-md w-full text-center space-y-6">
        <Link href="/" className="inline-block mb-2" aria-label="ABS home">
          <ABSLogo size={32} />
        </Link>
        <div className="mx-auto w-12 h-[2px] bg-accent" />
        <p className="font-mono text-sm text-text-muted tracking-wider uppercase">
          404
        </p>
        <h1 className="text-3xl font-bold text-text tracking-tight">
          Page not found
        </h1>
        <p className="text-text-muted text-sm leading-relaxed">
          The page you&apos;re looking for doesn&apos;t exist or has been
          moved.
        </p>
        <div className="flex items-center justify-center pt-2">
          <Link
            href="/"
            className="inline-flex items-center justify-center font-sans font-semibold border-[1.5px] px-[18px] py-[11px] text-[13.5px] tracking-[-0.01em] bg-text text-surface border-text transition-[transform,opacity] duration-100"
          >
            Back to home
          </Link>
        </div>
      </div>
    </div>
  );
}
