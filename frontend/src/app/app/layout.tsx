import Link from "next/link";

export default function AppLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <>
      <header className="app-header">
        <Link className="app-brand" href="/app/find">
          <span className="brand-mark" aria-hidden="true">O</span>
          <span>Outlook Email Finder</span>
        </Link>
        <nav className="app-nav" aria-label="Main navigation">
          <Link href="/app/find">Find email</Link>
          <Link href="/app/ask">Ask</Link>
          <Link href="/app/commitments">Commitments</Link>
          <Link href="/app/contacts">Contacts</Link>
          <Link href="/app/settings">Settings</Link>
        </nav>
      </header>
      {children}
    </>
  );
}
