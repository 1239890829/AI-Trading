"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { ModalShell } from "@/components/ui/modal-shell";
import { IconButton } from "@/components/ui/icon-button";
import { HugeiconsIcon } from "@hugeicons/react";
import Shield01Icon from "@hugeicons/core-free-icons/Shield01Icon";
import "./device-access.css";
import { clearDeviceSessionReads } from "@/lib/api/internal";
import { useExitPresence } from "@/hooks/use-exit-presence";

type Device = {state: "active" | "pending" | "unpaired"; id?: string; label?: string; owner?: boolean; expires_at?: number; maintenance?: boolean; maintenance_until?: number};
type Access = {device: Device | null; refresh: () => Promise<void>; open: () => void};
const Context = createContext<Access>({device: null, refresh: async () => {}, open: () => {}});
const messages: Record<string, string> = {
  device_pairing_required: "请重新配对这台设备。", maintenance_approval_required: "请先取得临时维护授权。",
  owner_approval_required: "请在已配对的管理电脑上操作。", bootstrap_code_invalid: "初始化码已失效或不正确，请在电脑上重新生成。",
  owner_already_paired: "已有管理电脑，请用它批准这台设备。", pairing_queue_full: "待批准设备较多，请先在管理电脑上处理。",
  device_access_requires_server_token: "服务端尚未配置访问凭据，请在电脑上完成启动配置。",
  device_access_unavailable: "暂时无法确认访问权限，请稍后重试。", device_not_found: "这条配对请求已经过期。",
};
async function call<T>(action: string, body?: object): Promise<T> {
  const response = await fetch(`/api/access/${action}`, {method: body ? "POST" : "GET", cache: "no-store",
    headers: body ? {"content-type": "application/json"} : undefined, body: body ? JSON.stringify(body) : undefined});
  const data = await response.json();
  if (!response.ok) throw new Error(messages[data.detail] ?? "操作未完成，请重试。");
  return data as T;
}
export function useDeviceAccess() {return useContext(Context);}

export function DeviceAccessGate({children}: {children: ReactNode}) {
  const [device, setDevice] = useState<Device | null>(null);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const inflight = useRef<Promise<void> | null>(null);
  const mounted = useRef(true);
  const generation = useRef(0);
  const refresh = useCallback(() => {
    if (inflight.current) return inflight.current;
    const epoch = generation.current;
    inflight.current = call<Device>("status").then(value => {
      if (mounted.current && epoch === generation.current) {setDevice(value); setError("");}
    }).catch(err => {if (mounted.current) setError(err.message);}).finally(() => {inflight.current = null;});
    return inflight.current;
  }, []);
  useEffect(() => {
    mounted.current = true;
    void refresh();
    const expired = () => {generation.current++; clearDeviceSessionReads(); setDevice({state: "unpaired"}); setOpen(false);};
    const focus = () => {if (document.visibilityState === "visible") void refresh();};
    window.addEventListener("ashare-access-expired", expired);
    document.addEventListener("visibilitychange", focus);
    return () => {mounted.current = false; window.removeEventListener("ashare-access-expired", expired); document.removeEventListener("visibilitychange", focus);};
  }, [refresh]);
  useEffect(() => {
    if (!device || device.state === "unpaired") return;
    const expiry = Math.min(device.expires_at ?? Infinity, device.maintenance ? device.maintenance_until ?? Infinity : Infinity) * 1000;
    const delay = device.state === "pending" ? 3000 : Math.max(100, Math.min(2_000_000_000, expiry - Date.now()));
    const timer = window.setTimeout(() => {
      if ((device.expires_at ?? Infinity) * 1000 <= Date.now()) {generation.current++; clearDeviceSessionReads(); setDevice({state: "unpaired"});}
      else if (device.maintenance && (device.maintenance_until ?? 0) * 1000 <= Date.now()) setDevice({...device, maintenance: false});
      void refresh();
    }, delay);
    return () => clearTimeout(timer);
  }, [device, refresh]);
  const value = {device, refresh, open: () => setOpen(true)};
  return <Context.Provider value={value}>
    {device?.state === "active" ? children : <PairingScreen device={device} error={error} refresh={refresh} onPaired={next => {generation.current++; clearDeviceSessionReads(); setDevice(next);}} />}
    {device?.state === "active" && <AccessCenter open={open} onClose={() => setOpen(false)} />}
  </Context.Provider>;
}

function PairingScreen({device, error, refresh, onPaired}: {device: Device | null; error: string; refresh: () => Promise<void>; onPaired: (device: Device) => void}) {
  const [label, setLabel] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [localError, setError] = useState("");
  async function submit(bootstrap: boolean) {
    setBusy(true); setError("");
    try {onPaired(await call<Device>(bootstrap ? "bootstrap" : "start", {label: label.trim() || (bootstrap ? "管理电脑" : "我的设备"), ...(bootstrap ? {code: code.trim()} : {})})); setCode("");}
    catch (err) {setError((err as Error).message);} finally {setBusy(false);}
  }
  return <main className="device-access-screen">
    <section className="device-access-intro"><span className="device-access-eyebrow">AShare · 设备访问</span><h1>你的工作台，<br/>由你批准访问。</h1><p>手机首次使用，在管理电脑上确认一次。以后打开即可继续查看行情与选股。</p><div className="device-access-scope"><span>行情与选股</span><span>自选与记录</span><span>消息通知</span></div></section>
    <section className="device-access-card" aria-label="设备配对">
      <HugeiconsIcon icon={Shield01Icon} size={28} aria-hidden="true" />
      <h2>{device?.state === "pending" ? "等待电脑确认" : "连接你的设备"}</h2>
      {device?.state === "pending" ? <><p>在管理电脑的「设备与权限」里，核对名称与识别码后批准。</p><strong className="device-access-code">{device.id?.slice(-6).toUpperCase()}</strong><p>{device.label} · 请求 5 分钟内有效</p><button className="quiet-action" onClick={() => void refresh()}>检查批准结果</button></> : <>
        <label>设备名称<input value={label} onChange={e => setLabel(e.target.value)} maxLength={60} autoComplete="off" placeholder="例如：我的手机" /></label>
        <button className="device-primary" disabled={busy || !device} onClick={() => void submit(false)}>{busy ? "正在连接…" : "请求配对"}</button>
        <details><summary>电脑首次启用</summary><p>在这台电脑的项目终端运行下面的命令，输入一次性初始化码。已有管理电脑时，直接由它批准即可。</p><code>backend/.venv/bin/python scripts/local-services.py pairing-code</code><label>一次性初始化码<input type="password" autoComplete="off" value={code} onChange={e => setCode(e.target.value)} maxLength={100} /></label><button className="quiet-action" disabled={busy || !code.trim()} onClick={() => void submit(true)}>设为管理电脑</button></details>
      </>}
      {(localError || error) && <p role="alert" className="device-access-error">{localError || error}</p>}
      {!device && <button className="quiet-action" onClick={() => void refresh()}>{error ? "重新连接" : "正在确认服务…"}</button>}
      <p className="device-access-footnote">维护操作单独授权。仅在可信局域网内使用；HTTP 连接没有传输加密。</p>
    </section>
  </main>;
}

export function AccessButton() {
  const {open} = useDeviceAccess();
  return <IconButton aria-label="设备与权限" onClick={open}><HugeiconsIcon icon={Shield01Icon} size={18} strokeWidth={1.6} aria-hidden="true" /></IconButton>;
}

export function MaintenanceBoundary({children}: {children: ReactNode}) {
  const {device, open, refresh} = useDeviceAccess();
  if (device?.maintenance) return children;
  return <section className="device-maintenance-prompt"><HugeiconsIcon icon={Shield01Icon} size={26} aria-hidden="true"/><h2>维护操作需要临时授权</h2><p>行情、选股和消息可以正常使用。维护授权每次持续 15 分钟。</p><div><button className="quiet-action" onClick={open}>设备与权限</button><button className="quiet-action" onClick={() => void refresh()}>检查授权</button></div></section>;
}

function AccessCenter({open, onClose}: {open: boolean; onClose: () => void}) {
  const {device, refresh} = useDeviceAccess();
  const presence = useExitPresence(open ? true : null);
  const [devices, setDevices] = useState<Device[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    if (device?.owner) {
      try {setDevices((await call<{devices: Device[]}>("devices")).devices); setError("");}
      catch (err) {setError((err as Error).message);}
    }
  }, [device?.owner]);
  useEffect(() => {if (open) void load();}, [open, load]);
  async function action(device_id: string, action: string) {
    setBusy(true); setError("");
    try {await call("manage", {device_id, action}); await refresh(); await load();}
    catch (err) {setError((err as Error).message);} finally {setBusy(false);}
  }
  async function logout() {
    setBusy(true);
    try {await call("logout", {}); onClose(); window.dispatchEvent(new Event("ashare-access-expired"));}
    catch (err) {setError((err as Error).message);} finally {setBusy(false);}
  }
  if (!presence.value) return null;
  return <ModalShell open={presence.active} label="设备与权限" size="md" presentation="drawer" onClose={onClose} header={<h2 className="text-lg font-semibold">设备与权限</h2>} bodyClassName="device-access-body">
    <div className="device-access-current"><strong>{device?.label}</strong><span>{device?.owner ? "管理电脑" : "已配对设备"} · {device?.maintenance ? "临时维护已开启" : "日常使用"}</span></div>
    <p>配对后可以查看数据、管理自选和填写个人记录。维护授权不会开放真实交易或绕过参数晋级审批。</p>
    {device?.owner ? <><div className="device-access-row"><h3>批准与管理</h3><button className="quiet-action" disabled={busy} onClick={() => void load()}>刷新设备</button></div><ul className="device-access-list">{devices.map(row => <li key={row.id}><div><strong>{row.label}{row.id === device.id ? " · 本机" : ""}</strong><span>{row.id?.slice(-6).toUpperCase()} · {row.state === "pending" ? "待批准" : row.maintenance ? "维护授权中" : "已配对"}</span></div><div className="device-access-actions">
      {row.state === "pending" ? <button className="quiet-action" disabled={busy} onClick={() => void action(row.id!, "approve")}>批准配对</button> : <button className="quiet-action" disabled={busy} onClick={() => void action(row.id!, row.maintenance ? "end-maintenance" : "grant-maintenance")}>{row.maintenance ? "结束维护" : "授权维护 15 分钟"}</button>}
      {row.id !== device.id && <button className="quiet-action" disabled={busy} onClick={() => void action(row.id!, "revoke")}>{row.state === "pending" ? "拒绝" : "撤销配对"}</button>}
    </div></li>)}</ul></> : <p>需要维护权限时，请在管理电脑上为本设备授权，再点击「检查授权」。</p>}
    {error && <p role="alert" className="device-access-error">{error}</p>}
    <div className="device-access-actions"><button className="quiet-action" disabled={busy} onClick={() => void refresh()}>检查授权</button><button className="quiet-action" disabled={busy} onClick={() => void logout()}>退出此设备</button></div>
  </ModalShell>;
}
