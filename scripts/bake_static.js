#!/usr/bin/env node
/**
 * scripts/bake_static.js — Prebuild: copy precomputed data into web/public/data/
 *
 * Generates:
 *   web/public/data/webinars.json          — ordered list of all webinar index objects
 *   web/public/data/{slug}/results.json    — {index, top_moments, content}
 *   web/public/data/{slug}/events.json     — replay events array
 *
 * Run from repo root: node scripts/bake_static.js
 * Or via: npm run prebuild (from web/)
 */

const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const DATA_DIR = path.join(ROOT, "data");
const PRECOMPUTED_DIR = path.join(DATA_DIR, "precomputed");
const OUT_DIR = path.join(ROOT, "web", "public", "data");

function readJSON(p) {
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return null;
  }
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

ensureDir(OUT_DIR);

const slugDirs = fs
  .readdirSync(PRECOMPUTED_DIR, { withFileTypes: true })
  .filter((e) => e.isDirectory())
  .map((e) => e.name);

const webinars = [];

for (const slug of slugDirs) {
  const indexPath = path.join(PRECOMPUTED_DIR, slug, "index.json");
  const eventsPath = path.join(PRECOMPUTED_DIR, slug, "events.json");
  const candidatesPath = path.join(DATA_DIR, `${slug}-candidates.golden.json`);
  const contentPath = path.join(DATA_DIR, `${slug}-content.golden.json`);

  const index = readJSON(indexPath);
  if (!index) {
    console.warn(`  skip ${slug}: no index.json`);
    continue;
  }

  const candidates = readJSON(candidatesPath) || [];
  const content = readJSON(contentPath) || [];
  const events = readJSON(eventsPath) || [];

  // Build top_moments (same logic as backend /results/{slug})
  const contentIds = new Set(content.map((c) => c.id));
  const topMoments = candidates
    .filter((c) => c.top8 && contentIds.has(c.id))
    .sort((a, b) => (b.score || 0) - (a.score || 0))
    .map((c) => ({
      id: c.id,
      score: Math.min(c.score || 0, 95),
      _raw_score: c.score || 0,
      speaker_name: c.speaker_name || "",
      post_for: c.post_for || "",
      moment_type: c.moment_type || "",
      angle: c.angle || "",
      top8: c.top8 || false,
      why: c.why || "",
      trimmed: c.trimmed || false,
      start_ms: c.start_ms || 0,
      end_ms: c.end_ms || 0,
      duration_s: c.duration_s || 0,
      hook_line: c.hook_line || "",
    }));

  const slugDir = path.join(OUT_DIR, slug);
  ensureDir(slugDir);

  fs.writeFileSync(
    path.join(slugDir, "results.json"),
    JSON.stringify({ index, top_moments: topMoments, content }, null, 2)
  );
  fs.writeFileSync(
    path.join(slugDir, "events.json"),
    JSON.stringify(events, null, 2)
  );

  webinars.push(index);
  console.log(`  ✓ ${slug}`);
}

// Sort by date descending (same as backend /webinars)
webinars.sort((a, b) => (b.date || "").localeCompare(a.date || ""));
fs.writeFileSync(
  path.join(OUT_DIR, "webinars.json"),
  JSON.stringify(webinars, null, 2)
);

console.log(`\nBaked ${webinars.length} webinars → web/public/data/`);
