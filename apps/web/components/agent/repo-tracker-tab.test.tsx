import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RepoTrackerTab } from "./repo-tracker-tab";

vi.mock("@/lib/api", () => ({
  getAgentKbFile: vi.fn(async () => ({content: "## 条目一\n第一条的依据\n## 条目二\n第二条的依据"})),
}));
afterEach(cleanup);

it("选择仓库条目后保留原生焦点，详情与选中条目一致", async () => {
  render(<RepoTrackerTab />);
  const row = await screen.findByRole("button", {name: "条目二"});
  row.focus();
  fireEvent.click(row);
  await waitFor(() => expect(screen.getByRole("button", {name: "条目二"}).getAttribute("aria-pressed")).toBe("true"));
  expect(document.activeElement).toBe(row);
  expect(screen.getByText("第二条的依据")).toBeTruthy();
});
