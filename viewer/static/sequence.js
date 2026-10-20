import * as THREE from 'three';
import { sampleProject } from '/static/animation-project.js';

/** One scrub/export timeline, with hard shot boundaries and rest values for
 * properties missing from a shot. No crossfade between unrelated camera cuts. */
export function stitchClips(parts, rest) {
  const definitions = new Map(), defaults = new Map();
  for (const [obj, values] of rest) {
    const name = THREE.PropertyBinding.sanitizeNodeName(obj.name);
    ['position', 'quaternion', 'scale'].forEach((prop, i) => defaults.set(`${name}.${prop}`, values[i].toArray()));
  }
  for (const { clip } of parts) for (const track of clip.tracks) definitions.set(track.name, track);
  let duration = 0;
  const segments = parts.map(part => {
    const segment = { ...part, start: duration, duration: Math.max(part.clip.duration, 1 / 60) };
    duration += segment.duration;
    return segment;
  });
  const tracks = [];
  for (const [name, definition] of definitions) {
    const width = definition.getValueSize(), times = [], values = [];
    const fallback = defaults.get(name) || (width === 4 ? [0, 0, 0, 1] : name.endsWith('.scale') ? [1, 1, 1] : [0, 0, 0]);
    for (const segment of segments) {
      const track = segment.clip.tracks.find(t => t.name === name);
      const sample = track?.createInterpolant();
      const at = t => sample ? [...sample.evaluate(t)] : fallback;
      // Keep strictly increasing Float32 times across cuts, including long sequences.
      const epsilon = Math.max(1e-5, (segment.start + segment.duration) * 2e-7);
      const end = segment.duration - epsilon;
      const push = (t, value) => { times.push(segment.start + t); values.push(...value); };
      push(0, at(0));
      if (track) for (const t of track.times) if (t > 0 && t < end) push(t, at(t));
      push(end, at(end));
    }
    tracks.push(new definition.constructor(name, times, values, definition.getInterpolation()));
  }
  const clip = new THREE.AnimationClip('Full sequence', duration, tracks);
  clip.segments = segments;
  return clip;
}

export function segmentAt(clip, time) {
  return clip?.segments?.findLast(s => s.start <= time) || null;
}

/** Sample companion facial weights on the same timebase as the body. */
export function animatedFace(clip, time) {
  if (clip?.authorProject) return sampleProject(clip.authorProject, time).face;
  const segment = segmentAt(clip, time);
  const motion = segment ? segment.clip.faceMotion : clip?.faceMotion;
  if (!motion?.tracks.length) return null;
  const frame = Math.max(0, time - (segment?.start || 0)) * motion.fps;
  const out = { eye_l: [], eye_r: [], eyebrow_l: [], eyebrow_r: [], mouth: [] };
  for (const track of motion.tracks) {
    const last = track.values.length - 1, i = Math.min(last, Math.floor(frame)), j = Math.min(last, i + 1);
    let weight = track.values[i] + (track.values[j] - track.values[i]) * Math.min(1, frame - i);
    if (track.channel === 'mouth') weight = Math.abs(weight);
    if (Math.abs(weight) > 0.00001) out[track.channel].push([track.shape, weight]);
  }
  return out;
}
