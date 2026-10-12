import assert from 'node:assert/strict';
import {
  appendFileSync, copyFileSync, mkdirSync, mkdtempSync,
  rmSync, symlinkSync, writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

test('document check handles Markdown forms and rejects a broken link', () => {
  const root = mkdtempSync(join(tmpdir(), 'project-j-docs-'));
  try {
    const web = join(root, 'project_j/web');
    mkdirSync(web, { recursive: true });
    for (const name of ['check_docs.mjs', '.remarkrc.json']) {
      copyFileSync(new URL(name, import.meta.url), join(web, name));
    }
    symlinkSync(fileURLToPath(new URL('node_modules', import.meta.url)),
      join(web, 'node_modules'), 'dir');
    for (const dir of ['design', 'docs', 'decisions']) {
      mkdirSync(join(root, dir));
      writeFileSync(join(root, dir, 'README.md'), '# Example\n');
    }
    writeFileSync(join(root, 'AGENTS.md'), '# Rules\n');
    writeFileSync(join(root, 'project_j/README.md'), '# Tools\n');
    const fixture = join(root, 'README.md');
    writeFileSync(fixture, '# Same\n\n# Same\n\n' +
      '[Title](#same-1 "title")\n\n[Reference][ref]\n\n[ref]: #same-1\n\n' +
      '```md\n[Code](missing.md)\n```\n');
    const run = () => spawnSync(process.execPath, [join(web, 'check_docs.mjs')], {
      cwd: tmpdir(), encoding: 'utf8',
    });
    const valid = run();
    assert.equal(valid.status, 0, valid.stderr);
    appendFileSync(fixture, '\n[Broken](missing.md)\n');
    const invalid = run();
    assert.equal(invalid.status, 1, invalid.stderr);
    assert.match(invalid.stderr, /missing\.md/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
