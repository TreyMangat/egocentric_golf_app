/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        // Distinctive choices over Inter — JetBrains Mono for numerics, Fraunces for display
        mono: ["JetBrains Mono", "ui-monospace", "monospace"],
        display: ["Fraunces", "ui-serif", "serif"],
        sans: ["Söhne", "ui-sans-serif", "system-ui"],
      },
      colors: {
        ink: {
          950: "#0a0d0f",
          900: "#10141a",
          800: "#161b22",
          700: "#1f2630",
          600: "#2a323e",
          500: "#475160",
          400: "#7a8497",
          300: "#a4adbe",
          200: "#cad1de",
          100: "#e8ebf2",
        },
        signal: {
          green: "#9ce28a",
          amber: "#f3c969",
          red: "#ff6b6b",
        },
        accent: "#d4ff5a", // sharp single accent — used sparingly
      },
      letterSpacing: {
        wider2: "0.18em",
      },
    },
  },
};
