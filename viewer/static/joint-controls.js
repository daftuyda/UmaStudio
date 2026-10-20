import * as THREE from 'three';
import { TransformControls } from 'three/addons/controls/TransformControls.js';

// Pixel-based rotation avoids ray/plane singularities and remains equally precise at any zoom.
export class JointControls extends TransformControls {
  pointerHover(pointer) {
    super.pointerHover(pointer);
    if (this.axis === 'XYZE') this.axis = null;
  }

  pointerDown(pointer) {
    if (!this.object || this.dragging || pointer.button !== 0 || !this.axis) return;
    this.updateMatrixWorld();
    this._quaternionStart.copy(this.object.quaternion);
    const localAxis = new THREE.Vector3();
    if (this.axis === 'E') localAxis.copy(this.eye).applyQuaternion(this.worldQuaternion.clone().invert());
    if (this.axis === 'X') localAxis.set(1, 0, 0);
    if (this.axis === 'Y') localAxis.set(0, 1, 0);
    if (this.axis === 'Z') localAxis.set(0, 0, 1);
    const worldAxis = localAxis.clone().applyQuaternion(this.worldQuaternion);
    const tangent = worldAxis.clone().cross(this.eye);
    if (tangent.lengthSq() < .01) {
      const center = this.worldPosition.clone().project(this.camera);
      const radial = new THREE.Vector3(pointer.x - center.x, pointer.y - center.y, 0)
        .applyQuaternion(this.cameraQuaternion).normalize();
      tangent.crossVectors(worldAxis, radial);
    }
    tangent.applyQuaternion(this.cameraQuaternion.clone().invert());
    const length = Math.hypot(tangent.x, tangent.y);
    this._jointDrag = {
      x: pointer.x, y: pointer.y, angle: 0, axis: localAxis.normalize(),
      tx: length > 1e-6 ? tangent.x / length : 1,
      ty: length > 1e-6 ? tangent.y / length : 0,
    };
    this.dragging = true;
    this.dispatchEvent({ type: 'mouseDown', mode: 'rotate' });
  }

  pointerMove(pointer) {
    const d = this._jointDrag;
    if (!this.object || !this.dragging || !d || pointer.button !== -1) return;
    const rect = this.domElement.getBoundingClientRect();
    const dx = (pointer.x - d.x) * rect.width / 2, dy = (pointer.y - d.y) * rect.height / 2;
    d.x = pointer.x;
    d.y = pointer.y;
    if (!Number.isFinite(dx + dy)) return;
    d.angle += (dx * d.tx + dy * d.ty) * THREE.MathUtils.degToRad(pointer.shiftKey ? .03 : .3);
    this.object.quaternion.copy(this._quaternionStart)
      .multiply(new THREE.Quaternion().setFromAxisAngle(d.axis, d.angle)).normalize();
    this.dispatchEvent({ type: 'change' });
    this.dispatchEvent({ type: 'objectChange' });
  }
}
