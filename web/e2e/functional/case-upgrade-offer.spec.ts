// Functional: when the agent calls request_tier_upgrade (driven by
// the MOCK_REQUEST_UPGRADE scenario keyword in the user message), the
// SSE "case_upgrade_offer" event renders the CaseUpgradePrompt UI.

import { expect, openCaseViaApi, test } from "../fixtures/test-env";

test("agent-driven upgrade offer renders the upgrade prompt", async ({
  page,
}) => {
  const { caseId } = await openCaseViaApi({ tier: "quick" });
  await page.goto(`/app?case_id=${caseId}`);

  const textarea = page.getByPlaceholder(/Ask about this parcel/);
  await textarea.fill("MOCK_REQUEST_UPGRADE — please reason deeply");
  await page.getByRole("button", { name: /^Send/ }).click();

  // The upgrade prompt component should appear. We look for either
  // its tier word or a phrase from the offer copy. The component is
  // stable enough that a substring match on the recommended tier
  // works.
  await expect(page.getByText(/complex/i).first()).toBeVisible({
    timeout: 15_000,
  });
});
