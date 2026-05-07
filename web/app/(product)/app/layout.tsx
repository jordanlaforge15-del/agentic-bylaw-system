// Product shell layout. Bypasses the marketing TopNav + Footer entirely.
// The /app route owns its own chrome — see AppHeader inside the page.

export default function AppLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <div className="bg-surface text-text">{children}</div>;
}
