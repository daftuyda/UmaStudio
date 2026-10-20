import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { SourceTextModule } from 'node:vm';

const three = new SourceTextModule(await readFile(new URL('../static/vendor/three.module.js', import.meta.url), 'utf8'));
await three.link(() => {});
await three.evaluate();
const module = new SourceTextModule(await readFile(new URL('../static/surface-collision.js', import.meta.url), 'utf8'));
await module.link(() => three);
await module.evaluate();
const T = three.namespace, { SurfaceCollision } = module.namespace;
const root = new T.Group(), chest = new T.Bone(), tie = new T.Bone();
chest.name = 'Chest';
tie.name = 'Sp_Ch_Tie0_F_00';
tie.position.set(0, .45, .14);
chest.add(tie);
root.add(chest);
const geometry = new T.BoxGeometry(.4, 1, .2);
const n = geometry.attributes.position.count;
geometry.setAttribute('skinIndex', new T.Uint16BufferAttribute(new Uint16Array(n * 4), 4));
geometry.setAttribute('skinWeight', new T.Float32BufferAttribute(Array.from({ length: n * 4 }, (_, i) => i % 4 ? 0 : 1), 4));
const body = new T.SkinnedMesh(geometry, new T.MeshBasicMaterial());
body.userData.collisionBody = true;
root.add(body);
body.bind(new T.Skeleton([chest]));
const offset = new T.Vector3(0, -.25, -.06);
const guard = new SurfaceCollision(root, [{ bone: tie.name, end: offset.toArray(), radius: .003 }]);
assert.equal(guard.faces.length, 12);
guard.apply();
assert.ok(guard.stats.contacts > 0);
let tip = tie.localToWorld(offset.clone());
assert.ok(tip.z > .105, `Tie should be outside the front surface, got ${tip.z}`);
assert.ok(Math.abs(tip.distanceTo(tie.getWorldPosition(new T.Vector3())) - offset.length()) < 1e-6, 'Bone length stays fixed');
guard.restore();
assert.equal(tie.quaternion.w, 1, 'The spring solver receives the uncorrected input');
guard.apply();
tie.quaternion.setFromAxisAngle(new T.Vector3(0, 1, 0), .7);
const animated = tie.quaternion.clone();
guard.restore();
assert.ok(tie.quaternion.equals(animated), 'Restoring contacts must not overwrite a newly animated rotation');
tie.quaternion.identity();
guard.enabled = false;
guard.apply();
assert.equal(tie.quaternion.w, 1, 'Disabling the guard leaves the spring result untouched');
guard.enabled = true;
chest.position.set(1, .2, 0);
chest.rotation.y = .7;
root.updateMatrixWorld(true);
guard.apply();
tip = chest.worldToLocal(tie.localToWorld(offset.clone()));
assert.ok(tip.z > .105, 'Collision surface follows the posed skeleton');
root.scale.x = -1;
tie.quaternion.identity();
root.updateMatrixWorld(true);
guard.apply();
tip = chest.worldToLocal(tie.localToWorld(offset.clone()));
assert.ok(tip.z > .105, 'Mirroring keeps outward collision normals');
assert.ok(guard.stats.milliseconds < 100);

// Nearby surfaces can alternate their contact direction as a strand crosses a seam.
// Only the contact offset is damped; native/animated motion stays immediate.
root.scale.x = 1;
chest.position.set(0, 0, 0);
chest.quaternion.identity();
tie.quaternion.identity();
guard.reset();
let side = 1;
guard.contact = () => side ? { normal: new T.Vector3(side, 0, 0), depth: .04 } : null;
guard.apply();
let previous = guard.targets[0].correction.clone();
for (let frame = 0; frame < 120; frame++) {
  side *= -1;
  tie.quaternion.identity();
  guard.apply(1 / 60);
  const correction = guard.targets[0].correction;
  assert.ok(previous.angleTo(correction) <= Math.PI / 120 + 1e-7, 'Contact changes are rate-limited');
  previous.copy(correction);
}
for (let frame = 0; frame < 20; frame++) guard.apply(0);
assert.ok(previous.angleTo(guard.targets[0].correction) < 1e-7, 'Extra render frames do not advance contact smoothing');
tie.quaternion.copy(animated);
guard.apply(0);
assert.ok(tie.quaternion.angleTo(animated.clone().multiply(previous)) < 1e-7, 'Animation is not delayed by contact smoothing');
side = 0;
for (let frame = 0; frame < 240; frame++) {
  tie.quaternion.identity();
  guard.apply(1 / 60);
}
assert.ok(guard.targets[0].correction.angleTo(new T.Quaternion()) < 1e-5, 'Contacts release smoothly when the obstacle moves away');
guard.reset();
tie.quaternion.identity();
side = -1;
guard.apply();
assert.ok(guard.targets[0].correction.angleTo(new T.Quaternion()) > .05, 'Seeking resets contact history for the new pose');

// A loose garment is not allowed to collide with its own triangles.
chest.name = 'Sp_Ch_Tie0_F_01';
const excluded = new SurfaceCollision(root, []);
assert.equal(excluded.faces.length, 0);

// Check the actual spring request path, not just the contact helper: post-contact
// quaternions must never become ANIM_ROT on the next native simulation step.
chest.name = 'Chest';
tie.quaternion.identity();
const ui = new SourceTextModule('export const post = () => {};');
await ui.link(() => {});
await ui.evaluate();
const physics = new SourceTextModule(await readFile(new URL('../static/physics.js', import.meta.url), 'utf8'));
await physics.link(spec => spec === 'three' ? three : spec.endsWith('ui.js') ? ui : module);
await physics.evaluate();
const springs = new physics.namespace.GameSprings(root, new Map([[tie, [tie.position.clone(), new T.Quaternion(), tie.scale.clone()]]]), {
  id: 'test', parents: ['Chest'], roots: [tie.name], bones: [tie.name], hip: null,
  contacts: [{ bone: tie.name, end: offset.toArray(), radius: .003 }],
});
springs.finals[0] = new T.Quaternion();
springs.apply();
assert.ok(tie.quaternion.angleTo(new T.Quaternion()) > .05);
const fetchBefore = globalThis.fetch;
try {
  let posted;
  globalThis.fetch = async (_url, options) => {
    posted = new Float32Array(options.body);
    return { ok: true, arrayBuffer: async () => new Float32Array([1, 0, 0, 0, 1]).buffer };
  };
  await springs.step();
  assert.ok([...posted.slice(-4, -1)].every(v => Math.abs(v) < 1e-7) && posted.at(-1) === 1,
    'The wire request excludes rendered contact corrections');
  const correction = springs.surface.targets[0].correction.clone();
  springs.frame(0);
  assert.ok(correction.angleTo(springs.surface.targets[0].correction) < 1e-7);
  springs.setEnabled(false);
  assert.ok(tie.quaternion.equals(new T.Quaternion()), 'Disabling physics restores the rest pose');
} finally {
  globalThis.fetch = fetchBefore;
}
console.log('Surface collision tests passed: collision response, mirroring, solver isolation, animated input, contact smoothing, render timing, release and seeking.');
