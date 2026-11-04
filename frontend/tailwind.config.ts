import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}", "./features/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#111111",
        blue: { 50: "#eef5ff", 100: "#dceaff", 600: "#1458c8", 700: "#1048a7" },
        line: "#e4e8ef",
        warm: "#f8f7f4",
      },
      boxShadow: { soft: "0 12px 36px rgba(16,29,54,.07)" },
    },
  },
  plugins: [],
};
export default config;
