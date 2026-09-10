/**
 * The World: the machine's work, as territory.
 *
 * One plot of land per project that has an agent doing something in it, a
 * figure standing on each, and the rest of the library as unlit terrain in the
 * distance. It is a view of /api/agents, not a game: every building and every
 * animation is driven by a real session's real tool calls.
 *
 * Two rules shape the whole file.
 *
 * **Nothing renders unless something moves.** GSO-1 runs all day from a
 * LaunchAgent and we spent a release getting its idle cost down; a 60fps
 * requestAnimationFrame loop would hand all of that back. The loop starts when
 * a figure is animating or a plot is rising, and stops the moment the scene
 * settles. A world of idle agents costs one frame and then nothing.
 *
 * **Presence is measured from the transcript, never from `status`.** A session
 * that died mid-turn claims "busy" for ever. Time since its last tool call
 * cannot lie: under ACTIVE_SECONDS the figure is swinging, under PRESENT_SECONDS
 * it is standing on its plot, beyond that the land goes back to being terrain.
 * The grace between the two is what stops a plot flickering in and out while an
 * agent is simply thinking between tools.
 */

import * as THREE from "./vendor/three/three.module.js";

/** Still swinging: a tool call this recently means visibly at work. */
const ACTIVE_SECONDS = 30;
/** Still on the map. The gap to ACTIVE_SECONDS is the anti-flicker grace. */
const PRESENT_SECONDS = 300;

const COL = {
  sky: 0x07070c,
  ground: 0x0d0c14,
  terrain: 0x1c1b2b,      // the dormant 271
  plot: 0x272442,
  plotEdge: 0x4b4580,
  body: 0x502ce7,         // the brand purple, as in favicon.svg
  bodyIdle: 0x322a5e,
  head: 0xe8e6f5,
  active: 0x00e0b7,       // the teal, for the one that is working
};

/** Deterministic scatter: the same project sits in the same place every time,
 *  so the map is somewhere you can learn rather than a new shuffle each poll. */
function hash(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return (h >>> 0) / 4294967295;
}

/** A text sprite. Kept small and power-of-two-ish; it is read, not admired. */
function makeLabel(text) {
  const pad = 16, font = 30;
  const c = document.createElement("canvas");
  const ctx = c.getContext("2d");
  ctx.font = `600 ${font}px -apple-system, system-ui, sans-serif`;
  const w = Math.ceil(ctx.measureText(text).width) + pad * 2;
  c.width = Math.min(512, w);
  c.height = 56;
  const g = c.getContext("2d");
  g.font = `600 ${font}px -apple-system, system-ui, sans-serif`;
  g.fillStyle = "rgba(10,9,16,0.72)";
  g.beginPath();
  const r = 12;
  g.roundRect(0, 4, c.width, 44, r);
  g.fill();
  g.fillStyle = "#e8e6f5";
  g.textBaseline = "middle";
  g.fillText(text, pad, 27);

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }),
  );
  sprite.scale.set(c.width / 56 * 0.9, 0.9, 1);
  return sprite;
}

export function createWorld(canvas) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  renderer.setClearColor(COL.sky, 1);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(COL.sky, 26, 80);

  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 300);
  // Fixed three-quarter view: the Clash of Clans angle. Orbiting is a later
  // decision; a camera you cannot get lost in is better than a free one.
  camera.position.set(12.5, 11.5, 14);
  camera.lookAt(0, 0, 0);

  scene.add(new THREE.HemisphereLight(0x9a93ff, 0x0a0910, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 1.5);
  key.position.set(10, 18, 8);
  scene.add(key);

  const ground = new THREE.Mesh(
    new THREE.CircleGeometry(70, 48),
    new THREE.MeshStandardMaterial({ color: COL.ground, roughness: 1 }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.02;
  scene.add(ground);

  // ---- the dormant library, as terrain -------------------------------------
  // One InstancedMesh for all of them: 271 separate objects would be 271 draw
  // calls to say "nothing is happening here".
  let terrain = null;
  function setTerrain(names) {
    if (terrain) { scene.remove(terrain); terrain.geometry.dispose(); terrain.material.dispose(); }
    if (!names.length) { terrain = null; return; }
    const geo = new THREE.BoxGeometry(0.7, 0.35, 0.7);
    const mat = new THREE.MeshStandardMaterial({ color: COL.terrain, roughness: 1 });
    terrain = new THREE.InstancedMesh(geo, mat, names.length);
    const m = new THREE.Object3D();
    names.forEach((name, i) => {
      // A ring well outside the active plots, so the middle stays readable.
      const a = hash(name) * Math.PI * 2;
      const r = 15 + hash(name + "r") * 44;
      m.position.set(Math.cos(a) * r, 0.17, Math.sin(a) * r);
      m.rotation.y = hash(name + "y") * Math.PI;
      m.updateMatrix();
      terrain.setMatrixAt(i, m.matrix);
    });
    terrain.instanceMatrix.needsUpdate = true;
    scene.add(terrain);
  }

  // ---- the active plots ----------------------------------------------------
  const plots = new Map();       // project -> { group, figure, arm, target, t }

  function buildPlot(project) {
    const group = new THREE.Group();

    const pad = new THREE.Mesh(
      new THREE.CylinderGeometry(2.6, 2.8, 0.5, 6),
      new THREE.MeshStandardMaterial({ color: COL.plot, roughness: 0.85 }),
    );
    pad.position.y = 0.25;
    group.add(pad);

    const rim = new THREE.Mesh(
      new THREE.TorusGeometry(2.62, 0.05, 8, 6),
      new THREE.MeshStandardMaterial({ color: COL.plotEdge, roughness: 0.6 }),
    );
    rim.rotation.x = Math.PI / 2;
    rim.position.y = 0.5;
    group.add(rim);

    // The figure: a body, a head, and an arm that swings while it works.
    const figure = new THREE.Group();
    const bodyMat = new THREE.MeshStandardMaterial({ color: COL.bodyIdle, roughness: 0.5 });
    const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.28, 0.55, 4, 10), bodyMat);
    body.position.y = 0.95;
    figure.add(body);

    const head = new THREE.Mesh(
      new THREE.SphereGeometry(0.22, 16, 12),
      new THREE.MeshStandardMaterial({ color: COL.head, roughness: 0.4 }),
    );
    head.position.y = 1.52;
    figure.add(head);

    const arm = new THREE.Mesh(
      new THREE.CapsuleGeometry(0.09, 0.42, 3, 8),
      new THREE.MeshStandardMaterial({ color: COL.body, roughness: 0.5 }),
    );
    // Pivot at the shoulder so a rotation reads as a swing, not a slide.
    arm.geometry.translate(0, -0.26, 0);
    arm.position.set(0.32, 1.2, 0.05);
    figure.add(arm);

    figure.position.y = 0.5;
    group.add(figure);

    // A name plate, so a plot is a place rather than a shape. Drawn to a canvas
    // and used as a sprite: three.js has no text of its own, and pulling in a
    // font loader to write six short words would not be worth its weight.
    const label = makeLabel(project);
    label.position.y = 2.5;
    group.add(label);

    group.userData = { project };
    scene.add(group);
    return { group, figure, arm, body: bodyMat, rise: 0 };
  }

  function layout() {
    // A ring, ordered by name so a plot does not jump when a neighbour leaves.
    const keys = [...plots.keys()].sort();
    keys.forEach((k, i) => {
      const p = plots.get(k);
      const a = (i / Math.max(keys.length, 1)) * Math.PI * 2;
      const r = keys.length <= 1 ? 0 : 3.2 + keys.length * 0.75;
      p.target = new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r);
    });
  }

  let agents = new Map();        // project -> { idle, name, tool }
  let needsFrame = true;

  /** Feed it a /api/agents snapshot. Returns true if the scene must animate. */
  function update(snapshot, allProjects) {
    const now = Date.now() / 1000;
    const next = new Map();
    for (const s of snapshot.sessions || []) {
      const idle = s.activity && s.activity.idle_seconds;
      if (!s.project || idle === null || idle === undefined) continue;
      if (idle > PRESENT_SECONDS) continue;            // gone back to terrain
      const tools = (s.activity && s.activity.tools) || [];
      next.set(s.project, {
        idle,
        name: s.name,
        tool: tools.length ? tools[0].tool : null,
        working: idle <= ACTIVE_SECONDS,
      });
    }

    let changed = false;
    for (const [project, info] of next) {
      if (!plots.has(project)) { plots.set(project, buildPlot(project)); changed = true; }
      plots.get(project).info = info;
    }
    for (const project of [...plots.keys()]) {
      if (next.has(project)) continue;
      const p = plots.get(project);
      scene.remove(p.group);
      plots.delete(project);
      changed = true;
    }
    if (changed) layout();

    // Terrain is everything in the library that is not currently inhabited.
    if (allProjects && (changed || !terrain)) {
      setTerrain(allProjects.filter(n => !plots.has(n)));
    }
    agents = next;
    needsFrame = true;
    return [...next.values()].some(a => a.working) || changed;
  }

  const clock = new THREE.Clock();

  /** One frame. Returns true while anything is still moving. */
  function frame() {
    const dt = Math.min(clock.getDelta(), 0.1);
    const t = clock.elapsedTime;
    let moving = false;

    for (const [, p] of plots) {
      // Plots rise when they appear rather than popping into existence.
      if (p.rise < 1) {
        p.rise = Math.min(1, p.rise + dt * 1.8);
        moving = true;
      }
      const ease = 1 - Math.pow(1 - p.rise, 3);
      if (p.target) {
        p.group.position.x = p.target.x;
        p.group.position.z = p.target.z;
        p.group.position.y = -2.2 * (1 - ease);
      }
      p.group.scale.setScalar(0.6 + 0.4 * ease);

      const working = p.info && p.info.working;
      p.body.color.setHex(working ? COL.body : COL.bodyIdle);
      if (working) {
        // Swing the arm and bob the body: visible work, at a readable rate.
        p.arm.rotation.x = Math.sin(t * 7) * 1.1 - 0.3;
        p.figure.position.y = 0.5 + Math.abs(Math.sin(t * 7)) * 0.06;
        p.figure.rotation.y = Math.sin(t * 0.8) * 0.25;
        moving = true;
      } else {
        p.arm.rotation.x += (0 - p.arm.rotation.x) * Math.min(1, dt * 6);
        p.figure.position.y += (0.5 - p.figure.position.y) * Math.min(1, dt * 6);
        if (Math.abs(p.arm.rotation.x) > 0.01) moving = true;
      }
    }

    renderer.render(scene, camera);
    needsFrame = false;
    return moving;
  }

  function resize(w, h) {
    if (!w || !h) return;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    needsFrame = true;
  }

  function dispose() {
    scene.traverse(o => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => m.dispose());
    });
    renderer.dispose();
  }

  return { update, frame, resize, dispose, get agents() { return agents; },
           get pending() { return needsFrame; } };
}

export { ACTIVE_SECONDS, PRESENT_SECONDS };
