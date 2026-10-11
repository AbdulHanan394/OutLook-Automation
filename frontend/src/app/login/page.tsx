"use client";
import { api } from "../../lib/api";
export default function Login() {
  async function signIn() {
    const { authorization_url } = await api<{ authorization_url: string }>("/api/auth/login");
    window.location.assign(authorization_url);
  }

  return (
    <main className="login-layout">
      <section className="card login-card">
        <div className="login-mark" aria-hidden="true">O</div>
        <h1>Find your Outlook email</h1>
        <p className="page-description">Sign in to connect selected Outlook folders. The app requests read-only mail access.</p>
        <button onClick={signIn}>Sign in with Microsoft</button>
      </section>
    </main>
  );
}
