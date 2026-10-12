export default function ThemeToggle({ className = "" }: { className?: string }) {
  return (
    <button
      className={`theme-toggle ${className}`.trim()}
      type="button"
      data-theme-toggle
      aria-label="Switch to dark theme"
    >
      <span aria-hidden="true" data-theme-icon="dark">◐</span>
      <span aria-hidden="true" data-theme-icon="light">◑</span>
      <span data-theme-label>Dark theme</span>
    </button>
  );
}
