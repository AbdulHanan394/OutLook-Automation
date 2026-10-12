(() => {
  const storageKey = "email-finder-theme";
  const validThemes = ["light", "dark"];

  function setTheme(theme, persist) {
    const selected = validThemes.includes(theme) ? theme : "light";
    document.documentElement.dataset.theme = selected;
    document.querySelectorAll("[data-theme-toggle]").forEach(button => {
      const nextTheme = selected === "dark" ? "light" : "dark";
      button.setAttribute("aria-label", `Switch to ${nextTheme} theme`);
      const label = button.querySelector("[data-theme-label]");
      if (label) label.textContent = `${nextTheme[0].toUpperCase()}${nextTheme.slice(1)} theme`;
    });
    if (persist) {
      try {
        localStorage.setItem(storageKey, selected);
      } catch {
        // Theme selection still applies for the current page when storage is unavailable.
      }
    }
  }

  let initialTheme = "";
  try {
    initialTheme = localStorage.getItem(storageKey) || "";
  } catch {
    initialTheme = "";
  }
  if (!validThemes.includes(initialTheme)) {
    initialTheme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  setTheme(initialTheme, false);
  document.addEventListener("DOMContentLoaded", () => {
    setTheme(document.documentElement.dataset.theme, false);
  }, { once: true });

  document.addEventListener("click", event => {
    const button = event.target instanceof Element
      ? event.target.closest("[data-theme-toggle]")
      : null;
    if (!button) return;
    const currentTheme = document.documentElement.dataset.theme;
    setTheme(currentTheme === "dark" ? "light" : "dark", true);
  });
})();
