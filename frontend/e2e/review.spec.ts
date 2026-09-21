import { expect, test, type Page } from "@playwright/test";

/**
 * The spec's smoke: reviewer approves Barton Creek `website_design` assigning rep1 →
 * rep1 sees it in My leads and opens the read-only lead page → reviewer marks another
 * business do-not-contact → it leaves the queue → (v0.7.0, with `CRM_DESTINATION=fake`)
 * the CRM manager sends Barton Creek now, `/crm` shows it synced, rep1 sees "In CRM ✓",
 * the reviewer marks Barton Creek do-not-contact and the fake record is flagged. Runs
 * against the demo stack, which `make e2e` resets first (`make reset-demo-data`), so no
 * manual click is assumed. Nothing here talks to a live external API.
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
    // Human labels and one number per chip: "Website redesign · Score NN", never a raw code.
    const chip = row.getByTestId("service-chip").filter({ hasText: "Website redesign" });
    await expect(chip).toHaveCount(1);
    await expect(chip).toContainText(/Score \d{1,3}$/);
    await expect(row).not.toContainText("website_design");
    await row.getByRole("link", { name: /Barton Creek/ }).click();
    await expect(page.getByRole("heading", { level: 1 })).toContainText(BARTON);
    await expect(page.getByTestId("ai-label").first()).toBeVisible();
    await expect(page.getByTestId("open-summary")).toContainText("Website redesign");
    await expect(page.getByText("(512) 555-0102")).toBeVisible();

    const card = page.getByTestId("opportunity-card").filter({ hasText: "Website redesign" });
    await expect(card.getByTestId("rule-reason")).toBeVisible();
    await expect(card.getByTestId("ai-rationale")).toBeVisible();
    await expect(card.getByTestId("ai-agrees").first()).toBeVisible();
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
    await expect(lead).toContainText("Website redesign");
    await expect(lead).toContainText("(512) 555-0102");
    await expect(page.getByRole("navigation", { name: "Main" })).not.toContainText("Review queue");

    // The name opens the read-only lead page: facts, plain-language findings, approval.
    await lead.getByRole("link", { name: BARTON }).click();
    await expect(page).toHaveURL(/\/leads\/[0-9a-f-]{36}$/);
    await expect(page.getByTestId("lead-detail")).toBeVisible();
    await expect(page.getByRole("heading", { level: 1 })).toContainText(BARTON);
    await expect(page.getByTestId("approval")).toContainText("reviewer@example.com");
    await expect(page.getByTestId("approval")).toContainText("rep1@example.com");
    await expect(page.getByTestId("finding").first()).toBeVisible();
    await expect(page.getByTestId("lead-detail").getByRole("button")).toHaveCount(0);
    await page.getByRole("link", { name: "← Leads" }).click();
    await expect(page).toHaveURL(/\/leads$/);
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
    await signOut(page);
  });

  await test.step("the CRM manager sends Barton Creek now and /crm shows it synced", async () => {
    await signIn(page, "crm@example.com");
    await page.getByRole("link", { name: "CRM" }).click();
    await expect(page).toHaveURL(/\/crm$/);
    const destination = page.getByTestId("crm-destination");
    await expect(destination).toBeVisible();
    const text = (await destination.textContent()) ?? "";
    test.skip(!text.includes("Destination: fake"), `CRM_DESTINATION is not fake (${text.trim().split("\n")[0]}); set it to fake for the CRM flow`);
    await expect(page.getByTestId("crm-health")).toContainText("Healthy");
    // Barton Creek is scheduled behind its undo window; "Sync all due" leaves it waiting.
    await expect(page.getByTestId("crm-scheduled")).toContainText(BARTON);
    await page.getByRole("button", { name: "Sync all due" }).click();
    await expect(page.getByRole("status")).toContainText("still waiting");
    await expect(page.getByTestId("crm-scheduled")).toContainText(BARTON);

    // Send now skips the wait but goes through the API's human gate.
    await page.getByTestId("crm-scheduled").getByTestId("crm-row").filter({ hasText: BARTON }).getByRole("button", { name: "Send now" }).click();
    await expect(page.getByRole("status").last()).toContainText("In CRM");
    await expect(page.getByTestId("crm-synced")).toContainText(BARTON);
    await expect(page.getByTestId("crm-scheduled")).not.toContainText(BARTON);
    await signOut(page);
  });

  await test.step("rep1 sees In CRM ✓ on the lead", async () => {
    await signIn(page, "rep1@example.com");
    const lead = page.getByTestId("lead-row").filter({ hasText: BARTON });
    await expect(lead.getByTestId("crm-badge")).toContainText("In CRM ✓");
    await lead.getByRole("link", { name: BARTON }).click();
    await expect(page.getByTestId("crm-badge")).toContainText("In CRM ✓");
    await expect(page.getByTestId("crm-attempt").first()).toContainText("Created");
    await signOut(page);
  });

  await test.step("a do-not-contact after the sync flags the fake CRM record", async () => {
    await signIn(page, "reviewer@example.com");
    await page.getByLabel("Search").fill("Barton Creek");
    const row = page.getByTestId("queue-row").filter({ hasText: BARTON });
    await expect(row).toHaveCount(1);
    await row.getByRole("link", { name: /Barton Creek/ }).click();
    await page.getByRole("button", { name: "Do not contact" }).click();
    await page.getByLabel(/Note/).fill("E2E: owner asked not to be contacted");
    await page.getByRole("button", { name: "Confirm: do not contact" }).click();
    await expect(page.getByRole("status")).toContainText("do not contact");
    await signOut(page);

    await signIn(page, "crm@example.com");
    await page.goto("/crm");
    // The flag goes out on the next tick; force it with Sync all due and read the record.
    await page.getByRole("button", { name: "Sync all due" }).click();
    await expect(page.getByRole("status")).toBeVisible();
    const token = await accessToken(page);
    const leads = await (await page.request.get(`${API}/crm/leads?status=synced`, { headers: { Authorization: `Bearer ${token}` } })).json();
    const barton = leads.items.find((item: { business_name: string }) => item.business_name.includes(BARTON));
    expect(barton).toBeTruthy();
    const attempts = await (await page.request.get(`${API}/crm/leads/${barton.id}/attempts`, { headers: { Authorization: `Bearer ${token}` } })).json();
    expect(attempts.map((a: { action: string }) => a.action)).toContain("mark_dnc");
    const record = await (await page.request.get(`${API}/crm/fake-records/${barton.external_id}`, { headers: { Authorization: `Bearer ${token}` } })).json();
    expect(record.fields["Do not contact"]).toBe(true);
  });
});

const API = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

/** The access token the signed-in page holds; refreshed through the same cookie the app uses. */
async function accessToken(page: Page): Promise<string> {
  const response = await page.request.post(`${API}/auth/refresh`);
  expect(response.ok()).toBeTruthy();
  return (await response.json()).access_token as string;
}



/**
 * v0.8.0: an admin creates a user who must change their password; the searches page
 * shows the demo job's four pipeline stages; the web and API responses carry the
 * security headers. Needs ADMIN_EMAIL + ADMIN_PASSWORD from .env (make e2e sources it);
 * with a generated admin password the admin steps are skipped with a message.
 */
const ADMIN_EMAIL = process.env.ADMIN_EMAIL ?? "";
const ADMIN_PASSWORD = process.env.ADMIN_PASSWORD ?? "";
/**
 * One fixed throw-away user, not a new `e2e-<timestamp>` each run: `make reset-demo-data`
 * (which `make e2e` runs first) deactivates every `e2e-*@example.com` user — it cannot
 * delete one who has signed in, because the audit log is append-only — so the first run
 * creates this user and every later run reactivates it and sets a fresh temporary
 * password from the same Users page. Both paths end in the forced password change.
 */
const E2E_USER = "e2e-user@example.com";
const TEMP_PASSWORD = "temporary-e2e-pass-9f3k2";
const NEW_PASSWORD = "chosen-by-the-e2e-user-42";

async function signInAs(page: Page, email: string, password: string) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

test("admin creates a user who must change their password; the searches page follows the demo pipeline", async ({ page }) => {
  test.skip(!ADMIN_EMAIL || !ADMIN_PASSWORD, "ADMIN_EMAIL / ADMIN_PASSWORD are not both set in .env; the admin steps need them");
  const newbie = E2E_USER;

  await test.step("admin creates the user from the Users page (or reactivates and resets the one from last time)", async () => {
    await signInAs(page, ADMIN_EMAIL, ADMIN_PASSWORD);
    // A generated admin password would itself force a change; the .env one does not.
    await expect(page.getByTestId("whoami")).toContainText(ADMIN_EMAIL);
    await page.getByRole("link", { name: "Users" }).click();
    await expect(page).toHaveURL(/\/admin\/users$/);
    // The list is loaded once the admin's own row is there.
    await expect(page.getByTestId("user-row").filter({ hasText: ADMIN_EMAIL })).toHaveCount(1);
    const row = page.getByTestId("user-row").filter({ hasText: newbie });
    if ((await row.count()) === 0) {
      await page.getByLabel("Email", { exact: true }).fill(newbie);
      await page.getByRole("combobox", { name: "Role", exact: true }).selectOption("reviewer");
      await page.getByLabel("Temporary password").fill(TEMP_PASSWORD);
      await page.getByRole("button", { name: "Create user" }).click();
      await expect(page.getByTestId("created-once")).toContainText(TEMP_PASSWORD);
    } else {
      // Left deactivated by `make reset-demo-data`; bring it back with a fresh temporary
      // password, which forces the change just as a creation does.
      if ((await row.textContent())?.includes("deactivated")) {
        await row.getByRole("button", { name: "Reactivate" }).click();
        await expect(row).not.toContainText("deactivated");
      }
      await row.getByRole("button", { name: "Reset password" }).click();
      const dialog = page.getByRole("dialog", { name: `Reset password for ${newbie}` });
      await dialog.getByLabel(/New temporary password/).fill(TEMP_PASSWORD);
      await dialog.getByRole("button", { name: "Set password" }).click();
      await expect(page.getByTestId("reset-once")).toContainText(TEMP_PASSWORD);
    }
    await expect(row).toContainText("must change");
    await signOut(page);
  });

  await test.step("the new user is sent to change their password before anything else", async () => {
    await signInAs(page, newbie, TEMP_PASSWORD);
    await expect(page).toHaveURL(/\/profile\?forced=1$/);
    await expect(page.getByTestId("forced-notice")).toBeVisible();
    await page.goto("/review");
    await expect(page).toHaveURL(/\/profile\?forced=1$/);
    await page.getByLabel("Current password").fill(TEMP_PASSWORD);
    await page.getByLabel("New password", { exact: true }).fill(NEW_PASSWORD);
    await page.getByLabel("New password again").fill(NEW_PASSWORD);
    await page.getByRole("button", { name: "Change password" }).click();
    await expect(page).toHaveURL(/\/review$/);
    await expect(page.getByRole("status")).toContainText("Password changed");
    await signOut(page);
  });

  await test.step("the demo search shows its four pipeline stages and links to the queue", async () => {
    await signInAs(page, ADMIN_EMAIL, ADMIN_PASSWORD);
    await page.getByRole("link", { name: "Searches" }).click();
    await expect(page).toHaveURL(/\/searches$/);
    await expect(page.getByTestId("estimate")).toBeVisible();
    const demo = page.getByTestId("search-row").filter({ hasText: "Demo" }).first();
    await expect(demo.getByTestId("last-run")).toContainText("done");
    await demo.getByRole("link").first().click();
    await expect(page).toHaveURL(/\/searches\/[0-9a-f-]{36}$/);
    for (const stage of ["discovery", "resolution", "audit", "classification"]) {
      await expect(page.getByTestId(`stage-${stage}`).getByTestId("stage-status")).toHaveText("done");
    }
    // `stored_new` is only non-zero the first time a stack loads the demo fixture; a later
    // `make load-demo-data` updates the same records instead, so it says 0 on any dev
    // machine that has loaded them before. `fetched` counts what the run read either way.
    await expect(page.getByTestId("count-discovery-fetched")).not.toHaveText("0");
    await expect(page.getByTestId("review-link")).toHaveAttribute("href", /\/review\?city=/);
    await page.getByTestId("review-link").click();
    await expect(page).toHaveURL(/\/review\?city=/);
    await expect(page.getByTestId("queue-row").first()).toBeVisible();
    await signOut(page);
  });

  await test.step("web and API responses carry the security headers", async () => {
    const web = await page.request.get("/login");
    expect(web.headers()["x-content-type-options"]).toBe("nosniff");
    expect(web.headers()["x-frame-options"]).toBe("DENY");
    expect(web.headers()["referrer-policy"]).toBe("no-referrer");
    expect(web.headers()["content-security-policy"]).toMatch(/default-src 'self'/);
    expect(web.headers()["content-security-policy"]).toMatch(/script-src 'self' 'nonce-/);
    const api = await page.request.get(`${API}/health`);
    expect(api.headers()["x-content-type-options"]).toBe("nosniff");
    expect(api.headers()["x-frame-options"]).toBe("DENY");
  });
});
