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

      // S1-5（2026-09-11）：列表接口禁用 `getJson<X[]>(...)` 直取。
      // 后端用 `data: null` 表达「无数据」是**合法**形态，而 `getJson` 原样透传
      // ⇒ 调用方拿到 `undefined`，崩溃点漂移到很远的 `.length` / `.map` 处
      // （典型现场：`Cannot read properties of undefined (reading 'length')`）。
      // 列表一律走 `getJsonArray`（null → [] 归一；缺 data 键仍由 request 层抛错，不吞）。
      // 对象信封（`getJson<{items: X[]}>`）不在此规则内——它的失败形态是显式抛错，非静默。
      "no-restricted-syntax": [
        "error",
        {
          selector: "CallExpression[callee.name='getJson'][typeArguments.params.0.type='TSArrayType']",
          message: "列表接口请用 getJsonArray<元素类型>()，不要用 getJson<X[]>()：后端 data:null 会让调用方拿到 undefined（S1-5）。",
        },
      ],
    },
  },
];

export default eslintConfig;
