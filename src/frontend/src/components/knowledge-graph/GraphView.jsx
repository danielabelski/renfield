import { useState, useEffect, useRef, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import ForceGraph2D from 'react-force-graph-2d';
import apiClient from '../../utils/axios';

const TYPE_COLORS = {
  person: '#3b82f6',
  place: '#22c55e',
  organization: '#a855f7',
  thing: '#f59e0b',
  event: '#ec4899',
  concept: '#14b8a6',
};

const TYPE_COLORS_DARK = {
  person: '#60a5fa',
  place: '#4ade80',
  organization: '#c084fc',
  thing: '#fbbf24',
  event: '#f472b6',
  concept: '#2dd4bf',
};

const TYPE_LABELS = {
  person: 'Person',
  place: 'Ort',
  organization: 'Organisation',
  thing: 'Objekt',
  event: 'Ereignis',
  concept: 'Konzept',
};

const MAX_NODES = 200;
const LABEL_ZOOM_THRESHOLD = 1.2;

export default function GraphView({ onEntityClick, onSwitchToEntities, isDark }) {
  const { t } = useTranslation();
  const graphRef = useRef();
  const containerRef = useRef();
  const [graphData, setGraphData] = useState({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const [hoveredNode, setHoveredNode] = useState(null);
  const [wsConnected, setWsConnected] = useState(false);
  const wsRef = useRef(null);
  const entityMapRef = useRef(new Map());

  const colors = isDark ? TYPE_COLORS_DARK : TYPE_COLORS;

  // Measure container size — also re-measure when loading finishes
  useEffect(() => {
    if (!containerRef.current) return;

    const measure = () => {
      if (!containerRef.current) return;
      const { width, height } = containerRef.current.getBoundingClientRect();
      if (width > 0 && height > 0) {
        setDimensions({ width: Math.floor(width), height: Math.floor(height) });
      }
    };

    // Measure immediately
    measure();

    const observer = new ResizeObserver(() => measure());
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [loading]);

  // Fetch initial data
  useEffect(() => {
    const fetchData = async () => {
      try {
        setError(null);
        const [entitiesRes, relationsRes] = await Promise.all([
          apiClient.get('/api/knowledge-graph/entities', { params: { size: MAX_NODES } }),
          apiClient.get('/api/knowledge-graph/relations', { params: { size: 200 } }),
        ]);

        const entities = entitiesRes.data.entities || [];
        const relations = relationsRes.data.relations || [];

        const entityMap = new Map();
        const nodes = entities.map((e) => {
          const node = {
            id: e.id,
            name: e.name,
            type: e.entity_type,
            mentionCount: e.mention_count || 1,
            val: Math.max(2, Math.min(10, (e.mention_count || 1))),
          };
          entityMap.set(e.id, node);
          return node;
        });

        const links = relations
          .filter((r) => {
            const sId = r.subject_id ?? r.subject?.id;
            const oId = r.object_id ?? r.object?.id;
            return sId && oId && entityMap.has(sId) && entityMap.has(oId);
          })
          .map((r) => ({
            source: r.subject_id ?? r.subject?.id,
            target: r.object_id ?? r.object?.id,
            label: r.predicate,
            confidence: r.confidence,
          }));

        entityMapRef.current = entityMap;
        setGraphData({ nodes, links });

        // Center graph after data loads
        setTimeout(() => {
          graphRef.current?.zoomToFit(400, 40);
        }, 500);
      } catch (err) {
        console.error('Failed to load KG graph data:', err);
        setError(t('knowledgeGraph.graphError', 'Graph konnte nicht geladen werden.'));
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [t]);

  // WebSocket for live updates
  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/knowledge-graph`;
    let reconnectTimer;

    const connect = () => {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => setWsConnected(true);

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'kg_update') {
            handleKgUpdate(data.entities || [], data.relations || []);
          }
        } catch (err) {
          console.error('KG WS message parse error:', err);
        }
      };

      ws.onclose = () => {
        wsRef.current = null;
        setWsConnected(false);
        reconnectTimer = setTimeout(connect, 5000);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  const handleKgUpdate = useCallback((newEntities, newRelations) => {
    setGraphData((prev) => {
      const entityMap = entityMapRef.current;
      const nodes = [...prev.nodes];
      const links = [...prev.links];

      for (const e of newEntities) {
        if (!entityMap.has(e.id)) {
          const node = {
            id: e.id,
            name: e.name,
            type: e.type,
            mentionCount: e.mention_count || 1,
            val: Math.max(2, Math.min(10, (e.mention_count || 1))),
            _isNew: true,
          };
          entityMap.set(e.id, node);
          nodes.push(node);
        } else {
          const existing = entityMap.get(e.id);
          if (existing && e.mention_count) {
            existing.mentionCount = e.mention_count;
            existing.val = Math.max(2, Math.min(10, e.mention_count));
          }
        }
      }

      for (const r of newRelations) {
        if (entityMap.has(r.subject_id) && entityMap.has(r.object_id)) {
          links.push({
            source: r.subject_id,
            target: r.object_id,
            label: r.predicate,
            confidence: r.confidence,
          });
        }
      }

      // FIFO eviction if over max
      if (nodes.length > MAX_NODES) {
        const toRemove = nodes.splice(0, nodes.length - MAX_NODES);
        const removeIds = new Set(toRemove.map((n) => n.id));
        for (const id of removeIds) {
          entityMap.delete(id);
        }
        const filtered = links.filter(
          (l) => !removeIds.has(l.source?.id ?? l.source) && !removeIds.has(l.target?.id ?? l.target)
        );
        return { nodes, links: filtered };
      }

      return { nodes, links };
    });
  }, []);

  const nodeCanvasObject = useCallback((node, ctx, globalScale) => {
    const radius = Math.max(4, node.val * 1.5);
    const color = colors[node.type] || colors.thing;
    const isHovered = hoveredNode?.id === node.id;

    // Node circle
    ctx.beginPath();
    ctx.arc(node.x, node.y, isHovered ? radius * 1.3 : radius, 0, 2 * Math.PI);
    ctx.fillStyle = color;
    ctx.fill();

    // Glow for new or hovered nodes
    if (node._isNew || isHovered) {
      ctx.shadowColor = color;
      ctx.shadowBlur = isHovered ? 20 : 15;
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI);
      ctx.fill();
      ctx.shadowBlur = 0;
      if (node._isNew) {
        setTimeout(() => { node._isNew = false; }, 3000);
      }
    }

    // Label: show only when zoomed in enough, or when hovered
    if (globalScale > LABEL_ZOOM_THRESHOLD || isHovered) {
      const fontSize = isHovered ? Math.max(12, 14 / globalScale) : Math.max(9, 11 / globalScale);
      ctx.font = `${isHovered ? 'bold ' : ''}${fontSize}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = isDark ? '#e5e7eb' : '#1f2937';
      ctx.fillText(node.name, node.x, node.y + radius + fontSize);
    }
  }, [colors, isDark, hoveredNode]);

  const linkCanvasObject = useCallback((link, ctx, globalScale) => {
    const start = link.source;
    const end = link.target;
    if (!start || !end || typeof start.x === 'undefined') return;

    ctx.beginPath();
    ctx.moveTo(start.x, start.y);
    ctx.lineTo(end.x, end.y);
    ctx.strokeStyle = isDark ? 'rgba(148, 163, 184, 0.3)' : 'rgba(107, 114, 128, 0.3)';
    ctx.lineWidth = 0.5;
    ctx.stroke();

    if (link.label && globalScale > 1.5) {
      const midX = (start.x + end.x) / 2;
      const midY = (start.y + end.y) / 2;
      const fontSize = Math.max(8, 10 / globalScale);
      ctx.font = `${fontSize}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = isDark ? 'rgba(148, 163, 184, 0.5)' : 'rgba(107, 114, 128, 0.5)';
      ctx.fillText(link.label, midX, midY);
    }
  }, [isDark]);

  const handleNodeClick = useCallback((node) => {
    if (onEntityClick) {
      onEntityClick(node.id);
    }
  }, [onEntityClick]);

  const handleRetry = () => {
    setLoading(true);
    setError(null);
    // Re-trigger fetch by remounting
    setGraphData({ nodes: [], links: [] });
    entityMapRef.current = new Map();
  };

  // Retry triggers re-fetch
  useEffect(() => {
    if (loading && !error && graphData.nodes.length === 0 && entityMapRef.current.size === 0) {
      const fetchData = async () => {
        try {
          const [entitiesRes, relationsRes] = await Promise.all([
            apiClient.get('/api/knowledge-graph/entities', { params: { size: MAX_NODES } }),
            apiClient.get('/api/knowledge-graph/relations', { params: { size: 200 } }),
          ]);
          const entities = entitiesRes.data.entities || [];
          const relations = relationsRes.data.relations || [];
          const entityMap = new Map();
          const nodes = entities.map((e) => {
            const node = { id: e.id, name: e.name, type: e.entity_type, mentionCount: e.mention_count || 1, val: Math.max(2, Math.min(10, (e.mention_count || 1))) };
            entityMap.set(e.id, node);
            return node;
          });
          const links = relations.filter((r) => {
            const sId = r.subject_id ?? r.subject?.id;
            const oId = r.object_id ?? r.object?.id;
            return sId && oId && entityMap.has(sId) && entityMap.has(oId);
          }).map((r) => ({ source: r.subject_id ?? r.subject?.id, target: r.object_id ?? r.object?.id, label: r.predicate, confidence: r.confidence }));
          entityMapRef.current = entityMap;
          setGraphData({ nodes, links });
          setTimeout(() => { graphRef.current?.zoomToFit(400, 40); }, 500);
        } catch (err) {
          setError(t('knowledgeGraph.graphError', 'Graph konnte nicht geladen werden.'));
        } finally {
          setLoading(false);
        }
      };
      // Only run on retry (skip initial mount, handled by first useEffect)
      if (entityMapRef.current.size === 0 && !loading) fetchData();
    }
  }, [loading, error, graphData.nodes.length, t]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-500" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-96 gap-4">
        <p className="text-gray-500 dark:text-gray-400">{error}</p>
        <button onClick={handleRetry} className="btn-primary px-4 py-2 text-sm">
          {t('common.retry', 'Erneut versuchen')}
        </button>
      </div>
    );
  }

  if (graphData.nodes.length === 0) {
    return (
      <div className="flex items-center justify-center h-96 text-gray-500 dark:text-gray-400">
        {t('knowledgeGraph.noEntities', 'Keine Entitäten vorhanden. Starte eine Unterhaltung, um den Wissensgraph aufzubauen.')}
      </div>
    );
  }

  return (
    <div ref={containerRef} className="relative w-full h-[calc(100vh-280px)] min-h-[400px] overflow-hidden" aria-label={t('knowledgeGraph.graphAriaLabel', 'Wissensgraph Visualisierung')}>
      <ForceGraph2D
        ref={graphRef}
        graphData={graphData}
        width={dimensions.width}
        height={dimensions.height}
        nodeCanvasObject={nodeCanvasObject}
        linkCanvasObject={linkCanvasObject}
        onNodeClick={handleNodeClick}
        onNodeHover={setHoveredNode}
        nodeId="id"
        linkSource="source"
        linkTarget="target"
        cooldownTicks={100}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
        backgroundColor={isDark ? '#0f1117' : '#f8f7f5'}
        enableNodeDrag={true}
        enableZoomPanInteraction={true}
      />

      {/* Hover tooltip */}
      {hoveredNode && (
        <div className="absolute top-3 left-3 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2 shadow-lg text-sm pointer-events-none">
          <p className="font-medium text-gray-900 dark:text-white">{hoveredNode.name}</p>
          <p className="text-gray-500 dark:text-gray-400">
            {TYPE_LABELS[hoveredNode.type] || hoveredNode.type} · {hoveredNode.mentionCount}x {t('knowledgeGraph.mentions', 'erwähnt')}
          </p>
        </div>
      )}

      {/* Color legend */}
      <div className="absolute bottom-10 left-3 flex flex-wrap gap-2 text-xs">
        {Object.entries(TYPE_LABELS).map(([type, label]) => (
          <span key={type} className="flex items-center gap-1 text-gray-500 dark:text-gray-400">
            <span className="w-2.5 h-2.5 rounded-full inline-block" style={{ backgroundColor: colors[type] }} />
            {label}
          </span>
        ))}
      </div>

      {/* Status bar */}
      <div className="absolute bottom-2 right-3 flex items-center gap-2 text-xs text-gray-400 dark:text-gray-600">
        <span className={`w-1.5 h-1.5 rounded-full ${wsConnected ? 'bg-green-400' : 'bg-gray-400'}`} />
        {graphData.nodes.length} {t('knowledgeGraph.entities', 'Entitäten')} · {graphData.links.length} {t('knowledgeGraph.relations', 'Relationen')}
      </div>

      {/* Accessibility: table view hint on small screens */}
      <div className="absolute bottom-2 left-3 text-xs text-gray-400 dark:text-gray-600 sm:hidden">
        {onSwitchToEntities && (
          <button onClick={onSwitchToEntities} className="underline hover:text-gray-600 dark:hover:text-gray-400">
            {t('knowledgeGraph.switchToTable', 'Tabellenansicht')}
          </button>
        )}
      </div>
    </div>
  );
}
