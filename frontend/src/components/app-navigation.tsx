"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import ThemeToggle from "./theme-toggle";

const links = [
  { href: "/app/find", label: "Find email" },
  { href: "/app/ask", label: "Ask" },
  { href: "/app/commitments", label: "Commitments" },
  { href: "/app/contacts", label: "Contacts" },
  { href: "/app/settings", label: "Settings" },
];

export default function AppNavigation() {
  const pathname = usePathname();

  return (
    <nav className="app-nav" aria-label="Main navigation">
      {links.map(link => (
        <Link key={link.href} href={link.href} aria-current={pathname === link.href ? "page" : undefined}>
          {link.label}
        </Link>
      ))}
      <ThemeToggle />
    </nav>
  );
}
