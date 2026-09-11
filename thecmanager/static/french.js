/**
 * The French World: Èze village, with your agents as Pokémon.
 *
 * A second world beside the valley, and a different idea of one. The valley is
 * a map you look down on and occasionally walk; this is a place you are in.
 * Ash is you. Each running Claude session is a Pokémon standing somewhere in
 * the village, and walking up to one and pressing E opens its session.
 *
 * **The models are not ours and are not shipped.** Èze is somebody's scan and
 * the characters are Nintendo's, so they live in a gitignored folder, GSO-1
 * serves them off disk at /worlds, and a machine without them is simply never
 * offered this world. Nothing here goes into the repository or the installer.
 *
 * Everything arrives at a different scale: the village is 452,000 units across,
 * Ash is 3.2 tall and Bulbasaur is 0.01. Each is normalised to a real height on
 * load, which is why the table below is in metres and not in multipliers.
 */

import * as THREE from "./vendor/three/three.module.js";
import { GLTFLoader } from "./vendor/three/addons/GLTFLoader.js";
import { clone as cloneSkinned } from "./vendor/three/addons/SkeletonUtils.js";

const ASSETS = "/worlds/";

/** Real-world heights, in metres. */
const SIZES = {
  ash: 1.75,
  pikachu: 1.1,
  bulbasaur: 1.0,
  charizard: 2.6,
  celebi: 1.2,
};

/**
 * The scans this module can host.
 *
 * Nothing below is specific to Èze: find the ground by raycast, reject faces
 * too steep to stand on, put the characters on the flattest high ground. What
 * differs between a hill village and a valley floor is the scale it wants and
 * how far apart things should stand, so that is all a preset carries.
 *
 * `span` is how wide the scan should be in metres. `pitch` is the radius the
 * agents are spread over, `band` how far below the high point still counts as
 * the same place rather than a cliff below it.
 */
export const SCANS = {
  french: {
    name: "French World",
    files: ["eze_village_-_france-2.glb", "eze_village_-_france.glb"],
    span: 240, pitch: 8, pitchStep: 5, band: 26, fog: [120, 420],
    anchor: "high",
  },
  nature: {
    name: "Nature",
    // A natural landscape has no roofs and no walls: every surface is ground,
    // which is the case this walks best on.
    files: ["photogrammetry__3d_scan_nature_landscape.glb"],
    span: 320, pitch: 14, pitchStep: 9, band: 45, fog: [160, 620],
    anchor: "middle",
    // This scan keeps its terrain and its planting apart: Box001 is the
    // hillside, twenty-three thousand triangles of it, and Box002 is the
    // million and a half of grass, bushes and trees standing on it. The
    // hillside alone is not where you walk — the meadow is knee-deep geometry
    // and standing on the soil under it buries you — so the grass is allowed to
    // raise the ground by up to a stride, and the hedges, which are far taller
    // than that, are not.
    ground: "box001", undergrowth: 1.8,
  },
};

const POKEMON = [
  { id: "pikachu",   file: "pikachu_navidad_-_pokemon.glb" },
  { id: "bulbasaur", file: "bulbasaur_pokemon_animated.glb" },
  { id: "charizard", file: "charizard_-_pokemon.glb" },
  { id: "celebi",    file: "pokemon_3ds_celebi.glb" },
];

const WALK = 4.5, RUN = 9.0, TRAIL = 5.2, EYE = 1.9;
/** How close Ash must be before a Pokémon offers to talk. */
const REACH = 7.0;

function clipsOf(gltf) {
  const m = new Map();
  for (const c of gltf.animations || []) m.set((c.name || "").split("|").pop(), c);
  return m;
}

/** Pick the first clip whose name contains any of `wants`, else the first. */
function pickClip(clips, wants) {
  for (const w of wants) {
    for (const [name, clip] of clips) {
      if (name.toLowerCase().includes(w)) return clip;
    }
  }
  return clips.values().next().value || null;
}

export async function createScanWorld(canvas, scanId = "french") {
  const cfg = SCANS[scanId] || SCANS.french;
  const loader = new GLTFLoader();
  const load = (file) => loader.loadAsync(ASSETS + encodeURIComponent(file));

  // A preset may list several files, best first: the two Èze scans carry
  // identical geometry and differ only in texture size, so the sharp one is
  // preferred and the small one is the fallback.
  let villageFile = cfg.files[cfg.files.length - 1];
  for (const f of cfg.files) {
    const ok = await fetch(ASSETS + encodeURIComponent(f), { method: "HEAD" })
      .then((r) => r.ok).catch(() => false);
    if (ok) { villageFile = f; break; }
  }

  const [villageGltf, ashGltf, ...pokeGltf] = await Promise.all([
    load(villageFile),
    load("ash_ketchup_-_pokemon.glb"),
    ...POKEMON.map((p) => load(p.file).catch(() => null)),
  ]);

  const renderer = new THREE.WebGLRenderer({
    canvas, antialias: true, preserveDrawingBuffer: true,
  });
  renderer.setClearColor(0xbcd8ef, 1);
  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0xbcd8ef, cfg.fog[0], cfg.fog[1]);

  const camera = new THREE.PerspectiveCamera(52, 1, 0.1, 2000);

  scene.add(new THREE.HemisphereLight(0xdcefff, 0x6a6152, 2.0));
  const sun = new THREE.DirectionalLight(0xfff3dd, 2.4);
  sun.position.set(70, 120, 50);
  scene.add(sun);

  const sky = new THREE.Mesh(
    new THREE.SphereGeometry(900, 24, 12),
    new THREE.MeshBasicMaterial({ color: 0xa9d2f0, side: THREE.BackSide, depthWrite: false }),
  );
  scene.add(sky);

  // ---- the village ----------------------------------------------------------
  const village = villageGltf.scene;
  {
    // Normalise by footprint. The scan carries stray geometry far below the
    // streets, so the vertical extent is not a useful guide to its size.
    const box = new THREE.Box3().setFromObject(village);
    const size = box.getSize(new THREE.Vector3());
    const span = Math.max(size.x, size.z) || 1;
    const k = cfg.span / span;
    village.scale.setScalar(k);
    village.updateMatrixWorld(true);
    const box2 = new THREE.Box3().setFromObject(village);
    const mid = box2.getCenter(new THREE.Vector3());
    village.position.sub(new THREE.Vector3(mid.x, 0, mid.z));
  }
  scene.add(village);

  // The scan ships with its own sky sphere, which is a perfectly good sky and
  // stays in the scene, but it is not ground and is kept out of the survey. It
  // was also the whole of the model's apparent height: the plus or minus 85 was
  // the skybox radius, not the hill.
  /** A mesh's own name and those of everything it hangs from, lowercased. */
  function nameChain(o) {
    let s = "";
    for (let n = o; n; n = n.parent) s += (n.name || "").toLowerCase() + "/";
    return s;
  }

  const groundMeshes = [], planting = [];
  village.traverse((o) => {
    if (!o.isMesh) return;
    const chain = nameChain(o);
    if (/sky|cloud|backdrop/.test(chain)) return;
    if (cfg.ground && !chain.includes(cfg.ground)) planting.push(o);
    else groundMeshes.push(o);
  });

  const bounds = new THREE.Box3();
  for (const m of groundMeshes) bounds.expandByObject(m);

  /**
   * Where the ground is, as a grid of heights.
   *
   * A photogrammetry scan has no height function to ask, and raycasting one is
   * not an option either: the nature landscape is a million and a half
   * triangles with no spatial index, so a single ray costs tens of
   * milliseconds, the survey that looks for somewhere to stand took longer than
   * the download, and walking would have cost a ray a frame. Visiting every
   * triangle once and writing it into a grid turns the question into a lookup.
   *
   * Which triangles count is the other half of the problem. The scan has no
   * idea which of its faces is a street and which is a roof, so the only thing
   * to go on is which way the surface faces: streets, squares and terraces are
   * close to level, roofs are pitched and cliffs are vertical.
   *
   * Where two standable surfaces sit over each other the higher one wins,
   * which is the answer a ray dropped from the sky gave. A scan that names its
   * terrain separately says so in its preset, and then only that object is
   * surveyed — otherwise the tops of the bushes are standable too, and Ash
   * steps up onto the shrubbery and walks along it.
   */
  const MAX_WALK_SLOPE = 0.62;        // cos of about 52 degrees
  const CELLS = 640;
  const GX0 = bounds.min.x, GZ0 = bounds.min.z;
  const CELL = Math.max(bounds.max.x - GX0, bounds.max.z - GZ0) / CELLS || 1;

  /**
   * Write every standable face of `meshes` into `into`, keeping the highest.
   *
   * With `base` given, a face may only raise a cell to `lift` metres above the
   * height already surveyed there, which is how the grass is told from the
   * hedge standing in it.
   */
  function survey(meshes, into, base = null, lift = 0) {
    const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
    const ab = new THREE.Vector3(), ac = new THREE.Vector3(), n = new THREE.Vector3();
    const put = (cx, cz, y) => {
      if (cx < 0 || cz < 0 || cx >= CELLS || cz >= CELLS) return;
      const k = cz * CELLS + cx;
      if (base) {
        const floor = base[k];
        if (!(floor === floor) || y > floor + lift) return;   // NaN, or overhead
      }
      if (!(into[k] >= y)) into[k] = y;           // NaN-safe: an empty cell takes it
    };
    for (const mesh of meshes) {
      const geo = mesh.geometry;
      const pos = geo && geo.getAttribute("position");
      if (!pos) continue;
      const idx = geo.getIndex();
      const tris = Math.floor((idx ? idx.count : pos.count) / 3);
      const mat = mesh.matrixWorld;
      for (let t = 0; t < tris; t++) {
        const i = t * 3;
        a.fromBufferAttribute(pos, idx ? idx.getX(i) : i).applyMatrix4(mat);
        b.fromBufferAttribute(pos, idx ? idx.getX(i + 1) : i + 1).applyMatrix4(mat);
        c.fromBufferAttribute(pos, idx ? idx.getX(i + 2) : i + 2).applyMatrix4(mat);
        ab.subVectors(b, a); ac.subVectors(c, a);
        n.crossVectors(ab, ac);
        const len = n.length();
        if (!len || Math.abs(n.y) / len < MAX_WALK_SLOPE) continue;
        const det = ab.x * ac.z - ac.x * ab.z;
        if (!det) continue;
        const cx0 = Math.floor((Math.min(a.x, b.x, c.x) - GX0) / CELL);
        const cx1 = Math.floor((Math.max(a.x, b.x, c.x) - GX0) / CELL);
        const cz0 = Math.floor((Math.min(a.z, b.z, c.z) - GZ0) / CELL);
        const cz1 = Math.floor((Math.max(a.z, b.z, c.z) - GZ0) / CELL);
        let wrote = false;
        for (let cz = cz0; cz <= cz1; cz++) {
          const pz = GZ0 + (cz + 0.5) * CELL;
          for (let cx = cx0; cx <= cx1; cx++) {
            const px = GX0 + (cx + 0.5) * CELL;
            // Barycentric in plan view: where in the triangle this cell centre
            // falls, and therefore how high the surface is over it.
            const qx = px - a.x, qz = pz - a.z;
            const u = (qx * ac.z - ac.x * qz) / det;
            const v = (ab.x * qz - qx * ab.z) / det;
            if (u < 0 || v < 0 || u + v > 1) continue;
            put(cx, cz, a.y + ab.y * u + ac.y * v);
            wrote = true;
          }
        }
        if (!wrote) {
          // A triangle smaller than a cell covers no cell centre. Keeping it at
          // its middle stops the finer parts of a scan coming out as holes.
          put(Math.floor((((a.x + b.x + c.x) / 3) - GX0) / CELL),
              Math.floor((((a.z + b.z + c.z) / 3) - GZ0) / CELL),
              (a.y + b.y + c.y) / 3);
        }
      }
    }
  }

  const heights = new Float32Array(CELLS * CELLS).fill(NaN);
  survey(groundMeshes, heights);
  if (cfg.undergrowth && planting.length) {
    survey(planting, heights, Float32Array.from(heights), cfg.undergrowth);
  }

  function cellAt(cx, cz) {
    if (cx < 0 || cz < 0 || cx >= CELLS || cz >= CELLS) return NaN;
    return heights[cz * CELLS + cx];
  }

  /** The height of the ground you can stand on here, or null for nowhere. */
  function groundAt(x, z) {
    const fx = (x - GX0) / CELL - 0.5, fz = (z - GZ0) / CELL - 0.5;
    const x0 = Math.floor(fx), z0 = Math.floor(fz);
    const tx = fx - x0, tz = fz - z0;
    const h00 = cellAt(x0, z0), h10 = cellAt(x0 + 1, z0);
    const h01 = cellAt(x0, z0 + 1), h11 = cellAt(x0 + 1, z0 + 1);
    if (h00 === h00 && h10 === h10 && h01 === h01 && h11 === h11) {
      // Four neighbours: interpolate, so a slope is walked and not climbed in
      // half-metre steps.
      return (h00 * (1 - tx) + h10 * tx) * (1 - tz)
           + (h01 * (1 - tx) + h11 * tx) * tz;
    }
    // At an edge — a doorway, the lip of a cliff — take the cell underfoot.
    const y = cellAt(Math.round(fx), Math.round(fz));
    return y === y ? y : null;
  }

  /**
   * Find the part of the scan worth living on.
   *
   * Where that is depends on the scan. Èze is a village on a crag in the middle
   * of a square of French coastline, so the centre of the bounding box is a
   * road at the foot of the hill, eighty metres below the streets, and the
   * highest walkable ground is the town. A natural landscape has no town: its
   * highest ground is a peak, which is steep, small and a poor place to stand a
   * village of agents. There the middle of the country is what is wanted, so
   * the anchor is the walkable point nearest the centre at the typical height.
   */
  let TOWN = new THREE.Vector3(0, 0, 0);
  {
    const pts = [];
    const step = Math.max(1, Math.floor(CELLS / 160));
    for (let cz = 0; cz < CELLS; cz += step) {
      for (let cx = 0; cx < CELLS; cx += step) {
        const y = heights[cz * CELLS + cx];
        if (y === y) {
          pts.push(new THREE.Vector3(GX0 + (cx + 0.5) * CELL, y, GZ0 + (cz + 0.5) * CELL));
        }
      }
    }
    if (!pts.length) {
      TOWN.set(0, 0, 0);
    } else if (cfg.anchor === "middle") {
      const ys = pts.map((q) => q.y).sort((p, q) => p - q);
      const mid = ys[Math.floor(ys.length / 2)];
      let best = null, bestD = Infinity;
      for (const q of pts) {
        if (Math.abs(q.y - mid) > cfg.band / 2) continue;
        const d = q.x * q.x + q.z * q.z;
        if (d < bestD) { bestD = d; best = q; }
      }
      TOWN.copy(best || pts[Math.floor(pts.length / 2)]);
    } else {
      let best = pts[0];
      for (const q of pts) if (q.y > best.y) best = q;
      TOWN.copy(best);
    }
  }
  const TOWN_Y = TOWN.y;
  // Deep enough to take in the terraces below the summit, shallow enough to
  // keep Ash off the cliff.
  const TOWN_BAND = cfg.band;

  /** Somewhere on the village itself, not down the rock face. */
  function standable(x, z) {
    const y = groundAt(x, z);
    if (y !== null && Math.abs(y - TOWN_Y) < TOWN_BAND) return new THREE.Vector3(x, y, z);
    for (let r = 4; r < 90; r += 4) {
      for (let i = 0; i < 16; i++) {
        const a = (i / 16) * Math.PI * 2;
        const px = x + Math.cos(a) * r, pz = z + Math.sin(a) * r;
        const py = groundAt(px, pz);
        if (py !== null && Math.abs(py - TOWN_Y) < TOWN_BAND) {
          return new THREE.Vector3(px, py, pz);
        }
      }
    }
    return new THREE.Vector3(x, TOWN_Y, z);
  }

  // ---- characters -----------------------------------------------------------
  /**
   * The scale that makes a model `targetHeight` tall.
   *
   * Measured on the original scene, never on the clone: Box3.setFromObject on a
   * cloned SkinnedMesh reports the raw geometry bounds rather than the posed
   * ones, and for these models that is a few thousandths of a unit. Fitting the
   * clone made Ash two hundred and ninety times too big, which put the camera
   * inside his head and looked, from outside, like the village had no one in it.
   */
  function scaleFor(gltf, targetHeight) {
    const box = new THREE.Box3().setFromObject(gltf.scene);
    const h = box.getSize(new THREE.Vector3()).y || 1;
    return targetHeight / h;
  }

  function makeActor(gltf, targetHeight, wants) {
    const k = scaleFor(gltf, targetHeight);
    const root = cloneSkinned(gltf.scene);
    root.scale.setScalar(k);
    root.traverse((o) => { if (o.isMesh) o.frustumCulled = false; });
    const mixer = new THREE.AnimationMixer(root);
    const clips = clipsOf(gltf);
    const actions = new Map();
    for (const [name, clip] of clips) actions.set(name, mixer.clipAction(clip));
    const idle = pickClip(clips, wants.idle);
    const move = pickClip(clips, wants.move);
    return {
      root, mixer, actions, current: null,
      idleName: idle && (idle.name || "").split("|").pop(),
      moveName: move && (move.name || "").split("|").pop(),
    };
  }

  function play(actor, name, fade = 0.2) {
    if (!actor || !name || actor.current === name) return;
    const next = actor.actions.get(name);
    if (!next) return;
    const prev = actor.current && actor.actions.get(actor.current);
    next.reset().setEffectiveWeight(1).fadeIn(fade).play();
    if (prev) prev.fadeOut(fade);
    actor.current = name;
  }

  const ash = makeActor(ashGltf, SIZES.ash,
                        { idle: ["house", "idle", "wait"], move: ["walk"] });
  scene.add(ash.root);

  const pokeTemplates = [];
  POKEMON.forEach((p, i) => {
    const g = pokeGltf[i];
    if (g) pokeTemplates.push({ id: p.id, gltf: g });
  });

  // ---- the agents standing in the village ----------------------------------
  const spots = [];
  {
    // Ring of pitches around the village centre, each dropped onto the ground.
    const n = 12;
    for (let i = 0; i < n; i++) {
      const a = (i / n) * Math.PI * 2;
      const r = cfg.pitch + (i % 3) * cfg.pitchStep;
      spots.push(standable(TOWN.x + Math.cos(a) * r, TOWN.z + Math.sin(a) * r));
    }
  }

  const agents = new Map();     // project -> { actor, kind, info, pos }

  function update(snapshot) {
    const wanted = new Map();
    for (const s of snapshot.sessions || []) {
      if (!s.project) continue;
      const a = s.activity || {};
      wanted.set(s.project, {
        name: s.name,
        tool: (a.tools && a.tools[0] && a.tools[0].tool) || null,
        idle: a.idle_seconds ?? null,
        working: a.idle_seconds !== null && a.idle_seconds !== undefined && a.idle_seconds <= 30,
      });
    }

    for (const project of [...agents.keys()]) {
      if (wanted.has(project)) continue;
      scene.remove(agents.get(project).actor.root);
      agents.delete(project);
    }

    let i = 0;
    for (const [project, info] of wanted) {
      let a = agents.get(project);
      if (!a && pokeTemplates.length) {
        // One species per agent while there are enough; after that they repeat,
        // which is the honest thing to do until more are downloaded.
        const t = pokeTemplates[agents.size % pokeTemplates.length];
        const actor = makeActor(t.gltf, SIZES[t.id] || 1.2,
                                { idle: ["idle", "wait", "default"], move: ["walk", "run"] });
        const at = spots[agents.size % spots.length];
        actor.root.position.copy(at);
        scene.add(actor.root);
        a = { actor, kind: t.id, pos: at.clone() };
        agents.set(project, a);
      }
      if (a) a.info = info;
      i++;
    }
    return agents.size;
  }

  // ---- Ash, and the keys that move him -------------------------------------
  const player = {
    pos: standable(TOWN.x + 4, TOWN.z + 4),
    vel: new THREE.Vector3(),
    yaw: Math.PI, pitch: 0.18,
    keys: new Set(),
  };
  ash.root.position.copy(player.pos);

  const KEYS = { KeyW: "f", KeyS: "b", KeyA: "l", KeyD: "r",
                 ArrowUp: "f", ArrowDown: "b", ArrowLeft: "l", ArrowRight: "r",
                 ShiftLeft: "run", ShiftRight: "run" };

  let onInteract = () => {};
  let active = false;
  let nearest = null;

  function keyDown(e) {
    if (!active) return;
    if (e.code === "KeyE" && nearest) { e.preventDefault(); onInteract(nearest); return; }
    const k = KEYS[e.code];
    if (!k) return;
    e.preventDefault();
    player.keys.add(k);
  }
  function keyUp(e) {
    const k = KEYS[e.code];
    if (k) player.keys.delete(k);
  }
  window.addEventListener("keydown", keyDown);
  window.addEventListener("keyup", keyUp);

  function onMouse(e) {
    if (!active) return;
    const locked = document.pointerLockElement === canvas;
    if (!locked && !(e.buttons & 1)) return;
    player.yaw -= (e.movementX || 0) * 0.0024;
    player.pitch = Math.max(-0.3, Math.min(0.8, player.pitch + (e.movementY || 0) * 0.0018));
  }
  document.addEventListener("mousemove", onMouse);

  const clock = new THREE.Clock();

  function frame() {
    const dt = Math.min(clock.getDelta(), 0.1);

    const k = player.keys;
    const speed = k.has("run") ? RUN : WALK;
    const fwd = new THREE.Vector3(Math.sin(player.yaw), 0, Math.cos(player.yaw));
    const right = new THREE.Vector3(fwd.z, 0, -fwd.x);
    const want = new THREE.Vector3();
    if (k.has("f")) want.add(fwd);
    if (k.has("b")) want.sub(fwd);
    if (k.has("r")) want.add(right);
    if (k.has("l")) want.sub(right);
    const walking = want.lengthSq() > 0;
    if (walking) want.normalize().multiplyScalar(speed);
    player.vel.lerp(want, Math.min(1, dt * 10));

    const next = player.pos.clone().addScaledVector(player.vel, dt);
    // Stay on the village: a step with no ground under it is not taken.
    const gy = groundAt(next.x, next.z);
    // A generous step, because the streets here are stairs cut into a hill,
    // but not so generous that he walks off the edge of the cliff.
    if (gy !== null && Math.abs(gy - player.pos.y) < 6
        && Math.abs(gy - TOWN_Y) < TOWN_BAND + 15) {
      player.pos.set(next.x, gy, next.z);
    } else {
      player.vel.multiplyScalar(0.2);
    }

    ash.root.position.copy(player.pos);
    if (player.vel.lengthSq() > 0.4) {
      ash.root.rotation.y = Math.atan2(player.vel.x, player.vel.z);
      play(ash, ash.moveName);
    } else {
      play(ash, ash.idleName);
    }
    ash.mixer.update(dt);

    // Who is in reach, and what is everyone doing?
    nearest = null;
    let bestD = REACH;
    for (const [project, a] of agents) {
      const d = a.actor.root.position.distanceTo(player.pos);
      if (d < bestD) { bestD = d; nearest = { project, kind: a.kind, info: a.info, distance: d }; }
      // Face Ash when he is close, so it reads as noticing him.
      if (d < REACH * 2.2) {
        const want2 = Math.atan2(player.pos.x - a.actor.root.position.x,
                                 player.pos.z - a.actor.root.position.z);
        let diff = ((want2 - a.actor.root.rotation.y + Math.PI) % (Math.PI * 2)) - Math.PI;
        a.actor.root.rotation.y += diff * Math.min(1, dt * 3);
      }
      play(a.actor, a.info && a.info.working && a.actor.moveName
                    ? a.actor.moveName : a.actor.idleName);
      a.actor.mixer.update(dt);
    }

    const back = fwd.clone().multiplyScalar(-TRAIL);
    camera.position.set(player.pos.x + back.x,
                        player.pos.y + EYE + player.pitch * 4.0,
                        player.pos.z + back.z);
    camera.lookAt(player.pos.x + fwd.x * 4, player.pos.y + 1.2 - player.pitch * 1.8,
                  player.pos.z + fwd.z * 4);

    renderer.render(scene, camera);
    return true;
  }

  function resize(w, h) {
    if (!w || !h) return;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }

  function dispose() {
    window.removeEventListener("keydown", keyDown);
    window.removeEventListener("keyup", keyUp);
    document.removeEventListener("mousemove", onMouse);
    renderer.dispose();
  }

  return {
    update, frame, resize, dispose,
    setActive(on) { active = on; if (!on) player.keys.clear(); },
    onInteract(fn) { onInteract = fn; },
    get nearest() { return nearest; },
    get count() { return agents.size; },
    // Exposed for diagnosis: where everyone actually ended up.
    get debug() {
      return {
        town: [+TOWN.x.toFixed(1), +TOWN.y.toFixed(1), +TOWN.z.toFixed(1)],
        ash: [+player.pos.x.toFixed(1), +player.pos.y.toFixed(1), +player.pos.z.toFixed(1)],
        ashScale: +ash.root.scale.x.toFixed(4),
        camera: [+camera.position.x.toFixed(1), +camera.position.y.toFixed(1),
                 +camera.position.z.toFixed(1)],
        agents: [...agents.entries()].map(([k, a]) => ({
          project: k, kind: a.kind,
          at: [+a.actor.root.position.x.toFixed(1), +a.actor.root.position.y.toFixed(1),
               +a.actor.root.position.z.toFixed(1)],
          d: +a.actor.root.position.distanceTo(player.pos).toFixed(1),
        })),
      };
    },
  };
}

/** The original entry point, kept so existing callers do not break. */
export async function createFrenchWorld(canvas) {
  return createScanWorld(canvas, "french");
}
