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
import { GLTFLoader } from "./vendor/three/addons/GLTFLoader.js";
import { clone as cloneSkinned } from "./vendor/three/addons/SkeletonUtils.js";

/** Still swinging: a tool call this recently means visibly at work. */
const ACTIVE_SECONDS = 30;
/** Recently at work: standing on lit land, ready to go again. */
const PRESENT_SECONDS = 300;

// Every live session gets a plot, whatever it is doing. Hiding the quiet ones
// was the wrong call: a session idle for two hours is still a session you have
// open, and a map that shows one of your six agents is not a map of your
// machine. State is carried in how a plot looks, not in whether it exists.

const COL = {
  sky: 0x8fd0f5,
  ground: 0x6aa84f,
  terrain: 0x5d9440,      // the dormant 271
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
  const texCache = loadParts._tex || (loadParts._tex = new Map());
  for (const p of data.parts) {
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(p.positions, 3));
    if (p.index) geo.setIndex(p.index);
    if (p.uvs) geo.setAttribute("uv", new THREE.Float32BufferAttribute(p.uvs, 2));
    geo.computeVertexNormals();
    let map = null;
    if (p.tex) {
      // One Texture per file, shared by every part that uses it: the packs
      // repeat the same atlas in every model, which is why they are so large.
      map = texCache.get(p.tex);
      if (!map) {
        map = new THREE.TextureLoader().load("/static/models/tex/" + p.tex);
        map.colorSpace = THREE.SRGBColorSpace;
        map.flipY = false;                       // glTF UV convention
        texCache.set(p.tex, map);
      }
    }
    // Blender writes linear base colours, which is what three works in.
    const colour = new THREE.Color().setRGB(p.color[0], p.color[1], p.color[2],
                                            THREE.LinearSRGBColorSpace);
    parts.set(p.name, {
      geo,
      map,
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
    color: opts.colour || (p.map ? new THREE.Color(0xffffff) : p.colour.clone()),
    map: p.map || null,
    // Foliage atlases are cut-outs; without this every leaf card is a square.
    alphaTest: p.map ? 0.5 : 0,
    transparent: false,
    side: p.map ? THREE.DoubleSide : THREE.FrontSide,
    roughness: opts.roughness ?? 0.72,
    flatShading: !p.map,
  }));
  mesh.position.copy(p.pivot);
  return mesh;
}

export async function createWorld(canvas) {
  const [workerParts, propParts, kitParts, groundParts, agentGltf] = await Promise.all([
    loadParts("/static/models/worker.json"),
    loadParts("/static/models/props.json"),
    loadParts("/static/models/kit.json"),
    loadParts("/static/models/ground.json"),
    // The agent is a rigged character with seventeen animation clips, which is
    // the one thing the JSON pipeline cannot express: skinning needs real
    // glTF, so GLTFLoader is vendored for this and only this.
    new GLTFLoader().loadAsync("/static/models/agent.glb").catch(() => null),
  ]);

  /**
   * How an agent's state reads as an animation.
   *
   * The clips came with the model, so the mapping is a matter of picking the
   * honest one: an agent editing files is picking things up and putting them
   * down, an agent running a command is doing something percussive, and an
   * agent that has been quiet for hours is lying on the floor.
   */
  const CLIP_FOR = {
    walk: "Walk", run: "Run", idle: "Idle",
    Edit: "Pickup", Write: "Pickup", NotebookEdit: "Pickup",
    Bash: "Shoot_Small", Task: "Dance",
    Read: "Yes", Grep: "No", Glob: "No",
    WebSearch: "No", WebFetch: "No",
  };

  const agentClips = new Map();
  if (agentGltf) {
    for (const c of agentGltf.animations) {
      agentClips.set((c.name || "").split("|").pop(), c);
    }
  }

  /** One character: a skinned clone with its own mixer and actions. */
  function makeAgent(tint) {
    if (!agentGltf) return null;
    const root = cloneSkinned(agentGltf.scene);
    root.traverse((o) => {
      if (!o.isMesh) return;
      o.material = o.material.clone();
      if (tint) o.material.color = new THREE.Color(tint);
      o.frustumCulled = false;       // skinned bounds go stale as it animates
    });
    const mixer = new THREE.AnimationMixer(root);
    const actions = new Map();
    for (const [name, clip] of agentClips) {
      actions.set(name, mixer.clipAction(clip));
    }
    return { root, mixer, actions, current: null };
  }

  /** Cross-fade to a clip. Cheap when it is already playing. */
  function playClip(agent, name, fade = 0.25) {
    if (!agent || agent.current === name) return;
    const next = agent.actions.get(name) || agent.actions.get("Idle");
    if (!next) return;
    const prev = agent.current && agent.actions.get(agent.current);
    next.reset().setEffectiveWeight(1).fadeIn(fade).play();
    if (prev) prev.fadeOut(fade);
    agent.current = name;
  }

  /** A whole kit model: its parts are one group per material, named
   *  `name__0`, `name__1`, and so on. */
  function model(name) {
    const g = new THREE.Group();
    let found = 0;
    for (const key of kitParts.keys()) {
      if (!key.startsWith(name + "__")) continue;
      const m = instance(kitParts, key, { roughness: 0.78 });
      if (m) { g.add(m); found++; }
    }
    return found ? g : null;
  }

  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
  renderer.setClearColor(0xcfe6f7, 1);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0xcfe6f7, 160, 760);

  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 300);
  // Fixed three-quarter view: the Clash of Clans angle. Orbiting is a later
  // decision; a camera you cannot get lost in is better than a free one.
  camera.position.set(12.5, 11.5, 14);
  camera.lookAt(0, 0, 0);

  scene.add(new THREE.HemisphereLight(0xbfe3ff, 0x5a7a3a, 1.5));
  const sun = new THREE.DirectionalLight(0xfff6e0, 2.1);
  sun.position.set(60, 90, 40);
  scene.add(sun);

  // A daylight sky: deep blue overhead easing to a pale haze at the horizon,
  // which is what makes distant hills read as distance rather than as fog.
  const sky = new THREE.Mesh(
    new THREE.SphereGeometry(1200, 32, 16),
    new THREE.ShaderMaterial({
      side: THREE.BackSide, depthWrite: false,
      uniforms: {
        top: { value: new THREE.Color(0x2f7fd4) },
        bottom: { value: new THREE.Color(0xdff0fb) },
      },
      vertexShader: `varying float h;
        void main(){ h = normalize(position).y;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }`,
      fragmentShader: `varying float h; uniform vec3 top; uniform vec3 bottom;
        void main(){ gl_FragColor = vec4(mix(bottom, top, smoothstep(-0.05, 0.55, h)), 1.0); }`,
    }),
  );
  scene.add(sky);

  /**
   * Rolling ground.
   *
   * A flat disc reads as a table with things on it. Three octaves of cheap
   * value noise give hills to walk over and horizons to walk towards, and the
   * same function answers "how high is the ground here?" so everything else,
   * villages, trees and your own feet, can sit on it.
   */
  const TERRAIN = { size: 900, seg: 300, height: 34 };

  function noise2(x, z) {
    const s = Math.sin(x * 12.9898 + z * 78.233) * 43758.5453;
    return s - Math.floor(s);
  }
  function smoothNoise(x, z) {
    const xi = Math.floor(x), zi = Math.floor(z);
    const xf = x - xi, zf = z - zi;
    const u = xf * xf * (3 - 2 * xf), v = zf * zf * (3 - 2 * zf);
    const a = noise2(xi, zi), b = noise2(xi + 1, zi);
    const c = noise2(xi, zi + 1), d = noise2(xi + 1, zi + 1);
    return a * (1 - u) * (1 - v) + b * u * (1 - v) + c * (1 - u) * v + d * u * v;
  }
  function groundHeight(x, z) {
    let h = 0, amp = 1, freq = 0.008, sum = 0;
    for (let o = 0; o < 4; o++) {
      h += smoothNoise(x * freq, z * freq) * amp;
      sum += amp;
      amp *= 0.5; freq *= 2.1;
    }
    h /= sum;
    // Push the middle down into a valley so villages sit in open ground and
    // the hills rise around them.
    const d = Math.hypot(x, z) / (TERRAIN.size * 0.5);
    // Ring of mountains around the rim, a broad valley in the middle.
    return (h - 0.5) * TERRAIN.height + Math.pow(Math.min(d, 1.5), 2.6) * 70;
  }

  const groundGeo = new THREE.PlaneGeometry(
    TERRAIN.size, TERRAIN.size, TERRAIN.seg, TERRAIN.seg);
  groundGeo.rotateX(-Math.PI / 2);
  {
    const pos = groundGeo.attributes.position;
    const colours = new Float32Array(pos.count * 3);
    const lowland = new THREE.Color(0x74b04a);
    const upland = new THREE.Color(0x4e8c3a);
    const rock = new THREE.Color(0x8d8f96);
    const c = new THREE.Color();
    for (let i = 0; i < pos.count; i++) {
      const x = pos.getX(i), z = pos.getZ(i);
      const y = groundHeight(x, z);
      pos.setY(i, y);
      // Grass in the valley, darker green on the slopes, bare rock up high.
      const t = Math.max(0, Math.min(1, (y + 8) / 26));
      c.copy(lowland).lerp(upland, t);
      if (y > 16) c.lerp(rock, Math.min(1, (y - 16) / 16));
      // A beach where the land meets the water.
      if (y < -7.0) c.lerp(new THREE.Color(0xd8cc9a), Math.min(1, (-7.0 - y) / 3.5));
      colours[i * 3] = c.r; colours[i * 3 + 1] = c.g; colours[i * 3 + 2] = c.b;
    }
    groundGeo.setAttribute("color", new THREE.BufferAttribute(colours, 3));
    groundGeo.computeVertexNormals();
  }
  /**
   * Water.
   *
   * A single plane at the height the valley floor falls below. Everywhere the
   * terrain dips under it becomes a lake, which means the lakes are wherever
   * the land actually is lowest rather than somewhere they were placed, and
   * they cost one draw call however many of them there are.
   */
  const WATER_LEVEL = -9.5;
  const water = new THREE.Mesh(
    new THREE.PlaneGeometry(TERRAIN.size, TERRAIN.size, 1, 1),
    new THREE.MeshStandardMaterial({
      color: 0x2e86c7, roughness: 0.18, metalness: 0.25,
      transparent: true, opacity: 0.86,
    }),
  );
  water.rotation.x = -Math.PI / 2;
  water.position.y = WATER_LEVEL;
  scene.add(water);

  const ground = new THREE.Mesh(
    groundGeo,
    new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 1, flatShading: true }),
  );
  scene.add(ground);

  /**
   * The height of the ground *as drawn*, which is not the same number as
   * groundHeight().
   *
   * The terrain is a grid of triangles three metres across, and a triangle is
   * flat while the noise it samples is not: on any bump the analytic surface
   * arches above the flat face drawn between its corners. Placing things by
   * the formula therefore left them hovering, worst on the crests. This reads
   * the same vertices the mesh uses and interpolates across the same triangle,
   * so anything placed with it sits exactly on the visible surface.
   */
  const gPos = groundGeo.attributes.position;
  const gStep = TERRAIN.size / TERRAIN.seg;
  const gHalf = TERRAIN.size / 2;
  function vertexY(ix, iy) {
    const cx = Math.max(0, Math.min(TERRAIN.seg, ix));
    const cy = Math.max(0, Math.min(TERRAIN.seg, iy));
    return gPos.getY(cy * (TERRAIN.seg + 1) + cx);
  }
  function terrainY(x, z) {
    const fx = (x + gHalf) / gStep;
    const fz = (z + gHalf) / gStep;
    const ix = Math.floor(fx), iz = Math.floor(fz);
    const tx = fx - ix, tz = fz - iz;
    const a = vertexY(ix, iz), b = vertexY(ix + 1, iz);
    const c = vertexY(ix, iz + 1), d = vertexY(ix + 1, iz + 1);
    // PlaneGeometry splits each quad along one diagonal; match it rather than
    // bilinear, or things still float by a few centimetres on the seam.
    return (tx + tz < 1)
      ? a + (b - a) * tx + (c - a) * tz
      : d + (c - d) * (1 - tx) + (b - d) * (1 - tz);
  }

  // ---- the dormant library, as terrain -------------------------------------
  // One InstancedMesh for all of them: 271 separate objects would be 271 draw
  // calls to say "nothing is happening here".
  // The dormant library used to be a field of little blocks. The landscape
  // itself is the scenery now, so there is nothing to stand in for it.
  let terrain = null;
  function setTerrain() { /* the ground is the world */ }

  /**
   * Scatter the landscape with trees and rocks.
   *
   * One InstancedMesh per material of each model: a few thousand separate
   * objects would be a few thousand draw calls to draw a wood. Placement is
   * deterministic, so the forest is in the same place every time you look, and
   * anything that lands too close to a village is dropped so the settlements
   * keep their clearings.
   */
  function scatterNature(clearings) {
    for (const g of scatterGroups) {
      scene.remove(g);
      g.geometry?.dispose?.();
    }
    scatterGroups.length = 0;

    const spread = TERRAIN.size * 0.46;

    // A seeded PRNG, not the string hash. Hashing "tree_x_1", "tree_x_2" and so
    // on gave correlated values for sequential inputs, and the forest came out
    // in visible rows. mulberry32 decorrelates properly and is still
    // deterministic, so the wood is random-looking but in the same place every
    // time you come back.
    const rng = (seed) => () => {
      seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
      let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    // Ground cover first, then the things that stand up out of it. `from`
    // picks the kit each model lives in: the fantasy buildings have no
    // textures, the nature ground cover does.
    const plans = [
      { from: groundParts, model: "Grass",             count: 5200, scale: [1.6, 3.2],  maxHeight: 16 },
      { from: groundParts, model: "Grass Wispy",       count: 3200, scale: [1.6, 3.0],  maxHeight: 14 },
      { from: groundParts, model: "Flower Group",      count: 1500,  scale: [1.2, 2.4],  maxHeight: 10 },
      { from: groundParts, model: "Bush",              count: 1500,  scale: [2.4, 5.0],  maxHeight: 18 },
      { from: groundParts, model: "Bush with Flowers", count: 600,  scale: [2.4, 4.4],  maxHeight: 12 },
      { from: groundParts, model: "Fern",              count: 1100,  scale: [1.6, 3.4],  maxHeight: 14 },
      { from: groundParts, model: "Mushroom",          count: 340,  scale: [1.0, 2.2],  maxHeight: 10 },
      { from: groundParts, model: "Pebble Round",      count: 700,  scale: [1.0, 2.6],  maxHeight: 30 },
      { from: kitParts,    model: "tree",              count: 2600, scale: [9, 19],     maxHeight: 24 },
      { from: kitParts,    model: "trees",             count: 1300,  scale: [8, 16],     maxHeight: 20 },
      { from: kitParts,    model: "rock",              count: 800,  scale: [2, 8],      maxHeight: 70 },
      { from: kitParts,    model: "logs",              count: 220,  scale: [1.6, 3.0],  maxHeight: 12 },
      // The rim. Big enough to read as mountains from the valley floor.
      { from: kitParts,    model: "mountain",          count: 220,  scale: [45, 110],   minHeight: 20 },
      { from: kitParts,    model: "mountain2",         count: 140,   scale: [55, 130],   minHeight: 26 },
    ];

    for (const plan of plans) {
      // Plants grow in company. Rather than sprinkling uniformly, most of each
      // species is drawn around a handful of thickets with the rest wandering
      // loose, which is what stops a wood looking like an orchard.
      const rand = rng(hash(plan.model) * 1e9 | 0);
      // Clump centres on a jittered grid rather than at random. Purely random
      // centres leave whole quarters of the valley bare and pile the rest into
      // one corner, which is what made the forest look lopsided; a jittered
      // grid covers the ground evenly and still looks unplanned.
      const clumps = [];
      const nClumps = Math.max(8, Math.round(plan.count / 22));
      const cols = Math.ceil(Math.sqrt(nClumps));
      const cell = (spread * 2) / cols;
      for (let cy = 0; cy < cols; cy++) {
        for (let cx = 0; cx < cols; cx++) {
          if (clumps.length >= nClumps) break;
          clumps.push({
            x: -spread + (cx + 0.15 + rand() * 0.7) * cell,
            z: -spread + (cy + 0.15 + rand() * 0.7) * cell,
            r: cell * (0.35 + rand() * 0.55),
          });
        }
      }
      const spots = [];
      for (let i = 0; i < plan.count * 6 && spots.length < plan.count; i++) {
        let x, z;
        if (rand() < 0.78 && clumps.length) {
          const c = clumps[(rand() * clumps.length) | 0];
          // Square-rooted radius: dense in the middle, thinning at the edge.
          const a = rand() * Math.PI * 2;
          const d = Math.sqrt(rand()) * c.r;
          x = c.x + Math.cos(a) * d;
          z = c.z + Math.sin(a) * d;
        } else {
          x = (rand() - 0.5) * 2 * spread;
          z = (rand() - 0.5) * 2 * spread;
        }
        if (Math.abs(x) > spread || Math.abs(z) > spread) continue;
        const y = terrainY(x, z);
        if (y < WATER_LEVEL + 1.2) continue;           // nothing grows in the lake
        if (plan.maxHeight !== undefined && y > plan.maxHeight) continue;
        if (plan.minHeight !== undefined && y < plan.minHeight) continue;
        if (clearings.some(c => Math.hypot(x - c.x, z - c.z) < 22)) continue;
        // Vary size and lean as well as position: identical copies on a grid
        // is the other half of why scatter reads as artificial.
        spots.push({
          x, y, z,
          s: plan.scale[0] + Math.pow(rand(), 1.7) * (plan.scale[1] - plan.scale[0]),
          r: rand() * Math.PI * 2,
          tilt: (rand() - 0.5) * 0.14,
        });
      }
      if (!spots.length) continue;

      for (const key of plan.from.keys()) {
        if (!key.startsWith(plan.model + "__")) continue;
        const part = plan.from.get(key);
        const mesh = new THREE.InstancedMesh(
          part.geo,
          new THREE.MeshStandardMaterial({
            color: part.map ? new THREE.Color(0xffffff) : part.colour.clone(),
            map: part.map || null,
            alphaTest: part.map ? 0.5 : 0,
            side: part.map ? THREE.DoubleSide : THREE.FrontSide,
            roughness: 0.85, flatShading: !part.map }),
          spots.length,
        );
        const m = new THREE.Object3D();
        spots.forEach((sp, i) => {
          m.position.set(sp.x, sp.y, sp.z);
          m.rotation.set(sp.tilt || 0, sp.r, (sp.tilt || 0) * 0.6);
          m.scale.set(sp.s * (0.85 + (sp.tilt || 0) + 0.15), sp.s, sp.s);
          m.updateMatrix();
          mesh.setMatrixAt(i, m.matrix);
        });
        mesh.instanceMatrix.needsUpdate = true;
        mesh.frustumCulled = false;
        scene.add(mesh);
        scatterGroups.push(mesh);
      }
    }
  }
  const scatterGroups = [];

  // ---- the active plots ----------------------------------------------------
  const plots = new Map();       // project -> { group, figure, arm, target, t }

  function buildPlot(project) {
    const group = new THREE.Group();

    // No pad. A village stands on the ground like anything else; the hexagon
    // made every project look like an exhibit on a plinth.
    const pad = { material: { color: { setHex() {} } } };   // kept for old callers
    const rimRef = { material: { color: { setHex() {} } } };

    // The village. Placed from a hash of the project name, so a plot looks
    // the same every time you come back to it rather than reshuffling.
    const village = new THREE.Group();
    const theme = themeFor(meta.get(project));

    // The buildings are Quaternius' CC0 fantasy RTS kit; they carry their own
    // palette, so the project's stack shows on the banner and the plot edge
    // rather than by repainting somebody else's model.
    // A village sits on a slope, so each building needs its own ground height.
    // The site is not known until layout() runs, so the offsets are recorded
    // here and resolved there.
    const props = [];
    const place = (name, x, z, scale, spin) => {
      const m = model(name);
      if (!m) return null;
      m.position.set(x, 0, z);
      m.scale.setScalar(scale);
      m.rotation.y = spin;
      village.add(m);
      props.push({ obj: m, x, z });
      return m;
    };

    // A settlement, not a diorama: a hall, outbuildings, a market and a wall
    // of trees, spread over enough ground that you can walk between them.
    place("towncenter", 0, -4, 9, (hash(project + "h") - 0.5) * 0.9);
    place("house", -7, -2, 7, hash(project + "h2") * 3);
    place("house", 6.5, 3.5, 6.4, hash(project + "h3") * 3);
    place("hut", -4.5, 5.5, 5, hash(project + "s") * 3);
    place("storage", 4, 7, 5.5, hash(project + "st") * 3);
    place("market", 0, 4.5, 6, hash(project + "mk") * 3);
    if (hash(project + "w") > 0.5) place("windmill", 10, -5, 8, hash(project + "wr") * 3);
    else place("tower", 10, -5, 8, hash(project + "tr") * 3);
    if (hash(project + "f") > 0.55) place("farm", -11, 6, 8, hash(project + "fr") * 3);
    if (hash(project + "tp") > 0.7) place("temple", -9, -8, 7, hash(project + "tpr") * 3);
    const tree = place("tree", -13, 2, 12, hash(project + "t") * 3);
    place("tree", 13, 6, 11, hash(project + "t2") * 3);
    place("logs", 2.5, 8.5, 2.4, hash(project + "l") * 3);
    for (let i = 0; i < 4 + Math.floor(hash(project + "c") * 4); i++) {
      const a = hash(project + "c" + i) * Math.PI * 2;
      const rr = 9 + hash(project + "cd" + i) * 6;
      place("rock", Math.cos(a) * rr, Math.sin(a) * rr,
            1.5 + hash(project + "rs" + i) * 3, hash(project + "rr" + i) * 3);
    }

    // The banner stays ours: it is the one thing on the plot that has to carry
    // GSO-1's own meaning rather than the kit's.
    const flag = new THREE.Group();
    const pole = instance(propParts, "flag_pole");
    const cloth = instance(propParts, "flag_cloth", { colour: new THREE.Color(theme.trim) });
    if (pole) flag.add(pole);
    if (cloth) flag.add(cloth);
    flag.position.set(3.5, 0, -1.5);
    village.add(flag);

    village.position.y = 0;
    group.add(village);

    // The agent. A rigged character when the model loaded, the old jointed
    // boxes if it did not, so the World still works without the asset.
    const figure = new THREE.Group();
    const character = makeAgent(null);
    const limb = {};
    if (character) {
      character.root.scale.setScalar(1.5);
      figure.add(character.root);
    } else {
      for (const n of ["torso", "head", "armL", "armR", "legL", "legR"]) {
        const m = instance(workerParts, n);
        if (m) { figure.add(m); limb[n] = m; }
      }
    }
    const bodyMat = limb.torso ? limb.torso.material : new THREE.MeshStandardMaterial();
    figure.position.set(1.5, 0, 6);

    // Somewhere to work, somewhere to rest, and room to move between them.
    const anchors = {
      work:  new THREE.Vector2(-2.5, 1.5),
      sleep: new THREE.Vector2(-3.0, 5.0),
    };
    const walker = {
      pos: new THREE.Vector2(0.4, 0.7),
      target: new THREE.Vector2(0.4, 0.7),
      facing: 0,
      mode: "wander",
      wait: 0,
      step: 0,
      settleFacing: null,
    };

    const figureBase = new THREE.Group();
    figureBase.position.y = 0;
    figureBase.add(figure);
    group.add(figureBase);

    // A name plate, so a plot is a place rather than a shape. Drawn to a canvas
    // and used as a sprite: three.js has no text of its own, and pulling in a
    // font loader to write six short words would not be worth its weight.
    const label = makeLabel(project);
    label.position.y = 15;
    group.add(label);
    const labelRef = label;

    // What it is doing, right now, over its head. Rebuilt only when the tool
    // changes: a new canvas texture every frame would be absurd.
    const tool = makeLabel(" ", 22);
    tool.position.y = 12.5;
    tool.visible = false;
    group.add(tool);

    group.userData = { project };
    scene.add(group);
    return { group, figure, base: figureBase, limb, character, flag, label: labelRef,
             props,
             rimColour: themeFor(meta.get(project)).roof,
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
      // The spiral spaces villages evenly; the jitter stops them looking placed.
      let a = n * 2.399963 + hash(k) * 1.5;
      let r = (34 * Math.sqrt(n) + 16) * (0.78 + hash(k + "r") * 0.5);
      // Nudge round the spiral until the site is dry land.
      for (let t = 0; t < 24; t++) {
        const x = Math.cos(a) * r, z = Math.sin(a) * r;
        if (terrainY(x, z) > WATER_LEVEL + 2.5) break;
        a += 0.32;
      }
      p.target = new THREE.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r);
      // Drop every building onto the ground beneath it.
      const baseY = terrainY(p.target.x, p.target.z);
      for (const pr of p.props || []) {
        pr.obj.position.y = terrainY(p.target.x + pr.x, p.target.z + pr.z) - baseY;
      }
    });
    // Frame the ring as it grows, but stop the moment somebody takes the
    // camera themselves: nothing is more irritating than a view that argues.
    const spread = keys.length ? 34 * Math.sqrt(keys.length) + 24 : 40;
    if (!orbit.userMoved && !roam.on) {
      orbit.dist = 60 + spread * 1.35;
      orbit.pol = 1.32;
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

    if (changed || !scatterGroups.length) {
      scatterNature([...plots.values()].map(
        (p) => p.target || new THREE.Vector3()));
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
    pos: new THREE.Vector3(0, 0, 40),
    vel: new THREE.Vector3(),
    yaw: Math.PI,
    pitch: 0.32,
    step: 0,
    keys: new Set(),
    avatar: null,
    limb: {},
    character: null,
  };

  const WALK = 7.5, RUN = 15.0, EYE = 3.4, TRAIL = 10.5;

  function buildAvatar() {
    const g = new THREE.Group();
    const ch = makeAgent(0x00e0b7);        // you are the teal one
    if (ch) {
      ch.root.scale.setScalar(0.85);
      g.add(ch.root);
      roam.character = ch;
      roam.limb = {};
    } else {
      const limb = {};
      for (const n of ["torso", "head", "armL", "armR", "legL", "legR"]) {
        const m = instance(workerParts, n,
                           n === "torso" ? { colour: new THREE.Color(0x00e0b7) } : {});
        if (m) { g.add(m); limb[n] = m; }
      }
      g.scale.setScalar(1.15);
      roam.limb = limb;
    }
    scene.add(g);
    return g;
  }

  /** Walk out to the nearest dry ground. The spawn point is fixed, and the
   *  lakes are wherever the noise put them, so sooner or later it is in one. */
  function findDryGround(from) {
    if (terrainY(from.x, from.z) > WATER_LEVEL + 1.5) return from;
    for (let ring = 8; ring < 320; ring += 8) {
      for (let i = 0; i < 16; i++) {
        const a = (i / 16) * Math.PI * 2;
        const x = from.x + Math.cos(a) * ring;
        const z = from.z + Math.sin(a) * ring;
        if (terrainY(x, z) > WATER_LEVEL + 1.5) return new THREE.Vector3(x, 0, z);
      }
    }
    return from;
  }

  function setRoam(on) {
    roam.on = on;
    if (on) {
      // Start at the edge of a village. Villages keep a clearing around them,
      // so you begin somewhere open and looking at something, rather than
      // buried in a spruce or standing in a lake.
      const first = [...plots.values()][0];
      let start = roam.pos.clone();
      if (first && first.target) {
        start = new THREE.Vector3(first.target.x + 16, 0, first.target.z + 16);
        roam.yaw = Math.atan2(first.target.x - start.x, first.target.z - start.z);
      }
      const dry = findDryGround(start);
      roam.pos.set(dry.x, 0, dry.z);
      roam.vel.set(0, 0, 0);
    }
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

  // Pointer lock is a convenience, not a requirement. Browsers refuse it for
  // all sorts of reasons, and tying roaming to it meant a denied request threw
  // you straight back out with no way to walk at all. Locked, the mouse looks
  // freely; unlocked, you hold a button and drag to look. Either way WASD works.
  function onRoamMouse(e) {
    if (!roam.on) return;
    const locked = document.pointerLockElement === canvas;
    if (!locked && !(e.buttons & 1)) return;
    roam.yaw -= (e.movementX || 0) * 0.0022;
    roam.pitch = Math.max(-0.25, Math.min(0.95, roam.pitch + (e.movementY || 0) * 0.0018));
    needsFrame = true;
    kick();
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
      if (len < 7.0 && len > 0.0001) {
        d.multiplyScalar((7.0 - len) / len);
        roam.pos.x += d.x;
        roam.pos.z += d.y;
      }
    }
    // The shore stops you. Wading is one thing; walking along a lake bed with
    // the camera underwater is just confusing.
    if (terrainY(roam.pos.x, roam.pos.z) < WATER_LEVEL + 0.8) {
      const dry = findDryGround(roam.pos);
      roam.pos.x += (dry.x - roam.pos.x) * Math.min(1, dt * 6);
      roam.pos.z += (dry.z - roam.pos.z) * Math.min(1, dt * 6);
      roam.vel.multiplyScalar(0.5);
    }

    const bound = 430;
    roam.pos.x = Math.max(-bound, Math.min(bound, roam.pos.x));
    roam.pos.z = Math.max(-bound, Math.min(bound, roam.pos.z));

    const a = roam.avatar;
    if (a) {
      a.position.set(roam.pos.x, terrainY(roam.pos.x, roam.pos.z), roam.pos.z);
      const moving2 = roam.vel.lengthSq() > 0.5;
      if (roam.character) {
        // Walk, run or stand, chosen from how fast you are actually going.
        // No early return here: the camera is positioned at the end of this
        // function, and returning skipped it, which left the view stuck on the
        // overview while the avatar walked away underneath it.
        const sp = roam.vel.length();
        playClip(roam.character, sp > 10 ? "Run" : sp > 0.7 ? "Walk" : "Idle", 0.18);
        roam.character.mixer.update(dt);
        if (moving2) a.rotation.y = Math.atan2(roam.vel.x, roam.vel.z);
      } else if (moving2) {
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
    const gy = terrainY(roam.pos.x, roam.pos.z);
    const bx = roam.pos.x + back.x, bz = roam.pos.z + back.z;
    camera.position.set(
      bx,
      Math.max(terrainY(bx, bz) + 1.6, gy + EYE + roam.pitch * 7.0),
      bz,
    );
    camera.lookAt(roam.pos.x + fwd.x * 6, gy + 1.6 - roam.pitch * 2.6, roam.pos.z + fwd.z * 6);
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
  const orbit = { az: 0.86, pol: 1.32, dist: 70, userMoved: false };

  function applyCamera() {
    const d = orbit.dist;
    camera.position.set(
      Math.sin(orbit.az) * Math.sin(orbit.pol) * d,
      Math.cos(orbit.pol) * d,
      Math.cos(orbit.az) * Math.sin(orbit.pol) * d,
    );
    camera.lookAt(0, 4, 0);
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
    orbit.pol = Math.max(0.22, Math.min(1.45, orbit.pol - (e.clientY - dragging.y) * 0.006));
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
      // Deselecting restores the project's own colour, not a generic edge.
      p.rim.material.color.setHex(name === selected ? COL.active : p.rimColour);
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
      // Villages fade up where they stand rather than rising out of the floor.
      if (p.rise < 1) {
        p.rise = Math.min(1, p.rise + dt * 1.6);
        moving = true;
      }
      const ease = 1 - Math.pow(1 - p.rise, 3);
      if (p.target) {
        p.group.position.set(p.target.x, terrainY(p.target.x, p.target.z), p.target.z);
      }
      p.group.scale.setScalar(0.55 + 0.45 * ease);

      const info = p.info || {};
      const working = info.working;

      // Where should this agent be? Working at the workshop, resting under the
      // tree, or wandering its plot between the two.
      const w = p.walker;
      // An idle agent stands about rather than falling over: it waits, strolls
      // a short way, and waits again. Lying down read as "collapsed" more than
      // "resting", and an agent that has merely gone quiet has not died.
      const wantMode = working ? "work" : "wander";
      if (w.mode !== wantMode) {
        w.mode = wantMode;
        w.wait = 0;
        if (wantMode === "work") w.target.copy(p.anchors.work);
        w.settleFacing = null;
      }
      // The longer it has been quiet, the less it moves.
      const restless = info.present ? 1 : 0.35;

      const toTarget = w.target.clone().sub(w.pos);
      const dist = toTarget.length();
      const walking = dist > 0.25;
      if (walking) {
        const speed = (w.mode === "work" ? 5.0 : 2.6) * dt;
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
          const r = (2 + Math.random() * 6) * restless;
          w.target.set(Math.cos(a) * r, Math.sin(a) * r);
          // Quiet agents pause for much longer between strolls.
          w.wait = (2 + Math.random() * 4) / restless;
        }
        moving = true;      // it is about to set off again
      }
      const vy = p.target
        ? terrainY(p.target.x + w.pos.x, p.target.z + w.pos.y) - terrainY(p.target.x, p.target.z)
        : 0;
      p.figure.position.set(w.pos.x, vy, w.pos.y);
      p.figure.rotation.y = w.facing;

      p.body.color.setHex(
        working ? COL.body : (info.present ? COL.bodyIdle : COL.bodyStale));
      p.pad.material.color.setHex(info.present ? COL.plot : COL.plotStale);
      // With a rigged character the pose comes from a clip; the hand-posed
      // limbs below are only for the fallback figure.
      if (p.character) {
        if (working) {
          if (p.toolText !== info.tool) {
            p.toolText = info.tool;
            p.group.remove(p.tool);
            p.tool = makeLabel(toolLabel(info.tool) || " ", 22);
            p.tool.position.y = 12.5;
            p.group.add(p.tool);
          }
          p.tool.visible = true;
        } else {
          p.tool.visible = false;
        }
        const clip =
          walking ? CLIP_FOR.walk
          : working ? (CLIP_FOR[info.tool] || "Pickup")
          : CLIP_FOR.idle;
        playClip(p.character, clip);
        p.character.mixer.update(dt);
        // An Idle clip loops for ever, so it must not be a reason to keep the
        // render loop open: the same rule as the sleepers' breathing.
        if (walking || working || anyAwake) moving = true;
        p.base.position.y = 0;
        p.figure.rotation.x = 0;
      } else if (working && !walking) {
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

        if (p.character) {
          /* the clip above is the whole pose */
        } else if (walking) {
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
        const k = d * 0.02;
        sp.scale.set((sp.userData.aspect || 3) * k, k, 1);
      }
    }

    if (roam.on) { stepRoam(dt); moving = true; }

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
