import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { SourceTextModule } from 'node:vm';

const three = new SourceTextModule(await readFile(new URL('../static/vendor/three.module.js', import.meta.url), 'utf8'));
await three.link(() => {}); await three.evaluate();
const module = new SourceTextModule(await readFile(new URL('../static/animation-project.js', import.meta.url), 'utf8'));
await module.link(() => three); await module.evaluate();
const { validateProject, sampleProject, projectClip, putKey, clone, CHANNELS } = module.namespace;
const T = three.namespace;
const face = name => Object.fromEntries(CHANNELS.map(ch => [ch, [[name, 1]]]));
const q = new T.Quaternion().setFromAxisAngle(new T.Vector3(0, 1, 0), Math.PI / 2);
const project = validateProject({
  format: 'UmaStudio animation', version: 1, name: 'Wave', rig: 'full', fps: 30, duration: 2, loop: false,
  keys: [
    { time: 0, ease: 'linear', rotations: { Hip: [0, 0, 0, 1] }, root: [0, 0, 0], face: face('Base'), mini: null },
    { time: 2, ease: 'smooth', rotations: { Hip: q.toArray() }, root: [2, 0, 0], face: face('WaraiA'), mini: null },
  ],
});
const angle = sample => new T.Quaternion().fromArray(sample.rotations.Hip).angleTo(new T.Quaternion());
assert.ok(Math.abs(angle(sampleProject(project, 1)) - Math.PI / 4) < 1e-6);
assert.deepEqual(sampleProject(project, 1).root, [1, 0, 0]);
assert.deepEqual(sampleProject(project, 1).face.mouth, [['Base', .5], ['WaraiA', .5]]);
assert.equal(sampleProject(project, -1).root[0], 0);
assert.equal(sampleProject(project, 20).root[0], 2);
const smooth = clone(project); smooth.keys[0].ease = 'smooth';
assert.ok(sampleProject(smooth, .5).root[0] < sampleProject(project, .5).root[0]);
const held = clone(project); held.keys[0].ease = 'hold';
assert.equal(sampleProject(held, 1.999).root[0], 0);
assert.equal(sampleProject(held, 2).root[0], 2);
assert.deepEqual(validateProject(JSON.parse(JSON.stringify(project))), project, 'Project round trip preserves every authored channel');
let edited = putKey(project, { ...clone(project.keys[1]), time: .999 });
assert.equal(edited.keys[1].time, 1, 'Keys snap to the selected frame rate');
edited = putKey(edited, { ...clone(project.keys[0]), time: 1 });
assert.equal(edited.keys.length, 3, 'Updating a key replaces it, never creates duplicate times');
for (const mutate of [
  p => p.keys[0].rotations.Hip[0] = NaN,
  p => p.keys[0].rotations.Hip = [0, 0, 0, 0],
  p => p.keys[0].rotations.Hair = [0, 0, 0, 1],
  p => p.keys[0].face.mouth = [['WaraiA', Infinity]],
  p => p.keys[1].time = 0,
  p => p.keys[0].root = [Infinity, 0, 0],
  p => p.duration = 999,
  p => p.version = 2,
  p => p.rig = 'mini',
]) {
  const bad = clone(project); mutate(bad); assert.throws(() => validateProject(bad));
}
const root = new T.Group(), hip = new T.Bone(), position = new T.Bone();
hip.name = 'Hip'; position.name = 'Position'; position.position.set(3, 1, 0);
const base = new T.Quaternion().setFromAxisAngle(new T.Vector3(1, 0, 0), .2);
hip.quaternion.copy(base); root.add(position); position.add(hip);
const rest = new Map([hip, position].map(b => [b, [b.position.clone(), b.quaternion.clone(), b.scale.clone()]]));
for (const p of [project, smooth, held]) {
  const clip = projectClip(p, rest);
  const mixer = new T.AnimationMixer(root), action = mixer.clipAction(clip).play();
  action.paused = true;
  for (const t of [0, .5, 1, 1.9, 2, .25, 0, 2]) {
    action.time = t; mixer.update(0);
    const expected = sampleProject(p, t);
    assert.ok(hip.quaternion.clone().normalize().angleTo(base.clone().multiply(new T.Quaternion().fromArray(expected.rotations.Hip))) < 1e-4,
      `${p.keys[0].ease} rotation at ${t}`);
    assert.ok(Math.abs(position.position.x - 3 - expected.root[0]) < 1e-4);
  }
  action.reset().setLoop(T.LoopOnce, 1); action.clampWhenFinished = true; action.play();
  mixer.update(3);
  assert.equal(action.time, 2, 'Play once holds the final frame');
  assert.equal(action.paused, true);
  assert.ok(Math.abs(position.position.x - 5) < 1e-4);
  action.reset().setLoop(T.LoopRepeat, Infinity).play();
  mixer.update(2.5);
  assert.equal(action.time, .5, 'Looping repeats the whole authored clip');
  mixer.stopAllAction(); mixer.uncacheClip(clip);
}
console.log('Animation project tests passed: validation, key replacement, save/load, easing, faces, rest-relative motion, reverse seeking, loop and final frame.');

const imported = clone(project);
imported.version = 2;
imported.source = {
  name: 'Imported dance', duration: 2, fps: 60, frames: 121,
  tracks: [
    { bone: 'Hip', type: 'quaternion', values: Array.from({ length: 121 }, (_, i) =>
      new T.Quaternion().setFromAxisAngle(new T.Vector3(1, 0, 0), i / 120).toArray()).flat() },
    { bone: 'Hip', type: 'position', values: Array.from({ length: 121 }, (_, i) => [0, i / 120, 0]).flat() },
    { bone: 'Position', type: 'position', values: [0, 0, .5] },
  ],
};
const importedRoundtrip = validateProject(JSON.parse(JSON.stringify(imported)));
const clip = projectClip(importedRoundtrip, rest);
const mixer = new T.AnimationMixer(root), action = mixer.clipAction(clip).play();
action.paused = true; action.time = 1; mixer.update(0);
const expected = base.clone().multiply(new T.Quaternion().setFromAxisAngle(new T.Vector3(1, 0, 0), .5))
  .multiply(new T.Quaternion().setFromAxisAngle(new T.Vector3(0, 1, 0), Math.PI / 4));
assert.ok(hip.quaternion.clone().normalize().angleTo(expected) < 1e-4, 'Imported motion and correction keys compose once');
assert.ok(Math.abs(hip.position.y - .5) < 1e-6, 'Imported pelvis motion survives');
assert.deepEqual(position.position.toArray(), [4, 1, .5], 'Character movement adds to imported movement');
assert.deepEqual(imported.source, importedRoundtrip.source, 'Saving retains the full base motion separately');
for (const mutate of [
  p => p.source.tracks[0].values.pop(),
  p => p.source.tracks.push(p.source.tracks[0]),
  p => p.source.tracks[0].bone = 'Hair',
  p => p.source.tracks[0].values[0] = NaN,
  p => p.source.frames = 999999,
  p => p.rig = 'mini',
]) {
  const bad = clone(imported); mutate(bad); assert.throws(() => validateProject(bad));
}
console.log('Imported motion tests passed: base/correction composition, root and pelvis movement, persistence and malformed-track rejection.');
