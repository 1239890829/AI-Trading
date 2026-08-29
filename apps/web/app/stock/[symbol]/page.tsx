import { redirect } from "next/navigation";

export default async function StockPage({ searchParams }: { searchParams: Promise<{ symbol?: string }> }) {
  const sp = await searchParams;
  const symbol = (sp?.symbol ?? "").trim();
  const target = symbol ? `/workbench?symbol=${symbol}` : "/workbench";
  redirect(target);
}
