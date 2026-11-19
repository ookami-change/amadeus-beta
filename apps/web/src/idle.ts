import * as THREE from "three";
import type { VRM } from "@pixiv/three-vrm";

const BONES = [
  "hips",
  "spine",
  "chest",
  "upperChest",
  "neck",
  "head",
  "leftShoulder",
  "rightShoulder",
  "leftUpperArm",
  "rightUpperArm",
  "leftLowerArm",
  "rightLowerArm",
  "leftEye",
  "rightEye",
] as const;
type BoneName = (typeof BONES)[number];

/**
 * Procedural idle. There is no clip, and every cycle re-rolls its period,
 * so a minute of watching has no loop point to catch.
 */
export class Idle {
  private readonly rest = new Map<BoneName, THREE.Quaternion>();
  private readonly nodes = new Map<BoneName, THREE.Object3D>();
  private breathPhase = 0;
  private breathPeriod = 3.6;
  private blinkIn = 1.5 + Math.random() * 2;
  private blinkLeft = 0;
  private blinkPhase: "wait" | "close" | "hold" | "open" = "wait";
  private wantDouble = false;
  private shiftIn = 8 + Math.random() * 6;
  private shiftFrom = 0;
  private shiftTo = 0;
  private shiftNow = 0;
  private shiftBlend = 1;
  private headNoise = 0;
  private headTarget = 0;
  private headIn = 1.2;

  constructor(private readonly vrm: VRM) {
    for (const name of BONES) {
      const node = vrm.humanoid?.getNormalizedBoneNode(name);
      if (!node) continue;
      this.nodes.set(name, node);
      this.rest.set(name, node.quaternion.clone());
    }
  }

  update(delta: number): void {
    for (const [name, node] of this.nodes) {
      const rest = this.rest.get(name);
      if (rest) node.quaternion.copy(rest);
    }
    this.stand();
    this.breath(delta);
    this.shift(delta);
    this.head(delta);
    this.blink(delta);
  }

  /** The file is a T-pose. A relaxed stance is a pose, not a clip. */
  private stand(): void {
    this.rotate("leftUpperArm", 0.25, 0, 1.15);
    this.rotate("rightUpperArm", 0.25, 0, -1.15);
    this.rotate("leftLowerArm", 0, 0, 0.3);
    this.rotate("rightLowerArm", 0, 0, -0.3);
  }

  private breath(delta: number): void {
    this.breathPhase += delta / this.breathPeriod;
    if (this.breathPhase >= 1) {
      this.breathPhase -= 1;
      this.breathPeriod = 3.3 + Math.random() * 0.9;
    }
    const inhale = Math.sin(this.breathPhase * Math.PI * 2) * 0.5 + 0.5;
    this.rotate("chest", -0.025 * inhale, 0, 0);
    this.rotate("upperChest", -0.015 * inhale, 0, 0);
    this.rotate("leftShoulder", 0, 0, 0.02 * inhale);
    this.rotate("rightShoulder", 0, 0, -0.02 * inhale);
    this.rotate("spine", -0.01 * inhale, 0, 0);
  }

  private shift(delta: number): void {
    this.shiftIn -= delta;
    if (this.shiftIn <= 0) {
      this.shiftFrom = this.shiftNow;
      this.shiftTo = (Math.random() * 2 - 1) * 0.045;
      this.shiftBlend = 0;
      this.shiftIn = 8 + Math.random() * 12;
    }
    this.shiftBlend = Math.min(1, this.shiftBlend + delta / 1.8);
    const eased = this.shiftBlend * this.shiftBlend * (3 - 2 * this.shiftBlend);
    this.shiftNow = this.shiftFrom + (this.shiftTo - this.shiftFrom) * eased;
    this.rotate("hips", 0, 0, this.shiftNow);
    this.rotate("spine", 0, 0, -this.shiftNow * 0.45);
  }

  private head(delta: number): void {
    this.headIn -= delta;
    if (this.headIn <= 0) {
      this.headTarget = (Math.random() * 2 - 1) * 0.02;
      this.headIn = 0.8 + Math.random() * 1.6;
    }
    this.headNoise = damp(this.headNoise, this.headTarget, 0.7, delta);
    this.rotate("head", this.headNoise * 0.6, this.headNoise, 0);
  }

  private blink(delta: number): void {
    if (this.blinkPhase === "wait") {
      this.blinkIn -= delta;
      if (this.blinkIn <= 0) {
        this.blinkPhase = "close";
        this.blinkLeft = 0.06;
      }
      this.setBlink(0);
      return;
    }
    this.blinkLeft -= delta;
    if (this.blinkPhase === "close") {
      const t = 1 - Math.max(0, this.blinkLeft) / 0.06;
      this.setBlink(t);
      if (this.blinkLeft <= 0) {
        this.blinkPhase = "hold";
        this.blinkLeft = 0.035;
      }
      return;
    }
    if (this.blinkPhase === "hold") {
      this.setBlink(1);
      if (this.blinkLeft <= 0) {
        this.blinkPhase = "open";
        this.blinkLeft = 0.08;
      }
      return;
    }
    const t = Math.max(0, this.blinkLeft) / 0.08;
    this.setBlink(t);
    if (this.blinkLeft <= 0) {
      this.blinkPhase = "wait";
      if (this.wantDouble) {
        this.wantDouble = false;
        this.blinkIn = 0.12;
      } else {
        this.wantDouble = Math.random() < 0.12;
        this.blinkIn = 2 + Math.random() * 4;
      }
    }
  }

  private setBlink(weight: number): void {
    this.vrm.expressionManager?.setValue("blink", clamp(weight, 0, 1));
  }

  private rotate(name: BoneName, x: number, y: number, z: number): void {
    const node = this.nodes.get(name);
    if (!node || (x === 0 && y === 0 && z === 0)) return;
    const turn = new THREE.Quaternion().setFromEuler(new THREE.Euler(x, y, z, "YXZ"));
    node.quaternion.multiply(turn);
  }
}

export function damp(current: number, target: number, tau: number, delta: number): number {
  const gain = 1 - Math.exp(-delta / Math.max(0.001, tau));
  return current + (target - current) * gain;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
