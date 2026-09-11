import { beforeEach, describe, expect, it } from "vitest";

import {
  __resetPrefsCache,
  countUnread,
  getPrefsSnapshot,
  isCleared,
  isUnread,
  loadClearBefore,
  loadReadState,
  parseTs,
  setPrefs,
  withAllRead,
  withRead,
  type ReadState,
} from "@/lib/notification-read";

/**
 * 回归背景（用户 2026-09-11 实测）：
 * 「一键已读后计数消失，但有新的一条却是在之前的累积上累加」。
 * 根因 = 已读水位存成 `toISOString()`（UTC 带 T），条目 ts 是北京 naive（带空格），
 * 两者做**字面比较** → 当天条目恒被判已读、跨日又整天一起计入未读。
 * 本文件把「必须先转 epoch 再比大小」这件事钉死。
 */

const BJ = (s: string) => `2026-09-11 ${s}`; // 后端 ts 形态：北京 naive

beforeEach(() => {
  localStorage.clear();
  __resetPrefsCache();
});

describe("parseTs", () => {
  it("北京 naive 字符串按 UTC+8 解释（不是本地时区）", () => {
    // 12:35 北京 = 04:35 UTC
    expect(parseTs(BJ("12:35:00"))).toBe(Date.UTC(2026, 8, 11, 4, 35, 0));
    // 00:30 北京 = 前一天 16:30 UTC（跨界不能错）
    expect(parseTs(BJ("00:30:00"))).toBe(Date.UTC(2026, 8, 10, 16, 30, 0));
  });

  it("带 Z / 偏移的 ISO 按绝对时刻解析", () => {
    expect(parseTs("2026-09-11T04:35:00.000Z")).toBe(Date.UTC(2026, 8, 11, 4, 35, 0));
    expect(parseTs("2026-09-11T12:35:00+08:00")).toBe(Date.UTC(2026, 8, 11, 4, 35, 0));
  });

  it("两种格式**可比较**——这正是旧实现栽的地方", () => {
    const watermark = parseTs("2026-09-11T04:31:38.000Z"); // 一键已读写的
    const newer = parseTs(BJ("12:35:00")); // 之后来的新条目（04:35 UTC）
    const older = parseTs(BJ("12:29:00")); // 水位之前的条目（04:29 UTC）
    expect(watermark).not.toBeNull();
    expect(newer! > watermark!).toBe(true);
    expect(older! > watermark!).toBe(false);
    // 旧实现用的字面比较会得出相反结论（当天条目一律"已读"）
    expect(BJ("12:35:00") > "2026-09-11T04:31:38.000Z").toBe(false);
  });

  it("多种容忍格式与非法输入", () => {
    expect(parseTs(BJ("09:31"))).toBe(Date.UTC(2026, 8, 11, 1, 31, 0)); // 无秒
    expect(parseTs("2026-09-11")).toBe(Date.UTC(2026, 8, 10, 16, 0, 0)); // 仅日期 → 北京零点
    expect(parseTs(null)).toBeNull();
    expect(parseTs("")).toBeNull();
    expect(parseTs("garbage-a")).toBeNull();
    expect(parseTs("garbage-b")).toBeNull();
  });
});

describe("未读判定", () => {
  const items = [
    { id: "a", ts: BJ("09:35:00") }, // 旧
    { id: "b", ts: BJ("12:35:00") }, // 新
  ];

  it("水位之前的算已读，之后的算未读", () => {
    const state: ReadState = { seenBefore: Date.UTC(2026, 8, 11, 3, 0), readIds: [] }; // 11:00 北京
    expect(isUnread(items[0], state)).toBe(false);
    expect(isUnread(items[1], state)).toBe(true);
    expect(countUnread(items, state)).toBe(1);
  });

  it("逐条已读：只影响该条，且重复标记不再增长", () => {
    const base: ReadState = { seenBefore: 0, readIds: [] };
    const once = withRead(base, items[1]);
    expect(isUnread(items[1], once)).toBe(false);
    expect(isUnread(items[0], once)).toBe(true);
    expect(withRead(once, items[1])).toBe(once); // 引用不变 = 无多余渲染
  });

  it("水位已覆盖的条目不再登记 id（readIds 不无限增长）", () => {
    const state: ReadState = { seenBefore: Date.UTC(2026, 8, 11, 5, 0), readIds: [] };
    expect(withRead(state, items[0])).toBe(state);
  });

  it("ts 缺失按未读（宁可多提醒，不静默吞）", () => {
    expect(isUnread({ id: "x", ts: null }, { seenBefore: Date.now(), readIds: [] })).toBe(true);
  });
});

describe("一键已读语义（用户 bug 的正面案例）", () => {
  it("已读后：旧条目已读、当天新条目仍未读，且不会把旧条目一起计入", () => {
    const items = [
      { id: "old-1", ts: BJ("09:40:00") },
      { id: "old-2", ts: BJ("10:20:00") },
    ];
    // 用户在 12:31 点了「全部已读」
    const now = Date.UTC(2026, 8, 11, 4, 31, 38);
    const read = withAllRead({ seenBefore: 0, readIds: [] }, now);
    expect(countUnread(items, read)).toBe(0);

    // 12:35 来了一条新的
    const after = [...items, { id: "new-1", ts: BJ("12:35:00") }];
    expect(countUnread(after, read)).toBe(1); // 既不是 0（旧实现的当天盲区），也不是 3（累积）
    expect(isUnread({ id: "new-1", ts: BJ("12:35:00") }, read)).toBe(true);
  });

  it("重复点「全部已读」幂等（水位只前进不后退）", () => {
    const a = withAllRead({ seenBefore: 0, readIds: ["x"] }, 1000);
    const b = withAllRead(a, 500);
    expect(b.seenBefore).toBe(1000);
    expect(b.readIds).toEqual([]); // 水位覆盖一切更早条目 → 逐条表清空
  });
});

describe("清除水位", () => {
  it("早于清除时刻的条目隐藏，之后的正常显示", () => {
    const cb = Date.UTC(2026, 8, 11, 3, 0); // 11:00 北京
    expect(isCleared({ id: "a", ts: BJ("10:00:00") }, cb)).toBe(true);
    expect(isCleared({ id: "b", ts: BJ("12:00:00") }, cb)).toBe(false);
    expect(isCleared({ id: "c", ts: null }, cb)).toBe(false);
  });

  it("已清除的条目不计入未读", () => {
    const state: ReadState = { seenBefore: 0, readIds: [] };
    const items = [
      { id: "cleared", ts: BJ("10:00:00") },
      { id: "kept", ts: BJ("12:00:00") },
    ];
    expect(countUnread(items, state)).toBe(2);
    expect(countUnread(items, state, Date.UTC(2026, 8, 11, 3, 0))).toBe(1);
  });
});

describe("旧键迁移", () => {
  it("lastSeenTs（UTC ISO）解析成 epoch 落新键，旧键删除", () => {
    localStorage.setItem("ashare.notifications.lastSeenTs", "2026-09-11T04:31:38.000Z");
    const state = loadReadState();
    expect(state.seenBefore).toBe(Date.UTC(2026, 8, 11, 4, 31, 38));
    expect(localStorage.getItem("ashare.notifications.lastSeenTs")).toBeNull();
    expect(JSON.parse(localStorage.getItem("ashare.notifications.read")!)).toEqual(state);
  });

  it("clearBeforeTs 同样迁移为数字", () => {
    localStorage.setItem("ashare.notifications.clearBeforeTs", "2026-09-11 11:00:00");
    expect(loadClearBefore()).toBe(Date.UTC(2026, 8, 11, 3, 0));
    expect(localStorage.getItem("ashare.notifications.clearBeforeTs")).toBeNull();
  });

  it("新键存在时不读旧键；脏数据不抛异常", () => {
    setPrefs({ read: { seenBefore: 42, readIds: ["z"] }, clearBefore: 7 });
    localStorage.setItem("ashare.notifications.lastSeenTs", "2026-09-11T04:31:38.000Z");
    localStorage.setItem("ashare.notifications.read", "{not json");
    expect(loadReadState()).toEqual({ seenBefore: 0, readIds: [] }); // 脏数据 → 空状态，不炸

    localStorage.setItem("ashare.notifications.read", JSON.stringify({ seenBefore: 1.5, readIds: "no" }));
    expect(loadReadState()).toEqual({ seenBefore: 1.5, readIds: [] });
  });
});

describe("外部存储快照", () => {
  it("同一状态返回同一引用（useSyncExternalStore 的稳定性要求）", () => {
    const a = getPrefsSnapshot();
    expect(getPrefsSnapshot()).toBe(a);
    setPrefs({ read: { seenBefore: 9, readIds: [] }, clearBefore: 0 });
    const b = getPrefsSnapshot();
    expect(b).not.toBe(a);
    expect(b.read.seenBefore).toBe(9);
  });
});
