import { chromium } from "playwright";
const [url, out] = process.argv.slice(2);
const browser = await chromium.launch({ headless: false });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2 });
await page.goto(url, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(8000);
await page.screenshot({ path: out });
await browser.close();
console.log("saved", out);
