import * as THREE from "three";

/** Procedural 16×16 pixel-art textures (generated, no external assets). */

function rng(seed: number) {
  let s = seed >>> 0 || 1;
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296);
}

const cache = new Map<string, THREE.CanvasTexture>();

function make(key: string, draw: (px: (x: number, y: number, c: string) => void, r: () => number) => void,
              repeat: [number, number] = [1, 1]) {
  const ck = `${key}|${repeat.join(",")}`;
  const hit = cache.get(ck);
  if (hit) return hit;
  const c = document.createElement("canvas");
  c.width = c.height = 16;
  const ctx = c.getContext("2d")!;
  const px = (x: number, y: number, col: string) => { ctx.fillStyle = col; ctx.fillRect(x, y, 1, 1); };
  draw(px, rng(hash(key)));
  const t = new THREE.CanvasTexture(c);
  t.magFilter = THREE.NearestFilter;
  t.minFilter = THREE.NearestFilter;
  t.colorSpace = THREE.SRGBColorSpace;
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(repeat[0], repeat[1]);
  cache.set(ck, t);
  return t;
}

function hash(s: string) {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

const pick = (r: () => number, arr: string[]) => arr[Math.floor(r() * arr.length)];

const GRASS = ["#5fae3c", "#68b844", "#57a336", "#71c04b", "#62b03f"];
const DIRT = ["#8a5a3b", "#7d5034", "#966443", "#704829", "#8f5e3e"];
const STONE = ["#9a9a9a", "#8c8c8c", "#a7a7a7", "#7f7f7f", "#b0b0b0"];

export const grassTop = () => make("grassTop", (px, r) => {
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) px(x, y, pick(r, GRASS));
});

export const grassSide = () => make("grassSide", (px, r) => {
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    const edge = 3 + Math.floor(r() * 2);
    px(x, y, y < edge ? pick(r, GRASS) : pick(r, DIRT));
  }
});

export const dirt = () => make("dirt", (px, r) => {
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) px(x, y, pick(r, DIRT));
});

export const path = () => make("path", (px, r) => {
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    const seam = x % 8 === 0 || (y % 8 === 0);
    px(x, y, seam ? "#6f6f6f" : pick(r, STONE));
  }
});

export const stoneBricks = (repeat: [number, number] = [1, 1]) => make("stoneBricks", (px, r) => {
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    const row = Math.floor(y / 4);
    const off = row % 2 ? 4 : 0;
    const seam = y % 4 === 3 || (x + off) % 8 === 7;
    px(x, y, seam ? "#5e5e66" : pick(r, ["#a3a3ad", "#9a9aa4", "#b0b0b8", "#94949e"]));
  }
}, repeat);

function shade(hex: string, f: number) {
  const n = parseInt(hex.slice(1), 16);
  const c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => Math.max(0, Math.min(255, Math.round(v * f))));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

export const planks = (color: string, repeat: [number, number] = [1, 1]) => make(`planks-${color}`, (px, r) => {
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    const seam = y % 4 === 3;
    const end = (x + (Math.floor(y / 4) % 2) * 5) % 11 === 0;
    const f = seam ? 0.62 : end ? 0.75 : 0.9 + r() * 0.2;
    px(x, y, shade(color, f));
  }
}, repeat);

export const roofTiles = (color: string, repeat: [number, number] = [1, 1]) => make(`roof-${color}`, (px, r) => {
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    const seam = y % 3 === 2 || (x + (Math.floor(y / 3) % 2) * 2) % 4 === 0;
    px(x, y, shade(color, seam ? 0.6 : 0.85 + r() * 0.2));
  }
}, repeat);

export const logSide = () => make("logSide", (px, r) => {
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++)
    px(x, y, x % 4 === 0 ? "#4a3220" : pick(r, ["#6b4a2e", "#5f412a", "#755234"]));
});

export const leaves = () => make("leaves", (px, r) => {
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++)
    px(x, y, pick(r, ["#3f8a2c", "#347a24", "#4a9a34", "#2e6e20", "#55a83c"]));
});

export const gold = () => make("gold", (px, r) => {
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    const border = x === 0 || y === 0 || x === 15 || y === 15;
    px(x, y, border ? "#d99a1e" : pick(r, ["#f7d34a", "#fbe06b", "#f2c434", "#ffe98a"]));
  }
});

export const crate = () => make("crate", (px, r) => {
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    const frame = x < 2 || y < 2 || x > 13 || y > 13 || x === y || x === 15 - y;
    px(x, y, frame ? "#7a4f22" : pick(r, ["#c08a4a", "#b67f40", "#c99555"]));
  }
});

export const windowTex = () => make("window", (px) => {
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) {
    const frame = x < 2 || y < 2 || x > 13 || y > 13 || x === 7 || x === 8 || y === 7 || y === 8;
    px(x, y, frame ? "#3b2a1a" : y < 7 ? "#fff3b0" : "#ffe27a");
  }
});
