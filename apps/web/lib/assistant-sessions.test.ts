import { beforeEach, describe, expect, it } from "vitest";

import {
  CURRENT_KEY,
  MAX_MESSAGES,
  MAX_SESSIONS,
  SESSIONS_KEY,
  TITLE_MAX,
  loadCurrentId,
  loadSessions,
  newSessionId,
  normalizeSessions,
  removeSession,
  saveCurrentId,
  saveSessions,
  titleFromMessages,
  toStoredMessages,
  upsertSession,
  type StoredMsg,
  type StoredSession,
} from "@/lib/assistant-sessions";

/**
 * 助手会话历史（`IMP-004`，2026-09-15）。
 *
 * 本文件的判据都指向**用户能看见的行为**，而不是内部结构：
 *  - 刷新后能看到上次的对话（且最后一条不会卡在"思考中"）；
 *  - 列表只留最近 10 条、单会话消息有上界；
 *  - 库里的脏数据不会让整份历史消失；
 *  - 存不下时丢最旧的，而不是静默失效。
 */

function msg(partial: Partial<StoredMsg> & { id: number; role: "user" | "assistant" }): StoredMsg {
  return { content: "", status: "ok", ...partial };
}

function session(partial: Partial<StoredSession> & { id: string }): StoredSession {
  return {
    title: `T-${partial.id}`,
    updatedAt: 0,
    messages: [msg({ id: 1, role: "user", content: `问 ${partial.id}` })],
    ...partial,
  };
}

/** 可设配额的假存储：`setItem` 超过 budget 时抛（模拟 QuotaExceededError）。 */
function makeStore(budget = Number.POSITIVE_INFINITY): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (k: string) => map.get(k) ?? null,
    key: (i: number) => [...map.keys()][i] ?? null,
    removeItem: (k: string) => void map.delete(k),
    setItem: (k: string, v: string) => {
      if (v.length > budget) throw new Error("QuotaExceededError");
      map.set(k, v);
    },
  } as Storage;
}

beforeEach(() => {
  localStorage.clear();
});

describe("toStoredMessages：脏元素丢弃 + 落盘形状收敛", () => {
  it("非数组 / 非对象 / 非法 role / 空正文一律丢弃", () => {
    expect(toStoredMessages(null)).toEqual([]);
    expect(toStoredMessages("nope")).toEqual([]);
    expect(
      toStoredMessages([
        42,
        { id: 1, role: "system", content: "越权角色" },
        { id: 2, role: "user", content: "" }, // 空正文
        { id: 3, role: "assistant", content: "", status: "streaming" }, // 一个字都没吐
        { id: 4, role: "user", content: "留下我" },
      ]),
    ).toEqual([{ id: 4, role: "user", content: "留下我", status: "ok" }]);
  });

  it("streaming 落盘即转 interrupted —— 否则刷新后那条会永远停在“思考中”", () => {
    const [m] = toStoredMessages([
      { id: 1, role: "assistant", content: "半截回答", status: "streaming" },
    ]);
    // 没有 SSE 连接再回来推进它，恢复成"生成中"就是一个永动的活动提示
    expect(m.status).toBe("interrupted");
  });

  it("id 缺失 / 重复 / 非正整数 → 就地重编号（React key 不能撞）", () => {
    const out = toStoredMessages([
      { role: "user", content: "a" },
      { id: 0, role: "assistant", content: "b" },
      { id: 7, role: "user", content: "c" },
      { id: 7, role: "assistant", content: "d" },
    ]);
    const ids = out.map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(ids.every((i) => Number.isInteger(i) && i > 0)).toBe(true);
    // 顺序与内容不能被编号过程打乱
    expect(out.map((m) => m.content)).toEqual(["a", "b", "c", "d"]);
    // 已经合法的 id 保持原值（不要无谓改写）
    expect(ids[2]).toBe(7);
  });

  it("tools / sources 保留，非法项过滤；缺失则不出现在结果里", () => {
    const [m] = toStoredMessages([
      {
        id: 1,
        role: "assistant",
        content: "带溯源",
        status: "ok",
        tools: ["龙虎榜", "", 3 as unknown as string],
        sources: [
          { symbol: "603042", name: "华脉科技", source: "实时快照", as_of: "2026-09-15 07:40" },
          { name: "缺 symbol" }, // 无 symbol ⇒ 丢弃
          "垃圾",
        ],
      },
    ]);
    expect(m.tools).toEqual(["龙虎榜"]);
    expect(m.sources).toEqual([
      { symbol: "603042", name: "华脉科技", source: "实时快照", as_of: "2026-09-15 07:40" },
    ]);

    const [bare] = toStoredMessages([{ id: 1, role: "user", content: "x" }]);
    expect("tools" in bare).toBe(false);
    expect("sources" in bare).toBe(false);
  });

  it("超过单会话消息上界时保留尾部（丢最早的）", () => {
    const many = Array.from({ length: MAX_MESSAGES + 5 }, (_, i) =>
      msg({ id: i + 1, role: "user", content: `第 ${i + 1} 问` }),
    );
    const out = toStoredMessages(many);
    expect(out).toHaveLength(MAX_MESSAGES);
    expect(out[0].content).toBe("第 6 问");
    expect(out[out.length - 1].content).toBe(`第 ${MAX_MESSAGES + 5} 问`);
  });
});

describe("titleFromMessages：首句作标题", () => {
  it("取首句用户提问，折叠空白并截断", () => {
    const long = "一".repeat(TITLE_MAX + 10);
    expect(
      titleFromMessages([
        msg({ id: 1, role: "assistant", content: "先说话的是我" }),
        msg({ id: 2, role: "user", content: "  今天大盘  怎么样？\n顺便看看涨停 "}),
      ]),
    ).toBe("今天大盘 怎么样？ 顺便看看涨停");
    expect(titleFromMessages([msg({ id: 1, role: "user", content: long })])).toBe(
      `${"一".repeat(TITLE_MAX)}…`,
    );
  });

  it("没有用户消息时退回首条内容；全空给“新会话”", () => {
    expect(titleFromMessages([msg({ id: 1, role: "assistant", content: "只有我说" })])).toBe(
      "只有我说",
    );
    expect(titleFromMessages([])).toBe("新会话");
    expect(titleFromMessages([msg({ id: 1, role: "user", content: "   " })])).toBe("新会话");
  });
});

describe("normalizeSessions：脏数据能救的都救", () => {
  it("按 updatedAt 新→旧排序，并截到 MAX_SESSIONS", () => {
    const raw = Array.from({ length: MAX_SESSIONS + 3 }, (_, i) =>
      session({ id: `k${i}`, updatedAt: i }),
    );
    const out = normalizeSessions(raw);
    expect(out).toHaveLength(MAX_SESSIONS);
    expect(out[0].id).toBe(`k${MAX_SESSIONS + 2}`); // 最新的在最前
    expect(out.some((s) => s.id === "k0")).toBe(false); // 最旧的三条被截掉
  });

  it("空会话 / 无 id / id 重复 / 非对象项一律跳过，其余照留", () => {
    const out = normalizeSessions([
      "垃圾",
      null,
      { id: "", updatedAt: 5, messages: [msg({ id: 1, role: "user", content: "无 id" })] },
      { id: "dup", updatedAt: 3, messages: [msg({ id: 1, role: "user", content: "先来的" })] },
      { id: "dup", updatedAt: 9, messages: [msg({ id: 1, role: "user", content: "后来的" })] },
      { id: "empty", updatedAt: 9, messages: [] }, // 空会话不占额度
      { id: "ok", updatedAt: 1, messages: [msg({ id: 1, role: "user", content: "正常" })] },
    ]);
    expect(out.map((s) => s.id)).toEqual(["dup", "ok"]);
    expect(out[0].messages[0].content).toBe("先来的"); // 先到者胜，不被同 id 覆盖
  });

  it("title / updatedAt 缺失时给可用默认值（标题由消息派生）", () => {
    const out = normalizeSessions([
      { id: "a", messages: [msg({ id: 1, role: "user", content: "帮我看看自选" })] },
      { id: "b", title: "   ", updatedAt: "昨天", messages: [msg({ id: 1, role: "user", content: "x" })] },
    ]);
    const a = out.find((s) => s.id === "a");
    const b = out.find((s) => s.id === "b");
    expect(a?.title).toBe("帮我看看自选");
    expect(a?.updatedAt).toBe(0);
    expect(b?.title).toBe("x");
    expect(b?.updatedAt).toBe(0);
  });

  it("非数组输入 → 空列表（不抛）", () => {
    expect(normalizeSessions(undefined)).toEqual([]);
    expect(normalizeSessions({})).toEqual([]);
  });
});

describe("saveSessions / loadSessions：往返与写失败兜底", () => {
  it("写进去的必须能读回来（存/读共用同一套校验）", () => {
    const list = [
      session({
        id: "a",
        updatedAt: 2,
        messages: [
          msg({ id: 1, role: "user", content: "问题" }),
          msg({ id: 2, role: "assistant", content: "回答", status: "ok", tools: ["龙虎榜"] }),
        ],
      }),
      session({ id: "b", updatedAt: 1 }),
    ];
    saveSessions(list, localStorage);
    expect(loadSessions(localStorage)).toEqual(list);
  });

  it("写入时自动排序 + 截断（不依赖调用方先排好）", () => {
    saveSessions(
      Array.from({ length: MAX_SESSIONS + 2 }, (_, i) => session({ id: `s${i}`, updatedAt: i })),
      localStorage,
    );
    const back = loadSessions(localStorage);
    expect(back).toHaveLength(MAX_SESSIONS);
    expect(back[0].updatedAt).toBe(MAX_SESSIONS + 1);
  });

  it("配额写不下时逐条淘汰最旧会话，直到写进去（而不是整份丢或抛异常）", () => {
    const list = Array.from({ length: 4 }, (_, i) => session({ id: `s${i}`, updatedAt: i }));
    const full = JSON.stringify(list).length;
    // 只够放下约一半：必须淘汰到能写进去，且**先丢最旧的**
    const store = makeStore(Math.floor(full / 2));
    expect(() => saveSessions(list, store)).not.toThrow();
    const back = loadSessions(store);
    expect(back.length).toBeGreaterThan(0);
    expect(back.length).toBeLessThan(list.length);
    expect(back.some((s) => s.id === "s3")).toBe(true); // 最新的必须还在
    expect(back.some((s) => s.id === "s0")).toBe(false); // 最旧的先被丢
  });

  it("配额小到一条都放不下（含隐私模式恒抛）→ 静默放弃，不抛不死循环", () => {
    const store = makeStore(0);
    expect(() => saveSessions([session({ id: "a", updatedAt: 1 })], store)).not.toThrow();
    expect(() => saveSessions([], store)).not.toThrow();
  });

  it("库里的 JSON 整体坏掉 → 返回空列表（不抛）", () => {
    localStorage.setItem(SESSIONS_KEY, "{ 不是 JSON");
    expect(loadSessions(localStorage)).toEqual([]);
  });

  it("store 不可用（隐私模式 / 非浏览器）→ 读返空、写静默", () => {
    expect(loadSessions(null)).toEqual([]);
    expect(() => saveSessions([session({ id: "a" })], null)).not.toThrow();
    expect(loadCurrentId(null)).toBeNull();
    expect(() => saveCurrentId("x", null)).not.toThrow();
  });
});

describe("列表操作与当前会话 id", () => {
  it("upsertSession 同 id 覆盖并置顶", () => {
    const list = [session({ id: "a", updatedAt: 1 }), session({ id: "b", updatedAt: 2 })];
    const out = upsertSession(list, session({ id: "a", updatedAt: 3, title: "改过标题" }));
    expect(out.map((s) => s.id)).toEqual(["a", "b"]);
    expect(out[0].title).toBe("改过标题");
    expect(out).toHaveLength(2);
  });

  it("upsertSession 也受条数上界约束", () => {
    const list = Array.from({ length: MAX_SESSIONS }, (_, i) => session({ id: `s${i}`, updatedAt: i }));
    const out = upsertSession(list, session({ id: "new", updatedAt: 99 }));
    expect(out).toHaveLength(MAX_SESSIONS);
    expect(out[0].id).toBe("new");
    expect(out.some((s) => s.id === "s0")).toBe(false);
  });

  it("removeSession 只删指定的一条", () => {
    const list = [session({ id: "a" }), session({ id: "b" })];
    expect(removeSession(list, "a").map((s) => s.id)).toEqual(["b"]);
    expect(removeSession(list, "不存在").map((s) => s.id)).toEqual(["a", "b"]);
  });

  it("当前会话 id 往返；缺失或空白 → null；空白不写入", () => {
    expect(loadCurrentId(localStorage)).toBeNull();
    saveCurrentId("s1", localStorage);
    expect(loadCurrentId(localStorage)).toBe("s1");
    localStorage.setItem(CURRENT_KEY, "   ");
    expect(loadCurrentId(localStorage)).toBeNull();
  });

  it("newSessionId 同一毫秒内也不重复", () => {
    const a = newSessionId(1_700_000_000_000);
    const b = newSessionId(1_700_000_000_000);
    expect(a).not.toBe(b);
    expect(a.startsWith("s")).toBe(true);
  });
});
