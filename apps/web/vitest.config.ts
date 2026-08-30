import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    // 前端测试只覆盖 lib/ 纯函数（技术指标/格式化）——组件渲染测试待引入 jsdom 后扩展
    include: ["lib/**/*.test.ts"],
    environment: "node",
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
});
