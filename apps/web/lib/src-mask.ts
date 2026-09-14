/**
 * 源码「骨架」掩码器：把注释 / 注释+字符串替换成**等长空白**，只留可执行骨架。
 *
 * 抽为独立模块（2026-09-14 审查批次 B5）：`env-secrecy.test.ts` 与
 * `css-vars.test.ts` 两个**源码级守卫**都需要它。此前它内联在凭据守卫文件里，
 * 而测试文件之间互相 import 会**连带执行对方的 describe**（用例被重复注册），
 * 所以只能抽到非测试模块。
 *
 * 两个面各有用途，不可混用（第一版凭据守卫就栽在这里）：
 * - `maskCommentsAndStrings` —— 「这段代码真的读了密钥变量吗」。字符串里的同名
 *   文字是惰性的，注释里提到更是必然（修复代码必须解释自己改了什么），不掩掉
 *   就会**假红**，而假红的失败信息会引导后人删掉正确的说明文字（KB-ENG-66）。
 * - `maskComments` —— 结构断言（如「源码里是否出现 `var(--x)`」）。此时**必须
 *   保留字符串字面量**：被断言的标识本身可能就是字符串（如 CSS 变量名的内联
 *   注入写法 `"--right-w"`），用上一个面会把自己的目标一起掩掉。
 *
 * 两者都保留长度 ⇒ 行列号与原文一致，失败信息可以直接指到行。
 */

export function maskCommentsAndStrings(src: string): string {
  return mask(src, false);
}

export function maskComments(src: string): string {
  return mask(src, true);
}

function mask(src: string, keepStrings: boolean): string {
  const out = src.split("");
  const n = src.length;
  const blank = (j: number) => {
    out[j] = src[j] === "\n" ? "\n" : " ";
  };
  let i = 0;
  while (i < n) {
    const c = src[i];
    const next = src[i + 1];
    if (c === "/" && next === "/") {
      while (i < n && src[i] !== "\n") blank(i++);
    } else if (c === "/" && next === "*") {
      blank(i++);
      blank(i++);
      while (i < n && !(src[i] === "*" && src[i + 1] === "/")) blank(i++);
      if (i < n) {
        blank(i++);
        blank(i++);
      }
    } else if (c === '"' || c === "'" || c === "`") {
      // 字符串必须被「识别并整体跳过」，哪怕不掩码：否则 `"http://x"` 里的 `//`
      // 会被当成行注释，把该行后面的真实代码一并掩掉（漏报）。
      const quote = c;
      const start = i;
      i++;
      while (i < n && src[i] !== quote) {
        if (src[i] === "\\") {
          i += 2;
          continue;
        }
        i++;
      }
      if (i < n) i++;
      if (!keepStrings) for (let k = start; k < i; k++) blank(k);
    } else {
      i++;
    }
  }
  return out.join("");
}

/** 失败信息用的行号定位。 */
export function lineOf(src: string, index: number): number {
  let line = 1;
  for (let i = 0; i < index && i < src.length; i++) if (src[i] === "\n") line++;
  return line;
}
