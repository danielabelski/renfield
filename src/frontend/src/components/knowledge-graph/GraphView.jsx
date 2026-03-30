import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
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

const MAX_NODES = 200;

export default function GraphView({ onEntityClick, isDark }) {
  const { t } = useTranslation();
  const graphRef = useRef();
  const containerRef = useRef();
  const [graphData, setGraphData] = useState({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const wsRef = useRef(null);
  const entityMapRef = useRef(new Map());

  const colors = isDark ? TYPE_COLORS_DARK : TYPE_COLORS;

  // Measure container size
  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const { width, height } = entry.contentRect;
        setDimensions({ width: Math.max(400, width), height: Math.max(300, height) });
      }
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  // Fetch initial data
  useEffect(() => {
    const fetchData = async () => {
      try {
        const [entitiesRes, relationsRes] = await Promise.all([
          apiClient.get('/api/knowledge-graph/entities', { params: { size: MAX_NODES } }),
          apiClient.get('/api/knowledge-graph/relations', { params: { size: 500 } }),
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
          .filter((r) => entityMap.has(r.subject_id) && entityMap.has(r.object_id))
          .map((r) => ({
            source: r.subject_id,
            target: r.object_id,
            label: r.predicate,
            confidence: r.confidence,
          }));

        entityMapRef.current = entityMap;
        setGraphData({ nodes, links });
      } catch (err) {
        console.error('Failed to load KG graph data:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  // WebSocket for live updates
  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/knowledge-graph`;
    let reconnectTimer;

    const connect = () => {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

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
          // Update existing node mention count
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
        // Remove links referencing removed nodes
        const filtered = links.filter(
          (l) => !removeIds.has(l.source?.id ?? l.source) && !removeIds.has(l.target?.id ?? l.target)
        );
        return { nodes, links: filtered };
      }

      return { nodes, links };
    });
  }, []);

  const nodeCanvasObject = useCallback((node, ctx, globalScale) => {
    const label = node.name;
    const fontSize = Math.max(10, 12 / globalScale);
    const radius = Math.max(4, node.val * 1.5);
    const color = colors[node.type] || colors.thing;

    // Node circle
    ctx.beginPath();
    ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI);
    ctx.fillStyle = color;
    ctx.fill();

    // Glow for new nodes
    if (node._isNew) {
      ctx.shadowColor = color;
      ctx.shadowBlur = 15;
      ctx.beginPath();
      ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI);
      ctx.fill();
      ctx.shadowBlur = 0;
      // Clear the new flag after a few renders
      setTimeout(() => { node._isNew = false; }, 3000);
    }

    // Label
    ctx.font = `${fontSize}px sans-serif`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = isDark ? '#e5e7eb' : '#1f2937';
    ctx.fillText(label, node.x, node.y + radius + fontSize);
  }, [colors, isDark]);

  const linkCanvasObject = useCallback((link, ctx, globalScale) => {
    const start = link.source;
    const end = link.target;
    if (!start || !end || typeof start.x === 'undefined') return;

    // Line
    ctx.beginPath();
    ctx.moveTo(start.x, start.y);
    ctx.lineTo(end.x, end.y);
    ctx.strokeStyle = isDark ? 'rgba(148, 163, 184, 0.3)' : 'rgba(107, 114, 128, 0.3)';
    ctx.lineWidth = 0.5;
    ctx.stroke();

    // Label at midpoint
    if (link.label && globalScale > 0.5) {
      const midX = (start.x + end.x) / 2;
      const midY = (start.y + end.y) / 2;
      const fontSize = Math.max(8, 10 / globalScale);
      ctx.font = `${fontSize}px sans-serif`;
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillStyle = isDark ? 'rgba(148, 163, 184, 0.6)' : 'rgba(107, 114, 128, 0.6)';
      ctx.fillText(link.label, midX, midY);
    }
  }, [isDark]);

  const handleNodeClick = useCallback((node) => {
    if (onEntityClick) {
      onEntityClick(node.id);
    }
  }, [onEntityClick]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary-500" />
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
    <div ref={containerRef} className="w-full h-[calc(100vh-280px)] min-h-[400px] rounded-lg overflow-hidden bg-gray-50 dark:bg-gray-900">
      <ForceGraph2D
        ref={graphRef}
        graphData={graphData}
        width={dimensions.width}
        height={dimensions.height}
        nodeCanvasObject={nodeCanvasObject}
        linkCanvasObject={linkCanvasObject}
        onNodeClick={handleNodeClick}
        nodeId="id"
        linkSource="source"
        linkTarget="target"
        cooldownTicks={100}
        d3AlphaDecay={0.02}
        d3VelocityDecay={0.3}
        backgroundColor={isDark ? '#111827' : '#f9fafb'}
        enableNodeDrag={true}
        enableZoomPanInteraction={true}
      />
      <div className="absolute bottom-2 right-2 text-xs text-gray-400 dark:text-gray-600">
        {graphData.nodes.length} {t('knowledgeGraph.entities', 'Entitäten')} · {graphData.links.length} {t('knowledgeGraph.relations', 'Relationen')}
      </div>
    </div>
  );
}
