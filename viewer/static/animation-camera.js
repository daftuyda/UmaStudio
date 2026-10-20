import * as THREE from 'three';
import { segmentAt } from './sequence.js';

const samplers = new WeakMap();
function tracks(motion) {
  if (!motion) return {};
  if (!samplers.has(motion)) {
    const out = {};
    for (const t of motion.tracks) {
      const width = t.type === 'quaternion' ? 4 : 3;
      const times = Array.from({ length: t.values.length / width }, (_, i) => i / motion.fps);
      const Type = t.type === 'quaternion' ? THREE.QuaternionKeyframeTrack : THREE.VectorKeyframeTrack;
      out[t.type] = new Type(t.type, times, t.values).createInterpolant();
    }
    samplers.set(motion, out);
  }
  return samplers.get(motion);
}

export function cameraAt(clip, time) {
  const segment = segmentAt(clip, time);
  const data = (segment?.clip || clip)?.cameraMotion;
  if (!data) return null;
  const local = Math.max(0, time - (segment?.start || 0));
  const camera = tracks(data.camera), placement = tracks(data.position);
  const p = [...camera.position.evaluate(local)], q = [...camera.quaternion.evaluate(local)];
  const fov = camera.scale.evaluate(local)[0];
  if (![...p, ...q, fov].every(Number.isFinite) || fov <= 0 || fov >= 179) return null;
  if (data.mirrored) { p[0] *= -1; q[1] *= -1; q[2] *= -1; }
  const rootP = placement.position ? [...placement.position.evaluate(local)] : [0, 0, 0];
  const rootQ = placement.quaternion ? [...placement.quaternion.evaluate(local)] : [0, 0, 0, 1];
  if (data.positionMirrored) { rootP[0] *= -1; rootQ[1] *= -1; rootQ[2] *= -1; }
  return { p, q, fov, rootP, rootQ };
}

/** Temporarily own the camera, restoring the manual view when off or unavailable. */
export class AnimationCamera {
  constructor(camera, controls) { this.camera = camera; this.controls = controls; this.saved = null; }
  restore() {
    const s = this.saved;
    if (!s) return;
    const c = this.camera;
    c.position.copy(s.p); c.quaternion.copy(s.q); c.fov = s.fov;
    c.updateProjectionMatrix(); c.updateMatrixWorld(true);
    this.controls.target.copy(s.target); this.controls.enabled = s.enabled;
    s.root.position.copy(s.rootP); s.root.quaternion.copy(s.rootQ);
    s.root.updateMatrixWorld(true);
    this.saved = null;
  }
  apply(current, enabled, heightRatio = 1) {
    if (this.saved && this.saved.root !== current?.root) this.restore();
    const state = enabled && current?.action && cameraAt(current.action.getClip(), current.action.time);
    if (!state) { this.restore(); return false; }
    const c = this.camera, root = current.root;
    if (!this.saved) this.saved = {
      root, p: c.position.clone(), q: c.quaternion.clone(), fov: c.fov,
      target: this.controls.target.clone(), enabled: this.controls.enabled,
      rootP: root.position.clone(), rootQ: root.quaternion.clone(), rootS: root.scale.clone()
    };
    const s = this.saved;
    const base = new THREE.Matrix4().compose(s.rootP, s.rootQ, s.rootS);
    root.position.fromArray(state.rootP).applyMatrix4(base);
    root.quaternion.copy(s.rootQ).multiply(new THREE.Quaternion().fromArray(state.rootQ).normalize());
    root.updateMatrixWorld(true);
    c.position.fromArray(state.p).applyMatrix4(base);
    c.quaternion.copy(s.rootQ).multiply(new THREE.Quaternion().fromArray(state.q).normalize());
    // The authored vertical FOV belongs inside the render guide, not the extra
    // space above/below it in the editor. Exports crop this same guide.
    c.fov = THREE.MathUtils.radToDeg(2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(state.fov) / 2) * heightRatio));
    c.updateProjectionMatrix(); c.updateMatrixWorld(true);
    this.controls.enabled = false;
    return true;
  }
}
