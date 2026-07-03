/**
 * GraphView — Wissensgraph 3D, single unified scene.
 *
 * Two modes driven by the ?focus= URL param:
 *
 *   - Corpus mode (no ?focus=): renders the connected-component
 *     clusters returned by /api/wissensbasis/graph. Translucent
 *     spheres with hub entities distributed over each cluster's
 *     surface, real hub↔hub relations as filaments inside.
 *
 *   - Focus mode (?focus=<entity_id>): renders the entity's
 *     neighborhood. Focus entity at center, hop1 entities on an inner
 *     Fibonacci shell, hop2 entities on an outer shell, and the REAL
 *     relation edges from the backend between all of them.
 *
 * The scene is deliberately volumetric: cluster centres and all shell
 * nodes are placed by golden-angle (Fibonacci) distribution over
 * spheres, never on a flat ring — the old layout collapsed the whole
 * corpus onto one XZ "ecliptic" (y ≤ ±1.6 while x/z reached 14).
 *
 * Visual encoding (DESIGN.md):
 *   - node colour = circle tier (the locked tier ladder tokens,
 *     0 self → 4 public), echoed as text in the label's sub-line so
 *     colour is never the only signal;
 *   - node size = mention_count / importance (sqrt-scaled);
 *   - cluster shells alternate the brand palette (crimson / turquoise
 *     / cream) — identity only, tier lives on the entity nodes;
 *   - edges are real relations; a hovered entity lights its incident
 *     edges in the accent turquoise.
 *
 * Motion: a slow OrbitControls auto-rotate provides the parallax that
 * makes 3D legible on a still screen (motivated motion); it stops on
 * the first user interaction and is disabled entirely under
 * prefers-reduced-motion.
 *
 * Search overlay (top-left) drives the camera in either mode: type a
 * name, pick a suggestion, the URL ?focus= updates and the scene
 * re-renders focused on that entity. Click a hub in the scene → same
 * URL-param change → same re-render. No page navigation, no flat-list
 * handoff.
 *
 * Render budget: corpus mode ≤ ~170 meshes (backend caps: 16+1
 * clusters × ≤6 hubs); focus mode = 1 + ≤30 + ≤30 nodes + O(100) line
 * segments. Either way trivially 60fps.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useSearchParams } from 'react-router';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

import apiClient from '../../utils/axios';
import { useTheme } from '../../context/ThemeContext';
import type {
  FocusEntity,
  FocusNeighborhood,
  SearchHit,
} from '../../api/resources/wissensbasis';

import {
  isFocusNeighborhood,
  isGraphResponse,
  type Cluster,
  type GraphResponse,
  type Hub,
} from './graphResponseGuards';

// Re-export so downstream type references in this file stay unqualified.
export type { Cluster, GraphResponse, Hub };

// DESIGN.md tier ladder (0 self … 4 public) — node colour IS the access
// signal, so these are the locked tier tokens, not decorative hues.
const TIER_COLORS: number[] = [0xa5162f, 0xe63e54, 0xf0e6d3, 0x71fbd0, 0x00937c];
const tierColor = (tier: number | undefined) =>
  TIER_COLORS[Math.min(Math.max(tier ?? 0, 0), 4)];

// Cluster-shell identity alternates the brand palette only (crimson /
// turquoise / cream) — the old palette's violet/yellow/orange are gone
// (AI-slop blacklist #1 / off-brand hues).
const SHELL_COLORS: number[] = [0xe63e54, 0x00e4b8, 0xf0e6d3];
const shellColor = (seed: number) => SHELL_COLORS[seed % SHELL_COLORS.length];

const EDGE_COLOR = 0x475569;
const EDGE_HIGHLIGHT = 0x00e4b8;

// Clusters with fewer entities than this stay unlabelled in the corpus
// overview (their orb is still visible; the caption appears on hover) — 17
// captions at once was an unreadable wall of overlapping text.
const CLUSTER_LABEL_MIN_ENTITIES = 5;

/** i-th of n directions distributed by golden angle over the unit sphere. */
function fibonacciDirection(i: number, n: number): THREE.Vector3 {
  if (n <= 1) return new THREE.Vector3(0, 1, 0);
  const golden = Math.PI * (3 - Math.sqrt(5));
  const y = 1 - (i / (n - 1)) * 2;
  const r = Math.sqrt(Math.max(0, 1 - y * y));
  const theta = golden * i;
  return new THREE.Vector3(Math.cos(theta) * r, y, Math.sin(theta) * r);
}

// Stable debounce — keeps each keystroke from firing /search.
function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

export default function GraphView() {
  const { t } = useTranslation();
  const { isDark } = useTheme();
  const [searchParams, setSearchParams] = useSearchParams();
  const focusId = searchParams.get('focus') || '';

  const rootRef = useRef<HTMLDivElement>(null);
  const labelsRef = useRef<HTMLDivElement>(null);

  const [corpus, setCorpus] = useState<GraphResponse | null>(null);
  const [focusData, setFocusData] = useState<FocusNeighborhood | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Bumped by the error view's retry button so the load effect re-runs even
  // when focusId is unchanged (the corpus view's only other dep).
  const [retryNonce, setRetryNonce] = useState(0);

  // Mode selector. Fetches the appropriate endpoint; clears the other.
  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    // A failure tears down any prior scene so the Three.js RAF loop stops
    // (its effect keys on corpus/focusData) instead of rendering to the
    // now-detached canvas behind the error view.
    const fail = (msg: string) => {
      if (cancelled) return;
      setLoadError(msg);
      setCorpus(null);
      setFocusData(null);
    };
    if (focusId) {
      apiClient.get<FocusNeighborhood>('/api/wissensbasis/focus', {
        params: { entity_id: focusId, hops: 2 },
      })
        .then(res => {
          if (cancelled) return;
          if (isFocusNeighborhood(res.data)) { setFocusData(res.data); setCorpus(null); }
          else fail('malformed');
        })
        .catch(err => fail(err?.message || String(err)));
    } else {
      apiClient.get<GraphResponse>('/api/wissensbasis/graph')
        .then(res => {
          if (cancelled) return;
          if (isGraphResponse(res.data)) { setCorpus(res.data); setFocusData(null); }
          else fail('malformed');
        })
        .catch(err => fail(err?.message || String(err)));
    }
    return () => { cancelled = true; };
  }, [focusId, retryNonce]);

  // Three.js scene lifecycle.
  useEffect(() => {
    const root = rootRef.current;
    const labelsLayer: HTMLDivElement | null = labelsRef.current;
    if (!root || !labelsLayer) return;
    if (!corpus && !focusData) return;

    // Theme-aware palette: the 3D scene follows light/dark instead of being a
    // hardcoded black stage. Light mode uses a warm paper background with
    // solid (low-emissive) orbs read by lighting; dark mode keeps the glow.
    const themeBg = isDark ? 0x0a0f1c : 0xeee9df;
    const th = {
      isDark,
      bg: themeBg,
      emissive: isDark ? 0.9 : 0.32,
      hubEmissive: isDark ? 0.85 : 0.35,
      haloOpacity: isDark ? 0.14 : 0.3,
      spokeOpacity: isDark ? 0.06 : 0.12,
    };

    const scene = new THREE.Scene();
    // Fog is set AFTER the camera fit below (it must scale with the scene size
    // — a fixed density fogged the whole graph to black once the volumetric
    // spread pushed the camera far back).

    const W = () => root.clientWidth;
    const H = () => root.clientHeight;
    const camera = new THREE.PerspectiveCamera(45, W() / H(), 0.1, 1000);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(W(), H());
    renderer.setClearColor(themeBg, 1);
    root.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    // Brighter, whiter light in light mode so the low-emissive orbs are lit
    // into solid saturated colour rather than reading flat.
    scene.add(new THREE.AmbientLight(isDark ? 0x6080a0 : 0xffffff, isDark ? 0.55 : 0.95));
    const key = new THREE.DirectionalLight(0xffffff, isDark ? 0.6 : 0.85);
    key.position.set(8, 20, 14);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0xe63e54, isDark ? 0.18 : 0.12);
    rim.position.set(-12, -8, -16);
    scene.add(rim);

    // Track all labelable things (cluster spheres OR focus entities)
    // for the 2D label overlay + raycaster targets that drive
    // click-to-refocus.
    //
    // `tier` controls label styling: 'primary' (cluster center / focus
    // entity) shows full size; 'secondary' (hubs / hop1 / hop2) is
    // smaller, fades with distance so the center label stays readable.
    // `object` is read each frame for the world position so moving
    // nodes carry their labels.
    const labeled: Array<{
      object: THREE.Object3D;
      name: string;
      sub?: string;
      yOffset: number;
      tier: 'primary' | 'secondary';
      entityId?: string;
      /** Exempt from distance culling (focus-mode hop1 — the point of the view). */
      alwaysLabel?: boolean;
      /** Small-cluster primary label — hidden until hovered. */
      minor?: boolean;
    }> = [];
    const clickable: Array<{ mesh: THREE.Object3D; entityId: string }> = [];
    // entity id → incident relation lines, for the hover highlight.
    const edgesByEntity = new Map<string, THREE.Line[]>();

    const tierName = (tier: number | undefined) =>
      t(`circles.tier.${Math.min(Math.max(tier ?? 0, 0), 4)}`);

    if (corpus) {
      buildCorpusScene(scene, corpus, labeled, clickable, edgesByEntity, tierName, th);
    } else if (focusData) {
      buildFocusScene(scene, focusData, labeled, clickable, edgesByEntity, tierName, th);
    }

    // Frame the whole constellation: bounding sphere → camera distance.
    // (The old hardcoded (0,14,36) under/over-framed depending on data.)
    const bounds = new THREE.Box3().setFromObject(scene);
    const sphere = bounds.getBoundingSphere(new THREE.Sphere());
    // Empty graph → Box3 is empty and the sphere degenerates (radius -1);
    // fall back to a sane default frame instead of a negative distance.
    if (!(sphere.radius > 0)) {
      sphere.center.set(0, 0, 0);
      sphere.radius = 16;
    }
    const fitDistance =
      (sphere.radius / Math.tan((camera.fov * Math.PI) / 360)) * 0.92;
    const viewDir = new THREE.Vector3(0.35, 0.22, 1).normalize();
    camera.position.copy(sphere.center.clone().add(viewDir.multiplyScalar(fitDistance)));
    // Depth fog scaled to the scene: clear through the near/mid orbs, only the
    // far back of the cloud recedes. Tied to fitDistance so it never blacks the
    // graph out regardless of how far the fit pushes the camera.
    scene.fog = new THREE.Fog(themeBg, fitDistance * 0.35, fitDistance + sphere.radius * 2.2);
    controls.target.copy(sphere.center);
    controls.minDistance = Math.max(2, sphere.radius * 0.2);
    controls.maxDistance = fitDistance * 2.4;

    // Slow auto-orbit = the parallax that makes depth legible on a still
    // screen. Stops permanently on the user's first interaction; never
    // runs under prefers-reduced-motion.
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    controls.autoRotate = !reducedMotion;
    controls.autoRotateSpeed = 0.5;
    const stopAutoRotate = () => { controls.autoRotate = false; };
    controls.addEventListener('start', stopAutoRotate);

    // Theme-aware label colours: readable in both the dark 3D stage and the
    // light-mode warm-paper background.
    const lbl = {
      text: isDark ? '#ffffff' : '#1f2937',
      sub: isDark ? 'rgba(156,163,175,1)' : 'rgba(75,85,99,0.95)',
      pillPrimary: isDark ? 'rgba(10,15,28,0.72)' : 'rgba(255,253,248,0.85)',
      pillSecondary: isDark ? 'rgba(10,15,28,0.55)' : 'rgba(255,253,248,0.72)',
      shadow: isDark
        ? '0 1px 2px rgba(0,0,0,0.95), 0 0 4px rgba(0,0,0,0.85)'
        : '0 1px 1px rgba(255,255,255,0.85)',
    };

    type Rect = { left: number; top: number; right: number; bottom: number };
    const overlaps = (a: Rect, b: Rect) =>
      a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
    // Approximate the label's screen box (centred at x, growing upward from y)
    // so overlaps can be culled without measuring the DOM every frame.
    const labelBox = (item: Labeled, x: number, y: number): Rect => {
      const primary = item.tier === 'primary';
      const maxLen = primary ? 48 : 24;
      const len = Math.min(item.name.length, maxLen);
      const w = len * (primary ? 7.6 : 5.4) + (primary ? 16 : 12);
      const h = item.sub ? (primary ? 34 : 26) : primary ? 24 : 18;
      return { left: x - w / 2, right: x + w / 2, top: y - h, bottom: y };
    };

    const makeLabelDiv = (item: Labeled, x: number, y: number, opacity: number) => {
      const div = document.createElement('div');
      const clickableLabel = !!item.entityId;
      div.className = clickableLabel
        ? 'absolute whitespace-nowrap cursor-pointer select-none'
        : 'pointer-events-none absolute whitespace-nowrap';
      // The labels layer is pointer-events-none; clickable captions must opt
      // back in or every 'click' falls through to the canvas.
      if (clickableLabel) div.style.pointerEvents = 'auto';
      div.style.left = `${x}px`;
      div.style.top = `${y}px`;
      div.style.transform = 'translate(-50%, -100%)';
      div.style.textShadow = lbl.shadow;
      div.style.opacity = String(opacity);
      div.style.borderRadius = '4px';
      if (item.tier === 'primary') {
        div.style.padding = '2px 7px';
        div.style.background = lbl.pillPrimary;
        div.style.textAlign = 'center';
      } else {
        div.style.padding = '1px 5px';
        div.style.background = lbl.pillSecondary;
      }
      if (clickableLabel) {
        const eid = item.entityId!;
        // pointerdown, not click: the label divs are rebuilt every frame, so
        // mousedown and mouseup never hit the SAME element and a click event
        // can never assemble.
        div.addEventListener('pointerdown', (e) => {
          e.stopPropagation();
          e.preventDefault();
          setSearchParams((prev) => {
            const next = new URLSearchParams(prev);
            next.set('focus', eid);
            return next;
          });
        });
      }
      const name = document.createElement('div');
      name.className = item.tier === 'primary' ? 'font-semibold text-sm' : 'font-medium text-[11px]';
      name.style.color = lbl.text;
      if (item.tier === 'primary') name.style.fontFamily = 'Cormorant, Georgia, serif';
      const maxLen = item.tier === 'primary' ? 48 : 24;
      name.textContent = item.name.length > maxLen ? item.name.slice(0, maxLen - 1) + '…' : item.name;
      div.appendChild(name);
      if (item.sub) {
        const sub = document.createElement('div');
        sub.className = item.tier === 'primary' ? 'text-[10px] font-normal' : 'text-[9px] font-normal';
        sub.style.color = lbl.sub;
        sub.textContent = item.sub;
        div.appendChild(sub);
      }
      return div;
    };

    const tmpWorld = new THREE.Vector3();
    function updateLabels() {
      if (!labelsLayer) return;
      while (labelsLayer.firstChild) labelsLayer.removeChild(labelsLayer.firstChild);
      const camPos = camera.position;

      // 1. Collect the visible candidates (apply the per-tier cull + fade).
      const cands: Array<{ item: Labeled; x: number; y: number; distance: number; opacity: number; hovered: boolean }> = [];
      for (const item of labeled) {
        item.object.getWorldPosition(tmpWorld);
        const distance = tmpWorld.distanceTo(camPos);
        tmpWorld.y += item.yOffset;
        const v = tmpWorld.clone().project(camera);
        if (v.z > 1) continue;
        const x = (v.x + 1) * 0.5 * W();
        const y = (1 - (v.y + 1) * 0.5) * H();
        const hovered = !!item.entityId && item.entityId === hoveredEntityId;
        let opacity = 1;
        if (item.tier === 'secondary') {
          // Hub captions appear only up close or on hover (focus-mode hop1
          // opts out via alwaysLabel) — the overview reads as cluster names.
          if (distance > 26 && !hovered && !item.alwaysLabel) continue;
          opacity = hovered ? 1 : Math.max(0.4, Math.min(1, 1.3 - (distance - 12) / 20));
        } else if (item.tier === 'primary') {
          if (item.minor && !hovered) continue; // small clusters: hover to reveal
          if (!hovered) opacity = Math.max(0.5, Math.min(1, 1.25 - Math.max(0, distance - 55) / 80));
        }
        cands.push({ item, x, y, distance, opacity, hovered });
      }

      // 2. Priority order: hovered first, then cluster labels over hub labels,
      //    then nearer over farther — the label a viewer wants most wins the space.
      cands.sort((a, b) => {
        if (a.hovered !== b.hovered) return a.hovered ? -1 : 1;
        const ap = a.item.tier === 'primary' ? 0 : 1;
        const bp = b.item.tier === 'primary' ? 0 : 1;
        if (ap !== bp) return ap - bp;
        return a.distance - b.distance;
      });

      // 3. Greedily place, skipping any label whose screen box overlaps one
      //    already placed (a hovered label always shows).
      const placed: Rect[] = [];
      for (const c of cands) {
        const box = labelBox(c.item, c.x, c.y);
        if (!c.hovered && placed.some((p) => overlaps(box, p))) continue;
        placed.push(box);
        labelsLayer.appendChild(makeLabelDiv(c.item, c.x, c.y, c.opacity));
      }
    }

    // Hover + click raycasting. Hover raises the node's emissive, snaps
    // its label opaque, and lights its incident relation edges in the
    // accent turquoise — the neighborhood answer to "what connects here".
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let hoveredEntityId: string | null = null;
    let hoveredMesh: THREE.Mesh | null = null;

    function entityIdOf(object: THREE.Object3D | null): string | null {
      let obj: THREE.Object3D | null = object;
      while (obj && !(obj.userData as { entityId?: string }).entityId) {
        obj = obj.parent;
      }
      return (obj?.userData as { entityId?: string })?.entityId ?? null;
    }

    function setEdgeHighlight(entityId: string | null, on: boolean) {
      if (!entityId) return;
      for (const line of edgesByEntity.get(entityId) ?? []) {
        const mat = line.material as THREE.LineBasicMaterial;
        const base = (line.userData as { baseOpacity?: number }).baseOpacity ?? 0.3;
        mat.color.setHex(on ? EDGE_HIGHLIGHT : EDGE_COLOR);
        mat.opacity = on ? 0.85 : base;
      }
    }

    function setMeshHighlight(mesh: THREE.Mesh | null, on: boolean) {
      const mat = mesh?.material as THREE.MeshStandardMaterial | undefined;
      if (mat && 'emissiveIntensity' in mat) {
        mat.emissiveIntensity = on ? 0.95 : 0.5;
      }
    }

    function onMove(e: MouseEvent) {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((e.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((e.clientY - rect.top) / rect.height) * 2 + 1;

      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects(clickable.map(c => c.mesh), true);
      let nextId: string | null = null;
      let nextMesh: THREE.Mesh | null = null;
      for (const h of hits) {
        const eid = entityIdOf(h.object);
        if (eid) {
          nextId = eid;
          nextMesh = h.object as THREE.Mesh;
          break;
        }
      }
      if (nextId !== hoveredEntityId) {
        setEdgeHighlight(hoveredEntityId, false);
        setMeshHighlight(hoveredMesh, false);
        hoveredEntityId = nextId;
        hoveredMesh = nextMesh;
        setEdgeHighlight(hoveredEntityId, true);
        setMeshHighlight(hoveredMesh, true);
        renderer.domElement.style.cursor = nextId ? 'pointer' : '';
      }
    }
    function onClick() {
      raycaster.setFromCamera(pointer, camera);
      const meshes = clickable.map(c => c.mesh);
      const hits = raycaster.intersectObjects(meshes, true);
      for (const h of hits) {
        const eid = entityIdOf(h.object);
        if (eid) {
          // Stay in scene. Just bump the URL param; the mode effect
          // refetches and the scene rebuilds on the next pass.
          setSearchParams((prev) => {
            const next = new URLSearchParams(prev);
            next.set('focus', eid);
            return next;
          });
          return;
        }
      }
    }
    renderer.domElement.addEventListener('mousemove', onMove);
    renderer.domElement.addEventListener('click', onClick);

    function onResize() {
      camera.aspect = W() / H();
      camera.updateProjectionMatrix();
      renderer.setSize(W(), H());
    }
    window.addEventListener('resize', onResize);

    let rafId = 0;
    function loop() {
      controls.update();
      renderer.render(scene, camera);
      updateLabels();
      rafId = requestAnimationFrame(loop);
    }
    loop();

    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener('resize', onResize);
      renderer.domElement.removeEventListener('mousemove', onMove);
      renderer.domElement.removeEventListener('click', onClick);
      controls.removeEventListener('start', stopAutoRotate);
      controls.dispose();
      // The scene rebuilds on every focus change — without this sweep
      // every rebuild leaked its GPU geometry/material buffers.
      scene.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        if (mesh.geometry) mesh.geometry.dispose();
        const material = (mesh as { material?: THREE.Material | THREE.Material[] }).material;
        if (Array.isArray(material)) material.forEach((m) => m.dispose());
        else if (material) material.dispose();
      });
      renderer.dispose();
      if (root.contains(renderer.domElement)) root.removeChild(renderer.domElement);
    };
  }, [corpus, focusData, setSearchParams, t, isDark]);

  function setFocus(entityId: string | null) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (entityId) next.set('focus', entityId);
      else next.delete('focus');
      return next;
    });
  }

  if (loadError) {
    // A 'malformed' body is the transient deploy case (SPA fallback for /api) —
    // show a calm, retryable message rather than a raw error string.
    const message =
      loadError === 'malformed'
        ? t('knowledgeGraph.graphUnavailable', 'Graph temporarily unavailable.')
        : t('knowledgeGraph.graphLoadError', 'Could not load graph: {{err}}', { err: loadError });
    return (
      <div
        role="alert"
        className="flex items-center gap-3 text-xs text-red-700 dark:text-red-300 px-3 py-2 bg-red-50 dark:bg-red-900/20 rounded"
      >
        <span>{message}</span>
        <button
          type="button"
          onClick={() => { setLoadError(null); setRetryNonce((n) => n + 1); }}
          className="shrink-0 px-2 py-0.5 rounded border border-red-300 dark:border-red-700 hover:bg-red-100 dark:hover:bg-red-900/40 focus:outline-none focus-visible:ring-2 focus-visible:ring-accent-500"
        >
          {t('knowledgeGraph.graphRetry', 'Retry')}
        </button>
      </div>
    );
  }

  return (
    <div className="relative w-full" style={{ height: '70vh', minHeight: 480 }}>
      <div
        ref={rootRef}
        className="absolute inset-0 rounded-lg overflow-hidden"
        style={{ background: isDark ? '#0a0f1c' : '#eee9df' }}
      />
      <div ref={labelsRef} className="pointer-events-none absolute inset-0" />

      <SearchOverlay onPick={(eid) => setFocus(eid)} />

      {focusId && (
        <button
          type="button"
          onClick={() => setFocus(null)}
          className="absolute top-3 right-3 text-[11px] px-2 py-1 rounded
            bg-black/50 text-white/80 hover:bg-black/70 hover:text-white"
          title={t('knowledgeGraph.graph.backToCorpus', 'Back to corpus view')}
        >
          {t('knowledgeGraph.graph.backToCorpus', '← Corpus')}
        </button>
      )}

      <div className="pointer-events-none absolute left-3 bottom-3 text-[10px] text-gray-500 bg-black/40 px-2 py-1 rounded">
        {t('knowledgeGraph.graph.hint', 'Drag to orbit · scroll to zoom · click a hub to focus')}
      </div>

      {corpus && !focusId && (
        <div className="pointer-events-none absolute right-3 bottom-3 text-[10px] text-gray-500 bg-black/40 px-2 py-1 rounded">
          {corpus.total_entities} entities · {corpus.clusters.length} clusters
          {corpus.truncated && ' · truncated'}
        </div>
      )}
      {focusData && (
        <div className="pointer-events-none absolute right-3 bottom-3 text-[10px] text-gray-500 bg-black/40 px-2 py-1 rounded">
          {focusData.focus.display_name} · {focusData.hop1.length} hop1 · {focusData.hop2.length} hop2
        </div>
      )}
    </div>
  );
}

// =========================================================================
// Scene builders
// =========================================================================

type Labeled = {
  object: THREE.Object3D;
  name: string;
  sub?: string;
  yOffset: number;
  tier: 'primary' | 'secondary';
  entityId?: string;
  /** Exempt from distance culling (focus-mode hop1 — the point of the view). */
  alwaysLabel?: boolean;
  /** Small-cluster primary label — hidden until hovered so the overview isn't
   *  a wall of 17 overlapping captions. */
  minor?: boolean;
};

type EdgeMap = Map<string, THREE.Line[]>;

interface SceneTheme {
  isDark: boolean;
  bg: number;
  emissive: number;
  hubEmissive: number;
  haloOpacity: number;
  spokeOpacity: number;
}

function registerEdge(edgesByEntity: EdgeMap, entityId: string, line: THREE.Line) {
  const list = edgesByEntity.get(entityId);
  if (list) list.push(line);
  else edgesByEntity.set(entityId, [line]);
}

function makeEdgeLine(
  from: THREE.Vector3,
  to: THREE.Vector3,
  opacity: number,
): THREE.Line {
  const geom = new THREE.BufferGeometry().setFromPoints([from.clone(), to.clone()]);
  const line = new THREE.Line(
    geom,
    new THREE.LineBasicMaterial({ color: EDGE_COLOR, transparent: true, opacity }),
  );
  line.userData = { baseOpacity: opacity };
  return line;
}

function buildCorpusScene(
  scene: THREE.Scene,
  data: GraphResponse,
  labeled: Labeled[],
  clickable: Array<{ mesh: THREE.Object3D; entityId: string }>,
  edgesByEntity: EdgeMap,
  tierName: (tier: number | undefined) => string,
  theme: SceneTheme,
) {
  const N = data.clusters.length;
  const maxMention = Math.max(
    1,
    ...data.clusters.flatMap((c) => c.hubs.map((h) => h.mention_count)),
  );

  void edgesByEntity; // corpus mode draws no hover-edges (see hub studs below)

  data.clusters.forEach((c, i) => {
    // Volumetric placement: golden-angle direction plus a wide, layered
    // shell radius so the orbs — and their labels — spread through the room
    // instead of piling up. Importance rank (i) keeps the layout stable
    // across loads (backend orders clusters deterministically).
    const dir = fibonacciDirection(i, N);
    const shellR = N <= 1 ? 0 : 15 + (i % 3) * 5.5 + i * 0.35;
    const pos = dir.multiplyScalar(shellR);
    const accent = shellColor(c.color_seed);
    // The core IS the cluster — a solid, bright, weight-sized orb (the old
    // near-transparent body fogged everything into invisibility).
    const coreR = Math.max(1.0, Math.min(4.2, 0.9 + Math.sqrt(c.entity_count) * 0.42));

    const group = new THREE.Group();
    group.position.copy(pos);
    group.userData = { cluster: c };

    // Faint spoke from the centre → depth cue + constellation structure.
    scene.add(makeEdgeLine(new THREE.Vector3(0, 0, 0), pos, theme.spokeOpacity));

    const core = new THREE.Mesh(
      new THREE.SphereGeometry(coreR, 32, 24),
      new THREE.MeshStandardMaterial({
        color: accent, emissive: accent, emissiveIntensity: theme.emissive, roughness: 0.35, metalness: 0.1,
      }),
    );
    group.add(core);
    // Thin wireframe halo — a hint of extent without a fogging solid body.
    group.add(new THREE.Mesh(
      new THREE.SphereGeometry(coreR * 1.4, 24, 16),
      new THREE.MeshBasicMaterial({ color: accent, wireframe: true, transparent: true, opacity: theme.haloOpacity }),
    ));

    // Click anywhere on the orb → focus its namesake. Loose-ends has no
    // namesake, so it stays non-clickable (its hub studs remain clickable).
    if (c.namesake_entity_id) {
      core.userData = { entityId: c.namesake_entity_id };
      clickable.push({ mesh: core, entityId: c.namesake_entity_id });
    }

    // Hubs as bright, tier-coloured studs sitting ON the orb surface — the
    // cluster reads as a glowing sphere flecked with its top entities.
    c.hubs.forEach((hub, hi) => {
      const hubPos = fibonacciDirection(hi, c.hubs.length).multiplyScalar(coreR * 1.02);
      const color = tierColor(hub.circle_tier);
      const size = 0.28 + 0.5 * Math.sqrt(hub.mention_count / maxMention);
      const hubMesh = new THREE.Mesh(
        new THREE.SphereGeometry(size, 14, 14),
        new THREE.MeshStandardMaterial({ color, emissive: color, emissiveIntensity: theme.hubEmissive, roughness: 0.3 }),
      );
      hubMesh.position.copy(hubPos);
      hubMesh.userData = { entityId: hub.entity_id, hub };
      group.add(hubMesh);
      clickable.push({ mesh: hubMesh, entityId: hub.entity_id });
      labeled.push({
        object: hubMesh,
        name: hub.name,
        sub: `${hub.entity_type} · ${tierName(hub.circle_tier)}`,
        yOffset: size + 0.5,
        tier: 'secondary',
        entityId: hub.entity_id,
      });
    });

    scene.add(group);
    labeled.push({
      object: group,
      name: c.label,
      sub: c.sub_label,
      yOffset: coreR + 1.6,
      tier: 'primary',
      entityId: c.namesake_entity_id || undefined,
      // Only the sizeable clusters stay labelled in the overview; small ones
      // reveal their caption on hover (the orb itself is always visible).
      minor: c.entity_count < CLUSTER_LABEL_MIN_ENTITIES,
    });
  });
}

function buildFocusScene(
  scene: THREE.Scene,
  data: FocusNeighborhood,
  labeled: Labeled[],
  clickable: Array<{ mesh: THREE.Object3D; entityId: string }>,
  edgesByEntity: EdgeMap,
  tierName: (tier: number | undefined) => string,
  theme: SceneTheme,
) {
  const positions = new Map<string, THREE.Vector3>();
  const maxImportance = Math.max(
    1,
    ...data.hop1.map((e) => e.importance),
    ...data.hop2.map((e) => e.importance),
  );

  // Focus entity — large central emissive sphere in its tier colour.
  const focusColor = tierColor(data.focus.circle_tier);
  const focusMesh = new THREE.Mesh(
    new THREE.SphereGeometry(1.4, 32, 32),
    new THREE.MeshStandardMaterial({
      color: focusColor, emissive: focusColor, emissiveIntensity: theme.emissive * 0.7, roughness: 0.3,
    }),
  );
  focusMesh.userData = { entityId: data.focus.entity_id, focusEntity: data.focus };
  scene.add(focusMesh);
  positions.set(data.focus.entity_id, new THREE.Vector3(0, 0, 0));
  // Atmospheric shell.
  scene.add(new THREE.Mesh(
    new THREE.SphereGeometry(2.5, 32, 32),
    new THREE.MeshBasicMaterial({
      color: focusColor, transparent: true, opacity: 0.08, depthWrite: false,
    }),
  ));
  labeled.push({
    object: focusMesh,
    name: data.focus.display_name,
    sub: `${data.focus.entity_type} · ${tierName(data.focus.circle_tier)}`,
    yOffset: 3.2,
    tier: 'primary',
    // The focus label is the entity you're already on — no need to
    // re-focus on click; leaving entityId unset keeps it
    // click-transparent so the user can drag-orbit through the centre.
  });

  const placeShell = (
    entities: FocusEntity[], R: number, baseSize: number, alwaysLabel = false,
  ) => {
    entities.forEach((e, i) => {
      const posV = fibonacciDirection(i, entities.length).multiplyScalar(R);
      const color = tierColor(e.circle_tier);
      const size = baseSize + 0.22 * Math.sqrt(e.importance / maxImportance);
      const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(size, 12, 12),
        new THREE.MeshStandardMaterial({
          color, emissive: color, emissiveIntensity: theme.hubEmissive * 0.6, roughness: 0.35,
        }),
      );
      mesh.position.copy(posV);
      mesh.userData = { entityId: e.entity_id, entity: e };
      scene.add(mesh);
      positions.set(e.entity_id, posV);
      clickable.push({ mesh, entityId: e.entity_id });
      labeled.push({
        object: mesh,
        name: e.display_name,
        sub: `${e.entity_type} · ${tierName(e.circle_tier)}`,
        yOffset: size + 0.4,
        tier: 'secondary',
        entityId: e.entity_id,
        alwaysLabel,
      });
    });
  };

  // Two concentric Fibonacci shells — hop1 close, hop2 far. Both truly
  // volumetric (the old hop1 ring was flat: y ≤ ±0.6 at radius 7).
  placeShell(data.hop1, 7, 0.3, true);
  placeShell(data.hop2, 13, 0.22);

  // REAL relation edges from the backend — focus↔hop1, hop1↔hop1,
  // hop1↔hop2 all render (the old scene drew only synthetic focus
  // spokes and threw the edge list away).
  const focusId = data.focus.entity_id;
  for (const edge of data.edges ?? []) {
    const from = positions.get(edge.from_entity);
    const to = positions.get(edge.to_entity);
    if (!from || !to) continue;
    const touchesFocus = edge.from_entity === focusId || edge.to_entity === focusId;
    const line = makeEdgeLine(from, to, touchesFocus ? 0.45 : 0.2);
    scene.add(line);
    registerEdge(edgesByEntity, edge.from_entity, line);
    registerEdge(edgesByEntity, edge.to_entity, line);
  }

  // Wireframe outer-shell hint (the "2 HOPS" boundary from the A4 mock).
  scene.add(new THREE.Mesh(
    new THREE.SphereGeometry(13, 32, 16),
    new THREE.MeshBasicMaterial({
      color: 0x475569, wireframe: true, transparent: true, opacity: 0.08,
    }),
  ));
}

// =========================================================================
// Search overlay
// =========================================================================

function SearchOverlay({ onPick }: { onPick: (entityId: string) => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const debouncedQ = useDebounced(q, 180);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    let cancelled = false;
    if (!debouncedQ.trim()) {
      setHits([]);
      return;
    }
    apiClient.get<{ items: SearchHit[] }>('/api/wissensbasis/search', {
      params: { q: debouncedQ },
    })
      .then(res => { if (!cancelled) setHits(res.data.items || []); })
      .catch(() => { if (!cancelled) setHits([]); });
    return () => { cancelled = true; };
  }, [debouncedQ]);

  useEffect(() => { setActiveIndex(0); }, [hits.length]);

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown' && hits.length > 0) {
      e.preventDefault();
      setActiveIndex(i => Math.min(i + 1, hits.length - 1));
    } else if (e.key === 'ArrowUp' && hits.length > 0) {
      e.preventDefault();
      setActiveIndex(i => Math.max(i - 1, 0));
    } else if (e.key === 'Enter' && hits.length > 0) {
      e.preventDefault();
      onPick(hits[activeIndex].entity_id);
      setOpen(false);
      setQ('');
    } else if (e.key === 'Escape') {
      setOpen(false);
      setQ('');
    }
  }

  return (
    <div className="absolute top-3 left-3 z-10">
      {open ? (
        <div className="w-72 bg-black/70 backdrop-blur-sm rounded-lg shadow-lg p-2">
          <input
            ref={inputRef}
            type="text"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={t('knowledgeGraph.graph.searchPlaceholder', 'Find entity…')}
            className="w-full bg-transparent text-white text-sm outline-none px-2 py-1
              border-b border-white/20 focus:border-white/50"
            autoComplete="off"
            spellCheck={false}
          />
          {hits.length > 0 && (
            <ul role="listbox" className="mt-1.5 max-h-72 overflow-y-auto">
              {hits.map((hit, i) => (
                <li key={hit.entity_id}>
                  <button
                    type="button"
                    onClick={() => { onPick(hit.entity_id); setOpen(false); setQ(''); }}
                    onMouseEnter={() => setActiveIndex(i)}
                    className={`w-full text-left rounded px-2 py-1.5 text-xs transition-colors
                      ${i === activeIndex ? 'bg-white/15' : 'hover:bg-white/10'}`}
                  >
                    <p className="text-white truncate">{hit.display_name}</p>
                    <p className="text-[10px] text-gray-400 mt-0.5">
                      {hit.entity_type}
                      {hit.mention_count > 0 && ` · ${hit.mention_count} mentions`}
                    </p>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {debouncedQ && hits.length === 0 && (
            <p className="text-[11px] text-gray-400 italic px-2 py-1.5">
              {t('knowledgeGraph.graph.searchNoResults', 'Nothing found')}
            </p>
          )}
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="bg-black/50 hover:bg-black/70 text-white/80 hover:text-white
            px-3 py-1.5 rounded text-xs flex items-center gap-1.5"
          title={t('knowledgeGraph.graph.searchTitle', 'Search an entity')}
        >
          <span>🔍</span>
          <span>{t('knowledgeGraph.graph.searchButton', 'Find entity')}</span>
        </button>
      )}
    </div>
  );
}
