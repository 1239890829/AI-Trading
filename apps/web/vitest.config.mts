import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  // 注：vitest 4 默认用 oxc 转译（会自动处理 JSX），不要再设 esbuild 选项，
  // 否则会报 "Both esbuild and oxc options were set... esbuild options will be ignored"
  test: {
    include: ["lib/**/*.test.ts", "components/**/*.test.tsx"],
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
