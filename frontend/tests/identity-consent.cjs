// Synthetic cloud acceptance only. Credentials arrive over stdin and are never logged.
const { chromium } = require("@playwright/test");
let input = "";
process.stdin.on("data", chunk => { input += chunk; });
process.stdin.on("end", async () => {
  let browser;
  let page;
  let stage = "setup";
  try {
    const params = JSON.parse(input);
    if (!/^onchain-.*@example\.invalid$/.test(params.email)) throw new Error("Synthetic account required");
    browser = await chromium.launch();
    page = await browser.newPage();
    let uri = "";
    await page.route(params.return_url + "**", async route => {
      uri = new URL(route.request().url()).searchParams.get("session_id");
      await route.fulfill({ contentType: "text/plain", body: "Synthetic authorization received" });
    });
    stage = "authorization URL";
    await page.goto(params.authorization_url);
    stage = "Cognito login";
    await page.waitForURL(/\.amazoncognito\.com\//);
    await page.locator('input[name="username"]').fill(params.email);
    await page.locator('input[name="password"]').fill(params.password);
    stage = "Cognito submit";
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    stage = "Identity callback";
    // Browser routing does not intercept every hop of a cross-origin redirect chain.
    await page.waitForURL(url => url.origin === new URL(params.return_url).origin
      && (url.searchParams.has("session_id") || url.searchParams.has("identity_session")));
    const landed = new URL(page.url());
    uri ||= landed.searchParams.get("session_id") || landed.searchParams.get("identity_session");
    if (!uri) throw new Error("Missing authorization session");
    process.stdout.write(uri);
  } catch {
    process.stderr.write("Synthetic Identity consent failed at " + stage + ".\n");
    if (page) await page.screenshot({ path: require("node:path").resolve(__dirname, "../../.deployment/identity-acceptance-failure.png") }).catch(() => {});
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
  }
});
