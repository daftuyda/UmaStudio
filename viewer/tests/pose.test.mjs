// node --experimental-vm-modules viewer/tests/pose.test.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { SourceTextModule, SyntheticModule } from 'node:vm';

class Element {
  listeners = new Map();
  style = {};
  clientHeight = 800;
  clientWidth = 1000;
  captured = new Set();
  addEventListener(type, fn, capture = false) {
    const list = this.listeners.get(type) || [];
    if (!list.some(x => x.fn === fn)) list.push({ fn, capture });
    this.listeners.set(type, list);
  }
  removeEventListener(type, fn) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter(x => x.fn !== fn));
  }
  dispatchEvent(e) {
    e.preventDefault ||= () => {};
    e.stopImmediatePropagation = () => { e.stopped = true; };
    for (const { fn } of [...(this.listeners.get(e.type) || [])].sort((a, b) => +b.capture - +a.capture)) {
      fn(e);
      if (e.stopped) break;
    }
  }
  getBoundingClientRect() { return { left: 0, top: 0, width: 1000, height: 800 }; }
  getRootNode() { return document; }
  setPointerCapture(id) { this.captured.add(id); }
  hasPointerCapture(id) { return this.captured.has(id); }
  releasePointerCapture(id) {
    if (this.captured.delete(id)) this.dispatchEvent({ type: 'lostpointercapture', pointerId: id });
  }
}
globalThis.window = new Element();
globalThis.document = new Element();
document.defaultView = window;
const canvas = new Element();
canvas.ownerDocument = document;
const elements = new Map();
const $ = id => {
  if (!elements.has(id)) elements.set(id, new Element());
  return elements.get(id);
};
const three = new SourceTextModule(await readFile(new URL('../static/vendor/three.module.js', import.meta.url), 'utf8'));
await three.link(() => {});
await three.evaluate();
const THREE = three.namespace;
const ui = new SyntheticModule(['$', 'store', 'toast', 'typing'], function () {
  this.setExport('$', $);
  this.setExport('store', { get: (k, d) => d, set() {} });
  this.setExport('toast', () => {});
  this.setExport('typing', () => false);
});
await ui.link(() => {});
await ui.evaluate();
async function module(path, deps) {
  const m = new SourceTextModule(await readFile(new URL(path, import.meta.url), 'utf8'));
  await m.link(name => deps[name] || three);
  await m.evaluate();
  return m;
}
const transform = await module('../static/vendor/addons/controls/TransformControls.js', {});
const orbit = await module('../static/vendor/addons/controls/OrbitControls.js', {});
const joint = await module('../static/joint-controls.js', { 'three/addons/controls/TransformControls.js': transform });
const poseModule = await module('../static/pose.js', { '/static/ui.js': ui, '/static/joint-controls.js': joint });
const pose = poseModule.namespace;
const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(45, 1.25, .1, 100);
camera.position.z = 5;
camera.updateMatrixWorld();
const controls = new orbit.namespace.OrbitControls(camera, canvas);
let paused = false;
pose.initPose({ scene, camera, canvas, controls, pauseMotion: () => { paused = true; return () => { paused = false; }; } });
const root = new THREE.Group(), bone = new THREE.Bone();
bone.name = 'Hip';
root.add(bone);
scene.add(root);
pose.onModel({ root });
pose.poseDebug.setShowing(true);
pose.updateDots();
scene.updateMatrixWorld(true);
const tc = scene.children.find(x => x instanceof joint.namespace.JointControls);
const emit = (type, extra = {}) => canvas.dispatchEvent({
  type, pointerId: 1, pointerType: 'mouse', button: type === 'pointermove' ? -1 : 0,
  buttons: type === 'pointerup' ? 0 : 1, clientX: 500, clientY: 400, ...extra,
});
emit('pointerdown');
emit('pointerup');
assert.equal(pose.poseDebug.selected, bone);
assert.equal(canvas.captured.size, 0, 'Picking a dot releases capture');
const cameraBefore = camera.quaternion.clone();
emit('pointermove', { buttons: 0, clientX: 900 });
assert.ok(camera.quaternion.angleTo(cameraBefore) < 1e-6, 'Hover after selection must not orbit');

// Deterministic ring hit: exercise actual DOM drag handlers and rotation math.
tc.pointerHover = () => { if (!tc.dragging) tc.axis = 'Y'; };
function begin() { emit('pointerdown'); assert.ok(tc.dragging); assert.ok(paused); }
begin();
emit('pointermove', { clientX: 600 });
assert.ok(Math.abs(tc.object.quaternion.angleTo(new THREE.Quaternion()) - Math.PI / 6) < 1e-6, '100 pixels is 30 degrees');
emit('pointerup', { clientX: 600 });
assert.equal(tc.dragging, false);
assert.equal(paused, false);
assert.equal(controls.enabled, true);
const offset = pose.poseDebug.edits.get('Hip').clone();
for (let i = 0; i < 100; i++) { pose.beforeAnimation(); pose.applyEdits(); }
assert.ok(bone.quaternion.angleTo(offset) < 1e-6, 'An unanimated joint never accumulates the offset');
$('#poseResetJoint').onclick();
assert.ok(bone.quaternion.angleTo(new THREE.Quaternion()) < 1e-6, 'Reset joint restores base immediately');
pose.updateDots();
begin();
emit('pointermove', { clientX: 600, shiftKey: true });
assert.ok(Math.abs(tc.object.quaternion.angleTo(new THREE.Quaternion()) - Math.PI / 60) < 1e-6, 'Shift is ten times finer');
emit('pointermove', { clientX: 200000, clientY: -100000 });
assert.ok(tc.object.quaternion.toArray().every(Number.isFinite), 'Fast distant motion stays finite');
emit('pointermove', { clientX: 550, clientY: 400 });
assert.ok(tc.object.quaternion.toArray().every(Number.isFinite), 'Can return after fast motion');
emit('pointercancel');
assert.equal(tc.dragging, false);
assert.equal(paused, false);
for (const cancel of [
  () => emit('pointermove', { buttons: 0 }),
  () => canvas.releasePointerCapture(1),
  () => window.dispatchEvent({ type: 'blur' }),
  () => { tc.enabled = false; emit('pointerup'); tc.enabled = true; },
  () => pose.poseDebug.select(null),
]) {
  pose.poseDebug.select('Hip');
  begin();
  cancel();
  assert.equal(tc.dragging, false, 'Every interruption ends dragging');
  assert.equal(paused, false);
  assert.equal(canvas.captured.size, 0);
}
pose.poseDebug.select('Hip');
controls.enabled = false;
begin();
emit('pointerup');
assert.equal(controls.enabled, false, 'Authored camera keeps its orbit lock');
controls.enabled = true;
pose.poseDebug.edits.set('Hip', offset);
pose.beforeAnimation();
pose.applyEdits();
$('#poseResetAll').onclick();
assert.ok(bone.quaternion.angleTo(new THREE.Quaternion()) < 1e-6);
document.dispatchEvent({ type: 'keydown', key: 'z', ctrlKey: true });
pose.beforeAnimation();
pose.applyEdits();
assert.ok(bone.quaternion.angleTo(offset) < 1e-6, 'Undo restores the reset edit');
// A real mixer changes the base; repeated paused samples and missing tracks both stay stable.
const mixer = new THREE.AnimationMixer(root);
const base = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), .4);
const clip = new THREE.AnimationClip('base', 1, [new THREE.QuaternionKeyframeTrack('Hip.quaternion', [0], base.toArray())]);
mixer.clipAction(clip).play();
for (let i = 0; i < 20; i++) { pose.beforeAnimation(); mixer.update(0); pose.applyEdits(); }
assert.ok(bone.quaternion.angleTo(base.clone().multiply(offset)) < 1e-6);
$('#poseResetAll').onclick();
assert.ok(bone.quaternion.angleTo(base) < 1e-6, 'Reset keeps the underlying animation pose');
console.log('Joint tests passed: release, precision, rapid motion, interruption recovery, camera lock, stable offsets, reset and undo.');
