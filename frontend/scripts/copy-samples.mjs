// Copies the sample bills into public/samples so the dashboard can offer "try a sample".
import { copyFileSync, mkdirSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const from = join(here, "..", "..", "samples");
const to = join(here, "..", "public", "samples");
mkdirSync(to, { recursive: true });
for (const name of readdirSync(from)) {
  if (/\.(png|jpe?g|pdf)$/i.test(name)) copyFileSync(join(from, name), join(to, name));
}
