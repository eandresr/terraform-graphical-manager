/**
 * Terraform Graphical Manager — D3.js Dependency Graph Renderer
 *
 * Renders a force-directed graph from { nodes: [...], links: [...] } data
 * produced by `terraform graph` (DOT format parsed server-side).
 *
 * Usage:
 *   window.TGMGraph.render('graph-svg', graphData);
 *   window.TGMGraph.resetZoom('graph-svg');
 */

'use strict';

(function (global) {

  // ── Node colour by type ──────────────────────────────────────────────
  const NODE_COLOR = {
    data:     '#3b82f6',   // blue  — data sources
    module:   '#8b5cf6',   // purple — modules
    var:      '#64748b',   // slate  — variables / meta
    local:    '#64748b',
    output:   '#0891b2',   // cyan   — outputs
    default:  '#5c4ee5',   // tf purple — managed resources
  };

  const LINK_COLOR  = '#1e293b';
  const ARROW_COLOR = '#334155';

  let _simulation = null;
  let _zoom = null;
  let _svg = null;


  // ────────────────────────────────────────────────────────────────────
  // Public: render
  // ────────────────────────────────────────────────────────────────────

  function render(containerId, data) {
    const el = document.getElementById(containerId);
    if (!el || !data) return;

    // Clear previous render
    el.innerHTML = '';
    if (_simulation) _simulation.stop();

    const W = el.clientWidth  || el.parentElement.clientWidth  || 900;
    const H = el.clientHeight || el.parentElement.clientHeight || 600;

    _svg = d3.select(`#${containerId}`);

    // Arrow marker
    const defs = _svg.append('defs');
    defs.append('marker')
        .attr('id', 'arrow')
        .attr('viewBox', '0 -4 8 8')
        .attr('refX', 18)
        .attr('refY', 0)
        .attr('markerWidth', 6)
        .attr('markerHeight', 6)
        .attr('orient', 'auto')
        .append('path')
        .attr('d', 'M0,-4L8,0L0,4')
        .attr('fill', ARROW_COLOR);

    // Zoom container
    const g = _svg.append('g').attr('class', 'canvas');

    _zoom = d3.zoom()
      .scaleExtent([0.1, 5])
      .on('zoom', (event) => g.attr('transform', event.transform));

    _svg.call(_zoom);

    // Deep-copy nodes & links so D3 can mutate them
    const nodes = data.nodes.map(n => ({ ...n }));
    const links = data.links.map(l => ({
      source: l.source,
      target: l.target,
    }));

    // ── Links ──────────────────────────────────────────────────────────
    const link = g.append('g')
      .attr('class', 'links')
      .selectAll('line')
      .data(links)
      .enter()
      .append('line')
      .attr('class', 'link')
      .attr('stroke', LINK_COLOR)
      .attr('stroke-width', 1.5)
      .attr('marker-end', 'url(#arrow)');

    // ── Nodes ──────────────────────────────────────────────────────────
    const node = g.append('g')
      .attr('class', 'nodes')
      .selectAll('.node')
      .data(nodes)
      .enter()
      .append('g')
      .attr('class', 'node')
      .call(
        d3.drag()
          .on('start', dragStart)
          .on('drag',  dragged)
          .on('end',   dragEnd)
      )
      .on('click', (event, d) => showNodeTooltip(event, d))
      .on('mouseleave', hideTooltip);

    // Node circle
    node.append('circle')
      .attr('r', d => nodeRadius(d))
      .attr('fill', d => nodeColor(d))
      .attr('stroke', '#0f172a')
      .attr('stroke-width', 2);

    // Node label
    node.append('text')
      .attr('dy', d => nodeRadius(d) + 12)
      .attr('text-anchor', 'middle')
      .style('font-family', "'JetBrains Mono', monospace")
      .style('font-size', '9px')
      .style('fill', '#94a3b8')
      .text(d => shortenLabel(d.label || d.id));

    // ── Force simulation ───────────────────────────────────────────────
    _simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id(d => d.id).distance(120).strength(0.5))
      .force('charge', d3.forceManyBody().strength(-250))
      .force('center', d3.forceCenter(W / 2, H / 2))
      .force('collide', d3.forceCollide().radius(d => nodeRadius(d) + 20))
      .on('tick', () => {
        link
          .attr('x1', d => d.source.x)
          .attr('y1', d => d.source.y)
          .attr('x2', d => d.target.x)
          .attr('y2', d => d.target.y);

        node.attr('transform', d => `translate(${d.x},${d.y})`);
      });

    // Auto-fit after simulation settles
    _simulation.on('end', () => autoFit(g, W, H));
  }


  // ────────────────────────────────────────────────────────────────────
  // Public: resetZoom
  // ────────────────────────────────────────────────────────────────────

  function resetZoom(containerId) {
    if (!_svg || !_zoom) return;
    _svg.transition().duration(400).call(_zoom.transform, d3.zoomIdentity);
  }


  // ────────────────────────────────────────────────────────────────────
  // Node helpers
  // ────────────────────────────────────────────────────────────────────

  function nodeRadius(d) {
    const label = (d.label || d.id || '').toLowerCase();
    if (label.includes('module')) return 14;
    if (label.startsWith('[root]') || label === 'root') return 12;
    return 9;
  }

  function nodeColor(d) {
    const label = (d.label || d.id || '').toLowerCase();
    if (label.startsWith('data.')) return NODE_COLOR.data;
    if (label.includes('module.')) return NODE_COLOR.module;
    if (label.startsWith('var.'))  return NODE_COLOR.var;
    if (label.startsWith('local.')) return NODE_COLOR.local;
    if (label.startsWith('output.')) return NODE_COLOR.output;
    if (label.includes('[root]'))  return '#94a3b8';
    return NODE_COLOR.default;
  }

  function shortenLabel(label) {
    // Remove [root] prefix, truncate long names
    let s = label.replace(/^\[root\]\s+/, '').replace(/^module\.\S+\./, '');
    return s.length > 28 ? s.slice(0, 26) + '…' : s;
  }


  // ────────────────────────────────────────────────────────────────────
  // Drag handlers
  // ────────────────────────────────────────────────────────────────────

  function dragStart(event, d) {
    if (!event.active) _simulation.alphaTarget(0.3).restart();
    d.fx = d.x;
    d.fy = d.y;
  }
  function dragged(event, d) {
    d.fx = event.x;
    d.fy = event.y;
  }
  function dragEnd(event, d) {
    if (!event.active) _simulation.alphaTarget(0);
    d.fx = null;
    d.fy = null;
  }


  // ────────────────────────────────────────────────────────────────────
  // Tooltip
  // ────────────────────────────────────────────────────────────────────

  function showNodeTooltip(event, d) {
    const tip = document.getElementById('graph-tooltip');
    if (!tip) return;
    tip.innerHTML = `<span class="font-bold text-white">${escapeHtml(d.label || d.id)}</span>`;
    tip.classList.remove('hidden');
    tip.classList.add('flex');
    positionTooltip(tip, event);
  }

  function hideTooltip() {
    const tip = document.getElementById('graph-tooltip');
    if (tip) { tip.classList.add('hidden'); tip.classList.remove('flex'); }
  }

  function positionTooltip(tip, event) {
    const x = event.clientX + 14;
    const y = event.clientY - 8;
    tip.style.left = Math.min(x, window.innerWidth  - tip.offsetWidth  - 10) + 'px';
    tip.style.top  = Math.min(y, window.innerHeight - tip.offsetHeight - 10) + 'px';
  }


  // ────────────────────────────────────────────────────────────────────
  // Auto-fit
  // ────────────────────────────────────────────────────────────────────

  function autoFit(g, W, H) {
    if (!_svg || !_zoom) return;
    const bounds = g.node().getBBox();
    if (!bounds.width || !bounds.height) return;

    const scale = 0.85 / Math.max(bounds.width / W, bounds.height / H);
    const tx = W / 2 - scale * (bounds.x + bounds.width  / 2);
    const ty = H / 2 - scale * (bounds.y + bounds.height / 2);

    _svg.transition().duration(600).call(
      _zoom.transform,
      d3.zoomIdentity.translate(tx, ty).scale(scale)
    );
  }


  // ────────────────────────────────────────────────────────────────────
  // Escape helper
  // ────────────────────────────────────────────────────────────────────

  function escapeHtml(str) {
    const d = document.createElement('div');
    d.appendChild(document.createTextNode(String(str)));
    return d.innerHTML;
  }


  // ────────────────────────────────────────────────────────────────────
  // Export
  // ────────────────────────────────────────────────────────────────────

  global.TGMGraph = { render, resetZoom };

})(window);
