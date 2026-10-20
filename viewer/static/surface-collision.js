import * as THREE from 'three';

// A final contact pass against the posed body/clothing surface. The native springs
// still supply the motion; this only turns a spring out of a nearby body surface.
// Loose garments are excluded from the obstacle mesh to avoid self-collision.
const BODY_BONE = /^(Hip|Waist|Spine|Chest|Neck|Shoulder_[LR]|Arm_[LR]|Elbow_[LR]|Wrist_[LR]|Thigh_[LR]|Knee_[LR]|Sp_Ch_Bust.*)$/;
const LOOSE_BONE = /Hair|Tie|Neckt|Ribbon|Rbbn|Skirt|Coat|Jacket/;
const point = new THREE.Vector3(), closest = new THREE.Vector3(), normal = new THREE.Vector3();
const origin = new THREE.Vector3(), end = new THREE.Vector3(), sample = new THREE.Vector3();
const from = new THREE.Vector3(), to = new THREE.Vector3(), parentQ = new THREE.Quaternion();
const worldQ = new THREE.Quaternion(), turn = new THREE.Quaternion(), triangle = new THREE.Triangle();
const desired = new THREE.Quaternion(), filtered = new THREE.Quaternion(), identity = new THREE.Quaternion();

function treeFor(faces) {
  const box = new THREE.Box3();
  for (const face of faces) box.union(face.box);
  if (faces.length <= 12) return { box, faces };
  const size = box.getSize(new THREE.Vector3());
  const axis = size.x > size.y && size.x > size.z ? 'x' : size.y > size.z ? 'y' : 'z';
  faces.sort((a, b) => a.box.min[axis] + a.box.max[axis] - b.box.min[axis] - b.box.max[axis]);
  const mid = faces.length >> 1;
  return { box, left: treeFor(faces.slice(0, mid)), right: treeFor(faces.slice(mid)) };
}

function refit(node) {
  node.box.makeEmpty();
  if (node.faces) for (const face of node.faces) node.box.union(face.box);
  else { refit(node.left); refit(node.right); node.box.union(node.left.box).union(node.right.box); }
}

export class SurfaceCollision {
  constructor(root, contacts) {
    this.root = root;
    this.margin = .003;
    this.enabled = true;
    this.targets = contacts.filter(c => LOOSE_BONE.test(c.bone)).map(c => ({
      bone: root.getObjectByName(c.bone), offset: new THREE.Vector3(-c.end[0], c.end[1], c.end[2]),
      radius: Math.min(.012, Math.max(.002, c.radius)),
      raw: new THREE.Quaternion(), applied: new THREE.Quaternion(), hasApplied: false,
      goal: new THREE.Quaternion(), correction: new THREE.Quaternion(), initialized: false,
    })).filter(c => c.bone && c.offset.lengthSq() > 1e-8);
    this.meshes = [];
    const faces = [];
    root.updateMatrixWorld(true);
    root.traverse(mesh => {
      if (!mesh.isSkinnedMesh || !mesh.userData.collisionBody) return;
      const geometry = mesh.geometry, indices = geometry.index;
      const skin = geometry.attributes.skinIndex, weights = geometry.attributes.skinWeight;
      if (!skin || !weights) return;
      const keep = i => {
        let total = 0;
        for (let k = 0; k < 4; k++) if (BODY_BONE.test(mesh.skeleton.bones[skin.getComponent(i, k)]?.name || '')) total += weights.getComponent(i, k);
        return total >= .75;
      };
      const vertices = new Map(), meshFaces = [];
      const count = indices ? indices.count : geometry.attributes.position.count;
      mesh.skeleton.update();
      for (let i = 0; i + 2 < count; i += 3) {
        const ids = [0, 1, 2].map(k => indices ? indices.getX(i + k) : i + k);
        if (!ids.every(keep)) continue;
        const points = ids.map(id => {
          if (!vertices.has(id)) vertices.set(id, mesh.getVertexPosition(id, new THREE.Vector3()).applyMatrix4(mesh.matrixWorld));
          return vertices.get(id);
        });
        // Use supplied normals to handle meshes whose winding differs from their visible side.
        const local = ids.map(id => new THREE.Vector3().fromBufferAttribute(geometry.attributes.position, id));
        triangle.set(...local).getNormal(normal);
        const normals = geometry.attributes.normal;
        const avg = normals ? ids.reduce((v, id) => v.add(point.fromBufferAttribute(normals, id)), new THREE.Vector3()) : normal;
        const face = { points, mesh, sign: normal.dot(avg) < 0 ? -1 : 1, box: new THREE.Box3().setFromPoints(points) };
        faces.push(face);
        meshFaces.push(face);
      }
      if (meshFaces.length) this.meshes.push({ mesh, vertices, faces: meshFaces });
    });
    this.faces = faces;
    this.tree = faces.length ? treeFor(faces.slice()) : null;
    this.stats = { contacts: 0, maxPenetration: 0, milliseconds: 0, faces: faces.length };
  }

  updateSurface() {
    for (const { mesh, vertices, faces } of this.meshes) {
      mesh.skeleton.update();
      mesh.userData.collisionSign = mesh.matrixWorld.determinant() < 0 ? -1 : 1;
      for (const [id, position] of vertices) mesh.getVertexPosition(id, position).applyMatrix4(mesh.matrixWorld);
      for (const face of faces) face.box.setFromPoints(face.points);
    }
    refit(this.tree);
  }

  contact(position, radius) {
    // Nearby contact only: do not pull a strand through the whole torso to its far side.
    const limit = .08 + radius;
    let best = limit * limit, hit = null;
    const visit = node => {
      if (node.box.distanceToPoint(position) ** 2 > best) return;
      if (!node.faces) { visit(node.left); visit(node.right); return; }
      for (const face of node.faces) {
        if (face.box.distanceToPoint(position) ** 2 > best) continue;
        triangle.set(...face.points).closestPointToPoint(position, closest);
        const distance = closest.distanceToSquared(position);
        if (distance >= best) continue;
        triangle.getNormal(normal).multiplyScalar(face.sign * face.mesh.userData.collisionSign);
        if (normal.lengthSq() < .5) continue;
        best = distance;
        hit = { point: closest.clone(), normal: normal.clone() };
      }
    };
    visit(this.tree);
    if (!hit) return null;
    const signed = point.subVectors(position, hit.point).dot(hit.normal);
    if (signed >= radius) return null;
    return { normal: hit.normal, depth: radius - signed };
  }

  restore() {
    // Contact corrections belong to the rendered pose, not the native solver's
    // animation input. Leave a bone alone when the mixer has since animated it.
    for (const target of this.targets) {
      if (target.hasApplied && 1 - Math.abs(target.bone.quaternion.dot(target.applied)) < 1e-12)
        target.bone.quaternion.copy(target.raw);
      target.hasApplied = false;
    }
  }

  reset() {
    this.restore();
    for (const target of this.targets) target.initialized = false;
  }

  apply(dt = 1 / 60) {
    this.restore();
    if (!this.enabled || !this.tree || !this.targets.length) {
      for (const target of this.targets) target.initialized = false;
      return;
    }
    dt = Math.max(0, Math.min(.1, dt));
    const started = performance.now();
    this.stats.contacts = 0;
    this.stats.maxPenetration = 0;
    this.root.updateMatrixWorld(true);
    this.updateSurface();
    for (const target of this.targets) {
      const { bone, offset, radius } = target;
      target.raw.copy(bone.quaternion);
      // Keep bone lengths and the native swing, correcting only the direction.
      for (let iteration = 0; iteration < 2; iteration++) {
        bone.getWorldPosition(origin);
        bone.localToWorld(end.copy(offset));
        let correction = null, fraction = 1;
        for (const k of [.5, 1]) {
          sample.lerpVectors(origin, end, k);
          const hit = this.contact(sample, radius + this.margin);
          if (hit && (!correction || hit.depth > correction.depth)) { correction = hit; fraction = k; }
        }
        if (!correction) break;
        this.stats.contacts++;
        this.stats.maxPenetration = Math.max(this.stats.maxPenetration, correction.depth);
        from.subVectors(end, origin).normalize();
        to.copy(end).addScaledVector(correction.normal, correction.depth / fraction).sub(origin).normalize();
        if (from.lengthSq() < .5 || to.lengthSq() < .5) break;
        turn.setFromUnitVectors(from, to);
        bone.getWorldQuaternion(worldQ).premultiply(turn);
        bone.parent.getWorldQuaternion(parentQ);
        bone.quaternion.copy(parentQ).invert().multiply(worldQ).normalize();
        bone.updateWorldMatrix(false, true);
      }
      // Adjacent triangles (especially collars and layered clothing) can switch
      // contact normals suddenly. Filter only the added local correction, never
      // the animation/native swing. Two stages suppress alternating contacts;
      // a speed limit prevents a single bad contact from snapping a strand.
      desired.copy(target.raw).invert().multiply(bone.quaternion).normalize();
      if (!target.initialized) {
        target.goal.copy(desired);
        target.correction.copy(desired);
        target.initialized = true;
      } else if (dt > 0) {
        const releasing = desired.angleTo(identity) < target.correction.angleTo(identity);
        const alpha = 1 - Math.exp(-dt / (releasing ? .12 : .075));
        target.goal.slerp(desired, alpha);
        filtered.copy(target.correction).slerp(target.goal, alpha);
        target.correction.rotateTowards(filtered, Math.PI / 2 * dt);
      }
      bone.quaternion.copy(target.raw).multiply(target.correction).normalize();
      bone.updateWorldMatrix(false, true);
      target.applied.copy(bone.quaternion);
      target.hasApplied = true;
    }
    this.stats.milliseconds = performance.now() - started;
  }
}
