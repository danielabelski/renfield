// Response-shape guards for the Wissensgraph endpoints, kept in their own
// module (no three.js import) so they are unit-testable in isolation.
//
// A rolling backend can briefly serve the SPA index.html for an /api path
// (backend momentarily unavailable → nginx/Traefik fallback), so axios can
// resolve with a NON-graph body (an HTML string) — or, mid-write, a partially
// shaped JSON. GraphView reads `corpus.clusters.length` in render and
// `c.hubs.map(...)` in the scene builder straight off the response, so an
// unvalidated body crashes the whole view into the error boundary. These
// guards let the caller route a bad body to the error state instead.

import type { FocusEdge, FocusNeighborhood } from '../../api/resources/wissensbasis';

export interface Hub {
  entity_id: string;
  name: string;
  entity_type: string;
  mention_count: number;
  circle_tier?: number;
}

export interface Cluster {
  id: string;
  label: string;
  sub_label: string;
  entity_count: number;
  hubs: Hub[];
  hub_edges?: FocusEdge[];
  color_seed: number;
  namesake_entity_id: string | null;
}

export interface GraphResponse {
  clusters: Cluster[];
  total_entities: number;
  total_relations: number;
  truncated: boolean;
}

/** True only for a corpus body whose `clusters` is an array AND every cluster
 *  carries the `hubs` array the scene builder dereferences — a partial body
 *  like `[{id:'x'}]` (truncated mid-deploy) is rejected. */
export function isGraphResponse(data: unknown): data is GraphResponse {
  if (!data || typeof data !== 'object') return false;
  const clusters = (data as GraphResponse).clusters;
  return (
    Array.isArray(clusters) &&
    clusters.every(
      (c) => !!c && typeof c === 'object' && Array.isArray((c as Cluster).hubs),
    )
  );
}

/** True only for a focus body with a `focus` entity and array `hop1`/`hop2`. */
export function isFocusNeighborhood(data: unknown): data is FocusNeighborhood {
  return (
    !!data &&
    typeof data === 'object' &&
    !!(data as FocusNeighborhood).focus &&
    Array.isArray((data as FocusNeighborhood).hop1) &&
    Array.isArray((data as FocusNeighborhood).hop2)
  );
}
