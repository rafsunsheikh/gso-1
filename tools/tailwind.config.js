/** Mirrors the config that used to live inline in index.html. */
module.exports = {
  content: ["../thecmanager/static/**/*.html", "../thecmanager/**/*.py"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Readex Pro", "system-ui", "-apple-system", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      colors: {
        slate: {
          50: "#f6f5fb", 100: "#edecf3", 200: "#e7e6f0", 300: "#c4c2d6",
          400: "#9a96b3", 500: "#6f6a8f", 600: "#4a4668", 700: "#2e2a4d",
          800: "#211d3d", 900: "#171430", 950: "#0a0717",
        },
        indigo: {
          300: "#b3a0f5", 400: "#9b82f5", 500: "#7c5cf0", 600: "#502ce7",
          700: "#4322c4", 800: "#351c9c", 900: "#241a4d",
        },
      },
    },
  },
};
