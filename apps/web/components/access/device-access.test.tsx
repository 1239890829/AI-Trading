import {act, cleanup, fireEvent, render, screen, waitFor} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {DeviceAccessGate, MaintenanceBoundary} from "./device-access";
vi.mock("@/components/ui/modal-shell", () => ({ModalShell: ({children}: {children: React.ReactNode}) => <div>{children}</div>}));
afterEach(() => {cleanup();vi.unstubAllGlobals();vi.useRealTimers();});
const response=(body: unknown) => Response.json(body);
it("未配对不会挂载行情与选股消费者", async () => {
  const child=vi.fn(); const Child=() => {child();return <p>行情</p>;};
  vi.stubGlobal("fetch", vi.fn(async () => response({state:"unpaired"})));
  render(<DeviceAccessGate><Child /></DeviceAccessGate>);
  await waitFor(() => expect((screen.getByRole("button",{name:"请求配对"}) as HTMLButtonElement).disabled).toBe(false));
  expect(child).not.toHaveBeenCalled();
});
it("配对请求待批准时只检查状态，批准后才挂载业务", async () => {
  vi.useFakeTimers();
  const fetcher=vi.fn().mockResolvedValueOnce(response({state:"unpaired"}))
    .mockResolvedValueOnce(response({state:"pending",id:"abcdef123456",label:"手机",expires_at:Date.now()/1000+300}))
    .mockResolvedValueOnce(response({state:"active",id:"abcdef123456",expires_at:Date.now()/1000+86400}));
  vi.stubGlobal("fetch",fetcher);
  render(<DeviceAccessGate><p>行情已挂载</p></DeviceAccessGate>);
  await act(async () => {});
  await act(async () => {fireEvent.click(screen.getByRole("button",{name:"请求配对"}));});
  expect(screen.getByText("等待电脑确认")).not.toBeNull();
  expect(screen.queryByText("行情已挂载")).toBeNull();
  await act(async () => {await vi.advanceTimersByTimeAsync(3000);});
  expect(screen.getByText("行情已挂载")).not.toBeNull();
  expect(fetcher).toHaveBeenCalledTimes(3);
});
it("普通会话不能挂载维护消费者", async () => {
  vi.stubGlobal("fetch",vi.fn(async () => response({state:"active",maintenance:false,expires_at:Date.now()/1000+86400})));
  render(<DeviceAccessGate><MaintenanceBoundary><p>维护已挂载</p></MaintenanceBoundary></DeviceAccessGate>);
  expect(await screen.findByText("维护操作需要临时授权")).not.toBeNull();
  expect(screen.queryByText("维护已挂载")).toBeNull();
});
it("撤销事件立即卸载业务，较早的状态响应不能重新放行", async () => {
  let resolve!: (response: Response) => void;
  const fetcher=vi.fn().mockResolvedValueOnce(response({state:"active",expires_at:Date.now()/1000+86400}))
    .mockImplementationOnce(() => new Promise<Response>(r => {resolve=r;}));
  vi.stubGlobal("fetch",fetcher);
  render(<DeviceAccessGate><p>行情已挂载</p></DeviceAccessGate>);
  await screen.findByText("行情已挂载");
  Object.defineProperty(document,"visibilityState",{configurable:true,value:"visible"});
  act(() => document.dispatchEvent(new Event("visibilitychange")));
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  act(() => window.dispatchEvent(new Event("ashare-access-expired")));
  await act(async () => {resolve(response({state:"active",expires_at:Date.now()/1000+86400}));});
  expect(screen.queryByText("行情已挂载")).toBeNull();
  expect(screen.getByRole("button",{name:"请求配对"})).not.toBeNull();
});
it("维护授权到期即使状态读取失败也卸载维护组件", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch",vi.fn().mockResolvedValueOnce(response({state:"active",maintenance:true,maintenance_until:Date.now()/1000+10,expires_at:Date.now()/1000+86400})).mockRejectedValue(new Error("offline")));
  render(<DeviceAccessGate><MaintenanceBoundary><p>维护已挂载</p></MaintenanceBoundary></DeviceAccessGate>);
  await act(async () => {});
  expect(screen.getByText("维护已挂载")).not.toBeNull();
  await act(async () => {await vi.advanceTimersByTimeAsync(10_001);});
  expect(screen.queryByText("维护已挂载")).toBeNull();
  expect(screen.getByText("维护操作需要临时授权")).not.toBeNull();
});
