// Post-build: the landing page is pure static content (links + <details>), so the React
// runtime Next.js ships is dead weight on 4G. Remove scripts from the exported HTML and
// delete the JS chunks, keeping CSS. Fails the build if any page still references JS.
import { readdir, readFile, writeFile, rm } from "node:fs/promises";
import { join } from "node:path";

const OUT = "out";

async function htmlFiles(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = await Promise.all(
    entries.map((e) => {
      const p = join(dir, e.name);
      if (e.isDirectory()) return e.name === "_next" ? [] : htmlFiles(p);
      return e.name.endsWith(".html") ? [p] : [];
    }),
  );
  return files.flat();
}

let pages = 0;
for (const file of await htmlFiles(OUT)) {
  const html = await readFile(file, "utf8");
  const stripped = html
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, "")
    .replace(/<link\b[^>]*\bas="script"[^>]*>/gi, "");
  if (/<script\b/i.test(stripped)) throw new Error(`script left in ${file}`);
  await writeFile(file, stripped);
  pages++;
}
await rm(join(OUT, "_next", "static", "chunks"), { recursive: true, force: true });
console.log(`strip-js: ${pages} pages, 0 KB JS`);
