import { promisify } from 'node:util';
import { remark } from 'remark';
import { engine } from 'unified-engine';

// Use the library engine without the CLI's unused file-watcher dependency.
process.exitCode = await promisify(engine)({
  processor: remark,
  cwd: new URL('.', import.meta.url),
  extensions: ['md'],
  files: [
    '../../README.md', '../../AGENTS.md', '../../design/**/*.md',
    '../../docs/**/*.md', '../../decisions/**/*.md', '../README.md',
  ],
  rcPath: '.remarkrc.json',
  frail: true,
  quiet: true,
  out: false,
  output: false,
});
