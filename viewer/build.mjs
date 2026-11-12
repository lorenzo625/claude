// Bundles the viewer, three.js and angel_bear.glb into one self-contained
// index.html that works when opened straight from disk.
import { build } from 'esbuild';
import { readFile, writeFile } from 'node:fs/promises';

const here = new URL('.', import.meta.url);
const result = await build({
  entryPoints: [new URL('src/main.js', here).pathname],
  bundle: true,
  minify: true,
  format: 'iife',
  target: 'es2020',
  loader: { '.glb': 'binary' },
  legalComments: 'inline',
  write: false,
});
const script = result.outputFiles[0].text.replaceAll('</script', '<\\/script');
const template = await readFile(new URL('template.html', here), 'utf8');
const html = template.replace('/*BUNDLE*/', () => script);
await writeFile(new URL('../index.html', here), html);
console.log(`index.html: ${(html.length / 1024).toFixed(0)} KiB`);
