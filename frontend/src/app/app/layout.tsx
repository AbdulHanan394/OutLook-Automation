import Link from "next/link";
import AppNavigation from "../../components/app-navigation";

export default function AppLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <>
      <header className="app-header">
        <Link className="app-brand" href="/app/find">
          <span className="brand-mark" aria-hidden="true">O</span>
          <span>Outlook Email Finder</span>
        </Link>
        <AppNavigation />
      </header>
      {children}
    </>
  );
}
