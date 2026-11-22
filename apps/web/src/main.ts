import "./style.css";
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils, type VRM } from "@pixiv/three-vrm";
import { Gaze } from "./gaze";
import { Idle } from "./idle";
import { LipSync, type VisemeKey } from "./lipsync";
import { PerfMonitor } from "./perf";
import { Bench } from "./bench";

// Public files resolve against the page, so the build also works under /l/<slug>/.
const asset = (path: string): string => `${import.meta.env.BASE_URL}${path}`;
const benchOn = import.meta.env.VITE_BENCH === "1" || new URLSearchParams(location.search).has("bench");

const canvas = document.querySelector<HTMLCanvasElement>("#stage");
const statusNode = document.querySelector<HTMLElement>("#status");
const fpsNode = document.querySelector<HTMLElement>("#fps");
const frameNode = document.querySelector<HTMLElement>("#frame");
const gpuNode = document.querySelector<HTMLElement>("#gpu");
const mouthNode = document.querySelector<HTMLElement>("#mouth");
const licenseNode = document.querySelector<HTMLElement>("#license");
const audio = document.querySelector<HTMLAudioElement>("#line");
const playButton = document.querySelector<HTMLButtonElement>("#play");
const stopButton = document.querySelector<HTMLButtonElement>("#stop");
const audioMode = document.querySelector<HTMLInputElement>("#mode-audio");
const timeMode = document.querySelector<HTMLInputElement>("#mode-time");

if (
  !canvas ||
  !statusNode ||
  !fpsNode ||
  !frameNode ||
  !gpuNode ||
  !mouthNode ||
  !licenseNode ||
  !audio ||
  !playButton ||
  !stopButton ||
  !audioMode ||
  !timeMode
) {
  throw new Error("demo markup is missing a node");
}

const stage = canvas;
const status = statusNode;
const license = licenseNode;
const lineAudio = audio;
const mouthLabel = mouthNode;
lineAudio.src = asset("audio/sample.wav");

const renderer = new THREE.WebGLRenderer({
  canvas: stage,
  alpha: true,
  antialias: true,
  powerPreference: "high-performance",
});
renderer.setPixelRatio(1);
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.outputColorSpace = THREE.SRGBColorSpace;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(28, window.innerWidth / window.innerHeight, 0.1, 30);
camera.position.set(0, 0.95, 3.2);

scene.add(new THREE.HemisphereLight(0xfff1e4, 0x3a2a24, 0.75));
const key = new THREE.DirectionalLight(0xfff6ec, 1.15);
key.position.set(0.6, 1.8, 1.4);
scene.add(key);

const perf = new PerfMonitor(fpsNode, frameNode, gpuNode);
const gl = renderer.getContext();
perf.setGpu(gl.getParameter(gl.RENDERER) as string);

const audioContext = new AudioContext();
const source = audioContext.createMediaElementSource(lineAudio);
const analyser = audioContext.createAnalyser();
source.connect(analyser);
analyser.connect(audioContext.destination);

let idle: Idle | null = null;
let gaze: Gaze | null = null;
let lips: LipSync | null = null;
let bench: Bench | null = null;

window.addEventListener("pointermove", (event) => {
  gaze?.pointer(event.clientX, event.clientY);
});

playButton.addEventListener("click", () => {
  void audioContext.resume();
  lineAudio.currentTime = 0;
  void lineAudio.play();
});
stopButton.addEventListener("click", () => {
  lineAudio.pause();
  lineAudio.currentTime = 0;
});
audioMode.addEventListener("change", () => {
  if (lips && audioMode.checked) lips.mode = "audio";
});
timeMode.addEventListener("change", () => {
  if (lips && timeMode.checked) lips.mode = "timestamp";
});

void boot();

function frameCamera(object: THREE.Object3D): void {
  const box = new THREE.Box3().setFromObject(object);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const distance = (size.y * 0.62) / Math.tan((camera.fov * Math.PI) / 360);
  camera.position.set(center.x, center.y, center.z + distance);
  camera.lookAt(center);
  camera.updateProjectionMatrix();
}

async function boot(): Promise<void> {
  try {
    const vrm = await loadVrm(asset("models/avatar.vrm"));
    scene.add(vrm.scene);
    frameCamera(vrm.scene);
    idle = new Idle(vrm);
    gaze = new Gaze(vrm);
    lips = new LipSync(vrm, analyser, lineAudio);
    const timeline = await loadTimeline(asset("lipsync/demo-timeline.json"));
    lips.setTimeline(timeline);
    license.textContent = `${modelCredit(vrm)}。来源与许可见 public/models/SOURCE.md。`;
    status.textContent = "待机。鼠标会带动视线，头部跟得慢一些。";
    clock(vrm);
    if (benchOn) bench = startBench(vrm);
  } catch (error) {
    status.textContent = error instanceof Error ? error.message : "模型没有加载";
  }
}

function startBench(vrm: VRM): Bench {
  return new Bench({
    play: () =>
      new Promise<void>((resolve) => {
        void audioContext.resume();
        lineAudio.currentTime = 0;
        lineAudio.addEventListener("ended", () => resolve(), { once: true });
        lineAudio.addEventListener("error", () => resolve(), { once: true });
        void lineAudio.play().catch(() => resolve());
      }),
    mouth: () => (lips ? lips.readout : "—"),
    mouthWeight: () =>
      Math.max(0, ...["aa", "ih", "ou", "ee", "oh"].map((v) => vrm.expressionManager?.getValue(v) ?? 0)),
    renderer: () => {
      const ext = gl.getExtension("WEBGL_debug_renderer_info");
      const gpu = (ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)) as string;
      return {
        gpu,
        pixelRatio: renderer.getPixelRatio(),
        bufferWidth: gl.drawingBufferWidth,
        bufferHeight: gl.drawingBufferHeight,
      };
    },
  });
}

function modelCredit(vrm: VRM): string {
  const meta = vrm.meta;
  if (!meta) return "VRM";
  if (meta.metaVersion === "1") {
    return `${meta.name} · ${meta.authors.join("、")}`;
  }
  return `${meta.title ?? "VRM"}${meta.author ? " · " + meta.author : ""}`;
}

function clock(vrm: VRM): void {
  function loop(now: number): void {
    const delta = perf.tick(now);
    bench?.frame(now);
    idle?.update(delta);
    gaze?.update(delta);
    lips?.update(delta);
    mouthLabel.textContent = lips ? lips.readout : "—";
    vrm.update(delta);
    renderer.render(scene, camera);
    requestAnimationFrame(loop);
  }
  requestAnimationFrame(loop);
}

async function loadVrm(url: string): Promise<VRM> {
  const loader = new GLTFLoader();
  loader.register((parser) => new VRMLoaderPlugin(parser));
  const gltf = await loader.loadAsync(url);
  const vrm = gltf.userData.vrm as VRM | undefined;
  if (!vrm) throw new Error("这个文件不是 VRM。先运行 pnpm download:vrm");
  if (vrm.meta?.metaVersion === "0") VRMUtils.rotateVRM0(vrm);
  vrm.scene.traverse((object) => {
    object.frustumCulled = false;
  });
  return vrm;
}

async function loadTimeline(url: string): Promise<VisemeKey[]> {
  try {
    const response = await fetch(url);
    if (!response.ok) return [];
    const body = (await response.json()) as { visemes?: VisemeKey[] };
    return body.visemes ?? [];
  } catch {
    return [];
  }
}

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
});
