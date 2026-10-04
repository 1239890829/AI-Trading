import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./hooks/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Neutral graphite surfaces keep material separate from financial direction colours.
        zinc: { 50: "#fafafa", 100: "#f1f1f3", 200: "#e3e3e7", 300: "#d2d3d9", 400: "#b0b2bc", 500: "#81848e", 600: "#595d67", 700: "#454852", 800: "#2d3037", 900: "#1c1e23", 950: "#101113" },
        // A 股惯例：红涨绿跌。三档各有**专属角色**，不可互相替代（2026-09-11，P2-19 + P2-24）：
        //
        //   DEFAULT  深色底上的文字 / 图形色（亮，暗底可读）          默认档
        //   deep     **承载白色文字**时的底色（如 bg-up-deep text-white）
        //   ink      **亮色底上的文字**色（draws on zinc-50/100/white）
        //
        // DEFAULT 仅用于暗底；亮底文字用 ink，白字按钮用 deep。
        // 本轮更换亮色前景后，按当前页面合成表面重新核对，不沿用旧色值的比率。
        // 惯例：写 `text-up-ink dark:text-up`——亮色深墨、深色亮色，两侧都达标。
        up: { DEFAULT: "#fda4af", deep: "#e11d48", ink: "#be123c" },
        down: { DEFAULT: "#6ee7b7", deep: "#047857", ink: "#047857" },
      },
    },
  },
  plugins: [],
};

export default config;
