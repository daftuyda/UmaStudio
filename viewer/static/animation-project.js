import * as THREE from 'three';

export const JOINTS = ['Hip', 'Waist', 'Spine', 'Chest', 'Neck', 'Head',
  ...['L', 'R'].flatMap(s => ['Shoulder', 'Arm', 'Elbow', 'Wrist', 'Thigh', 'Knee', 'Ankle', 'Toe'].map(n => `${n}_${s}`)),
  ...['L', 'R'].flatMap(s => ['Thumb', 'Index', 'Middle', 'Ring', 'Pinky'].flatMap(n => [1, 2, 3].map(i => `${n}_0${i}_${s}`)))];
export const CHANNELS = ['eye_l', 'eye_r', 'eyebrow_l', 'eyebrow_r', 'mouth'];
export const clone = value => JSON.parse(JSON.stringify(value));
export const frameTime = (time, fps) => Math.round(time * fps) / fps;
const identity = [0, 0, 0, 1];
const finiteArray = (a, n) => Array.isArray(a) && a.length === n && a.every(Number.isFinite);

/** Project files contain rest-relative rotations, never simulated hair/cloth transforms. */
export function validateProject(input) {
  const fail = () => { throw new Error('Invalid animation project. Expected a UmaStudio animation file (version 1 or 2).'); };
  if (!input || input.format !== 'UmaStudio animation' || ![1, 2].includes(input.version) ||
      !['full', 'mini'].includes(input.rig) || ![30, 60].includes(input.fps) ||
      !Number.isFinite(input.duration) || input.duration < 1 / input.fps || input.duration > 120 ||
      typeof input.name !== 'string' || !input.name.trim() || input.name.length > 100 ||
      typeof input.loop !== 'boolean' || !Array.isArray(input.keys) || !input.keys.length || input.keys.length > 500) fail();
  const p = { format: input.format, version: input.version, name: input.name.trim(), rig: input.rig,
    fps: input.fps, duration: frameTime(input.duration, input.fps), loop: input.loop, keys: [] };
  if (input.version === 2) {
    const source = input.source;
    if (p.rig !== 'full' || !source || typeof source.name !== 'string' || source.name.length > 100 ||
        source.fps !== 60 || !Number.isInteger(source.frames) || source.frames < 2 || source.frames > 7201 ||
        !Number.isFinite(source.duration) || Math.abs(source.duration - (source.frames - 1) / 60) > 1e-5 ||
        !Array.isArray(source.tracks) || !source.tracks.length || source.tracks.length > 128) fail();
    const names = new Set();
    let count = 0;
    const tracks = source.tracks.map(t => {
      if (!t || ![...JOINTS, 'Position'].includes(t.bone) || !['position', 'quaternion'].includes(t.type)) fail();
      const width = t.type === 'quaternion' ? 4 : 3, key = `${t.bone}.${t.type}`;
      if (names.has(key) || !Array.isArray(t.values) || ![width, width * source.frames].includes(t.values.length) ||
          t.values.some(v => !Number.isFinite(v) || Math.abs(v) > 1000)) fail();
      names.add(key); count += t.values.length;
      if (count > 4000000) fail();
      if (width === 4) for (let i = 0; i < t.values.length; i += 4) {
        if (Math.abs(Math.hypot(...t.values.slice(i, i + 4)) - 1) > .01) fail();
      }
      return { bone: t.bone, type: t.type, values: [...t.values] };
    });
    p.source = { name: source.name, fps: 60, frames: source.frames, duration: source.duration, tracks };
  } else if (input.source) fail();
  for (const k of input.keys) {
    if (!k || !Number.isFinite(k.time) || k.time < 0 || k.time > p.duration + 1e-6 ||
        !['linear', 'smooth', 'hold'].includes(k.ease) || !finiteArray(k.root, 3) ||
        k.root.some(v => Math.abs(v) > 100) || !k.rotations || typeof k.rotations !== 'object') fail();
    const rotations = {};
    for (const [name, q] of Object.entries(k.rotations)) {
      if (!JOINTS.includes(name) || !finiteArray(q, 4)) fail();
      const length = Math.hypot(...q);
      if (!Number.isFinite(length) || length < 1e-8) fail();
      rotations[name] = q.map(v => v / length);
    }
    const face = {};
    for (const ch of CHANNELS) {
      if (!Array.isArray(k.face?.[ch]) || k.face[ch].length > 64) fail();
      face[ch] = k.face[ch].map(pair => {
        if (!Array.isArray(pair) || pair.length !== 2 || typeof pair[0] !== 'string' || pair[0].length > 100 ||
            !Number.isFinite(pair[1]) || Math.abs(pair[1]) > 10) fail();
        return [...pair];
      });
    }
    let mini = null;
    if (input.rig === 'mini') {
      mini = {};
      for (const ch of ['eye_l', 'eye_r', 'brow_l', 'brow_r', 'mouth', 'cheek']) {
        const v = k.mini?.[ch];
        if (!Number.isInteger(v) || v < 0 || v > 31) fail();
        mini[ch] = v;
      }
    }
    p.keys.push({ time: frameTime(k.time, p.fps), ease: k.ease, rotations, root: [...k.root], face, mini });
  }
  p.keys.sort((a, b) => a.time - b.time);
  if (p.keys.some((k, i) => i && Math.abs(k.time - p.keys[i - 1].time) < 1e-7)) fail();
  return p;
}

export function sampleProject(p, time) {
  const t = Math.max(0, Math.min(p.duration, time));
  const index = Math.max(0, p.keys.findLastIndex(k => k.time <= t));
  const a = p.keys[index], b = p.keys[index + 1] || a;
  let f = b.time === a.time ? 0 : Math.max(0, Math.min(1, (t - a.time) / (b.time - a.time)));
  if (a.ease === 'hold') f = 0;
  else if (a.ease === 'smooth') f = f * f * (3 - 2 * f);
  const rotations = {}, face = {};
  for (const name of new Set([...Object.keys(a.rotations), ...Object.keys(b.rotations)])) {
    rotations[name] = new THREE.Quaternion().fromArray(a.rotations[name] || identity)
      .slerp(new THREE.Quaternion().fromArray(b.rotations[name] || identity), f).toArray();
  }
  for (const ch of CHANNELS) {
    const weights = new Map();
    for (const [name, w] of a.face[ch]) weights.set(name, (weights.get(name) || 0) + w * (1 - f));
    for (const [name, w] of b.face[ch]) weights.set(name, (weights.get(name) || 0) + w * f);
    face[ch] = [...weights].filter(([, w]) => Math.abs(w) > 1e-6);
  }
  return { rotations, root: a.root.map((v, i) => v + (b.root[i] - v) * f), face, mini: a.mini };
}

export function putKey(p, key) {
  const k = clone(key);
  k.time = Math.max(0, Math.min(p.duration, frameTime(k.time, p.fps)));
  const keys = p.keys.filter(old => Math.abs(old.time - k.time) > 1e-7);
  keys.push(k);
  return validateProject({ ...p, keys });
}

/** Imported rest-relative motion is a separate base layer; authored keys are corrections. */
export function sourceValue(p, bone, type, time) {
  const track = p.source?.tracks.find(t => t.bone === bone && t.type === type);
  if (!track) return type === 'quaternion' ? identity : [0, 0, 0];
  const width = type === 'quaternion' ? 4 : 3, values = track.values;
  if (values.length === width) return values.slice();
  const frame = Math.max(0, Math.min(p.source.frames - 1, time * p.source.fps));
  const i = Math.floor(frame), j = Math.min(p.source.frames - 1, i + 1), f = frame - i;
  const a = values.slice(i * width, (i + 1) * width), b = values.slice(j * width, (j + 1) * width);
  if (width === 4) return new THREE.Quaternion().fromArray(a).slerp(new THREE.Quaternion().fromArray(b), f).normalize().toArray();
  return a.map((v, k) => v + (b[k] - v) * f);
}

/** Bake easing at 60 Hz into standard tracks so the existing mixer and export path work.
 * Include both sides of held cuts and every authored time, including the final frame. */
export function projectClip(project, rest) {
  const p = validateProject(project);
  const times = new Set([0, p.duration, ...p.keys.map(k => k.time)]);
  for (let i = 1; i < Math.ceil(p.duration * 60); i++) times.add(i / 60);
  p.keys.forEach((k, i) => { if (i && p.keys[i - 1].ease === 'hold') times.add(Math.max(0, k.time - 0.0001)); });
  const sorted = [...times].sort((a, b) => a - b), samples = sorted.map(t => sampleProject(p, t));
  const tracks = [];
  for (const [bone, [position, rotation]] of rest) {
    if (JOINTS.includes(bone.name) || (bone.name === 'Position' && p.source)) {
      const values = samples.flatMap((s, i) => rotation.clone()
        .multiply(new THREE.Quaternion().fromArray(sourceValue(p, bone.name, 'quaternion', sorted[i])))
        .multiply(new THREE.Quaternion().fromArray(s.rotations[bone.name] || identity)).normalize().toArray());
      tracks.push(new THREE.QuaternionKeyframeTrack(`${bone.name}.quaternion`, sorted, values));
    }
    if (bone.name === 'Position' || p.source?.tracks.some(t => t.bone === bone.name && t.type === 'position')) {
      tracks.push(new THREE.VectorKeyframeTrack(`${bone.name}.position`, sorted,
        samples.flatMap((s, index) => {
          const base = sourceValue(p, bone.name, 'position', sorted[index]);
          return position.toArray().map((v, i) => v + base[i] + (bone.name === 'Position' ? s.root[i] : 0));
        })));
    }
  }
  const clip = new THREE.AnimationClip(p.name, p.duration, tracks);
  clip.authorProject = p;
  return clip;
}
