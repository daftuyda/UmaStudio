import * as THREE from 'three';
import { post } from '/static/ui.js';
import { SurfaceCollision } from '/static/surface-collision.js';

// hair, ears, bust and tail, simulated by the game's own CySpring plugin on the server (cyspring.py).
// every 60 Hz tick the pose goes over (relative to the character root, in unity's axes) and each
// spring bone's final rotation comes back, applied the way the game applies it. between ticks the
// last result is kept on the moving pose.

export const TICK = 1 / 60;
const WARM_UP = 60;   // ticks on a still pose before showing it, like the game's warm-up

const _m = new THREE.Matrix4(), _inv = new THREE.Matrix4();
const _p = new THREE.Vector3(), _q = new THREE.Quaternion(), _s = new THREE.Vector3();

const find = (root, name) => root.getObjectByName(THREE.PropertyBinding.sanitizeNodeName(name));
// three.js <-> unity: X is mirrored
const uPos = (v, a, o) => { a[o] = -v.x; a[o + 1] = v.y; a[o + 2] = v.z; };
const uQuat = (q, a, o) => { a[o] = q.x; a[o + 1] = -q.y; a[o + 2] = -q.z; a[o + 3] = q.w; };

export class GameSprings {
  /** The rest skeleton goes to the server once; null when the character has no springs. */
  static async create(root, rest, pack) {
    const list = [...rest.keys()];
    const index = new Map(list.map((o, i) => [o, i]));
    const nodes = list.map(o => {
      const [p, q, s] = rest.get(o);
      const t = [0, 0, 0], r = [0, 0, 0, 1];
      uPos(p, t, 0); uQuat(q, r, 0);
      return [o.name, index.has(o.parent) ? index.get(o.parent) : -1, t, r, [s.x, s.y, s.z]];
    });
    const info = await post('/api/spring/new', { pack, nodes }, null);
    return info.bones.length ? new GameSprings(root, rest, info) : null;
  }

  constructor(root, rest, info) {
    this.root = root;
    this.id = info.id;
    this.parents = info.parents.map(n => find(root, n));
    this.roots = info.roots.map(n => find(root, n));
    this.bones = info.bones.map(n => find(root, n));
    this.hip = info.hip ? find(root, info.hip) : null;
    this.restQ = this.bones.map(b => rest.get(b)[1].clone());
    this.index = new Map(this.bones.map((b, i) => [b, i]));
    this.finals = this.bones.map(() => null);   // model-space rotations from the last tick
    this.enabled = true;
    this.acc = 0;
    this.pendingReset = true;
    this.dead = false;
    this.surface = new SurfaceCollision(root, info.contacts || []);
  }

  get count() { return this.bones.length; }

  /** Start over from the current pose (new clip, a jump in time): the game's reset + warm-up. */
  reset() { this.surface.reset(); this.pendingReset = true; this.acc = 0; }

  /** Physics off: the spring bones back to rest. */
  setEnabled(on) {
    this.surface.reset();
    this.enabled = on;
    this.finals.fill(null);
    if (!on) this.bones.forEach((b, i) => b.quaternion.copy(this.restQ[i]));
    this.reset();
  }

  modelSpace(obj) {
    _m.multiplyMatrices(_inv, obj.matrixWorld).decompose(_p, _q, _s);
  }

  /** `ticks` steps on the current pose (more than one only for warm-up / catching up). */
  async step(ticks = 1, signal = null) {
    if (!this.enabled || this.dead) return;
    this.surface.restore();
    const reset = this.pendingReset;
    if (reset) { ticks = WARM_UP; this.pendingReset = false; }
    this.root.updateMatrixWorld(true);
    _inv.copy(this.root.matrixWorld).invert();
    const a = new Float32Array(6 + this.parents.length * 7 + this.roots.length * 3 + this.bones.length * 4);
    a[0] = TICK; a[1] = ticks; a[2] = reset ? 1 : 0;
    if (this.hip) { this.modelSpace(this.hip); uPos(_p, a, 3); }
    let o = 6;
    for (const p of this.parents) { this.modelSpace(p); uPos(_p, a, o); uQuat(_q, a, o + 3); o += 7; }
    for (const r of this.roots) { uPos(r.position, a, o); o += 3; }
    for (const b of this.bones) { uQuat(b.quaternion, a, o); o += 4; }
    const requestID = String(this.sequence = (this.sequence || 0) + 1);
    let data;
    for (let attempt = 0; attempt < 3; attempt++) {
      signal?.throwIfAborted();
      const controller = new AbortController(), cancel = () => controller.abort();
      signal?.addEventListener('abort', cancel, { once: true });
      const timeout = setTimeout(cancel, 20000);
      try {
        const res = await fetch(`/api/spring/step?id=${this.id}`, { method: 'POST',
          signal: controller.signal, headers: { 'X-Studio': '1', 'X-Step-ID': requestID }, body: a.buffer });
        if (res.status === 410) { this.dead = true; throw new Error('Physics session expired. Reload the character.'); }
        if (!res.ok) throw new Error('Physics step failed. Reload the character and try again.');
        data = await res.arrayBuffer();
        break;
      } catch (e) {
        if (signal?.aborted || attempt === 2 || (!(e instanceof TypeError) && e.name !== 'AbortError'))
          this.pendingReset = true;
        signal?.throwIfAborted();
        if (!(e instanceof TypeError) && e.name !== 'AbortError') throw e;
        if (attempt === 2) throw new Error('The physics connection stopped responding. Try the export again.');
      } finally {
        clearTimeout(timeout);
        signal?.removeEventListener('abort', cancel);
      }
    }
    const r = new Float32Array(data);
    this.bones.forEach((b, i) => {
      this.finals[i] = r[5 * i] > 0.5 ? new THREE.Quaternion(r[5 * i + 1], -r[5 * i + 2], -r[5 * i + 3], r[5 * i + 4]) : null;
    });
    this.apply(ticks * TICK);
  }

  /** The last results onto the bones (PostSpring): each final rotation is a model-space
   *  rotation, so it is set against the parent's current one. */
  apply(dt = TICK) {
    if (!this.enabled) return;
    this.surface.restore();
    this.root.updateMatrixWorld(true);
    _inv.copy(this.root.matrixWorld).invert();
    const done = new Map();
    this.bones.forEach((b, i) => {
      let pq = done.get(b.parent);
      if (!pq) { this.modelSpace(b.parent); pq = _q.clone(); }
      const f = this.finals[i];
      if (f) b.quaternion.copy(pq).invert().multiply(f);
      done.set(b, pq.clone().multiply(b.quaternion));
    });
    this.root.updateMatrixWorld(true);
    this.surface.apply(dt);
  }

  /** Live view: a step when one is due (a promise), else the last result kept on. */
  frame(dt) {
    if (!this.enabled || this.dead) return null;
    this.acc = Math.min(this.acc + dt, 4 * TICK);
    if (this.acc < TICK && !this.pendingReset) { this.apply(0); return null; }
    const n = Math.max(1, Math.floor(this.acc / TICK));
    this.acc = Math.max(0, this.acc - n * TICK);
    return this.step(n);
  }
}
