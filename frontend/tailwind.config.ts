import type { Config } from "tailwindcss";

// Blueprint palette: navy headers, teal accents.
const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: { DEFAULT: "#0f1f3a", 700: "#16305a", 800: "#0f1f3a", 900: "#0a1528" },
        teal: { DEFAULT: "#2dd4bf", 300: "#5eead4", 400: "#2dd4bf", 600: "#0d9488", 700: "#0f766e" },
      },
    },
  },
  plugins: [],
};

export default config;
