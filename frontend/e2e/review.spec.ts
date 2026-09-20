import { expect, test, type Page } from "@playwright/test";

/**
 * The spec's smoke: reviewer approves Barton Creek `website_design` assigning rep1 →
 * rep1 sees it in My leads → reviewer marks another business do-not-contact → it leaves
 * the queue. Runs against the demo stack; nothing here talks to a live external API.
 */

const PASSWORD = process.env.DEMO_USERS_PASSWORD ?? "";
const BARTON = "Barton Creek Plumbing";

test.beforeEach(() => {
  test.skip(!PASSWORD, "DEMO_USERS_PASSWORD is not set; run through `make e2e` with .env filled in");
});

async function signIn(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("whoami")).toContainText(email);
}

async function signOut(page: Page) {
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
}

test("reviewer approves a lead, the rep sees it, do-not-contact removes a business", async ({ page }) => {
  await test.step("reviewer approves Barton Creek website_design for rep1", async () => {
    await signIn(page, "reviewer@example.com");
    await expect(page).toHaveURL(/\/review$/);
    await page.getByLabel("Search").fill("Barton Creek");
    const row = page.getByTestId("queue-row").filter({ hasText: BARTON });
    await expect(row).toHaveCount(1);
    await expect(row.getByTestId("queue-place")).toContainText("Austin, TX");
    await row.getByRole("link", { name: /Barton Creek/ }).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText(BARTON);
    await expect(page.getByTestId("ai-label").first()).toBeVisible();

    const card = page.getByTestId("opportunity-card").filter({ hasText: "Website design / redesign" });
    await card.getByRole("button", { name: "Approve" }).click();
    await page.getByLabel(/Assign to sales rep/).selectOption({ label: "rep1@example.com" });
    await page.getByRole("dialog").getByRole("button", { name: "Approve" }).click();
    await expect(page.getByRole("status")).toContainText("approved");
    await signOut(page);
  });

  await test.step("rep1 sees it in My leads and has no review pages", async () => {
    await signIn(page, "rep1@example.com");
    await expect(page).toHaveURL(/\/leads$/);
    const lead = page.getByTestId("lead-row").filter({ hasText: BARTON });
    await expect(lead).toHaveCount(1);
    await expect(lead.getByTestId("lead-place")).toContainText("Austin, TX");
    await expect(page.getByRole("navigation", { name: "Main" })).not.toContainText("Review queue");
    await page.goto("/review");
    await expect(page.getByText("Not available for your role")).toBeVisible();
    await signOut(page);
  });

  await test.step("reviewer marks another business do-not-contact and it leaves the queue", async () => {
    await signIn(page, "reviewer@example.com");
    const rows = page.getByTestId("queue-row").filter({ hasNotText: BARTON });
    const target = rows.first();
    const name = (await target.getByRole("link").first().textContent())?.trim() ?? "";
    expect(name).not.toBe("");
    await target.getByRole("link").first().click();
    await page.getByRole("button", { name: "Do not contact" }).click();
    await page.getByLabel(/Note/).fill("E2E: owner asked not to be contacted");
    await page.getByRole("button", { name: "Confirm: do not contact" }).click();
    await expect(page.getByRole("status")).toContainText("do not contact");
    await page.goto("/review");
    await page.getByLabel("Search").fill(name);
    await expect(page.getByText("Nothing to review with these filters.")).toBeVisible();
  });
});
