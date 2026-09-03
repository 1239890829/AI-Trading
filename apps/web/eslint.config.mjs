import coreWebVitals from "eslint-config-next/core-web-vitals";

const eslintConfig = [
  ...coreWebVitals,
  {
    ignores: [".next/**", "node_modules/**", "out/**", "next-env.d.ts"],
  },
  {
    rules: {
      // 挂账项（retro #3）：Next16 自带的 react-hooks v6 把「effect 里 setState」判为 error。
      // 本项目曾用「effect 里发起请求 → promise 回调里 setState」的经典取数模式，规则是静态
      // 启发式，无法区分「同步 setState 造成级联渲染」与「异步回调写回结果」。
      // ✅ 2026-09-03（a155d49）已全量清零：异步取数收敛到 usePollingFetch（latest-ref），
      // 真同步场景改渲染期 adjust-state 模式。保留 warn 作哨兵——新增违例会在门禁中显形。
      "react-hooks/set-state-in-effect": "warn",
    },
  },
];

export default eslintConfig;
