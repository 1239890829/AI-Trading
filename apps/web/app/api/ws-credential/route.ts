import { NextRequest } from "next/server";
import { accessBridge } from "@/lib/device-access-server";

export const dynamic = "force-dynamic";

/** One-use, 60-second ticket bound to this paired device and browser origin. */
export async function GET(req: NextRequest) {
  return accessBridge(req, "ws-ticket", "POST");
}
