// Export requests are retryable because frame indices and finish/status are
// idempotent on the server. A dropped acknowledgement must not duplicate a frame.
import { call } from '/static/ui.js';

export const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

export class FrameEncoder {
  constructor() {
    this.worker = new Worker(new URL('./frame-encoder.js', import.meta.url), { type: 'module' });
  }

  encode(data, width, height, signal) {
    signal.throwIfAborted();
    return new Promise((resolve, reject) => {
      const finish = (error, result) => {
        clearTimeout(timer);
        signal.removeEventListener('abort', cancel);
        this.worker.onmessage = this.worker.onerror = null;
        error ? reject(error) : resolve(result);
      };
      const cancel = () => finish(new DOMException('Export cancelled', 'AbortError'));
      const timer = setTimeout(() => finish(new Error('Frame capture stopped responding. Try the export again.')), 30000);
      signal.addEventListener('abort', cancel, { once: true });
      this.worker.onmessage = ({ data: result }) => finish(result.error ? new Error(result.error) : null, result);
      this.worker.onerror = () => finish(new Error('Could not start the video frame encoder. Restart the app and try again.'));
      try {
        this.worker.postMessage({ pixels: data.buffer, width, height }, [data.buffer]);
      } catch (error) { finish(error); }
    });
  }

  close() { this.worker.terminate(); }
}

export async function exportRequest(path, body, signal, binary = false) {
  for (let attempt = 0; attempt < 3; attempt++) {
    signal?.throwIfAborted();
    const controller = new AbortController();
    const cancel = () => controller.abort();
    signal?.addEventListener('abort', cancel, { once: true });
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      return await call(`/api/video/${path}`, {
        method: 'POST', signal: controller.signal,
        headers: { 'X-Studio': '1', ...(!binary && { 'Content-Type': 'application/json' }) },
        body: binary ? body : JSON.stringify(body),
      }, null);
    } catch (e) {
      signal?.throwIfAborted();
      if (!(e instanceof TypeError) && e.name !== 'AbortError') throw e;
      if (attempt === 2) throw new Error('The export connection stopped responding. Please try the export again.');
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener('abort', cancel);
    }
    await pause(150 * (attempt + 1));
  }
}

export async function uploadFrame(id, index, blob, signal) {
  const started = performance.now();
  while (true) {
    const result = await exportRequest(`frame?id=${id}&index=${index}`, blob, signal, true);
    if (result.accepted) return;
    if (performance.now() - started > 95000) throw new Error('The video encoder stopped accepting frames.');
    await pause(50);
  }
}

export function progressText(frame, frames, elapsed) {
  const rate = frame / Math.max(.001, elapsed);
  const remaining = Math.ceil((frames - frame) / rate);
  return `${frame} / ${frames} · ${rate.toFixed(1)} fps · ${Math.round(elapsed)} s elapsed` +
    (frame > 1 ? ` · ~${remaining} s left` : '');
}
