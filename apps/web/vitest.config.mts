import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  // 注：vitest 4 默认用 oxc 转译（会自动处理 JSX），不要再设 esbuild 选项，
  // 否则会报 "Both esbuild and oxc options were set... esbuild options will be ignored"
  test: {
    // hooks/ 于 2026-09-11（S2-5）纳入：useResource 的可见性暂停 / 盘外降频 /
    // 三态都是**行为契约**，只能靠钩子级测试钉住，靠组件测试间接覆盖不到。
    include: ["lib/**/*.test.ts", "components/**/*.test.tsx", "hooks/**/*.test.tsx"],
    // 统一 jsdom：组件测试需要 DOM，纯函数测试在 jsdom 下同样可跑
    environment: "jsdom",
    // 全局 mock next/navigation（useRouter 在 jsdom 无 app router 上下文会抛 invariant）
    setupFiles: ["./test/setup.ts"],
  },
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "."),
    },
  },
});
