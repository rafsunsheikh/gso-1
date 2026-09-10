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
  Edit:      { kind: "strike", rate: 9.0, arc: 1.3,  bob: 0.09, tint: 0x502ce7 },
  Write:     { kind: "strike", rate: 8.0, arc: 1.2,  bob: 0.08, tint: 0x502ce7 },
  NotebookEdit: { kind: "strike", rate: 8.0, arc: 1.2, bob: 0.08, tint: 0x502ce7 },
  Bash:      { kind: "crank",  rate: 5.5, arc: 0.9,  bob: 0.05, tint: 0x00e0b7 },
  Read:      { kind: "search", rate: 1.4, arc: 0.3,  bob: 0.01, tint: 0x6f6af8 },
  Grep:      { kind: "search", rate: 2.6, arc: 0.4,  bob: 0.02, tint: 0x6f6af8 },
  Glob:      { kind: "search", rate: 2.6, arc: 0.4,  bob: 0.02, tint: 0x6f6af8 },
  WebSearch: { kind: "search", rate: 1.9, arc: 0.4,  bob: 0.02, tint: 0x9b97ff },
  WebFetch:  { kind: "search", rate: 1.9, arc: 0.4,  bob: 0.02, tint: 0x9b97ff },
  Task:      { kind: "wave",   rate: 4.0, arc: 0.8,  bob: 0.07, tint: 0xf5b642 },
};
const DEFAULT_MOTION = { kind: "crank", rate: 6.0, arc: 0.9, bob: 0.05, tint: 0x502ce7 };

/**
 * What a project is, as a roof.
 *
 * GSO-1 already detects each project's stack, and a village whose buildings
 * differ by language means you can pick your Go service out of a field of
 * Python without reading a single label.
 */
const STACK_THEME = {
  python:    { roof: 0x3572a5, trim: 0xffd343 },
  django:    { roof: 0x0c4b33, trim: 0x44b78b },
  streamlit: { roof: 0xff4b4b, trim: 0xffd0d0 },
  node:      { roof: 0x3c873a, trim: 0x8cc84b },
  go:        { roof: 0x00add8, trim: 0xb7e8f5 },
  static:    { roof: 0x8a8aa3, trim: 0xd6d6e6 },
  script:    { roof: 0xd08a2e, trim: 0xf3c98b },
  unknown:   { roof: 0x502ce7, trim: 0x9b97ff },
};
function themeFor(kind) {
  return STACK_THEME[kind] || STACK_THEME.unknown;
}

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
  // Kept so frame() can hold the label at a constant size on screen.
  sprite.userData.aspect = c.width / c.height;
  const h = c.height / 56 * 0.9;
  sprite.scale.set(c.width / 56 * 0.9, h, 1);
  return sprite;
}

/**
 * Load a model exported by tools/blender_models.py.
 *
 * Plain JSON rather than glTF: these are flat-shaded boxes with no skinning,
 * textures or animation clips, and GLTFLoader would have meant vendoring it
 * plus BufferGeometryUtils and SkeletonUtils, about 190 KB of machinery, and an
 * import map, to read six cubes. Normals are not shipped either; three derives
 * flat faces from non-indexed geometry, which is the look we want anyway.
 *
 * Each part's vertices are relative to its own origin, and that origin is the
 * joint it turns about, so animation is a rotation and nothing else.
 */
async function loadParts(url) {
  const data = await fetch(url).then(r => r.json());
  const parts = new Map();
  for (const p of data.parts) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(p.positions, 3));
    geo.computeVertexNormals();
    // Blender writes linear base colours, which is what three works in.
    const colour = new THREE.Color().setRGB(p.color[0], p.color[1], p.color[2],
                                            THREE.LinearSRGBColorSpace);
    parts.set(p.name, {
      geo,
      colour,
      pivot: new THREE.Vector3(p.pivot[0], p.pivot[1], p.pivot[2]),
    });
  }
  return parts;
}

/** One instance of a loaded part, at its joint, with its own material so it
 *  can be tinted per agent without touching the others. */
function instance(parts, name, opts = {}) {
  const p = parts.get(name);
  if (!p) return null;
  const mesh = new THREE.Mesh(p.geo, new THREE.MeshStandardMaterial({
    color: opts.colour || p.colour.clone(),
    roughness: opts.roughness ?? 0.72,
    flatShading: true,
  }));
  mesh.position.copy(p.pivot);
  return mesh;
}

export async function createWorld(canvas) {
  const [workerParts, propParts] = await Promise.all([
    loadParts("/static/models/worker.json"),
    loadParts("/static/models/props.json"),
  ]);

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  renderer.setClearColor(COL.sky, 1);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(COL.sky, 40, 165);

  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 300);
  // Fixed three-quarter view: the Clash of Clans angle. Orbiting is a later
  // decision; a camera you cannot get lost in is better than a free one.
  camera.position.set(12.5, 11.5, 14);
  camera.lookAt(0, 0, 0);

  scene.add(new THREE.HemisphereLight(0x9a93ff, 0x0a0910, 1.1));
  const key = new THREE.DirectionalLight(0xffffff, 1.5);
  key.position.set(10, 18, 8);
  scene.add(key);

  // A sky dome so the horizon fades instead of ending in black. Rendered on the
  // inside, unlit, and it never moves, so it costs one draw call and no thought.
  const sky = new THREE.Mesh(
    new THREE.SphereGeometry(300, 24, 12),
    new THREE.ShaderMaterial({
      side: THREE.BackSide, depthWrite: false,
      uniforms: {
        top: { value: new THREE.Color(0x0a0913) },
        bottom: { value: new THREE.Color(0x1d1b30) },
      },
      vertexShader: `varying float h;
        void main(){ h = normalize(position).y;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
      fragmentShader: `varying float h; uniform vec3 top; uniform vec3 bottom;
        void main(){ gl_FragColor = vec4(mix(bottom, top, smoothstep(-0.1, 0.5, h)), 1.0); }`,
    }),
  );
  scene.add(sky);

  const ground = new THREE.Mesh(
    new THREE.CircleGeometry(130, 64),
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
      const r = 26 + hash(name + "r") * 88;
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

    // The village. Placed from a hash of the project name, so a plot looks
    // the same every time you come back to it rather than reshuffling.
    const village = new THREE.Group();
    const theme = themeFor(meta.get(project));
    const hut = new THREE.Group();
    for (const n of ["hut_base", "hut_wall", "hut_roof", "hut_door"]) {
      const m = instance(propParts, n,
                         n === "hut_roof" ? { colour: new THREE.Color(theme.roof) } : {});
      if (m) hut.add(m);
    }
    hut.position.set(-0.75, 0, -0.55);
    hut.rotation.y = (hash(project + "hut") - 0.5) * 0.7;
    village.add(hut);

    const store = new THREE.Group();
    for (const n of ["store_base", "store_wall", "store_roof"]) {
      const m = instance(propParts, n,
                         n === "store_roof" ? { colour: new THREE.Color(theme.roof) } : {});
      if (m) store.add(m);
    }
    store.position.set(0.95, 0, -0.85);
    store.rotation.y = hash(project + "store") * 2;
    village.add(store);

    const flag = new THREE.Group();
    const pole = instance(propParts, "flag_pole");
    const cloth = instance(propParts, "flag_cloth", { colour: new THREE.Color(theme.trim) });
    if (pole) flag.add(pole);
    if (cloth) flag.add(cloth);
    flag.position.set(1.35, 0, 0.75);
    village.add(flag);

    const tree = new THREE.Group();
    for (const n of ["tree_trunk", "tree_leaf1", "tree_leaf2", "tree_leaf3"]) {
      const m = instance(propParts, n);
      if (m) tree.add(m);
    }
    tree.position.set(-1.45, 0, 1.15);
    tree.rotation.y = hash(project + "tree") * 3;
    tree.scale.setScalar(0.85 + hash(project + "ts") * 0.3);
    village.add(tree);

    for (let i = 0; i < 2 + Math.floor(hash(project + "c") * 2); i++) {
      const crate = instance(propParts, "crate");
      if (!crate) break;
      const a = hash(project + "c" + i) * Math.PI * 2;
      crate.position.x += Math.cos(a) * 1.5;
      crate.position.z += Math.sin(a) * 1.5;
      crate.rotation.y = hash(project + "r" + i) * 3;
      village.add(crate);
    }
    village.position.y = 0.5;
    group.add(village);

    // The agent: separate parts, each turning about its own joint.
    const figure = new THREE.Group();
    const limb = {};
    for (const n of ["torso", "head", "armL", "armR", "legL", "legR"]) {
      const m = instance(workerParts, n);
      if (m) { figure.add(m); limb[n] = m; }
    }
    const bodyMat = limb.torso ? limb.torso.material : new THREE.MeshStandardMaterial();
    figure.position.set(0.1, 0, 0.95);
    figure.scale.setScalar(1.05);

    // Somewhere to work, somewhere to rest, and room to move between them.
    const anchors = {
      work:  new THREE.Vector2(-0.75, 0.45),
      sleep: new THREE.Vector2(-0.92, 1.52),
    };
    const walker = {
      pos: new THREE.Vector2(0.4, 0.7),
      target: new THREE.Vector2(0.4, 0.7),
      facing: 0,
      mode: "wander",
      wait: 0,
      step: 0,
    };

    const figureBase = new THREE.Group();
    figureBase.position.y = 0.5;
    figureBase.add(figure);
    group.add(figureBase);

    // A name plate, so a plot is a place rather than a shape. Drawn to a canvas
    // and used as a sprite: three.js has no text of its own, and pulling in a
    // font loader to write six short words would not be worth its weight.
    const label = makeLabel(project);
    label.position.y = 2.9;
    group.add(label);
    const labelRef = label;

    // What it is doing, right now, over its head. Rebuilt only when the tool
    // changes: a new canvas texture every frame would be absurd.
    const tool = makeLabel(" ", 22);
    tool.position.y = 2.0;
    tool.visible = false;
    group.add(tool);

    group.userData = { project };
    scene.add(group);
    return { group, figure, base: figureBase, limb, flag, label: labelRef,
             anchors, walker, tree,
             body: bodyMat, rim: rimRef, pad, tool, toolText: null, rise: 0 };
  }

  function layout() {
    // Villages sit where their name puts them, on a landscape big enough to
    // walk across. A ring was fine to look down on; you cannot roam a ring.
    const keys = [...plots.keys()].sort();
    keys.forEach((k, i) => {
      const p = plots.get(k);
      // Golden-angle spiral: even spacing, no two plots on top of each other,
      // and a given project keeps its place as neighbours come and go.
      const n = i + 1;
      const a = n * 2.399963 + hash(k) * 0.6;
      const r = 7.5 * Math.sqrt(n) + 4;
      p.target = new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r);
    });
    // Frame the ring as it grows, but stop the moment somebody takes the
    // camera themselves: nothing is more irritating than a view that argues.
    const spread = keys.length ? 7.5 * Math.sqrt(keys.length) + 6 : 8;
    if (!orbit.userMoved && !roam.on) {
      orbit.dist = 14 + spread * 1.15;
      applyCamera();
    }
  }

  let agents = new Map();        // project -> { idle, name, tool }
  const meta = new Map();        // project -> stack kind, for the theme
  let needsFrame = true;

  /** Feed it a /api/agents snapshot. Returns true if the scene must animate. */
  function update(snapshot, allProjects, projectMeta) {
    if (projectMeta) {
      for (const [k, v] of projectMeta) meta.set(k, v);
    }
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

  // ---- free roam ----------------------------------------------------------
  // You walk the landscape yourself: WASD to move, mouse to look, shift to run.
  // The camera trails an avatar rather than sitting behind your eyes, because
  // this is a world you are visiting, not a shooter, and a third-person view
  // makes it much easier to tell where you are relative to a village.
  const roam = {
    on: false,
    pos: new THREE.Vector3(0, 0, 26),
    vel: new THREE.Vector3(),
    yaw: Math.PI,
    pitch: 0.32,
    step: 0,
    keys: new Set(),
    avatar: null,
    limb: {},
  };

  const WALK = 7.5, RUN = 15.0, EYE = 3.4, TRAIL = 10.5;

  function buildAvatar() {
    const g = new THREE.Group();
    const limb = {};
    for (const n of ["torso", "head", "armL", "armR", "legL", "legR"]) {
      const m = instance(workerParts, n, n === "torso"
        ? { colour: new THREE.Color(0x00e0b7) }      // you are the teal one
        : {});
      if (m) { g.add(m); limb[n] = m; }
    }
    g.scale.setScalar(1.15);
    roam.limb = limb;
    scene.add(g);
    return g;
  }

  function setRoam(on) {
    roam.on = on;
    if (on && !roam.avatar) roam.avatar = buildAvatar();
    if (roam.avatar) roam.avatar.visible = on;
    if (!on) roam.keys.clear();
    needsFrame = true;
    kick();
    return roam.on;
  }

  const KEY_MAP = {
    KeyW: "f", ArrowUp: "f", KeyS: "b", ArrowDown: "b",
    KeyA: "l", ArrowLeft: "l", KeyD: "r", ArrowRight: "r",
    ShiftLeft: "run", ShiftRight: "run",
  };

  function onKey(e, down) {
    if (!roam.on) return;
    const k = KEY_MAP[e.code];
    if (!k) return;
    e.preventDefault();
    if (down) roam.keys.add(k); else roam.keys.delete(k);
    kick();
  }
  const keyDown = (e) => onKey(e, true);
  const keyUp = (e) => onKey(e, false);
  window.addEventListener("keydown", keyDown);
  window.addEventListener("keyup", keyUp);

  document.addEventListener("pointerlockchange", () => {
    // Losing the pointer drops you out of roaming rather than leaving you
    // walking blind.
    if (roam.on && document.pointerLockElement !== canvas) setRoam(false);
  });

  function onRoamMouse(e) {
    if (!roam.on || document.pointerLockElement !== canvas) return;
    roam.yaw -= e.movementX * 0.0022;
    roam.pitch = Math.max(-0.25, Math.min(0.95, roam.pitch + e.movementY * 0.0018));
    needsFrame = true;
  }
  document.addEventListener("mousemove", onRoamMouse);

  /** Move the avatar and put the camera behind it. Returns true while moving. */
  function stepRoam(dt) {
    const k = roam.keys;
    const speed = k.has("run") ? RUN : WALK;
    const fwd = new THREE.Vector3(Math.sin(roam.yaw), 0, Math.cos(roam.yaw));
    const right = new THREE.Vector3(fwd.z, 0, -fwd.x);
    const want = new THREE.Vector3();
    if (k.has("f")) want.add(fwd);
    if (k.has("b")) want.sub(fwd);
    if (k.has("r")) want.add(right);
    if (k.has("l")) want.sub(right);

    const walking = want.lengthSq() > 0;
    if (walking) want.normalize().multiplyScalar(speed);
    // Ease in and out so it does not start and stop like a chess piece.
    roam.vel.lerp(want, Math.min(1, dt * 9));
    roam.pos.addScaledVector(roam.vel, dt);

    // Villages are solid. Push out of any plot you walk into.
    for (const [, p] of plots) {
      const d = new THREE.Vector2(roam.pos.x - p.group.position.x,
                                  roam.pos.z - p.group.position.z);
      const len = d.length();
      if (len < 2.0 && len > 0.0001) {
        d.multiplyScalar((2.0 - len) / len);
        roam.pos.x += d.x;
        roam.pos.z += d.y;
      }
    }
    const bound = 120;
    roam.pos.x = Math.max(-bound, Math.min(bound, roam.pos.x));
    roam.pos.z = Math.max(-bound, Math.min(bound, roam.pos.z));

    const a = roam.avatar;
    if (a) {
      a.position.set(roam.pos.x, 0, roam.pos.z);
      const moving2 = roam.vel.lengthSq() > 0.5;
      if (moving2) {
        a.rotation.y = Math.atan2(roam.vel.x, roam.vel.z);
        roam.step += dt * (roam.vel.length() * 0.9);
        const sw = Math.sin(roam.step);
        roam.limb.legL && (roam.limb.legL.rotation.x = sw * 0.62);
        roam.limb.legR && (roam.limb.legR.rotation.x = -sw * 0.62);
        roam.limb.armL && (roam.limb.armL.rotation.x = -sw * 0.5);
        roam.limb.armR && (roam.limb.armR.rotation.x = sw * 0.5);
        a.position.y = Math.abs(Math.sin(roam.step * 2)) * 0.05;
      } else {
        for (const part of Object.values(roam.limb)) {
          part.rotation.x += (0 - part.rotation.x) * Math.min(1, dt * 8);
        }
        a.position.y += (0 - a.position.y) * Math.min(1, dt * 8);
      }
    }

    // Camera trails behind and above, looking where you are looking.
    const back = new THREE.Vector3(Math.sin(roam.yaw), 0, Math.cos(roam.yaw))
      .multiplyScalar(-TRAIL);
    camera.position.set(
      roam.pos.x + back.x,
      EYE + roam.pitch * 7.0,
      roam.pos.z + back.z,
    );
    camera.lookAt(roam.pos.x + fwd.x * 6, 1.6 - roam.pitch * 2.6, roam.pos.z + fwd.z * 6);
    return walking || roam.vel.lengthSq() > 0.02;
  }

  /** Which village are you standing next to? Drives the panel while roaming. */
  function nearestPlot(maxDist = 6.5) {
    let best = null, bestD = maxDist;
    for (const [name, p] of plots) {
      const d = Math.hypot(roam.pos.x - p.group.position.x,
                           roam.pos.z - p.group.position.z);
      if (d < bestD) { bestD = d; best = name; }
    }
    return best;
  }

  // ---- orbit ------------------------------------------------------------
  // Hand-rolled rather than vendoring OrbitControls: this needs drag-to-turn
  // and wheel-to-zoom and nothing else, and it has to be able to hand control
  // back to the auto-framing when the world changes shape.
  const orbit = { az: 0.86, pol: 1.0, dist: 18, userMoved: false };

  function applyCamera() {
    const d = orbit.dist;
    camera.position.set(
      Math.sin(orbit.az) * Math.sin(orbit.pol) * d,
      Math.cos(orbit.pol) * d,
      Math.cos(orbit.az) * Math.sin(orbit.pol) * d,
    );
    camera.lookAt(0, 0.6, 0);
    needsFrame = true;
  }

  let dragging = null;
  canvas.addEventListener("pointerdown", (e) => {
    dragging = { x: e.clientX, y: e.clientY };
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    orbit.az -= (e.clientX - dragging.x) * 0.008;
    // Clamped so you cannot end up under the ground or looking straight down.
    orbit.pol = Math.max(0.22, Math.min(1.32, orbit.pol - (e.clientY - dragging.y) * 0.006));
    dragging = { x: e.clientX, y: e.clientY };
    orbit.userMoved = true;
    applyCamera();
    kick();
  });
  const endDrag = (e) => {
    dragging = null;
    try { canvas.releasePointerCapture(e.pointerId); } catch (_) {}
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    orbit.dist = Math.max(7, Math.min(70, orbit.dist * (1 + Math.sign(e.deltaY) * 0.1)));
    orbit.userMoved = true;
    applyCamera();
    kick();
  }, { passive: false });

  // The scene owns its own wake-up: a drag has to redraw even when no agent is
  // working, and the page's loop only restarts when something asks it to.
  let kick = () => {};
  function onNeedsFrame(fn) { kick = fn; }

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
    // Sleepers breathe, which is charming and must never be a reason to hold
    // the render loop open: a world of idle agents would then run at 60fps for
    // ever. They breathe while somebody else is awake, and settle when the
    // village does.
    const anyAwake = [...plots.values()].some(
      (p) => p.info && (p.info.working || p.info.present));

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

      // Where should this agent be? Working at the workshop, resting under the
      // tree, or wandering its plot between the two.
      const w = p.walker;
      const wantMode = working ? "work" : (info.present ? "wander" : "sleep");
      if (w.mode !== wantMode) {
        w.mode = wantMode;
        w.wait = 0;
        if (wantMode === "work") w.target.copy(p.anchors.work);
        else if (wantMode === "sleep") w.target.copy(p.anchors.sleep);
        w.settleFacing = wantMode === "sleep" ? 1.9 : null;
      }

      const toTarget = w.target.clone().sub(w.pos);
      const dist = toTarget.length();
      const walking = dist > 0.06;
      if (walking) {
        const speed = (w.mode === "work" ? 1.5 : 0.8) * dt;
        w.pos.addScaledVector(toTarget.normalize(), Math.min(speed, dist));
        // Face where you are going, turning the short way round.
        const want = Math.atan2(toTarget.x, toTarget.y);
        let d = ((want - w.facing + Math.PI) % (Math.PI * 2)) - Math.PI;
        w.facing += d * Math.min(1, dt * 7);
        w.step += dt * 9;
        moving = true;
      } else if (w.mode === "sleep" && w.settleFacing !== null && w.settleFacing !== undefined) {
        // Turn side-on once it arrives, so a lying figure is a silhouette and
        // not a foreshortened smudge.
        const d = ((w.settleFacing - w.facing + Math.PI) % (Math.PI * 2)) - Math.PI;
        if (Math.abs(d) > 0.02) { w.facing += d * Math.min(1, dt * 4); moving = true; }
      } else if (w.mode === "wander") {
        // Stand a moment, then pick somewhere else on the plot to be.
        w.wait -= dt;
        if (w.wait <= 0) {
          const a = Math.random() * Math.PI * 2;
          const r = 0.5 + Math.random() * 1.3;
          w.target.set(Math.cos(a) * r, Math.sin(a) * r);
          w.wait = 1.5 + Math.random() * 3;
        }
        moving = true;      // it is about to set off again
      }
      p.figure.position.set(w.pos.x, 0, w.pos.y);
      p.figure.rotation.y = w.facing;

      p.body.color.setHex(
        working ? COL.body : (info.present ? COL.bodyIdle : COL.bodyStale));
      p.pad.material.color.setHex(info.present ? COL.plot : COL.plotStale);
      if (working && !walking) {
        const m = motionFor(info.tool);
        const swing = Math.sin(t * m.rate);
        const kind = m.kind;

        // Each tool reads as a different job. Hammering is two arms and a
        // forward lean; searching is the head turning and the body still.
        if (kind === "strike") {
          p.limb.armR && (p.limb.armR.rotation.x = swing * m.arc - 1.5);
          p.limb.armL && (p.limb.armL.rotation.x = swing * m.arc * 0.5 - 0.8);
          p.figure.rotation.x = 0.12 + swing * 0.05;
          p.limb.head && (p.limb.head.rotation.x = 0.25);
        } else if (kind === "crank") {
          p.limb.armR && (p.limb.armR.rotation.x = swing * m.arc - 0.9);
          p.limb.armL && (p.limb.armL.rotation.x = -swing * m.arc * 0.6 - 0.4);
          p.figure.rotation.x = 0.05;
          p.limb.head && (p.limb.head.rotation.x = 0.1);
        } else if (kind === "search") {
          // Barely moves: reading and grepping are not physical work.
          p.limb.head && (p.limb.head.rotation.y = Math.sin(t * m.rate) * 0.7);
          p.limb.armR && (p.limb.armR.rotation.x = -0.55);
          p.limb.armL && (p.limb.armL.rotation.x = -0.55);
          p.figure.rotation.x = 0.16;
        } else {
          p.limb.armR && (p.limb.armR.rotation.x = swing * m.arc - 0.4);
          p.limb.armL && (p.limb.armL.rotation.x = -swing * m.arc - 0.4);
          p.figure.rotation.x = 0;
        }
        // A small weight shift keeps it alive without looking like marching.
        p.limb.legL && (p.limb.legL.rotation.x = Math.sin(t * m.rate * 0.5) * 0.08);
        p.limb.legR && (p.limb.legR.rotation.x = -Math.sin(t * m.rate * 0.5) * 0.08);
        p.base.position.y = 0.5 + Math.abs(swing) * m.bob;
        p.base.rotation.y = Math.sin(t * 0.5) * 0.12;
        p.body.color.setHex(m.tint);
        if (p.flag) p.flag.rotation.y = Math.sin(t * 1.6) * 0.25;

        if (p.toolText !== info.tool) {
          p.toolText = info.tool;
          p.group.remove(p.tool);
          p.tool = makeLabel(toolLabel(info.tool) || " ", 22);
          p.tool.position.y = 2.35;
          p.group.add(p.tool);
        }
        p.tool.visible = true;
        moving = true;
      } else {
        p.tool.visible = false;
        const k = Math.min(1, dt * 5);

        if (walking) {
          // A walk cycle: legs opposed, arms counter-swinging, a little bounce.
          const sw = Math.sin(w.step);
          p.limb.legL && (p.limb.legL.rotation.x = sw * 0.55);
          p.limb.legR && (p.limb.legR.rotation.x = -sw * 0.55);
          p.limb.armL && (p.limb.armL.rotation.x = -sw * 0.42);
          p.limb.armR && (p.limb.armR.rotation.x = sw * 0.42);
          p.limb.head && (p.limb.head.rotation.y += (0 - p.limb.head.rotation.y) * k);
          for (const part of Object.values(p.limb)) {
            if (part.rotation.z) part.rotation.z += (0 - part.rotation.z) * k;
          }
          p.figure.rotation.x = 0.06;
          p.base.position.y = 0.5 + Math.abs(Math.sin(w.step * 2)) * 0.045;
          p.body.color.setHex(info.present ? COL.bodyIdle : COL.bodyStale);
        } else if (w.mode === "sleep") {
          // Lying down at the foot of the tree. The figure tips onto its back
          // and the limbs go slack; the whole thing breathes, slowly.
          const breathe = Math.sin(t * 1.1) * 0.02;
          p.figure.rotation.x += (-Math.PI / 2 + 0.12 - p.figure.rotation.x) * k;
          // Once the body is tipped on its back, a limb's own X rotation bends
          // it upwards, so these stay small or the sleeper splays like a
          // dropped puppet. The spread goes on Z instead, which reads as
          // slack rather than stiff.
          p.limb.legL && (p.limb.legL.rotation.x += (0.06 - p.limb.legL.rotation.x) * k);
          p.limb.legR && (p.limb.legR.rotation.x += (-0.02 - p.limb.legR.rotation.x) * k);
          p.limb.armL && (p.limb.armL.rotation.x += (0.10 - p.limb.armL.rotation.x) * k);
          p.limb.armR && (p.limb.armR.rotation.x += (0.04 - p.limb.armR.rotation.x) * k);
          p.limb.armL && (p.limb.armL.rotation.z += (0.34 - p.limb.armL.rotation.z) * k);
          p.limb.armR && (p.limb.armR.rotation.z += (-0.28 - p.limb.armR.rotation.z) * k);
          p.limb.head && (p.limb.head.rotation.y += (0.4 - p.limb.head.rotation.y) * k);
          p.base.position.y += (0.66 + breathe - p.base.position.y) * k;
          p.body.color.setHex(COL.bodyStale);
          // Still settling into the pose, or kept alive by somebody working.
          if (Math.abs(p.figure.rotation.x + Math.PI / 2 - 0.12) > 0.01) moving = true;
          if (anyAwake) moving = true;
        } else {
          let rest = 0;
          for (const [name, part] of Object.entries(p.limb)) {
            part.rotation.x += (0 - part.rotation.x) * k;
            if (part.rotation.z) part.rotation.z += (0 - part.rotation.z) * k;
            if (name === "head") part.rotation.y += (0 - part.rotation.y) * k;
            rest += Math.abs(part.rotation.x);
          }
          p.figure.rotation.x += (0 - p.figure.rotation.x) * k;
          p.base.position.y += (0.5 - p.base.position.y) * k;
          p.body.color.setHex(info.present ? COL.bodyIdle : COL.bodyStale);
          if (rest > 0.02) moving = true;
        }
      }
    }

    // A world-space sprite grows as you approach it, so the nearest plot's name
    // ended up several times the size of the far ones and dominated the picture.
    // Rescaling by distance each frame holds every label steady on screen.
    for (const [, p] of plots) {
      const d = camera.position.distanceTo(p.group.position);
      for (const sp of [p.label, p.tool]) {
        if (!sp || !sp.visible) continue;
        const k = d * 0.026;
        sp.scale.set((sp.userData.aspect || 3) * k, k, 1);
      }
    }

    if (roam.on && stepRoam(dt)) moving = true;

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
    window.removeEventListener("keydown", keyDown);
    window.removeEventListener("keyup", keyUp);
    document.removeEventListener("mousemove", onRoamMouse);
    scene.traverse(o => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) (Array.isArray(o.material) ? o.material : [o.material]).forEach(m => m.dispose());
    });
    renderer.dispose();
  }

  applyCamera();

  return { update, frame, resize, dispose, pick, select, onNeedsFrame,
           setRoam, nearestPlot, get roaming() { return roam.on; },
           resetCamera: () => { orbit.userMoved = false; layout(); },
           get selected() { return selected; },
           get agents() { return agents; },
           get pending() { return needsFrame; } };
}

export { ACTIVE_SECONDS, PRESENT_SECONDS };
