// Share in-flight work as well as completed results. Failed requests remain retryable.
export class LoadCache {
  constructor(limit = 64) { this.limit = limit; this.items = new Map(); }
  get(key, load) {
    if (this.items.has(key)) {
      const value = this.items.get(key);
      this.items.delete(key);
      this.items.set(key, value);
      return value;
    }
    const value = Promise.resolve().then(load);
    this.items.set(key, value);
    value.catch(() => { if (this.items.get(key) === value) this.items.delete(key); });
    while (this.items.size > this.limit) this.items.delete(this.items.keys().next().value);
    return value;
  }
}

export async function mapLimited(items, limit, load) {
  let next = 0;
  const results = new Array(items.length);
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (next < items.length) {
      const i = next++;
      results[i] = await load(items[i], i);
    }
  }));
  return results;
}
