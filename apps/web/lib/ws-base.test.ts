import {afterEach, expect, it, vi} from "vitest";

afterEach(() => {vi.unstubAllEnvs(); vi.unstubAllGlobals(); vi.resetModules();});

async function base(page: string, configured = "", apiBase = "/backend") {
  const location = new URL(page);
  vi.stubGlobal("window", {location});
  vi.stubEnv("NEXT_PUBLIC_WS_BASE", configured);
  vi.stubEnv("NEXT_PUBLIC_API_BASE", apiBase);
  vi.resetModules();
  return (await import("./api/client")).wsBase();
}

it("uses the frontend origin on a LAN phone when an old build has a loopback WS override", async () => {
  expect(await base("http://192.168.1.50:3000/workbench", "ws://127.0.0.1:8000")).toBe("ws://192.168.1.50:3000/backend");
  expect(await base("http://192.168.1.50:3000/workbench", "ws://localhost:8000")).toBe("ws://192.168.1.50:3000/backend");
});

it("preserves an explicitly configured non-loopback WS service", async () => {
  expect(await base("http://192.168.1.50:3000", "ws://192.168.1.50:8000")).toBe("ws://192.168.1.50:8000");
  expect(await base("https://app.example", "wss://quotes.example/backend")).toBe("wss://quotes.example/backend");
});

it("keeps a local loopback override valid for a local browser", async () => {
  expect(await base("http://127.0.0.1:3000", "ws://127.0.0.1:8000")).toBe("ws://127.0.0.1:8000");
});

it("uses the secure frontend origin instead of mixed content or a phone's direct loopback API address", async () => {
  expect(await base("https://app.example", "ws://app.example:8000")).toBe("wss://app.example/backend");
  expect(await base("http://192.168.1.50:3000", "", "http://127.0.0.1:8000")).toBe("ws://192.168.1.50:3000/backend");
});

it("does not accept credentials or query tokens in a configured WS base", async () => {
  expect(await base("https://app.example", "wss://user:password@quotes.example")).toBe("wss://app.example/backend");
  expect(await base("https://app.example", "wss://quotes.example?token=fixture")).toBe("wss://app.example/backend");
});

it("defaults to the same frontend host and retains its port for desktop and mobile", async () => {
  expect(await base("http://127.0.0.1:3000/workbench")).toBe("ws://127.0.0.1:3000/backend");
  expect(await base("https://app.example:444/workbench")).toBe("wss://app.example:444/backend");
});

it("normalizes a relative API prefix without interpreting a double slash prefix as another host", async () => {
  expect(await base("http://192.168.1.50:3000", "", "/backend/")).toBe("ws://192.168.1.50:3000/backend");
  expect(await base("http://192.168.1.50:3000", "", "backend/")).toBe("ws://192.168.1.50:3000/backend");
  expect(await base("http://192.168.1.50:3000", "", "//other.example")).toBe("ws://192.168.1.50:3000/backend");
});
