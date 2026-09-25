const fs = require('node:fs');
const file = 'src/client/initial-app/index.tsx';
const source = fs.readFileSync(file, 'utf8');
const marker = '// Analytics\n';
if (source.split(marker).length !== 2 || !source.includes("script.src = 'https://www.google-analytics.com/analytics.js';")) {
  throw new Error('Squoosh analytics source changed; review the pinned patch');
}
fs.writeFileSync(file, source.slice(0, source.indexOf(marker)) +
  '// ODS local build: keep call sites inert without queueing analytics events.\nwindow.ga = Object.assign(() => {}, { q: [] as any[] });\n');
