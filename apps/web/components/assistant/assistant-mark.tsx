/**
 * AI 助手品牌 mark：「对话气泡 × 上扬脉冲」。
 *
 * 设计意图（impeccable: 品牌在细节）：
 * - 气泡 = 对话；内部上扬折线 = 行情/分时——"会聊行情的助手"一眼可辨，
 *   替换掉旧版"灯泡天线"（与无数 AI 产品撞脸）与 sky→violet 渐变（AI 俗套配色，
 *   与全站 zinc 骨架 + rose 红涨品牌脱节）。
 * - 纯 stroke + currentColor：颜色由使用处决定（墨玉球反色 / 面板头徽标），
 *   明暗两态自动适配，笔画统一 1.8 圆角端点，缩到 15px 仍清晰。
 */
export function AssistantMark({
  size = 22,
  strokeWidth = 1.8,
  className,
}: {
  size?: number;
  strokeWidth?: number;
  className?: string;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      {/* 气泡轮廓（feather message-circle 骨架，笔画干净留白足） */}
      <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
      {/* 内部脉冲：上扬分时线，重心略偏下贴尾部 */}
      <path d="M7.2 13.2l2.3-3 2.2 3.8 2.4-5.4 2 4.6" />
    </svg>
  );
}
