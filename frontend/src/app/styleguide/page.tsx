import { notFound } from "next/navigation";

export const dynamic = "force-dynamic";
export const metadata = {
  robots: {
    index: false,
    follow: false,
  },
};

const themes = ["dark", "light"] as const;

export default function StyleguidePage() {
  if (process.env.NODE_ENV === "production" || process.env.ENABLE_STYLEGUIDE === "false") {
    notFound();
  }

  return (
    <main className="styleguide-page">
      <div className="page-intro">
        <h1>Interface styleguide</h1>
        <p className="page-description">Development-only component and state preview.</p>
      </div>
      <div className="styleguide-panels">
        {themes.map(theme => (
          <section className="styleguide-theme" data-theme={theme} key={theme}>
            <h2>{theme === "dark" ? "Dark theme" : "Light theme"}</h2>
            <h3>Typography and links</h3>
            <p>Primary label <span className="muted">Secondary description</span></p>
            <p><a href="#components">Underlined inline link</a></p>
            <h3>Buttons</h3>
            <div className="styleguide-inline">
              <button type="button">Primary</button>
              <button className="button-secondary" type="button">Secondary</button>
              <button type="button" disabled>Disabled</button>
            </div>
            <h3>Inputs and panels</h3>
            <input aria-label="Styleguide sample input" placeholder="Search your inbox" />
            <article className="card styleguide-card">
              <strong>Best match</strong>
              <p className="muted">Flat surface with a hairline frame.</p>
            </article>
          </section>
        ))}
      </div>
    </main>
  );
}
