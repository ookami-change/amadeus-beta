import * as THREE from "three";
import type { VRM } from "@pixiv/three-vrm";
import { damp } from "./idle";

/**
 * Eyes take the pointer immediately, within a small cone. The head follows
 * later and never as far, so it cannot snap or roll the eyes white.
 */
export class Gaze {
  private readonly nodes = new Map<string, THREE.Object3D>();
  private yaw = 0;
  private pitch = 0;
  private eyeYaw = 0;
  private eyePitch = 0;
  private headYaw = 0;
  private headPitch = 0;
  private saccadeYaw = 0;
  private saccadePitch = 0;
  private saccadeIn = 0.4;

  constructor(vrm: VRM) {
    for (const name of ["spine", "neck", "head", "leftEye", "rightEye"]) {
      const node = vrm.humanoid?.getNormalizedBoneNode(name as "head");
      if (node) this.nodes.set(name, node);
    }
    const lookAt = vrm.lookAt as { autoUpdate?: boolean } | null;
    if (lookAt && "autoUpdate" in lookAt) lookAt.autoUpdate = false;
  }

  pointer(clientX: number, clientY: number): void {
    const x = (clientX / window.innerWidth) * 2 - 1;
    const y = -((clientY / window.innerHeight) * 2 - 1);
    this.yaw = clamp(x * 0.45, -0.45, 0.45);
    this.pitch = clamp(y * 0.28, -0.22, 0.22);
  }

  update(delta: number): void {
    this.saccadeIn -= delta;
    if (this.saccadeIn <= 0) {
      this.saccadeYaw = (Math.random() * 2 - 1) * 0.05;
      this.saccadePitch = (Math.random() * 2 - 1) * 0.03;
      this.saccadeIn = 0.45 + Math.random() * 0.9;
    }
    this.eyeYaw = damp(this.eyeYaw, this.yaw + this.saccadeYaw, 0.05, delta);
    this.eyePitch = damp(this.eyePitch, this.pitch + this.saccadePitch, 0.05, delta);
    this.headYaw = damp(this.headYaw, this.yaw * 0.72, 0.18, delta);
    this.headPitch = damp(this.headPitch, this.pitch * 0.55, 0.2, delta);
    this.turn("spine", this.headPitch * 0.15, this.headYaw * 0.2);
    this.turn("neck", this.headPitch * 0.4, this.headYaw * 0.4);
    this.turn("head", this.headPitch, this.headYaw);
    const eyeYaw = clamp(this.eyeYaw, -0.22, 0.22);
    const eyePitch = clamp(this.eyePitch, -0.12, 0.12);
    this.turn("leftEye", eyePitch, eyeYaw);
    this.turn("rightEye", eyePitch, eyeYaw);
  }

  private turn(name: string, pitch: number, yaw: number): void {
    const node = this.nodes.get(name);
    if (!node) return;
    node.quaternion.multiply(new THREE.Quaternion().setFromEuler(new THREE.Euler(pitch, yaw, 0, "YXZ")));
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
