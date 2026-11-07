/**
 * Presence for the assistants: an abstract core, or a feminine or masculine bust, drawn in one of two original
 * styles. Modelled from simple 3D surfaces; no likeness of any real person or character.
 *
 * - "grid" (synthetic): a low-poly lattice of nodes and fine edges; pulses of light run along the edges while it
 *   thinks.
 * - "flow" (energy): streams of light flowing over the form with soft trails, like drifting mist.
 *
 * States: idle (breathing), listening (draws in and brightens), thinking (a band of light scans down, pulses run),
 * speaking (the jaw moves with the voice and the light swells). Changing form dissolves and re-forms the figure.
 */

import { FEMALE, FEMALE_EDGES, FEMALE_NODES, MALE, MALE_EDGES, MALE_NODES } from "./presence-data";

export type AvatarShape = "core" | "female" | "male";
export type AvatarState = "idle" | "listening" | "thinking" | "speaking";

export interface AvatarTheme {
  /** main colour, e.g. "#67e8f9" */
  color: string;
  /** accent for the rim light and motes */
  accent: string;
  /** "grid": synthetic lattice of nodes and edges; "flow": streams of light with soft trails */
  energy: "flow" | "grid";
}

const W = 240; // design space; scaled to fit the canvas
const H = 280;
const COUNT = 7000;
const LIGHT = norm(-0.35, -0.5, 0.8);

interface P {
  x: number; // centred design coordinates (0,0 = middle of the head)
  y: number;
  z: number;
  nx: number;
  ny: number;
  nz: number;
  b: number; // brightness 0..1
  r: number; // size
  jaw: number; // 0..1: how much this point moves when speaking
}

function norm(x: number, y: number, z: number): [number, number, number] {
  const l = Math.hypot(x, y, z) || 1;
  return [x / l, y / l, z / l];
}

function rand(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

function hexRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function shade(nx: number, ny: number, nz: number): number {
  const d = Math.max(0, nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2]);
  return 0.1 + 0.9 * Math.pow(d, 1.25);
}

/** Point cloud for a bust, from the baked CC0 synthetic human (see presence-data.ts). */
function bust(shape: "female" | "male", seed: number): P[] {
  const raw = atob(shape === "female" ? FEMALE : MALE);
  const bytes = new Int8Array(raw.length);
  for (let i = 0; i < raw.length; i++) bytes[i] = (raw.charCodeAt(i) << 24) >> 24;
  const base: P[] = [];
  for (let i = 0; i + 7 < bytes.length; i += 8) {
    const y = bytes[i + 1] - 30; // centre the figure in the design space
    const z = bytes[i + 2];
    const kind = bytes[i + 7];
    const jaw = kind === 0 && z > 5 && bytes[i + 1] > 24 && bytes[i + 1] < 46 ? Math.min(1, (bytes[i + 1] - 24) / 8) : 0;
    base.push({ x: bytes[i], y, z, nx: bytes[i + 3] / 127, ny: bytes[i + 4] / 127, nz: bytes[i + 5] / 127, b: Math.max(0, bytes[i + 6] / 127), r: 0, jaw });
  }
  BASE[shape] = base.length;
  const r = rand(seed);
  const out: P[] = [];
  for (let i = 0; i < COUNT; i++) {
    const p = base[i < base.length ? i : Math.floor(r() * base.length)];
    const j = i < base.length ? 0 : 0.8; // repeated points are nudged so nothing doubles up
    out.push({ ...p, x: p.x + (r() - 0.5) * j, y: p.y + (r() - 0.5) * j, z: p.z + (r() - 0.5) * j, r: i < NODES[shape] ? 0.55 : 0.35 + r() * 0.7 });
  }
  return out;
}

/** Geodesic sphere (subdivided icosahedron): vertices and edges, for the core's lattice. */
function geodesic(level: number): { v: [number, number, number][]; e: number[] } {
  const t = (1 + Math.sqrt(5)) / 2;
  let v: [number, number, number][] = [[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t], [0, -1, -t], [0, 1, -t], [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]].map(([x, y, z]) => norm(x, y, z));
  let f = [[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]];
  for (let l = 0; l < level; l++) {
    const mid = new Map<string, number>();
    const m = (a: number, b: number) => {
      const k = a < b ? `${a}_${b}` : `${b}_${a}`;
      if (!mid.has(k)) {
        const [x1, y1, z1] = v[a];
        const [x2, y2, z2] = v[b];
        v.push(norm(x1 + x2, y1 + y2, z1 + z2));
        mid.set(k, v.length - 1);
      }
      return mid.get(k)!;
    };
    f = f.flatMap(([a, b, c]) => {
      const ab = m(a, b), bc = m(b, c), ca = m(c, a);
      return [[a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]];
    });
  }
  const seen = new Set<string>();
  const e: number[] = [];
  for (const [a, b, c] of f)
    for (const [x, y] of [[a, b], [b, c], [c, a]]) {
      const k = x < y ? `${x}_${y}` : `${y}_${x}`;
      if (!seen.has(k)) {
        seen.add(k);
        e.push(x, y);
      }
    }
  return { v, e };
}

const GEO = geodesic(3);

/** The abstract core: a geodesic sphere (its vertices first) around a bright nucleus, with one thin ring. */
function core(seed: number): P[] {
  const r = rand(seed);
  const out: P[] = [];
  for (const [nx, ny, nz] of GEO.v) {
    out.push({ x: nx * 60, y: ny * 60 + 8, z: nz * 60, nx, ny, nz, b: Math.min(1, shade(nx, ny, nz) * 0.85 + 0.15), r: 0.6, jaw: 0 });
  }
  const shell = Math.round(COUNT * 0.5);
  for (let i = 0; i < shell; i++) {
    const [nx, ny, nz] = norm(r() * 2 - 1, r() * 2 - 1, r() * 2 - 1);
    const R = 58 + (r() - 0.5) * 3;
    out.push({ x: nx * R, y: ny * R + 8, z: nz * R, nx, ny, nz, b: Math.min(1, shade(nx, ny, nz) * 0.6 + Math.pow(1 - Math.abs(nz), 4) * 0.4), r: 0.3 + r() * 0.5, jaw: 0 });
  }
  while (out.length < COUNT * 0.88) {
    const g = () => (r() + r() + r() - 1.5) * 0.9;
    const x = g() * 26, y = g() * 26, z = g() * 26;
    out.push({ x, y: y + 8, z, nx: 0, ny: 0, nz: 1, b: Math.max(0.15, 1 - Math.hypot(x, y, z) / 40), r: 0.3 + r() * 0.8, jaw: 0 });
  }
  while (out.length < COUNT) {
    const a = r() * Math.PI * 2;
    const R = 86 + (r() - 0.5) * 3;
    out.push({ x: Math.cos(a) * R, y: Math.sin(a) * R * 0.16 + 8 + Math.cos(a) * R * 0.08, z: Math.sin(a) * R, nx: 0, ny: 0, nz: 1, b: 0.22 + r() * 0.25, r: 0.3 + r() * 0.4, jaw: 0 });
  }
  return out;
}

function decodeEdges(b64: string): Uint16Array {
  const raw = atob(b64);
  const out = new Uint16Array(raw.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = raw.charCodeAt(i * 2) | (raw.charCodeAt(i * 2 + 1) << 8);
  return out;
}

/** How many points of each form are real surface points; the rest become the energy aura in the "flow" style. */
const BASE: Record<AvatarShape, number> = { core: COUNT, female: COUNT, male: COUNT };
const NODES: Record<AvatarShape, number> = { core: GEO.v.length, female: FEMALE_NODES, male: MALE_NODES };

export class ParticleAvatar {
  private ctx: CanvasRenderingContext2D;
  private bloom = document.createElement("canvas");
  private bctx = this.bloom.getContext("2d")!;
  private from: P[] = [];
  private to: P[] = [];
  private cur: Float32Array = new Float32Array(COUNT * 3);
  private phase = new Float32Array(COUNT);
  private order = new Float32Array(COUNT);
  private morphStart = -1e9;
  private shapes: Partial<Record<AvatarShape, P[]>> = {};
  private sprite: HTMLCanvasElement | null = null;
  private raf = 0;
  private t0 = performance.now();
  private pointer = { x: 0, y: 0 };
  private look = { x: 0, y: 0 };
  private level = 0;
  private energy = { listen: 0, think: 0, speak: 0 };
  private sx = new Float32Array(COUNT);
  private sy = new Float32Array(COUNT);
  private sa = new Float32Array(COUNT);
  private ss = new Float32Array(COUNT);
  private edges: Partial<Record<AvatarShape, Uint16Array>> = {};
  shape: AvatarShape = "core";
  state: AvatarState = "idle";

  constructor(
    private canvas: HTMLCanvasElement,
    private theme: AvatarTheme,
    private reduced = false,
  ) {
    this.ctx = canvas.getContext("2d")!;
    const r = rand(7);
    for (let i = 0; i < COUNT; i++) this.phase[i] = r() * Math.PI * 2;
    this.to = this.points("core");
    this.from = this.to;
    this.to.forEach((p, i) => this.cur.set([p.x, p.y, p.z], i * 3));
    this.setTheme(theme);
  }

  private points(shape: AvatarShape): P[] {
    if (!this.shapes[shape]) this.shapes[shape] = shape === "core" ? core(11) : bust(shape, shape === "female" ? 21 : 31);
    return this.shapes[shape]!;
  }

  setTheme(theme: AvatarTheme) {
    this.theme = theme;
    const s = document.createElement("canvas");
    s.width = s.height = 32;
    const g = s.getContext("2d")!;
    const [r, gg, b] = hexRgb(theme.color);
    const lift = (c: number) => Math.min(255, c + 110);
    const grad = g.createRadialGradient(16, 16, 0, 16, 16, 16);
    grad.addColorStop(0, "rgba(255,255,255,1)");
    grad.addColorStop(0.15, `rgba(${lift(r)},${lift(gg)},${lift(b)},0.9)`);
    grad.addColorStop(0.4, `rgba(${r},${gg},${b},0.28)`);
    grad.addColorStop(1, `rgba(${r},${gg},${b},0)`);
    g.fillStyle = grad;
    g.fillRect(0, 0, 32, 32);
    this.sprite = s;
  }

  /** Lattice edges between the first NODES[shape] points. */
  private edgesFor(shape: AvatarShape): Uint16Array {
    if (!this.edges[shape])
      this.edges[shape] = shape === "core" ? Uint16Array.from(GEO.e) : decodeEdges(shape === "female" ? FEMALE_EDGES : MALE_EDGES);
    return this.edges[shape]!;
  }

  setShape(shape: AvatarShape) {
    if (shape === this.shape) return;
    this.shape = shape;
    this.from = this.to.map((p, i) => ({ ...p, x: this.cur[i * 3], y: this.cur[i * 3 + 1], z: this.cur[i * 3 + 2] }));
    this.to = this.points(shape);
    // re-form from the top down
    for (let i = 0; i < COUNT; i++) this.order[i] = Math.max(0, Math.min(1, (this.to[i].y + 70) / 220)) * 0.45 + this.phase[i] * 0.02;
    this.morphStart = (performance.now() - this.t0) / 1000;
  }

  setState(state: AvatarState) {
    this.state = state;
  }

  /** 0..1 loudness of the voice, for the speaking motion. */
  setLevel(level: number) {
    this.level = Math.max(0, Math.min(1, level));
  }

  setPointer(x: number, y: number) {
    this.pointer = { x, y };
  }

  start() {
    const loop = () => {
      this.frame();
      this.raf = requestAnimationFrame(loop);
    };
    if (this.reduced) {
      this.morphStart = -1e9;
      this.frame();
    } else this.raf = requestAnimationFrame(loop);
  }

  stop() {
    cancelAnimationFrame(this.raf);
  }

  private frame() {
    const { canvas, ctx } = this;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const cw = Math.round(canvas.clientWidth * dpr);
    const ch = Math.round(canvas.clientHeight * dpr);
    if (!cw || !ch) return;
    if (canvas.width !== cw || canvas.height !== ch) {
      canvas.width = cw;
      canvas.height = ch;
      this.bloom.width = Math.max(1, Math.round(cw / 4));
      this.bloom.height = Math.max(1, Math.round(ch / 4));
    }
    const t = (performance.now() - this.t0) / 1000;
    const isBust = this.shape !== "core";
    const scale = Math.min(cw / W, ch / H) * (isBust ? 1.25 : 1);
    const cx = cw / 2;
    const cy = isBust ? ch * 0.42 : ch / 2 - 22 * scale;
    const mesh = this.theme.energy === "grid"; // crisp lattice; otherwise softer lattice with streams of energy

    // ease state changes so nothing snaps
    const e = this.energy;
    const ease = (v: number, target: number) => v + (target - v) * 0.06;
    e.listen = ease(e.listen, this.state === "listening" ? 1 : 0);
    e.think = ease(e.think, this.state === "thinking" ? 1 : 0);
    e.speak = ease(e.speak, this.state === "speaking" ? 1 : 0);
    this.look.x += (this.pointer.x - this.look.x) * 0.05;
    this.look.y += (this.pointer.y - this.look.y) * 0.05;

    const voice = e.speak * Math.max(this.level, Math.max(0, Math.sin(t * 11) * 0.6 + Math.sin(t * 17.3) * 0.4));
    const isCore = this.shape === "core";
    const yaw = (isCore ? t * 0.22 : Math.sin(t * 0.3) * 0.16) + this.look.x * 0.5;
    const pitch = Math.sin(t * 0.23) * 0.03 + this.look.y * 0.2 + (isCore ? 0.2 : 0);
    const cyw = Math.cos(yaw);
    const syw = Math.sin(yaw);
    const cp = Math.cos(pitch);
    const sp = Math.sin(pitch);
    const morph = Math.min(1, (t - this.morphStart) / 1.8);
    const breathe = 1 + Math.sin(t * 1.1) * 0.006 - e.listen * 0.02;
    const scan = ((t * 0.55) % 1.6) * 220 - 110; // thinking: a band of light passing down
    const flowAmp = 0.7 + e.think * 1.4 + voice * 1.4 - e.listen * 0.4;
    const auraFrom = isBust && !mesh ? BASE[this.shape] : COUNT;

    // 1. project every point
    for (let i = 0; i < COUNT; i++) {
      const a = this.from[i];
      const b = this.to[i];
      let k = 1;
      if (morph < 1) {
        k = Math.max(0, Math.min(1, (morph - this.order[i]) / 0.55));
        k = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      }
      const ph = this.phase[i];
      const lift = Math.sin(k * Math.PI); // dissolve outward, then settle
      let x = a.x + (b.x - a.x) * k + Math.cos(ph) * lift * 14;
      let y = a.y + (b.y - a.y) * k + Math.sin(ph * 1.7) * lift * 14;
      let z = a.z + (b.z - a.z) * k + Math.sin(ph * 2.3) * lift * 10;
      const nx = a.nx + (b.nx - a.nx) * k;
      const nz = a.nz + (b.nz - a.nz) * k;
      if (mesh || i < NODES[this.shape]) {
        x += Math.sin(t * 0.6 + ph) * 0.25;
        y += Math.cos(t * 0.5 + ph * 1.3) * 0.25;
      } else {
        // streams: each point slides along a slowly turning field over the surface
        const fa = Math.sin(y * 0.045 + t * 0.9) + Math.cos(x * 0.05 - t * 0.7);
        x += Math.cos(fa * 1.7 + ph * 0.3) * flowAmp;
        y += Math.sin(fa * 1.7 + ph * 0.3) * flowAmp - (b.b < 0.3 && i % 17 === 0 ? (t * 7 + ph * 30) % 34 : 0);
      }
      if (i >= auraFrom) {
        // energy aura: ribbons of light circling the figure, wider and faster while speaking or thinking
        const lane = i % 6;
        const ang = (t * (0.18 + lane * 0.03) * (1 + e.think + voice * 0.6) + ph) % (Math.PI * 2);
        const R = 64 + lane * 7 + voice * 6;
        const ax = Math.cos(ang) * R;
        const az = Math.sin(ang) * R * 0.55;
        const ay = -18 + Math.sin(ang * 2 + lane) * 26 + lane * 9 + Math.sin(t * 0.7 + ph * 3) * 3;
        x += (ax - x) * k;
        y += (ay - y) * k;
        z += (az - z) * k;
      }
      if (b.jaw > 0 && k > 0.99) y += b.jaw * voice * 4.5;
      x *= breathe;
      y *= breathe;
      const rx = x * cyw + z * syw;
      let rz = -x * syw + z * cyw;
      const ry = y * cp - rz * sp;
      rz = y * sp + rz * cp;
      const facing = -nx * syw + nz * cyw;
      let alpha = i >= auraFrom ? 0.28 * k : (a.b + (b.b - a.b) * k) * (0.3 + 0.7 * Math.max(0, Math.min(1, (facing + 0.35) / 0.6)));
      const persp = 380 / (380 - rz);
      alpha *= 0.85 + 0.15 * Math.sin(t * 1.7 + ph * 5);
      alpha *= 1 + e.listen * 0.2 + voice * 0.15;
      if (e.think > 0.01) alpha *= 1 + e.think * 0.9 * Math.exp(-(((y - scan) / 9) ** 2));
      this.sx[i] = cx + rx * persp * scale;
      this.sy[i] = cy + ry * persp * scale;
      this.sa[i] = alpha > 1 ? 1 : alpha;
      this.ss[i] = (a.r + (b.r - a.r) * k) * scale * persp;
    }

    // 2. clear (the energy style keeps a soft trail of the last frames)
    ctx.globalCompositeOperation = "source-over";
    ctx.clearRect(0, 0, cw, ch);
    ctx.globalCompositeOperation = "lighter";
    const sprite = this.sprite!;
    const [r0, g0, b0] = hexRgb(this.theme.color);

    {
      // 3. lattice: fine edges in four brightness bands, then the nodes
      const edges = this.edgesFor(this.shape);
      const show = morph < 1 ? Math.max(0, (morph - 0.6) / 0.4) : 1;
      if (show > 0) {
        ctx.lineWidth = Math.max(0.6, 0.45 * scale);
        ctx.strokeStyle = `rgb(${r0},${g0},${b0})`;
        for (let band = 0; band < 4; band++) {
          ctx.beginPath();
          for (let n = 0; n < edges.length; n += 2) {
            const i = edges[n];
            const j = edges[n + 1];
            const al = (this.sa[i] + this.sa[j]) / 2;
            if (Math.min(3, Math.floor(al * 4)) !== band) continue;
            ctx.moveTo(this.sx[i], this.sy[i]);
            ctx.lineTo(this.sx[j], this.sy[j]);
          }
          ctx.globalAlpha = (0.08 + band * 0.1) * show * (mesh ? 1 : 0.6);
          ctx.stroke();
        }
        // pulses of light along the edges, more while thinking or speaking
        const pulses = Math.round((mesh ? 14 : 140) + e.think * 50 + voice * (mesh ? 30 : 90));
        ctx.fillStyle = this.theme.accent;
        for (let q = 0; q < pulses; q++) {
          const n = ((Math.floor(t * 0.7 + q * 7.31) * 2654435761) >>> 0) % (edges.length / 2);
          const i = edges[n * 2];
          const j = edges[n * 2 + 1];
          const u = (t * (mesh ? 0.7 : 0.45) + q * 7.31) % 1;
          const px = this.sx[i] + (this.sx[j] - this.sx[i]) * u;
          const py = this.sy[i] + (this.sy[j] - this.sy[i]) * u;
          ctx.globalAlpha = 0.9 * show * Math.sin(u * Math.PI) * Math.max(this.sa[i], 0.3);
          const s2 = 2.4 * scale;
          ctx.drawImage(sprite, px - s2, py - s2, s2 * 2, s2 * 2);
        }
      }
      const nodes = NODES[this.shape];
      for (let i = 0; i < COUNT; i++) {
        const node = i < nodes;
        const aura = i >= auraFrom;
        const size = this.ss[i] * (node ? 2.6 : aura ? 1.5 : 2);
        // nodes glow; the surface between them stays a faint dust (fuller while changing form)
        ctx.globalAlpha = aura ? this.sa[i] : this.sa[i] * (node ? (mesh ? 0.9 : 0.6) : (mesh ? 0.22 : 0.3) + 0.6 * (1 - show));
        ctx.drawImage(sprite, this.sx[i] - size / 2, this.sy[i] - size / 2, size, size);
      }
    }

    // a few motes drifting around the figure
    ctx.fillStyle = this.theme.accent;
    for (let j = 0; j < 30; j++) {
      const ph = this.phase[j * 131];
      const ang = t * 0.08 + ph;
      const R = (78 + ((j * 37) % 55)) * scale;
      ctx.globalAlpha = (0.15 + 0.2 * Math.sin(t * 0.9 + ph * 3)) ;
      const s2 = scale * (0.6 + (j % 3) * 0.3);
      ctx.fillRect(cx + Math.cos(ang) * R, cy + 20 * scale + Math.sin(ang * 1.4) * R * 0.75, s2, s2);
    }

    // 4. bloom: a blurred copy added back softly (only when the frame is fully redrawn)
    {
      this.bctx.globalCompositeOperation = "copy";
      this.bctx.drawImage(canvas, 0, 0, this.bloom.width, this.bloom.height);
      ctx.globalAlpha = 0.5;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(this.bloom, 0, 0, cw, ch);
    }
    ctx.globalAlpha = 1;
    ctx.globalCompositeOperation = "source-over";
  }
}
