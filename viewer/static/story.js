import * as THREE from 'three';

// one character of a story scene, played from the game's own timeline (/api/story): body
// motion, additive layers, turn, prop, face keys and the voice's lip-sync. frames are the
// timeline's (30 per second), counted from the start of the story.
//
// read from the data as-is: which motion / layer / face shape plays when, at what weight, speed
// and blend time. not in the data, so chosen here: face keys blend over FACE_BLEND frames. while
// the voice is audible the mouth goes from the key's second shape list (closed) to its first
// (talking) by how much the lip-sync opens it, with the vowel shapes on top (large ones inside
// "Type 1" mouth clips). "Type 0" mouth clips keep it closed. blinks follow a fixed seeded rhythm.

export const FPS = 30;
const FACE_BLEND = 10;
const MOTION_DIR = '3d/motion/event/body/type00/';

// "take03_01" -> anm_eve_type00_take03_01_loop, "walkin06_mirror" -> ..._walkin06_loop_mirror.
function motionPaths(name) {
  const n = name.toLowerCase(), m = n.endsWith('_mirror'), base = m ? n.slice(0, -7) : n, tail = m ? '_mirror' : '';
  return [`${MOTION_DIR}anm_eve_type00_${base}_loop${tail}`, `${MOTION_DIR}anm_eve_type00_${base}${tail}`];
}

// "upleftB" -> uprightb_plus_mirror, "downB_IK" -> downb_ik_add, ...
function layerPath(name) {
  let n = name.toLowerCase(), mirror = false;
  if (n.includes('left')) { n = n.replace('left', 'right'); mirror = true; }
  const file = n.endsWith('_ik') ? n.slice(0, -3) + '_ik_add' : n + '_plus';
  return `${MOTION_DIR}plus/anm_eve_type00_${file}${mirror ? '_mirror' : ''}`;
}

const smooth = x => { const u = Math.min(1, Math.max(0, x)); return u * u * (3 - 2 * u); };

// a Unity AnimationCurve as the timeline stores it: [time, value, inSlope, outSlope] keys
function curve(keys, x) {
  if (!keys || !keys.length) return x;
  if (x <= keys[0][0]) return keys[0][1];
  const last = keys[keys.length - 1];
  if (x >= last[0]) return last[1];
  const i = keys.findIndex(k => k[0] > x) - 1;
  const [t0, v0, , o0] = keys[i], [t1, v1, i1] = keys[i + 1];
  const d = t1 - t0, u = (x - t0) / d, u2 = u * u, u3 = u2 * u;
  return (2 * u3 - 3 * u2 + 1) * v0 + (u3 - 2 * u2 + u) * o0 * d + (-2 * u3 + 3 * u2) * v1 + (u3 - u2) * i1 * d;
}

function mixWeights(a, b, k) {
  const out = {};
  for (const [n, v] of Object.entries(a)) out[n] = (out[n] || 0) + v * (1 - k);
  for (const [n, v] of Object.entries(b)) out[n] = (out[n] || 0) + v * k;
  return out;
}

export function faceString(w) {
  const s = Object.entries(w).filter(([n, v]) => v > 0.005 && n !== 'Base')
    .map(([n, v]) => `${n}__${Math.round(v * 100)}`).join('|');
  return s || 'Base';
}

// a face part's keys: each blends from wherever the face was when it started
class FaceTrack {
  constructor(keys) {
    this.keys = keys.sort((a, b) => a.at - b.at);
    this.from = [];
  }
  value(t, i = this.keys.findLastIndex(k => k.at <= t)) {
    if (i < 0) return [{}, {}];
    const k = this.keys[i];
    if (this.from[i] === undefined) this.from[i] = this.value(k.at, i - 1);
    const f = this.from[i], x = smooth((t - k.at) / FACE_BLEND);
    return [mixWeights(f[0], k.a, x), mixWeights(f[1], k.b, x)];
  }
}

export class StoryPlayer {
  /** data: /api/story; slot: the character's track index in each block. */
  constructor(data, slot = 0) {
    this.talk = true;      // false: no lip-sync, the mouth only follows the face keys (closed list)
    this.tl = data.timeline;
    this.lips = data.lipsync || {};
    this.slot = slot;
    const all = kind => this.tl.blocks.flatMap(b => (b.charas[slot]?.[kind] || []).map(c => ({ ...c, at: b.start + (c.StartFrame || 0), block: b.index })));
    this.motions = all('motion');
    this.adds = all('add');
    this.turns = all('turn');
    this.offsets = all('offset');
    this.props = all('prop');
    this.mouthClips = all('lips');
    this.voice = this.tl.blocks.flatMap(b => b.text.filter(c => c.CueId >= 0 && c.VoiceLength > 0)
      .map(c => ({ at: b.start + c.StartFrame, len: c.VoiceLength, cue: c.CueId })));
    const face = kind => new FaceTrack(all(kind).map(c => ({ at: c.at, a: c.FaceInfoList1 || {}, b: c.FaceInfoList2 || {} })));
    this.brow = face('eyebrow');
    this.eye = face('eye');
    this.mouth = face('mouth');
    this.blinks = this.blinkTimes();
  }

  get length() { return this.tl.length; }

  /** Loads the clips used between frames `from` and `to` and sets up one mixer action
   *  per motion and layer. */
  async prepare(chara, libraryClip, from = 0, to = this.length) {
    this.c = chara;
    chara.mixer.stopAllAction();
    for (const [o, [p, q, s]] of chara.rest) { o.position.copy(p); o.quaternion.copy(q); o.scale.copy(s); }
    const used = list => new Set(this.runs(list).filter(m => m.at <= to && m.at + m.len >= from).map(m => m.MotionName));
    this.bodyActions = new Map();
    for (const name of used(this.motions)) {
      let clip = null;
      for (const path of motionPaths(name)) {
        try { clip = await libraryClip(path); break; } catch { /* try the next name */ }
      }
      if (!clip) { console.warn('story: no motion for', name); continue; }
      const a = chara.mixer.clipAction(clip);
      a.setLoop(THREE.LoopRepeat, Infinity);
      a.play();
      this.bodyActions.set(name, a);
    }
    this.layerActions = new Map();
    for (const name of used(this.adds)) {
      const base = await libraryClip(layerPath(name)).catch(() => null);
      if (!base) { console.warn('story: no layer for', name); continue; }
      const clip = THREE.AnimationUtils.makeClipAdditive(base.clone(), 0);
      const a = chara.mixer.clipAction(clip);
      a.blendMode = THREE.AdditiveAnimationBlendMode;
      a.setLoop(THREE.LoopOnce, 1);
      a.clampWhenFinished = true;
      a.play();
      this.layerActions.set(name, a);
    }
  }

  // a clip that starts a block with the same motion as the one before, at no blend, carries on
  // instead of restarting.
  runs(list) {
    const out = [];
    for (const c of list) {
      const prev = out[out.length - 1];
      if (prev && prev.MotionName === c.MotionName && !c.BlendTime && c.at <= prev.at + prev.len) {
        prev.len = Math.max(prev.len, c.at + c.ClipLength - prev.at);
        continue;
      }
      out.push({ ...c, len: c.ClipLength });
    }
    return out;
  }

  /** Poses the character for timeline frame t (fractional), face included. */
  pose(t, face) {
    const c = this.c;
    // body: the motion whose clip covers t, crossfading over its blend time
    const body = this.runs(this.motions);
    const weights = new Map();
    body.forEach((m, i) => {
      if (t < m.at) return;
      const next = body[i + 1];
      let w = m.BlendTime ? smooth((t - m.at) / (m.BlendTime * FPS)) : 1;
      if (next && t >= next.at) w *= next.BlendTime ? 1 - smooth((t - next.at) / (next.BlendTime * FPS)) : 0;
      if (w <= 0) return;
      const a = this.bodyActions.get(m.MotionName);
      if (!a) return;
      const dur = a.getClip().duration;
      a.time = (((t - m.at) / FPS) * (m.MotionSpeed || 1)) % dur;
      weights.set(m.MotionName, Math.max(weights.get(m.MotionName) || 0, w));
    });
    for (const [n, a] of this.bodyActions) { const w = weights.get(n) || 0; a.enabled = w > 0; a.setEffectiveWeight(w); }

    // additive layers: played once and held, faded in over their blend time while the one before
    // fades out, gone when their clip ends.
    const adds = this.runs(this.adds);
    const lw = new Map();
    adds.forEach((m, i) => {
      if (t < m.at || t >= m.at + m.len) return;
      const next = adds[i + 1];
      let w = m.BlendTime ? smooth((t - m.at) / (m.BlendTime * FPS)) : 1;
      if (next && t >= next.at) w *= next.BlendTime ? 1 - smooth((t - next.at) / (next.BlendTime * FPS)) : 0;
      const a = this.layerActions.get(m.MotionName);
      if (!a) return;
      a.time = Math.min(a.getClip().duration, ((t - m.at) / FPS) * (m.MotionSpeed || 1));
      lw.set(m.MotionName, Math.max(lw.get(m.MotionName) || 0, w * (m.BlendWeight ?? 1)));
    });
    for (const [n, a] of this.layerActions) { const w = lw.get(n) || 0; a.enabled = w > 0; a.setEffectiveWeight(w); }
    c.mixer.update(0);

    const turn = this.turns.findLast(k => k.at <= t);
    if (turn) {
      const k = turn.ClipLength ? Math.min(1, (t - turn.at) / turn.ClipLength) : 1;
      // unity's +Y turn is a -Y turn here (X is mirrored)
      c.root.rotation.y = -THREE.MathUtils.degToRad(turn.StartAngle + (turn.EndAngle - turn.StartAngle) * k);
    }
    // offset track: a move relative to her stage position (her own axes: unity's local -x, her left,
    // is +x here), along the clip's curve. it stays where it ends.
    const off = this.offsets.findLast(k => k.at <= t);
    c.root.position.set(0, 0, 0);
    if (off) {
      const k = curve(off.Curve, off.ClipLength ? Math.min(1, (t - off.at) / off.ClipLength) : 1);
      const a = off.StartPos, b = off.EndPos;
      c.root.position.set(-(a.x + (b.x - a.x) * k), a.y + (b.y - a.y) * k, a.z + (b.z - a.z) * k);
    }
    if (face) Object.assign(face, this.face(t));
  }

  /** The prop the timeline has in her hands at t: {id, show} or null before the first. */
  prop(t) {
    const p = this.props.findLast(k => k.at <= t);
    return p ? { id: p.PropMainId, show: !!p.Show } : null;
  }

  voiceAt(t) {
    const v = this.voice.find(v => t >= v.at && t < v.at + v.len);
    const rows = v && this.lips[v.cue];
    if (!rows || !rows.length) return null;
    const ms = (t - v.at) / FPS * 1000;
    const step = rows.length > 1 ? rows[1][0] - rows[0][0] : 1000 / 60;
    const r = rows[Math.min(rows.length - 1, Math.max(0, Math.round(ms / step)))];
    return { a: r[1], i: r[2], u: r[3], e: r[4], o: r[5], db: r[6] };
  }

  face(t) {
    const [browL, browR] = this.brow.value(t);
    let [eyeL, eyeR] = this.eye.value(t);
    const [talk, closed] = this.mouth.value(t);

    const clip = this.mouthClips.find(k => t >= k.at && t < k.at + k.ClipLength);
    const v = !this.talk || (clip && clip.Type === 0) ? null : this.voiceAt(t);
    let mouth = closed;
    if (v) {
      const size = clip && clip.Type === 1 ? 'L' : 'S';
      const open = Math.min(1, v.a + v.i + v.u + v.e + v.o);
      mouth = mixWeights(closed, talk, open);
      for (const [k, w] of [['A', v.a], ['I', v.i], ['U', v.u], ['E', v.e], ['O', v.o]])
        if (w > 0) mouth[`TalkA_${k}_${size}`] = (mouth[`TalkA_${k}_${size}`] || 0) + w;
    }
    const b = this.blink(t);
    if (b > 0) {
      const shut = w => ({ ...mixWeights(w, {}, b), CloseA: (w.CloseA || 0) * (1 - b) + b });
      eyeL = shut(eyeL); eyeR = shut(eyeR);
    }
    return { eyebrow_l: faceString(browL), eyebrow_r: faceString(browR), eye_l: faceString(eyeL),
             eye_r: faceString(eyeR), mouth: faceString(mouth) };
  }

  // blinks every 2.5-5 s (seeded, so a render is repeatable): shut in 2 frames, open in 4
  blinkTimes() {
    let s = 1068, t = 45;
    const out = [];
    const rand = () => ((s = (s * 16807) % 2147483647) / 2147483647);
    while (t < this.tl.length) { out.push(t); t += 75 + rand() * 75; }
    return out;
  }
  blink(t) {
    const k = this.blinks.findLast(x => x <= t);
    if (k === undefined) return 0;
    const d = t - k;
    return d < 2 ? d / 2 : d < 3 ? 1 : d < 7 ? 1 - (d - 3) / 4 : 0;
  }
}
