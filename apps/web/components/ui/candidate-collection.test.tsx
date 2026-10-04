import { useEffect, useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { CandidateCollection } from "./candidate-collection";
afterEach(cleanup);
it("同一候选切换列表保留对象状态和挂载身份", () => {
 const mount = vi.fn();
 function Candidate() {
  const [count,setCount]=useState(0);
  useEffect(()=>{mount();},[]);
  return <button onClick={()=>setCount(count+1)}>核对 {count}</button>;
 }
 render(<CandidateCollection><Candidate key="600127"/></CandidateCollection>);
 fireEvent.click(screen.getByRole("button",{name:"核对 0"}));
 fireEvent.click(screen.getByRole("button",{name:"列表"}));
 expect(screen.getByRole("button",{name:"核对 1"})).toBeTruthy();
 fireEvent.click(screen.getByRole("button",{name:"卡片"}));
 expect(mount).toHaveBeenCalledTimes(1);
 expect(screen.getByRole("button",{name:"核对 1"})).toBeTruthy();
});
