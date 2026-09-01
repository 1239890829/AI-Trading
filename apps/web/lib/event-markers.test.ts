/** 事件点映射测试：日期对齐/非交易日丢弃/同日合并/重要度/排序/空值安全。 */
import { describe, expect, it } from "vitest";
import { buildEventMarks, buildMinuteNewsEvents } from "./event-markers";

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

/** 分时事件点：当日过滤/槽区间过滤/同分钟合并/重要度/排序/空值安全。 */
describe("buildMinuteNewsEvents", () => {
  // 伪 UTC 2026-09-01 03:05 = 北京 11:05（ts + 8h）；分时点 ts 恒为 ISO
  const PTS = [{ ts: "2026-09-01T03:05:00Z" }];

  it("keeps only same-day events whose HH:MM falls in slot range", () => {
    const evs = buildMinuteNewsEvents(
      [
        { title: "盘中利好", date: "2026-09-01 10:30" },
        { title: "昨日新闻", date: "2026-08-31 10:30" }, // 非当日：丢弃
        { title: "盘前新闻", date: "2026-09-01 08:15" }, // 早于 09:25：不顺延丢弃
        { title: "午休新闻", date: "2026-09-01 12:00" }, // 午休：丢弃
        { title: "盘后新闻", date: "2026-09-01 15:30" }, // 晚于 15:00：丢弃
        { title: "无日期", date: null },
        { title: "竞价时刻", date: "2026-09-01 09:25" }, // 竞价槽：保留
        { title: "收盘时刻", date: "2026-09-01 15:00" }, // 收盘槽：保留
      ],
      PTS,
    );
    expect(evs.map((e) => e.hhmm)).toEqual(["09:25", "10:30", "15:00"]);
    expect(evs[1].count).toBe(1);
  });

  it("merges same-minute items and ORs importance", () => {
    const evs = buildMinuteNewsEvents(
      [
        { title: "甲", date: "2026-09-01 10:30", importance: "普通" },
        { title: "乙", date: "2026-09-01 10:30", importance: "高" },
      ],
      PTS,
    );
    expect(evs).toHaveLength(1);
    expect(evs[0].count).toBe(2);
    expect(evs[0].important).toBe(true);
    expect(evs[0].title).toBe("甲 ／ 乙");
  });

  it("tolerates empty news / empty points", () => {
    expect(buildMinuteNewsEvents(null, PTS)).toEqual([]);
    expect(buildMinuteNewsEvents([{ title: "x", date: "2026-09-01 10:30" }], [])).toEqual([]);
  });
});
