/**
 * Unit tests for the Wissensgraph response-shape guards. These back the
 * crash-prevention fix: a rolling backend can serve the SPA index.html (an
 * HTML string) or a truncated/partial JSON for /api/wissensbasis/graph, and
 * GraphView dereferences `.clusters` / `c.hubs` straight off the body — so the
 * guards must reject anything that would crash the render or the scene builder.
 */
import { describe, it, expect } from 'vitest';
import {
  isGraphResponse,
  isFocusNeighborhood,
} from '../../../../src/frontend/src/components/knowledge-graph/graphResponseGuards';

describe('isGraphResponse', () => {
  it('accepts a well-formed corpus body (every cluster has hubs)', () => {
    expect(
      isGraphResponse({
        clusters: [{ id: 'c1', label: 'x', sub_label: '', entity_count: 1, hubs: [], color_seed: 0, namesake_entity_id: null }],
        total_entities: 1,
        total_relations: 0,
        truncated: false,
      }),
    ).toBe(true);
  });

  it('accepts an empty-clusters corpus', () => {
    expect(isGraphResponse({ clusters: [], total_entities: 0, total_relations: 0, truncated: false })).toBe(true);
  });

  it('rejects the SPA HTML fallback (a string body)', () => {
    expect(isGraphResponse('<!doctype html><html></html>')).toBe(false);
  });

  it('rejects null / undefined / non-object', () => {
    expect(isGraphResponse(null)).toBe(false);
    expect(isGraphResponse(undefined)).toBe(false);
    expect(isGraphResponse(42)).toBe(false);
  });

  it('rejects an object without a clusters array', () => {
    expect(isGraphResponse({ total_entities: 5 })).toBe(false);
    expect(isGraphResponse({ clusters: 'nope' })).toBe(false);
  });

  it('rejects a partial body whose cluster is missing hubs (truncated mid-write)', () => {
    // the exact case the shallow guard would have let through → scene builder crash
    expect(isGraphResponse({ clusters: [{ id: 'x' }], total_entities: 1, total_relations: 0, truncated: false })).toBe(false);
  });
});

describe('isFocusNeighborhood', () => {
  const ok = {
    focus: { entity_id: '1', display_name: 'A', entity_type: 'person', importance: 1 },
    hop1: [],
    hop2: [],
    edges: [],
    overflow_hop1: 0,
    overflow_hop2: 0,
  };

  it('accepts a well-formed focus body', () => {
    expect(isFocusNeighborhood(ok)).toBe(true);
  });

  it('rejects the SPA HTML fallback and non-objects', () => {
    expect(isFocusNeighborhood('<!doctype html>')).toBe(false);
    expect(isFocusNeighborhood(null)).toBe(false);
  });

  it('rejects a body missing focus or hop arrays', () => {
    expect(isFocusNeighborhood({ hop1: [], hop2: [] })).toBe(false);
    expect(isFocusNeighborhood({ focus: ok.focus, hop1: 'x', hop2: [] })).toBe(false);
    expect(isFocusNeighborhood({ focus: ok.focus, hop1: [] })).toBe(false);
  });
});
