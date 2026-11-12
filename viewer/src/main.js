import {
  Clock, DirectionalLight, HemisphereLight, MeshLambertMaterial,
  PerspectiveCamera, Scene, WebGLRenderer,
} from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import glb from '../../angel_bear.glb';

const PS2_LINES = 448; // NTSC PS2 frame height

const canvas = document.getElementById('view');
const renderer = new WebGLRenderer({ canvas, antialias: false, alpha: true });
const scene = new Scene();
const camera = new PerspectiveCamera(35, 4 / 3, 0.05, 20);
camera.position.set(0.24, 0.5, 1.42);

const controls = new OrbitControls(camera, canvas);
controls.target.set(0, 0.23, 0);
controls.enableDamping = true;
controls.minDistance = 0.6;
controls.maxDistance = 4;
controls.autoRotateSpeed = 1.5;

scene.add(new HemisphereLight(0xffffff, 0x4a4060, 1.5));
const sun = new DirectionalLight(0xfff1dd, 2.4);
sun.position.set(-1.2, 2.2, 2);
scene.add(sun);

const state = { ps2: true, wire: false, spin: true };
const materials = new Map();
const wings = [];

new GLTFLoader().parse(glb.buffer, '', (gltf) => {
  let triangles = 0;
  gltf.scene.traverse((node) => {
    if (!node.isMesh) return;
    // Plain diffuse with baked vertex colours: no specular, no PBR.
    if (!materials.has(node.material)) {
      materials.set(node.material, new MeshLambertMaterial({ map: node.material.map, vertexColors: true }));
    }
    node.material = materials.get(node.material);
    triangles += node.geometry.index.count / 3;
  });
  for (const [name, sign] of [['WingL', 1], ['WingR', -1]]) {
    const wing = gltf.scene.getObjectByName(name);
    if (wing) wings.push({ wing, sign, rest: wing.rotation.y });
  }
  scene.add(gltf.scene);
  materials.forEach((m) => { m.wireframe = state.wire; });
  document.getElementById('stats').textContent = `${triangles.toLocaleString('en-US')} triangles · ${materials.size} textures`;
}, (error) => {
  document.getElementById('stats').textContent = `Failed to load model: ${error.message || error}`;
});

function resize() {
  const width = window.innerWidth;
  const height = window.innerHeight;
  if (state.ps2) {
    // Render at PS2 height and let the browser upscale with bilinear filtering.
    renderer.setPixelRatio(1);
    renderer.setSize(Math.round(PS2_LINES * width / height), PS2_LINES, false);
  } else {
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(width, height, false);
  }
  camera.aspect = width / height;
  camera.updateProjectionMatrix();
}

function toggle(id, key, apply) {
  const button = document.getElementById(id);
  const sync = () => {
    button.setAttribute('aria-pressed', String(state[key]));
    apply();
  };
  button.addEventListener('click', () => {
    state[key] = !state[key];
    sync();
  });
  sync();
}

toggle('ps2', 'ps2', resize);
toggle('wire', 'wire', () => materials.forEach((m) => { m.wireframe = state.wire; }));
toggle('spin', 'spin', () => { controls.autoRotate = state.spin; });
window.addEventListener('resize', resize);

const clock = new Clock();
renderer.setAnimationLoop(() => {
  const t = clock.getElapsedTime();
  for (const { wing, sign, rest } of wings) {
    wing.rotation.y = rest + sign * (state.spin ? 0.12 * Math.sin(t * 2.4) : 0);
  }
  controls.update();
  renderer.render(scene, camera);
});
