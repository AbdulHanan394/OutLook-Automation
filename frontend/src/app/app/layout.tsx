import Link from "next/link";
import AppNavigation from "../../components/app-navigation";

/**
 * The header is shared by every screen under /app. The Ask bar is deliberately
 * not here: it needs the selected mailbox and folder, which live in MailClient,
 * and it belongs above the message panes rather than in a page header.
 */
export default function AppLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <>
      <header className="app-header app-header-mail">
        <Link className="app-brand" href="/app/mail">
          <span className="brand-mark" aria-hidden="true">
            M
          </span>
          <span>Mail</span>
        </Link>
        <AppNavigation />
      </header>
      {children}
    </>
  );
}