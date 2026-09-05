/**
 * Cuts each recorded segment to the length of its narration and joins them.
 *
 *   node assemble.mjs
 *
 * Output: video/relay-demo.mp4 (1920x1080, 30fps, H.264/AAC)
 */

import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import ffmpegPath from "ffmpeg-static";
import { ROOT, narration, report, duration } from "./audio.mjs";

const OUT = join(ROOT, "video", "out");
const PARTS = join(OUT, "parts");
const FINAL = join(ROOT, "video", "relay-demo.mp4");

const ffmpeg = (args, label) => {
  const res = spawnSync(ffmpegPath, ["-hide_banner", "-loglevel", "error", "-y", ...args], {
    encoding: "utf8",
    maxBuffer: 32 * 1024 * 1024,
  });
  if (res.status !== 0) {
    console.error(`ffmpeg failed on ${label}:\n${res.stderr}`);
    process.exit(1);
  }
};

// ---------------------------------------------------------------------------

const voice = narration();
console.log("Narration:");
const spoken = report(voice);

if (voice.size === 0) {
  console.error("\nNo narration found in audio/. Nothing to assemble.");
  process.exit(1);
}

mkdirSync(PARTS, { recursive: true });
const ids = [...voice.keys()].sort();
const parts = [];

// The closing card is silent and optional.
const endCard = join(OUT, "scene-10.webm");
const END_SECONDS = 5;

console.log("\nBuilding parts:");
for (const [index, id] of ids.entries()) {
  const segment = join(OUT, `scene-${id}.webm`);
  if (!existsSync(segment)) {
    console.error(`  ${id}  missing ${segment} — run: node record.mjs ${id}`);
    process.exit(1);
  }
  const { file: audio, seconds } = voice.get(id);
  const videoSeconds = duration(segment);
  const part = join(PARTS, `part-${id}.mp4`);

  const filters = [
    "fps=30",
    "scale=1920:1080:flags=lanczos",
    "setsar=1",
    // Freeze the last frame if the segment is shorter than the line; -t trims if longer.
    "tpad=stop_mode=clone:stop_duration=60",
  ];
  if (index === 0) filters.push("fade=t=in:st=0:d=0.4");

  ffmpeg(
    [
      "-i", segment,
      "-i", audio,
      "-filter_complex",
      `[0:v]${filters.join(",")}[v];[1:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]`,
      "-map", "[v]", "-map", "[a]",
      "-t", seconds.toFixed(3),
      "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
      "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
      "-movflags", "+faststart",
      part,
    ],
    `part ${id}`
  );
  parts.push(part);
  const fit = videoSeconds >= seconds ? "trimmed" : `held ${(seconds - videoSeconds).toFixed(1)}s`;
  console.log(`  ${id}  ${seconds.toFixed(1)}s  (${fit})`);
}

if (existsSync(endCard)) {
  const part = join(PARTS, "part-10.mp4");
  ffmpeg(
    [
      "-i", endCard,
      "-f", "lavfi", "-t", String(END_SECONDS), "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
      "-filter_complex",
      `[0:v]fps=30,scale=1920:1080:flags=lanczos,setsar=1,tpad=stop_mode=clone:stop_duration=60,` +
        `fade=t=out:st=${(END_SECONDS - 0.8).toFixed(2)}:d=0.8[v]`,
      "-map", "[v]", "-map", "1:a",
      "-t", String(END_SECONDS),
      "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
      "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
      "-movflags", "+faststart",
      part,
    ],
    "end card"
  );
  parts.push(part);
  console.log(`  10  ${END_SECONDS}.0s  (closing card, silent)`);
}

const list = join(PARTS, "list.txt");
writeFileSync(list, parts.map((p) => `file '${p.replace(/\\/g, "/")}'`).join("\n"), "utf8");

console.log("\nJoining…");
rmSync(FINAL, { force: true });
ffmpeg(["-f", "concat", "-safe", "0", "-i", list, "-c", "copy", "-movflags", "+faststart", FINAL], "concat");

const total = duration(FINAL);
const minutes = Math.floor(total / 60);
const seconds = total - minutes * 60;
console.log(`\n${FINAL}`);
console.log(`Length ${minutes}m ${seconds.toFixed(0)}s  (narration ${spoken.toFixed(0)}s)`);
if (total > 300) console.error("OVER THE FIVE MINUTE LIMIT — cut a block.");
