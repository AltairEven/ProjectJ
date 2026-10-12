// P0 dependency smoke only. No browser, network, Vite server, or application.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const require = createRequire(new URL('../web/package.json', import.meta.url));
const lock = JSON.parse(readFileSync(new URL('../web/package-lock.json', import.meta.url)));
const pin = readFileSync(new URL('../.node-version', import.meta.url), 'utf8').trim();
assert.equal(process.versions.node, pin);
const react = require('react');
const ts = require('typescript');
const vite = await import(pathToFileURL(require.resolve('vite')));
require('react-dom');
assert.equal(react.version, lock.packages['node_modules/react'].version);
assert.equal(ts.version, lock.packages['node_modules/typescript'].version);
assert.equal(typeof vite.defineConfig, 'function');
execFileSync(fileURLToPath(new URL('../web/node_modules/.bin/tsc', import.meta.url)), [
  '--noEmit', '--strict', '--target', 'ES2022', '--module', 'nodenext',
  fileURLToPath(new URL('../web/fixtures/dependency-check.ts', import.meta.url)),
], { stdio: 'inherit' });
const versions = Object.fromEntries(
  ['react', 'react-dom', 'typescript', 'vite', '@types/react', '@types/react-dom']
    .map((name) => [name, lock.packages[`node_modules/${name}`].version]),
);
console.log(JSON.stringify({
  evidence: 'local_dependency_smoke', node: pin, versions,
  application: 'not_implemented', actual_voice: 'unverified',
  entry: fileURLToPath(import.meta.url),
}, null, 2));
