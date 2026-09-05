/**
 * Renders docs/architecture.svg to PNG at 2x.
 *
 * Devpost only accepts pdf/ppt/pptx/png/jpg for the architecture diagram field,
 * and the repo keeps the SVG as the source of truth, so this exports a copy.
 */

import { chromium } from "playwright";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SVG = join(ROOT, "docs", "architecture.svg");
const PNG = join(ROOT, "docs", "architecture.png");

const source = readFileSync(SVG, "utf8");
const [, w, h] = source.match(/viewBox="0 0 (\d+) (\d+)"/).map(Number);

const holder = join(ROOT, "video", "out", "diagram-export.html");
writeFileSync(
  holder,
  `<!doctype html><meta charset="utf-8"><style>
    html,body{margin:0;padding:0;background:#fff}
    img{display:block;width:${w}px;height:${h}px}
  </style><img src="file:///${SVG.replace(/\\/g, "/")}">`,
  "utf8"
);

const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({
  viewport: { width: w, height: h },
  deviceScaleFactor: 2, // 2x so it stays legible when a judge zooms in
});
await page.goto("file:///" + holder.replace(/\\/g, "/"));
await page.waitForTimeout(700);
await page.screenshot({ path: PNG });
await browser.close();

console.log(`${PNG}  (${w * 2}x${h * 2})`);
