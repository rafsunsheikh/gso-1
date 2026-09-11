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
  village: 240,        // across, not tall
  ash: 1.75,
  pikachu: 1.1,
  bulbasaur: 1.0,
  charizard: 2.6,
  celebi: 1.2,
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

export async function createFrenchWorld(canvas) {
  const loader = new GLTFLoader();
  const load = (file) => loader.loadAsync(ASSETS + encodeURIComponent(file));

  const [villageGltf, ashGltf, ...pokeGltf] = await Promise.all([
    load("eze_village_-_france.glb"),
    load("ash_ketchup_-_pokemon.glb"),
    ...POKEMON.map((p) => load(p.file).catch(() => null)),
  ]);

  const renderer = new THREE.WebGLRenderer({
    canvas, antialias: true, preserveDrawingBuffer: true,
  });
  renderer.setClearColor(0xbcd8ef, 1);
  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0xbcd8ef, 120, 420);

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
    const k = SIZES.village / span;
    village.scale.setScalar(k);
    village.updateMatrixWorld(true);
    const box2 = new THREE.Box3().setFromObject(village);
    const mid = box2.getCenter(new THREE.Vector3());
    village.position.sub(new THREE.Vector3(mid.x, 0, mid.z));
  }
  scene.add(village);

  // Ground is found by raycast rather than a formula: this is somebody's scan
  // of a real hillside, and it has no height function to ask.
  const down = new THREE.Raycaster();
  down.far = 4000;
  const DOWN = new THREE.Vector3(0, -1, 0);
  // The scan ships with its own sky sphere, and a downward ray from above the
  // model hits that long before it reaches a street. It was also the whole of
  // the model's apparent height: the ±85 was the skybox radius, not the hill.
  // It stays in the scene, because it is a perfectly good sky, and is kept out
  // of every raycast.
  const groundMeshes = [];
  village.traverse((o) => {
    if (!o.isMesh) return;
    if (/sky|cloud|backdrop/i.test(o.name || "")) return;
    groundMeshes.push(o);
  });

  // Bounds of the ground alone, so rays start just above the terrain.
  const bounds = new THREE.Box3();
  for (const m of groundMeshes) bounds.expandByObject(m);
  const SKY_Y = bounds.max.y + 15;

  function groundAt(x, z, from = SKY_Y) {
    down.set(new THREE.Vector3(x, from, z), DOWN);
    const hit = down.intersectObjects(groundMeshes, true);
    return hit.length ? hit[0].point.y : null;
  }

  /**
   * Find the village.
   *
   * The scan is a square of French coastline and Èze is a village on a crag in
   * the middle of it, so the centre of the bounding box is a road at the foot
   * of the hill, eighty metres below the streets. Probing a grid and taking the
   * highest ground finds the town itself, which is the only part worth walking.
   */
  let TOWN = new THREE.Vector3(0, 0, 0);
  {
    let best = -Infinity;
    const reach = Math.max(Math.abs(bounds.min.x), Math.abs(bounds.max.x),
                           Math.abs(bounds.min.z), Math.abs(bounds.max.z));
    const stepG = Math.max(3, reach / 30);
    for (let gx = -reach; gx <= reach; gx += stepG) {
      for (let gz = -reach; gz <= reach; gz += stepG) {
        const y = groundAt(gx, gz);
        if (y !== null && y > best) { best = y; TOWN.set(gx, y, gz); }
      }
    }
    if (best === -Infinity) TOWN.set(0, groundAt(0, 0) ?? 0, 0);
  }
  const TOWN_Y = TOWN.y;
  // Deep enough to take in the terraces below the summit, shallow enough to
  // keep Ash off the cliff.
  const TOWN_BAND = 26;

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
      const r = 8 + (i % 3) * 5;
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
