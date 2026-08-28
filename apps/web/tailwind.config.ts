import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./hooks/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        up: "#f43f5e", // A 股惯例：红涨
        down: "#10b981", // 绿跌
      },
    },
  },
  plugins: [],
};

export default config;
