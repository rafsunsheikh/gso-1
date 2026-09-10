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
/** Recently at work: standing on lit land, ready to go again. */
const PRESENT_SECONDS = 300;

// Every live session gets a plot, whatever it is doing. Hiding the quiet ones
// was the wrong call: a session idle for two hours is still a session you have
// open, and a map that shows one of your six agents is not a map of your
// machine. State is carried in how a plot looks, not in whether it exists.

const COL = {
  sky: 0x07070c,
  ground: 0x0d0c14,
  terrain: 0x1c1b2b,      // the dormant 271
  plot: 0x272442,
  plotStale: 0x1e1c30,
  plotEdge: 0x4b4580,
  body: 0x502ce7,         // the brand purple, as in favicon.svg
  bodyIdle: 0x322a5e,
  bodyStale: 0x2a2740,    // live, but nothing for a long while
  head: 0xe8e6f5,
  active: 0x00e0b7,       // the teal, for the one that is working
};

/**
 * How each tool reads as movement.
 *
 * The point of watching an agent rather than reading its log is that the shape
 * of the work is visible at a glance: hammering is not the same as reading, and
 * a push is not the same as a search. `rate` is the swing speed, `arc` how far
 * the arm travels, `bob` how much the whole figure moves with it.
 */
const TOOL_MOTION = {
  Edit:      { rate: 9.0, arc: 1.25, bob: 0.09, tint: 0x502ce7 },
  Write:     { rate: 9.0, arc: 1.25, bob: 0.09, tint: 0x502ce7 },
  NotebookEdit: { rate: 9.0, arc: 1.25, bob: 0.09, tint: 0x502ce7 },
  Bash:      { rate: 6.0, arc: 0.85, bob: 0.05, tint: 0x00e0b7 },
  Read:      { rate: 2.2, arc: 0.35, bob: 0.02, tint: 0x6f6af8 },
  Grep:      { rate: 4.0, arc: 0.5,  bob: 0.03, tint: 0x6f6af8 },
  Glob:      { rate: 4.0, arc: 0.5,  bob: 0.03, tint: 0x6f6af8 },
  WebSearch: { rate: 3.0, arc: 0.45, bob: 0.03, tint: 0x9b97ff },
  WebFetch:  { rate: 3.0, arc: 0.45, bob: 0.03, tint: 0x9b97ff },
  Task:      { rate: 5.0, arc: 0.7,  bob: 0.06, tint: 0xf5b642 },
};
const DEFAULT_MOTION = { rate: 7.0, arc: 1.0, bob: 0.06, tint: 0x502ce7 };

/** What to write on the chip. An MCP tool is `mcp__<server>__<tool>`, which
 *  truncates to gibberish over a figure's head; the tool is the useful half. */
function toolLabel(tool) {
  if (!tool) return "";
  const m = /^mcp__([^_]+(?:[-_][^_]+)*)__(.+)$/.exec(tool);
  return m ? m[2] : tool;
}

function motionFor(tool) {
  if (!tool) return DEFAULT_MOTION;
  if (TOOL_MOTION[tool]) return TOOL_MOTION[tool];
  // Anything unrecognised, an MCP tool for instance, still gets a character
  // rather than falling back to the same generic swing as everything else.
  return DEFAULT_MOTION;
}

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
function makeLabel(text, font = 30) {
  const pad = font >= 28 ? 16 : 11, maxText = 380;
  const c = document.createElement("canvas");
  const ctx = c.getContext("2d");
  const face = `600 ${font}px -apple-system, system-ui, sans-serif`;
  ctx.font = face;
  // Clamping the canvas instead of the text is what cut BUSA3005_Cybersecurity
  // off mid-word: the glyphs were still drawn full width, into a narrower
  // bitmap. Shorten the string until it fits, and end it with an ellipsis so
  // the reader knows something was dropped.
  let shown = text;
  if (ctx.measureText(shown).width > maxText) {
    while (shown.length > 4 && ctx.measureText(shown + "\u2026").width > maxText) {
      shown = shown.slice(0, -1);
    }
    shown += "\u2026";
  }
  const w = Math.ceil(ctx.measureText(shown).width) + pad * 2;
  c.width = w;
  c.height = Math.round(font * 1.85);
  const g = c.getContext("2d");
  g.font = `600 ${font}px -apple-system, system-ui, sans-serif`;
  g.fillStyle = "rgba(10,9,16,0.72)";
  g.beginPath();
  const r = 12;
  g.roundRect(0, 3, c.width, c.height - 8, r);
  g.fill();
  g.fillStyle = "#e8e6f5";
  g.textBaseline = "middle";
  g.fillText(shown, pad, c.height / 2);

  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const sprite = new THREE.Sprite(
    new THREE.SpriteMaterial({ map: tex, transparent: true, depthWrite: false }),
  );
  const h = c.height / 56 * 0.9;
  sprite.scale.set(c.width / 56 * 0.9, h, 1);
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
    // Kept so selection can light the edge of the chosen plot.
    const rimRef = rim;

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

    // What it is doing, right now, over its head. Rebuilt only when the tool
    // changes: a new canvas texture every frame would be absurd.
    const tool = makeLabel(" ", 22);
    tool.position.y = 2.0;
    tool.visible = false;
    group.add(tool);

    group.userData = { project };
    scene.add(group);
    return { group, figure, arm, body: bodyMat, rim: rimRef, pad,
             tool, toolText: null, rise: 0 };
  }

  function layout() {
    // A ring, ordered by name so a plot does not jump when a neighbour leaves.
    const keys = [...plots.keys()].sort();
    keys.forEach((k, i) => {
      const p = plots.get(k);
      const a = (i / Math.max(keys.length, 1)) * Math.PI * 2;
      const r = keys.length <= 1 ? 0 : 3.4 + keys.length * 0.78;
      p.target = new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r);
    });
    // Pull back as the ring grows, so six plots frame as well as one does.
    const spread = keys.length <= 1 ? 0 : 3.4 + keys.length * 0.78;
    const d = 11 + spread * 1.15;
    camera.position.set(d * 0.62, d * 0.58, d * 0.72);
    camera.lookAt(0, 0.6, 0);
    needsFrame = true;
  }

  let agents = new Map();        // project -> { idle, name, tool }
  let needsFrame = true;

  /** Feed it a /api/agents snapshot. Returns true if the scene must animate. */
  function update(snapshot, allProjects) {
    const now = Date.now() / 1000;
    const next = new Map();
    for (const s of snapshot.sessions || []) {
      if (!s.project) continue;          // a session outside every watched root
      const raw = s.activity && s.activity.idle_seconds;
      // No transcript yet is a brand-new session, not an old one.
      const idle = (raw === null || raw === undefined) ? null : raw;
      const tools = (s.activity && s.activity.tools) || [];
      // A project can hold more than one session; the busiest one speaks for it.
      const prev = next.get(s.project);
      const cand = {
        idle,
        name: s.name,
        status: s.status,
        tool: tools.length ? tools[0].tool : null,
        working: idle !== null && idle <= ACTIVE_SECONDS,
        present: idle !== null && idle <= PRESENT_SECONDS,
        sessions: (prev ? prev.sessions : 0) + 1,
      };
      if (!prev || (prev.idle === null) ||
          (cand.idle !== null && cand.idle < prev.idle)) {
        next.set(s.project, cand);
      } else {
        prev.sessions = cand.sessions;
      }
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

  // ---- picking --------------------------------------------------------------
  // Raycast against the plot groups so a click lands on a place, not a pixel.
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();

  function pick(clientX, clientY) {
    const r = canvas.getBoundingClientRect();
    pointer.x = ((clientX - r.left) / r.width) * 2 - 1;
    pointer.y = -((clientY - r.top) / r.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObjects([...plots.values()].map(p => p.group), true);
    for (const h of hits) {
      let o = h.object;
      while (o && !o.userData.project) o = o.parent;
      if (o) return o.userData.project;
    }
    return null;
  }

  let selected = null;
  function select(project) {
    selected = plots.has(project) ? project : null;
    for (const [name, p] of plots) {
      p.rim.material.color.setHex(name === selected ? COL.active : COL.plotEdge);
    }
    needsFrame = true;
    return selected;
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

      const info = p.info || {};
      const working = info.working;
      p.body.color.setHex(
        working ? COL.body : (info.present ? COL.bodyIdle : COL.bodyStale));
      p.pad.material.color.setHex(info.present ? COL.plot : COL.plotStale);
      if (working) {
        const m = motionFor(info.tool);
        p.arm.rotation.x = Math.sin(t * m.rate) * m.arc - 0.3;
        p.figure.position.y = 0.5 + Math.abs(Math.sin(t * m.rate)) * m.bob;
        p.figure.rotation.y = Math.sin(t * 0.8) * 0.25;
        p.body.color.setHex(m.tint);
        if (p.toolText !== info.tool) {
          p.toolText = info.tool;
          p.group.remove(p.tool);
          p.tool = makeLabel(toolLabel(info.tool) || " ", 22);
          p.tool.position.y = 2.0;
          p.group.add(p.tool);
        }
        p.tool.visible = true;
        moving = true;
      } else {
        p.tool.visible = false;
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

  return { update, frame, resize, dispose, pick, select,
           get selected() { return selected; },
           get agents() { return agents; },
           get pending() { return needsFrame; } };
}

export { ACTIVE_SECONDS, PRESENT_SECONDS };
