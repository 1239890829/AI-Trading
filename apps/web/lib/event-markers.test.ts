/** 事件点映射测试：日期对齐/非交易日丢弃/同日合并/重要度/排序/空值安全。 */
import { describe, expect, it } from "vitest";
import { buildEventMarks } from "./event-markers";

const BARS = ["2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28"];

describe("buildEventMarks", () => {
  it("drops items whose date is not a bar date (incl. weekends) or missing", () => {
    const marks = buildEventMarks(
      BARS,
      [
        { title: "交易日公告", date: "2026-08-26" },
        { title: "周六公告", date: "2026-08-30" }, // 非交易日：丢弃不顺延
        { title: "带时间戳", date: "2026-08-27 16:00:00" },
        { title: "无日期", date: null },
      ],
      null,
    );
    expect(marks.map((m) => [m.date, m.kind])).toEqual([
      ["2026-08-26", "公告"],
      ["2026-08-27", "公告"],
    ]);
    expect(marks[1].title).toBe("带时间戳");
  });

  it("merges same-day same-kind items and ORs importance", () => {
    const marks = buildEventMarks(
      BARS,
      [
        { title: "甲公告", date: "2026-08-26", importance: "普通" },
        { title: "乙公告", date: "2026-08-26", importance: "高" },
      ],
      null,
    );
    expect(marks).toHaveLength(1);
    expect(marks[0].important).toBe(true);
    expect(marks[0].title).toBe("甲公告 ／ 乙公告");
  });

  it("keeps 公告 and 新闻 as separate marks on the same day", () => {
    const marks = buildEventMarks(
      BARS,
      [{ title: "公告A", date: "2026-08-26" }],
      [{ title: "新闻B", date: "2026-08-26" }],
    );
    expect(marks.map((m) => m.kind)).toEqual(["公告", "新闻"]);
  });

  it("sorts by date across kinds and tolerates null lists", () => {
    const marks = buildEventMarks(
      BARS,
      [{ title: "公告A", date: "2026-08-27" }],
      [{ title: "新闻B", date: "2026-08-25" }],
    );
    expect(marks.map((m) => m.date)).toEqual(["2026-08-25", "2026-08-27"]);
    expect(buildEventMarks(BARS, null, undefined)).toEqual([]);
  });
});
