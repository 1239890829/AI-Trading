"use client";

/**
 * 根级错误边界（技术评审 F1）：layout 本身崩溃时的最后防线。
 * 注意：根边界不能依赖 layout 提供的主题类，需自带底色，避免白屏。
 */

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="zh-CN">
      <body style={{ background: "#0b121b", color: "#edf4fc", fontFamily: "system-ui, sans-serif" }}>
        <main style={{ display: "flex", minHeight: "100dvh", alignItems: "center", justifyContent: "center" }}>
          <div style={{ maxWidth: "min(420px, 90vw)", textAlign: "center", padding: 24, border: "1px solid #435a70", borderRadius: 16, background: "#121d2b" }}>
            <h2 style={{ fontSize: 16, fontWeight: 600, color: "#f59e0b" }}>应用发生严重错误</h2>
            <p style={{ marginTop: 8, fontSize: 12, color: "#c7d6e6", wordBreak: "break-all" }}>
              {error.message}
            </p>
            <button
              onClick={reset}
              style={{
                marginTop: 16,
                padding: "6px 14px",
                fontSize: 13,
                borderRadius: 6,
                border: "1px solid #435a70",
                background: "transparent",
                color: "#edf4fc",
                cursor: "pointer",
              }}
            >
              重试
            </button>
          </div>
        </main>
      </body>
    </html>
  );
}
