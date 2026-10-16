import { NextRequest } from "next/server";
import { accessBridge, accessDenied } from "@/lib/device-access-server";

export const dynamic = "force-dynamic";

async function route(req: NextRequest, context: {params: Promise<{action: string}>}) {
  const {action} = await context.params;
  const allowed = req.method === "GET" ? ["status", "devices"] : ["start", "bootstrap", "manage", "logout"];
  if (!allowed.includes(action)) return accessDenied(404, "access_action_not_found");
  return accessBridge(req, action);
}
export { route as GET, route as POST };
