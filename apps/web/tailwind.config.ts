import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./hooks/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // A 股惯例：红涨绿跌。三档各有**专属角色**，不可互相替代（2026-09-11，P2-19 + P2-24）：
        //
        //   DEFAULT  深色底上的文字 / 图形色（亮，暗底可读）          默认档
        //   deep     **承载白色文字**时的底色（如 bg-up-deep text-white）
        //   ink      **亮色底上的文字**色（draws on zinc-50/100/white）
        //
        // 为什么必须三档（都是实测，不是估计）：
        //   · 白字压在 DEFAULT 上不达标——up 3.67:1、down 仅 2.54:1（< 4.5）⇒ 底色要 deep（4.70 / 5.48）
        //   · DEFAULT 作**亮色底上的前景**也不达标——up 3.52:1、down 2.43:1
        //   · deep 作亮色底前景仍不够——up-deep 在 zinc-50 上 4.50 踩线、在 rose/10 淡底上只有 3.95
        //     ⇒ 亮色文字另设 ink：up-ink #be123c (rose-700) 最差 5.28:1；down-ink 与 deep 同值
        //       （#047857 绿色天然够深，最差 4.61:1，无需再设一档）
        //
        // 惯例：写 `text-up-ink dark:text-up`——亮色深墨、深色亮色，两侧都达标。
        up: { DEFAULT: "#f43f5e", deep: "#e11d48", ink: "#be123c" },
        down: { DEFAULT: "#10b981", deep: "#047857", ink: "#047857" },
      },
    },
  },
  plugins: [],
};

export default config;
