import { vi } from "vitest";

// NewsModal（新闻弹窗）等组件使用 next/navigation 的 useRouter 做实体点击导航。
// jsdom 单测没有 app router 上下文，真实 useRouter 会抛
// "invariant expected app router to be mounted"（E238），导致所有渲染它的测试连带失败。
// 全局 mock 为假实现：生产端 SPA 导航不受影响，单测里 push 只是可断言的 spy。
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    prefetch: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/",
}));
