import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./hooks/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Neutral reading surfaces; financial direction colours remain independent.
        zinc: { 50: "#f8fafc", 100: "#edf1f5", 200: "#dce2ea", 300: "#c8d1dd", 400: "#afbac9", 500: "#7f8d9e", 600: "#536273", 700: "#3b4757", 800: "#29313d", 900: "#191e26", 950: "#0d0f12" },
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
