/** Maps the recorded narration files to scene ids and reports their durations. */

import { readdirSync, existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import ffprobeStatic from "ffprobe-static";

const HERE = dirname(fileURLToPath(import.meta.url));
export const ROOT = resolve(HERE, "..");
export const AUDIO_DIR = join(ROOT, "audio");

// The recorder names files after the opening words, so match on those rather than
// asking for a rename. First match wins, so keep these distinctive.
const OPENERS = [
  ["01", "ten past eight"],
  ["02", "twenty minutes"],
  ["03", "here s the cancellation"],
  ["04", "two of them get asked"],
  ["05", "now the rota changes"],
  ["06", "second gap"],
  ["07", "two people accepting"],
  ["08", "one strands agent"],
  ["09", "thirty scenarios"],
];

const normalise = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();

export function duration(file) {
  const res = spawnSync(
    ffprobeStatic.path,
    ["-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", file],
    { encoding: "utf8" }
  );
  return parseFloat((res.stdout || "0").trim()) || 0;
}

/** @returns {Map<string, {file: string, seconds: number}>} */
export function narration() {
  const found = new Map();
  if (!existsSync(AUDIO_DIR)) return found;

  const files = readdirSync(AUDIO_DIR).filter((f) => /\.(mp3|m4a|wav|aac|ogg|webm)$/i.test(f));
  for (const [id, opener] of OPENERS) {
    // An explicit 01.mp3 style name always wins over the auto-generated one.
    const explicit = files.find((f) => new RegExp(`^${id}\\.`, "i").test(f));
    const byOpener = files.find((f) => normalise(f).includes(normalise(opener)));
    const chosen = explicit || byOpener;
    if (chosen) {
      const path = join(AUDIO_DIR, chosen);
      found.set(id, { file: path, seconds: duration(path) });
    }
  }
  return found;
}

export function report(found) {
  let total = 0;
  for (const [id, meta] of found) {
    total += meta.seconds;
    console.log(`  ${id}  ${meta.seconds.toFixed(1).padStart(5)}s  ${meta.file.split(/[\\/]/).pop()}`);
  }
  const missing = OPENERS.map(([id]) => id).filter((id) => !found.has(id));
  if (missing.length) console.log(`  missing: ${missing.join(", ")}`);
  console.log(`  total ${Math.floor(total / 60)}m ${(total % 60).toFixed(0)}s`);
  return total;
}
