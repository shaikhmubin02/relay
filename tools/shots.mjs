/**
 * Screenshots for the Devpost gallery, at the 3:2 ratio the form asks for.
 *
 *   node shots.mjs
 *
 * Output: video/shots/NN-name.png (1800x1200)
 */

import { chromium } from "playwright";
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const OUT = join(ROOT, "video", "shots");
const PY = join(ROOT, ".venv", "Scripts", "python.exe");
const PORT = 8012;
const BASE = `http://127.0.0.1:${PORT}`;
const TOKEN = "gallery-only-coordinator";
const SHOT = { width: 1800, height: 1200 }; // 3:2

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const env = {
  ...process.env,
  PYTHONPATH: join(ROOT, "src"),
  RELAY_DB: join(OUT, "gallery.db"),
  RELAY_WORKER_ENABLED: "0",
  RELAY_COORDINATOR_TOKEN: TOKEN,
  RELAY_TOKEN_SECRET: "gallery-only-not-a-real-secret",
  RELAY_INTAKE_TOKEN: "gallery-only-intake",
  RELAY_PUBLIC_BASE_URL: BASE,
  RELAY_MODEL_PROVIDER: "offline",
};

mkdirSync(OUT, { recursive: true });
rmSync(env.RELAY_DB, { force: true });
const server = spawn(PY, ["-m", "relay", "serve", "--port", String(PORT)], { cwd: ROOT, env, stdio: "ignore" });
process.on("exit", () => server.kill());

function cli(args) {
  const res = spawnSync(PY, args, {
    cwd: ROOT,
    env: { ...env, RELAY_DB: join(OUT, "cli.db") },
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
  });
  return (res.stdout || "") + (res.stderr || "");
}

function slice(text, from, to) {
  const lines = text.split(/\r?\n/);
  const a = lines.findIndex((l) => l.startsWith(from));
  if (a < 0) return "";
  let b = lines.length;
  if (to) {
    const rel = lines.slice(a + 1).findIndex((l) => l.startsWith(to));
    if (rel >= 0) b = a + 1 + rel;
  }
  return lines.slice(a, b).join("\n").replace(/\s+$/, "");
}

function page(html, name) {
  const file = join(OUT, name);
  writeFileSync(file, html, "utf8");
  return "file:///" + file.replace(/\\/g, "/");
}

const terminal = (command, body, name) =>
  page(
    `<!doctype html><meta charset="utf-8"><style>
      html,body{margin:0;height:100%;background:#0f1e29;display:flex;align-items:center;justify-content:center;
        font:15.5px/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#d7e6ea;}
      .win{width:1560px;background:#0b1720;border:1px solid #23404f;border-radius:12px;overflow:hidden;
        box-shadow:0 24px 70px rgba(0,0,0,.55);}
      .bar{background:#12242f;padding:11px 16px;border-bottom:1px solid #23404f;color:#8fa9b6;font-size:13.5px;}
      .bar b{color:#4fd1c5;font-weight:600;}
      pre{margin:0;padding:20px 24px;white-space:pre-wrap;word-break:break-word;}
      .cmd{color:#7fe3d4;}
    </style><div class="win"><div class="bar"><b>relay</b> &nbsp;·&nbsp; captured from a real run</div>
    <pre><span class="cmd">$ ${command}</span>\n\n${body.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]))}</pre></div>`,
    name
  );

async function waitUp() {
  for (let i = 0; i < 90; i += 1) {
    try {
      if ((await fetch(`${BASE}/healthz`)).ok) return;
    } catch {}
    await sleep(500);
  }
  throw new Error("server did not start");
}

async function shoot(page_, name) {
  await sleep(500);
  const file = join(OUT, `${name}.png`);
  await page_.screenshot({ path: file });
  console.log(`  ${name}.png`);
}

async function scrollTo(page_, locator, offset = 110) {
  const box = await locator.boundingBox();
  if (!box) return;
  const y = await page_.evaluate(() => window.scrollY);
  await page_.evaluate((t) => window.scrollTo(0, t), Math.max(0, y + box.y - offset));
  await sleep(400);
}

await waitUp();

const login = await fetch(`${BASE}/login`, {
  method: "POST",
  headers: { "content-type": "application/x-www-form-urlencoded" },
  body: new URLSearchParams({ token: TOKEN }),
  redirect: "manual",
});
const cookie = (login.headers.getSetCookie?.()[0] ?? login.headers.get("set-cookie")).split(";")[0];
const post = (path, form) =>
  fetch(BASE + path, {
    method: "POST",
    headers: { cookie, "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(form || {}),
    redirect: "manual",
  });

await post("/demo/seed");

console.log("Capturing CLI output…");
const demoOut = cli(["-m", "relay", "demo"]);
const evalOut = cli([join("eval", "run_eval.py"), "--repeats", "3"]);

const browser = await chromium.launch({ channel: "chrome" });
const context = await browser.newContext({ viewport: SHOT, deviceScaleFactor: 1 });
await context.addCookies([{ name: "relay_session", value: cookie.split("=")[1], url: BASE }]);
const p = await context.newPage();

console.log("Shooting:");

// 1 — the gap, with every exclusion reason on screen
const hero = await post("/demo/scenario", { scenario: "hero" });
const heroId = (hero.headers.get("location") || "").split("/").pop();
await p.goto(`${BASE}/workflows/${heroId}`);
await scrollTo(p, p.getByRole("heading", { name: /Who Relay ruled out/ }), 40);
await shoot(p, "01-why-each-volunteer-was-not-asked");

// 2 — the message a volunteer actually receives
await p.goto(`${BASE}/inbox`);
await scrollTo(p, p.locator("pre.message").first(), 150);
await shoot(p, "02-the-message-in-the-test-inbox");

// 3 — the link asks before it acts
const inboxHtml = await (await fetch(`${BASE}/inbox`, { headers: { cookie } })).text();
const acceptUrl = inboxHtml.match(/http:\/\/[^"<\s]+\/r\/[A-Za-z0-9_.\-]+/)[0];
await p.goto(acceptUrl);
await shoot(p, "03-the-link-asks-before-it-acts");

// 4 — confirmed, and the receipt behind it
await p.click('button[type="submit"]');
await p.waitForLoadState("networkidle");
await shoot(p, "04-confirmed");

await p.goto(`${BASE}/workflows/${heroId}`);
await scrollTo(p, p.getByRole("heading", { name: "Who Relay asked" }), 40);
await shoot(p, "05-the-receipt");

// 5 — it stops, and it will not relax a certification
const pallet = await post("/demo/scenario", { scenario: "no_candidate" });
const palletId = (pallet.headers.get("location") || "").split("/").pop();
await p.goto(`${BASE}/workflows/${palletId}`);
await shoot(p, "06-one-decision-with-the-evidence");

const escId = await p.locator('input[name="escalation_id"]').first().inputValue();
await p.locator("#opt-assign_specific_volunteer").click();
await p.selectOption('select[name="volunteer_id"]', "v_amara");
await p.getByRole("button", { name: /Apply this decision/ }).click();
await p.waitForLoadState("networkidle");
await p.evaluate(() => window.scrollTo(0, 0));
await shoot(p, "07-it-refuses-even-for-the-coordinator");

// 6 — the quiet overview
await p.goto(`${BASE}/`);
await shoot(p, "08-the-overview");

// 7 — architecture
await p.goto(
  page(
    `<!doctype html><meta charset="utf-8"><style>html,body{margin:0;height:100%;background:#fff;
     display:flex;align-items:center;justify-content:center}img{width:96%;max-height:96%;object-fit:contain}</style>
     <img src="file:///${join(ROOT, "docs", "architecture.svg").replace(/\\/g, "/")}">`,
    "diagram.html"
  )
);
await shoot(p, "09-architecture");

// 8 — the injection refusal, and the evaluation
await p.goto(terminal("python -m relay demo", slice(demoOut, "9. The same demand", "Where to look next"), "t-injection.html"));
await shoot(p, "10-a-compromised-planner-is-still-refused");

await p.goto(terminal("python eval/run_eval.py", slice(evalOut, "scenarios passing", "Wrote"), "t-eval.html"));
await shoot(p, "11-evaluation");

await browser.close();
server.kill();
console.log(`\nGallery in ${OUT}`);
