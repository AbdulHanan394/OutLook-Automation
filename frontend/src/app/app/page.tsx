import Link from "next/link";
import { redirect } from "next/navigation";

/**
 * The mail client is the main screen. `/app/find` and `/app/ask` still exist --
 * they carry saved searches, pinning, feedback and timeline answers that the
 * Ask bar does not replace -- but they are no longer the entry point.
 */
export default function AppIndex() {
  redirect("/app/mail");
  return (
    <main>
      <Link href="/app/mail">Open mail</Link>
    </main>
  );
}