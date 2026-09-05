/**
 * Records the demo segments by driving a local Relay instance.
 *
 * One video per narration block, so a re-recorded voice line only needs its own
 * segment redone. Everything shown is the real app doing the real thing; the two
 * terminal blocks display output captured from actual command runs at record time.
 *
 *   node record.mjs            # all scenes
 *   node record.mjs 03 06      # just these
 */

import { chromium } from "playwright";
import { spawn, spawnSync } from "node:child_process";
import { mkdirSync, rmSync, readdirSync, renameSync, writeFileSync, existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { narration, report } from "./audio.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");
const OUT = join(ROOT, "video", "out");
const RAW = join(OUT, "raw");
const PY = join(ROOT, ".venv", "Scripts", "python.exe");
const PORT = 8011;
const BASE = `http://127.0.0.1:${PORT}`;
const TOKEN = "recording-only-coordinator";
const VIEWPORT = { width: 1600, height: 900 };

const only = process.argv.slice(2);
const wanted = (id) => only.length === 0 || only.includes(id);

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---------------------------------------------------------------------------
// server
// ---------------------------------------------------------------------------

function startServer() {
  const env = {
    ...process.env,
    PYTHONPATH: join(ROOT, "src"),
    RELAY_DB: join(ROOT, "video", "out", "demo.db"),
    RELAY_WORKER_ENABLED: "0", // deterministic: the app ticks on page load instead
    RELAY_COORDINATOR_TOKEN: TOKEN,
    // Non-default secrets so the "development secrets in use" warning, which is
    // correct but not what this video is about, stays off the screen.
    RELAY_TOKEN_SECRET: "recording-only-not-a-real-secret",
    RELAY_INTAKE_TOKEN: "recording-only-intake",
    RELAY_PUBLIC_BASE_URL: BASE,
    RELAY_MODEL_PROVIDER: "offline",
  };
  rmSync(env.RELAY_DB, { force: true });
  const proc = spawn(PY, ["-m", "relay", "serve", "--port", String(PORT)], {
    cwd: ROOT,
    env,
    stdio: "ignore",
  });
  return proc;
}

async function waitForServer() {
  for (let i = 0; i < 90; i += 1) {
    try {
      const res = await fetch(`${BASE}/healthz`);
      if (res.ok) return;
    } catch {
      /* not up yet */
    }
    await sleep(500);
  }
  throw new Error("Relay did not start on " + BASE);
}

// ---------------------------------------------------------------------------
// real command output for the two terminal blocks
// ---------------------------------------------------------------------------

function run(args) {
  const res = spawnSync(PY, args, {
    cwd: ROOT,
    env: { ...process.env, PYTHONPATH: join(ROOT, "src"), RELAY_DB: join(ROOT, "video", "out", "cli.db"), RELAY_MODEL_PROVIDER: "offline" },
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
  });
  return (res.stdout || "") + (res.stderr || "");
}

function section(text, startsWith, stopWith) {
  const lines = text.split(/\r?\n/);
  const from = lines.findIndex((l) => l.startsWith(startsWith));
  if (from < 0) return "";
  let to = lines.length;
  if (stopWith) {
    const rel = lines.slice(from + 1).findIndex((l) => l.startsWith(stopWith));
    if (rel >= 0) to = from + 1 + rel;
  }
  return lines.slice(from, to).join("\n").replace(/\s+$/, "");
}

function diagramPage(file) {
  const svg = "file:///" + join(ROOT, "docs", "architecture.svg").replace(/\\/g, "/");
  writeFileSync(
    file,
    `<!doctype html><meta charset="utf-8"><style>
      html,body{margin:0;height:100%;background:#fff;display:flex;align-items:center;justify-content:center;}
      img{width:98%;max-height:98%;object-fit:contain;}
    </style><img src="${svg}">`,
    "utf8"
  );
  return "file:///" + file.replace(/\\/g, "/");
}

function cardPage(file) {
  writeFileSync(
    file,
    `<!doctype html><meta charset="utf-8"><style>
      html,body{margin:0;height:100%;background:#152b3c;color:#fff;
        font:16px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
        display:flex;align-items:center;justify-content:center;}
      .c{text-align:center;}
      h1{font-size:64px;margin:0 0 10px;letter-spacing:-.02em;}
      p.t{font-size:24px;color:#a9c6cb;margin:0 0 46px;font-weight:400;}
      .row{font-size:22px;margin:12px 0;color:#eaf5f4;}
      .row b{color:#6fd3d5;font-weight:600;margin-right:14px;font-size:15px;
        text-transform:uppercase;letter-spacing:.12em;vertical-align:middle;}
      code{font-family:ui-monospace,Menlo,Consolas,monospace;}
      .n{margin-top:44px;color:#7e98a4;font-size:15px;}
    </style><div class="c">
      <h1>Relay</h1>
      <p class="t">Routine recovery handled. The judgement stays with her.</p>
      <div class="row"><b>Try it</b><code>relay-volunteer-agent.vercel.app</code></div>
      <div class="row"><b>Code</b><code>github.com/shaikhmubin02/relay</code></div>
      <div class="n">MIT licensed &nbsp;·&nbsp; built with the Strands Agents SDK &nbsp;·&nbsp; synthetic data only</div>
    </div>`,
    "utf8"
  );
  return "file:///" + file.replace(/\\/g, "/");
}

function terminalPage(command, body, file) {
  const html = `<!doctype html><meta charset="utf-8"><style>
  html,body{margin:0;height:100%;background:#0f1e29;}
  body{display:flex;align-items:center;justify-content:center;
    font:15px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#d7e6ea;}
  .win{width:1380px;background:#0b1720;border:1px solid #23404f;border-radius:10px;overflow:hidden;
    box-shadow:0 20px 60px rgba(0,0,0,.5);}
  .bar{background:#12242f;padding:9px 14px;border-bottom:1px solid #23404f;color:#8fa9b6;font-size:13px;}
  .bar b{color:#4fd1c5;font-weight:600;}
  pre{margin:0;padding:18px 22px;white-space:pre-wrap;word-break:break-word;max-height:760px;overflow:hidden;}
  .cmd{color:#7fe3d4;}
  </style><div class="win"><div class="bar"><b>relay</b> &nbsp;·&nbsp; captured from a real run</div>
  <pre><span class="cmd">$ ${command}</span>\n\n${body.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]))}</pre></div>`;
  writeFileSync(file, html, "utf8");
  return "file:///" + file.replace(/\\/g, "/");
}

// ---------------------------------------------------------------------------
// browser helpers
// ---------------------------------------------------------------------------

const CURSOR = `
  const dot = document.createElement('div');
  dot.id = '__cursor';
  Object.assign(dot.style, {
    position:'fixed', width:'18px', height:'18px', borderRadius:'50%',
    background:'rgba(0,127,130,.35)', border:'2px solid #007f82', zIndex:2147483647,
    pointerEvents:'none', transform:'translate(-50%,-50%)', transition:'all .45s cubic-bezier(.4,0,.2,1)',
    left:'-50px', top:'-50px'
  });
  const put = () => (document.body || document.documentElement).appendChild(dot);
  if (document.body) put(); else document.addEventListener('DOMContentLoaded', put);
  window.__moveCursor = (x, y) => { dot.style.left = x + 'px'; dot.style.top = y + 'px'; };
`;

async function point(page, locator) {
  const box = await locator.boundingBox();
  if (!box) return;
  await page.evaluate(([x, y]) => window.__moveCursor?.(x, y), [box.x + box.width / 2, box.y + box.height / 2]);
  await sleep(600);
}

async function tap(page, locator) {
  await locator.scrollIntoViewIfNeeded();
  await sleep(250);
  await point(page, locator);
  await locator.click();
  await page.waitForLoadState("networkidle").catch(() => {});
  await sleep(600);
}

async function glide(page, to, ms = 1600) {
  await page.evaluate(
    ([target, duration]) => {
      const start = window.scrollY;
      const delta = target - start;
      const t0 = performance.now();
      return new Promise((done) => {
        const step = (t) => {
          const p = Math.min(1, (t - t0) / duration);
          const eased = p < 0.5 ? 2 * p * p : 1 - Math.pow(-2 * p + 2, 2) / 2;
          window.scrollTo(0, start + delta * eased);
          p < 1 ? requestAnimationFrame(step) : done();
        };
        requestAnimationFrame(step);
      });
    },
    [to, ms]
  );
}

async function toElement(page, locator, offset = 160, ms = 1600) {
  const box = await locator.boundingBox();
  if (!box) return;
  await glide(page, Math.max(0, (await page.evaluate(() => window.scrollY)) + box.y - offset), ms);
}

// ---------------------------------------------------------------------------
// scenes
// ---------------------------------------------------------------------------

const scenes = {
  // 01 - the problem: the rota, with the morning shift on it
  "01": async (page) => {
    await page.goto(`${BASE}/`);
    await sleep(1200);
    await toElement(page, page.getByRole("heading", { name: "Today's rota" }), 120, 2000);
    await sleep(1200);
    await point(page, page.getByText("Morning food packing").first());
    await sleep(2500);
  },

  // 02 - what it is: the quiet overview
  "02": async (page) => {
    await page.goto(`${BASE}/`);
    await sleep(1500);
    await point(page, page.getByRole("heading", { name: /Needs your decision/ }));
    await sleep(2000);
    await glide(page, 420, 1500);
    await sleep(2200);
  },

  // 03 - eligibility: nine ruled out, each with a reason
  "03": async (page, state) => {
    await page.goto(`${BASE}/`);
    await tap(page, page.getByRole("button", { name: /Iris cancels the morning packing/ }));
    state.heroId = page.url().split("/").pop();
    await sleep(1600);
    await toElement(page, page.getByRole("heading", { name: /Who Relay ruled out/ }), 90, 1800);
    await sleep(1800);
    for (const name of ["Cal Rivera", "Gita Rao", "Hugo Delaine"]) {
      await point(page, page.getByRole("cell", { name, exact: true }).first());
      await sleep(1500);
    }
    await sleep(1200);
  },

  // 04 - the message, and a link that asks before it acts
  "04": async (page, state) => {
    await page.goto(`${BASE}/inbox`);
    await sleep(1200);
    await toElement(page, page.locator("pre.message").first(), 130, 1600);
    await sleep(2600);
    const link = await page.locator("pre.message").first().innerText();
    const url = link.match(/http:\/\/\S+\/r\/[A-Za-z0-9_.\-]+/)[0];
    state.acceptUrl = url;
    await page.goto(url);
    await sleep(2800);
    await point(page, page.getByRole("button", { name: /Yes, I can cover it/ }));
    await sleep(1800);
  },

  // 05 - only now does the rota change
  "05": async (page, state) => {
    await page.goto(state.acceptUrl);
    await tap(page, page.getByRole("button", { name: /Yes, I can cover it/ }));
    await sleep(1800);
    await page.goto(`${BASE}/workflows/${state.heroId}`);
    await sleep(700);
    await toElement(page, page.getByRole("heading", { name: "Who Relay asked" }), 100, 1300);
    await sleep(1100);
    await toElement(page, page.getByRole("heading", { name: "Roster change" }), 100, 1300);
    await sleep(1200);
  },

  // 06 - it stops, and it will not relax a certification
  "06": async (page) => {
    await page.goto(`${BASE}/`);
    await tap(page, page.getByRole("button", { name: /Fen cancels the pallet reset/ }));
    await sleep(2400);
    await point(page, page.locator(".decision .question"));
    await sleep(2200);
    await tap(page, page.locator("#opt-assign_specific_volunteer"));
    await page.selectOption('select[name="volunteer_id"]', "v_amara");
    await sleep(1200);
    await tap(page, page.getByRole("button", { name: /Apply this decision/ }));
    await sleep(900);
    await glide(page, 0, 700);
    await sleep(3000);
  },

  // 07 - collisions and an injected instruction (real CLI output)
  "07": async (page, state) => {
    await page.goto(state.terminalDemo);
    await sleep(9000);
  },

  // 08 - how it holds together
  "08": async (page) => {
    await page.goto("file:///" + join(ROOT, "docs", "architecture.svg").replace(/\\/g, "/"));
    await sleep(9000);
  },

  // 10 - closing card: where to find it (silent, appended by assemble.mjs)
  "10": async (page) => {
    await page.goto(cardPage(join(OUT, "endcard.html")));
    await sleep(5200);
  },

  // 09 - the evaluation, then back to a quiet screen
  "09": async (page, state) => {
    await page.goto(state.terminalEval);
    await sleep(6500);
    await page.goto(`${BASE}/`);
    await sleep(3500);
  },
};

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

const server = startServer();
process.on("exit", () => server.kill());
process.on("SIGINT", () => { server.kill(); process.exit(1); });

try {
  mkdirSync(RAW, { recursive: true });
  const voice = narration();
  console.log("Narration found:");
  report(voice);
  await waitForServer();

  console.log("Seeding and preparing state…");
  const jar = [];
  const login = await fetch(`${BASE}/login`, {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ token: TOKEN }),
    redirect: "manual",
  });
  jar.push(login.headers.getSetCookie?.()[0] ?? login.headers.get("set-cookie"));
  const cookie = jar[0].split(";")[0];
  const post = (path, form) =>
    fetch(BASE + path, {
      method: "POST",
      headers: { cookie, "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(form || {}),
      redirect: "manual",
    });
  await post("/demo/seed");

  console.log("Capturing real command output…");
  const demoOut = run(["-m", "relay", "demo"]);
  const evalOut = run([join("eval", "run_eval.py"), "--repeats", "1"]);
  const state = {
    terminalDemo: terminalPage(
      "python -m relay demo",
      [
        section(demoOut, "5. Two volunteers accept", "6."),
        "",
        section(demoOut, "8. A cancellation note", "9."),
        "",
        section(demoOut, "9. The same demand", "Where to look next"),
      ].join("\n"),
      join(OUT, "terminal-demo.html")
    ),
    terminalEval: terminalPage(
      "python eval/run_eval.py",
      section(evalOut, "scenarios passing", "Wrote"),
      join(OUT, "terminal-eval.html")
    ),
  };

  const browser = await chromium.launch({ channel: "chrome", args: ["--force-device-scale-factor=1"] });

  for (const id of Object.keys(scenes)) {
    if (!wanted(id)) continue;

    // Scenes 03-05 share one storyline, so 05 needs what 03 and 04 set up.
    if (id === "05" && !state.acceptUrl) {
      const res = await post("/demo/scenario", { scenario: "hero" });
      state.heroId = (res.headers.get("location") || "").split("/").pop();
      const inbox = await (await fetch(`${BASE}/inbox`, { headers: { cookie } })).text();
      state.acceptUrl = inbox.match(/http:\/\/[^"<\s]+\/r\/[A-Za-z0-9_.\-]+/)[0];
    }

    const target = voice.get(id)?.seconds ?? 12;
    console.log(`Recording ${id}… (narration ${target.toFixed(1)}s)`);
    const startedAt = Date.now();
    const context = await browser.newContext({
      viewport: VIEWPORT,
      recordVideo: { dir: RAW, size: VIEWPORT },
    });
    await context.addInitScript(CURSOR);
    await context.addCookies([
      { name: "relay_session", value: cookie.split("=")[1], url: BASE },
    ]);
    const page = await context.newPage();
    try {
      await scenes[id](page, state);
    } catch (err) {
      console.error(`  scene ${id} failed: ${err.message}`);
    }
    // Hold the last frame so the segment covers the narration instead of being
    // stretched into slow motion later.
    const elapsed = (Date.now() - startedAt) / 1000;
    if (elapsed < target + 0.6) await sleep((target + 0.6 - elapsed) * 1000);
    else console.warn(`  scene ${id} ran ${(elapsed - target).toFixed(1)}s long; it will be trimmed`);
    const video = page.video();
    await context.close();
    const destination = join(OUT, `scene-${id}.webm`);
    rmSync(destination, { force: true });
    renameSync(await video.path(), destination);
    console.log(`  -> ${destination}`);
  }

  await browser.close();
  rmSync(RAW, { recursive: true, force: true });
  console.log("\nSegments in " + OUT);
  console.log("Record audio/01..09, then: node tools/assemble.mjs");
} finally {
  server.kill();
}
