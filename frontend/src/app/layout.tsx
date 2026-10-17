import "./globals.css";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/ibm-plex-mono/700.css";
import { ThemeScript } from "../components/theme-script";

// Every document must be rendered per request so that the CSP nonce minted in
// middleware can be stamped onto Next's inline RSC payload. A prerendered
// static page is baked at build time, has no nonce to apply, and is therefore
// blocked by `script-src 'self' 'nonce-...'` -- which is the state this app was
// in before, where hydration never ran at all.
export const dynamic = "force-dynamic";

export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" data-theme="light">
      <body>
        <ThemeScript />
        {children}
      </body>
    </html>
  );
}
