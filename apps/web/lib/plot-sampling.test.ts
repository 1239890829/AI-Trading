import {expect, it} from 'vitest';
import {plotSample} from './plot-sampling';

it('keeps extremes, order and endpoints without changing source records', () => {
  const rows = Array.from({length: 5000}, (_, i) => ({id: String(i), label: String(i), value: Math.sin(i)}));
  rows[17].value = -100;
  rows[4001].value = 90;
  const before = JSON.stringify(rows);
  const displayed = plotSample(rows);
  expect(displayed.length).toBeLessThanOrEqual(240);
  expect(displayed[0]).toBe(rows[0]);
  expect(displayed.at(-1)).toBe(rows.at(-1));
  expect(displayed).toContain(rows[17]);
  expect(displayed).toContain(rows[4001]);
  expect(displayed.map(r => +r.id)).toEqual(displayed.map(r => +r.id).sort((a,b) => a-b));
  expect(JSON.stringify(rows)).toBe(before);
  expect(plotSample(rows.slice(0, 20))).toEqual(rows.slice(0,20));
});

it('never bridges any missing segment, including multiple gaps in one bucket', () => {
  const rows = Array.from({length: 5000}, (_, i) => ({id: String(i), label: String(i), value: i % 5 === 0 ? null : i}));
  expect(plotSample(rows)).toBe(rows);
});
