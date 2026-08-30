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
      <body style={{ background: "#09090b", color: "#e4e4e7", fontFamily: "system-ui, sans-serif" }}>
        <main style={{ display: "flex", height: "100vh", alignItems: "center", justifyContent: "center" }}>
          <div style={{ maxWidth: 420, textAlign: "center", padding: 24 }}>
            <h2 style={{ fontSize: 16, fontWeight: 600, color: "#f59e0b" }}>应用发生严重错误</h2>
            <p style={{ marginTop: 8, fontSize: 12, color: "#a1a1aa", wordBreak: "break-all" }}>
              {error.message}
            </p>
            <button
              onClick={reset}
              style={{
                marginTop: 16,
                padding: "6px 14px",
                fontSize: 13,
                borderRadius: 6,
                border: "1px solid #3f3f46",
                background: "transparent",
                color: "#e4e4e7",
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
