// GET /api/billing/wallet — proxy to GET /v1/billing/wallet. Returns the
// signed-in user's token-wallet balance with backend-owned turns
// conversion (balance_tokens, approx_turns_remaining, tokens_per_turn,
// low_balance, chat_enabled, …). Auth-required.

import { callBackend } from "@/lib/api";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET() {
  const r = await callBackend("/v1/billing/wallet");
  const text = await r.text();
  return new Response(text, {
    status: r.status,
    headers: {
      "Content-Type":
        r.headers.get("Content-Type") || "application/json",
      "Cache-Control": "no-store",
    },
  });
}
