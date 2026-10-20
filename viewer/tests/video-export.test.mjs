import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { SourceTextModule, SyntheticModule } from 'node:vm';

let handler;
const ui = new SyntheticModule(['call'], function () {
  this.setExport('call', (...args) => handler(...args));
});
const mod = new SourceTextModule(await readFile(new URL('../static/video-export.js', import.meta.url), 'utf8'));
await mod.link(() => ui);
await mod.evaluate();
const { exportRequest, uploadFrame, progressText } = mod.namespace;
let attempts = [];
handler = async (url, options) => {
  attempts.push([url, options.body]);
  if (attempts.length === 1) throw new TypeError('Failed to fetch');
  return { accepted: true };
};
const blob = new Blob(['frame']);
await uploadFrame('job', 12, blob, new AbortController().signal);
assert.equal(attempts.length, 2);
assert.equal(attempts[0][0], attempts[1][0], 'Retry preserves the frame index');
assert.equal(attempts[0][1], blob, 'Retry preserves the exact frame');
assert.equal(attempts[1][1], blob);

let calls = 0;
handler = async () => ({ accepted: ++calls > 1 });
await uploadFrame('job', 2, blob, new AbortController().signal);
assert.equal(calls, 2, 'Backpressure retries the same frame');

handler = async () => { calls++; throw new Error('Encoder failed'); };
calls = 0;
await assert.rejects(exportRequest('status', { id: 'job' }), /Encoder failed/);
assert.equal(calls, 1, 'Encoder errors are reported, not retried as network errors');

handler = async () => { throw new TypeError('Failed to fetch'); };
await assert.rejects(exportRequest('status', { id: 'job' }), /connection stopped responding/);

const cancelled = new AbortController();
cancelled.abort();
handler = async () => { throw new Error('Must not send after cancellation'); };
await assert.rejects(uploadFrame('job', 1, blob, cancelled.signal), { name: 'AbortError' });
assert.match(progressText(60, 120, 2), /30.0 fps.*2 s elapsed.*2 s left/);
console.log('Video export retry, cancellation, backpressure and progress checks passed.');
