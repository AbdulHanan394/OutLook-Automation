"use client";
import { useState } from "react";
import { api } from "../../lib/api";
import ThemeToggle from "../../components/theme-toggle";
export default function Login() {
  const [signingIn, setSigningIn] = useState(false);
  const [error, setError] = useState("");

  async function signIn() {
    setSigningIn(true);
    setError("");
    try {
      const { authorization_url } = await api<{ authorization_url: string }>("/api/auth/login");
      window.location.assign(authorization_url);
    } catch {
      setSigningIn(false);
      setError("Could not start sign-in. Please try again.");
    }
  }

  return (
    <main className="login-layout">
      <ThemeToggle className="login-theme-toggle" />
      <section className="card login-card reveal">
        <div className="login-mark" aria-hidden="true">O</div>
        <h1>Find your Outlook email</h1>
        <p className="page-description">Sign in to connect selected Outlook folders. The app requests read-only mail access.</p>
        <button onClick={signIn} disabled={signingIn}>
          {signingIn ? (
            <>
              <span className="button-spinner" aria-hidden="true" />
              Redirecting to Microsoft…
            </>
          ) : (
            "Sign in with Microsoft"
          )}
        </button>
        {error && <p className="status-message" role="alert">{error}</p>}
      </section>
    </main>
  );
}
