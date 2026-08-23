/** Mirrors the config that used to live inline in index.html. */
module.exports = {
  content: ["../thecmanager/static/**/*.html", "../thecmanager/**/*.py"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Readex Pro", "system-ui", "-apple-system", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      // The existing markup is ~1,500 lines of slate-*/indigo-* utilities.
      // Rather than rewrite every class, the palette itself now points at the
      // design tokens, so those classes follow the active theme unchanged.
      // Channel triplets keep Tailwind's /opacity modifiers working.
      colors: {
        slate: {
          50: "rgb(var(--c-text) / <alpha-value>)",
          100: "rgb(var(--c-text) / <alpha-value>)",
          200: "rgb(var(--c-text) / <alpha-value>)",
          300: "rgb(var(--c-text-2) / <alpha-value>)",
          400: "rgb(var(--c-text-2) / <alpha-value>)",
          500: "rgb(var(--c-text-3) / <alpha-value>)",
          600: "rgb(var(--c-idle) / <alpha-value>)",
          700: "rgb(var(--c-line) / <alpha-value>)",
          800: "rgb(var(--c-card) / <alpha-value>)",
          900: "rgb(var(--c-surface) / <alpha-value>)",
          950: "rgb(var(--c-bg) / <alpha-value>)",
        },
        indigo: {
          300: "rgb(var(--c-primary-text) / <alpha-value>)",
          400: "rgb(var(--c-primary-text) / <alpha-value>)",
          500: "rgb(var(--c-primary-hi) / <alpha-value>)",
          600: "rgb(var(--c-primary) / <alpha-value>)",
          700: "rgb(var(--c-primary) / <alpha-value>)",
          800: "rgb(var(--c-primary) / <alpha-value>)",
          900: "rgb(var(--c-primary) / <alpha-value>)",
        },
      },
    },
  },
};
