import { FlatCompat } from "@eslint/eslintrc";

/**
 * ESLint 9 flat config.
 *
 * `eslint-config-next` is still published as an eslintrc config, so it is
 * bridged through FlatCompat. Without this file `npm run lint` dropped into
 * Next's interactive setup prompt and exited non-zero.
 */
const compat = new FlatCompat({ baseDirectory: import.meta.dirname });

const config = [
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "playwright-report/**",
      "test-results/**",
      "next-env.d.ts",
    ],
  },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    // Playwright fixture callbacks are named `use` by convention, which trips
    // the React hooks rule. These files are test harness code, not components.
    files: ["e2e/**/*.ts", "playwright.config.ts"],
    rules: {
      "react-hooks/rules-of-hooks": "off",
      "react-hooks/exhaustive-deps": "off",
    },
  },
];

export default config;