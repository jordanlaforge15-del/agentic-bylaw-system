// Transactional email sender, used by the admin invite approval flow.
//
// SMTP is intentionally env-driven so swapping providers (Porkbun →
// Resend → SES) is a config change, not a code change. All five env
// vars must be present for sendApprovalEmail to do anything; if any
// are missing the function logs a warning and returns ``{ sent: false,
// reason: ... }``. The caller (admin approve handler) should treat
// non-send as non-fatal — approval still succeeds, the admin can copy
// the sign-in link out-of-band.
//
// Env:
//   SMTP_HOST       e.g. "smtp.porkbun.com" or "fwd.porkbun.com"
//   SMTP_PORT       587 (STARTTLS) or 465 (SMTPS)
//   SMTP_USER       full mailbox address, e.g. "info@agenticbylawsystems.com"
//   SMTP_PASS       mailbox password / app password
//   MAIL_FROM       display from-address, e.g. "ABS° <info@agenticbylawsystems.com>"
//   INVITE_PUBLIC_URL  base URL the sign-in link points to, e.g.
//                  "https://agenticbylawsystems.com" — no trailing slash.

import nodemailer from "nodemailer";
import type { Transporter } from "nodemailer";

type SmtpConfig = {
  host: string;
  port: number;
  user: string;
  pass: string;
  from: string;
  signInBase: string;
};

declare global {
  // eslint-disable-next-line no-var
  var __smtpTransporter: Transporter | undefined;
}

function readConfig(): SmtpConfig | null {
  const host = process.env.SMTP_HOST?.trim();
  const portRaw = process.env.SMTP_PORT?.trim();
  const user = process.env.SMTP_USER?.trim();
  const pass = process.env.SMTP_PASS;
  const from = process.env.MAIL_FROM?.trim();
  const signInBase = (
    process.env.INVITE_PUBLIC_URL?.trim() || ""
  ).replace(/\/+$/, "");
  if (!host || !portRaw || !user || !pass || !from || !signInBase) {
    return null;
  }
  const port = Number(portRaw);
  if (!Number.isFinite(port)) return null;
  return { host, port, user, pass, from, signInBase };
}

function getTransporter(cfg: SmtpConfig): Transporter {
  if (global.__smtpTransporter) return global.__smtpTransporter;
  const t = nodemailer.createTransport({
    host: cfg.host,
    port: cfg.port,
    // 587 → STARTTLS (secure=false), 465 → SMTPS (secure=true).
    // nodemailer auto-handles this if we pass secure=undefined, but
    // explicit beats implicit when ops folks read the config.
    secure: cfg.port === 465,
    auth: { user: cfg.user, pass: cfg.pass },
  });
  if (process.env.NODE_ENV !== "production") {
    global.__smtpTransporter = t;
  }
  return t;
}

export type SendResult =
  | { sent: true; messageId: string }
  | { sent: false; reason: string };

// Send the approval email. Returns a structured result; never throws.
// The admin approve handler treats failure as non-fatal — the row is
// still flipped to approved, the email failure is logged for the
// admin to retry manually.
export async function sendApprovalEmail(args: {
  to: string;
  name: string;
  inviteId: string;
}): Promise<SendResult> {
  const cfg = readConfig();
  if (!cfg) {
    return {
      sent: false,
      reason: "SMTP env vars missing (set SMTP_HOST/PORT/USER/PASS, MAIL_FROM, INVITE_PUBLIC_URL)",
    };
  }
  const signInUrl = `${cfg.signInBase}/sign-in`;
  const { html, text } = renderApprovalEmail({
    name: args.name,
    signInUrl,
    inviteId: args.inviteId,
  });

  try {
    const info = await getTransporter(cfg).sendMail({
      from: cfg.from,
      to: args.to,
      subject: "You're in — ABS° private beta",
      html,
      text,
    });
    return { sent: true, messageId: info.messageId };
  } catch (e) {
    const reason = e instanceof Error ? e.message : String(e);
    console.error("sendApprovalEmail failed", reason);
    return { sent: false, reason };
  }
}

function renderApprovalEmail(args: {
  name: string;
  signInUrl: string;
  inviteId: string;
}): { html: string; text: string } {
  // Plain text first; the HTML mirrors it with light styling. Two
  // versions are required for good rendering across mail clients +
  // for accessibility.
  const text = `Hi ${args.name},

You've been approved for the ABS° private beta.

Sign in here — use the Google or Apple account associated with the
email this message was sent to:

  ${args.signInUrl}

Once signed in you'll land at the chat surface and your usage caps
are already provisioned to your account. Beta seats are time-limited;
your invite expires 14 days from approval if not used.

If you didn't request access, ignore this email.

— ABS°
Reference: ${args.inviteId}
`;

  const html = `<!doctype html>
<html lang="en">
  <body style="margin:0;padding:0;background:#f7f6f3;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:#111;line-height:1.5;">
    <div style="max-width:520px;margin:0 auto;padding:32px 24px;">
      <div style="font-family:'JetBrains Mono',ui-monospace,monospace;font-size:11px;letter-spacing:0.14em;text-transform:uppercase;color:#666;margin-bottom:8px;">
        ABS° · PRIVATE BETA
      </div>
      <h1 style="font-size:28px;font-weight:800;letter-spacing:-0.025em;line-height:1.1;margin:0 0 16px;">
        You're in.
      </h1>
      <p style="margin:0 0 16px;">Hi ${escapeHtml(args.name)},</p>
      <p style="margin:0 0 24px;">
        You've been approved for the ABS° private beta. Sign in below — use the Google or Apple account associated with this email address.
      </p>
      <p style="margin:0 0 32px;">
        <a href="${args.signInUrl}" style="display:inline-block;background:#111;color:#fff;text-decoration:none;padding:12px 20px;font-weight:600;letter-spacing:-0.005em;">
          Sign in →
        </a>
      </p>
      <p style="margin:0 0 8px;font-size:14px;color:#555;">
        Your usage caps are provisioned and waiting. Beta seats are time-limited — this invite expires <strong>14 days from approval</strong> if not used.
      </p>
      <p style="margin:24px 0 0;font-size:12px;color:#999;border-top:1px solid #e5e4e1;padding-top:16px;">
        If you didn't request access, ignore this email.<br/>
        Reference: ${escapeHtml(args.inviteId)}
      </p>
    </div>
  </body>
</html>`;
  return { html, text };
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
