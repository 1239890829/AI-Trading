import {afterEach, beforeEach, expect, it, vi} from "vitest";
import {wsSubprotocols} from "./ws-credential";
import {clearDeviceSessionReads} from "./api/internal";
const request = vi.fn();
beforeEach(() => {request.mockReset(); vi.stubGlobal("fetch", request);});
afterEach(() => vi.unstubAllGlobals());
function reply(subprotocol: unknown) {return {ok: true, json: async () => ({subprotocol})};}
it("每个连接取得独立短效票据，并发也不共享", async () => {
  request.mockResolvedValueOnce(reply("ashare-ticket.first")).mockResolvedValueOnce(reply("ashare-ticket.second"));
  expect(await Promise.all([wsSubprotocols(), wsSubprotocols()])).toEqual([["ashare-ticket.first"],["ashare-ticket.second"]]);
  expect(request).toHaveBeenCalledTimes(2);
  expect(request).toHaveBeenCalledWith("/api/ws-credential", {cache:"no-store"});
});
it.each([null, undefined, "ashare-token.old", {}, "bad value"])("拒绝长期或损坏凭据 %j", async value => {
  request.mockResolvedValueOnce(reply(value)); expect(await wsSubprotocols()).toEqual([]);
});
it("网络失败后下次连接可重试", async () => {
  request.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(reply("ashare-ticket.next"));
  expect(await wsSubprotocols()).toEqual([]); expect(await wsSubprotocols()).toEqual(["ashare-ticket.next"]);
});
it("401 通知根权限门结束业务组件", async () => {
  const listener = vi.fn(); window.addEventListener("ashare-access-expired", listener);
  request.mockResolvedValueOnce({ok:false,status:401});
  expect(await wsSubprotocols()).toEqual([]); expect(listener).toHaveBeenCalledTimes(1);
  window.removeEventListener("ashare-access-expired", listener);
});
it("旧会话的迟到401不撤销新配对", async () => {
  let finish!: (value: {ok: boolean; status: number}) => void;
  const listener = vi.fn(); window.addEventListener("ashare-access-expired", listener);
  try {
    request.mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    const pending = wsSubprotocols(); clearDeviceSessionReads(); finish({ok: false, status: 401});
    expect(await pending).toEqual([]); expect(listener).not.toHaveBeenCalled();
  } finally {window.removeEventListener("ashare-access-expired", listener);}
});
