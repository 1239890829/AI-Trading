import coreWebVitals from "eslint-config-next/core-web-vitals";

const eslintConfig = [
  ...coreWebVitals,
  {
    ignores: [".next/**", "node_modules/**", "out/**", "next-env.d.ts"],
  },
  {
    rules: {
      // 挂账项（retro #3）：Next16 自带的 react-hooks v6 把「effect 里 setState」判为 error。
      // 本项目 16 个页面/组件用的是「effect 里发起请求 → promise 回调里 setState」的经典取数模式，
      // 规则是静态启发式，无法区分「同步 setState 造成级联渲染」与「异步回调写回结果」。
      // 真正的问题（渲染期读写 ref）已逐个修掉；这里降为 warn，
      // 后续若要彻底清零，方向是把取数统一收敛到 use()/Suspense 或引入数据层（React Query），
      // 属于独立重构，不混在版本升级里做。
      "react-hooks/set-state-in-effect": "warn",
    },
  },
];

export default eslintConfig;
