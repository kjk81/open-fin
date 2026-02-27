import { useState, useEffect, useRef, useCallback } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { fetchGraphNodes, fetchGraphEdges } from "../../api";
import type { GraphNode, GraphEdge, NodeKind, EdgeKind } from "../../types";

// ── Node columns ─────────────────────────────────────────────────────────────

const nodeHelper = createColumnHelper<GraphNode>();

const nodeColumns = [
  nodeHelper.accessor("id", {
    header: "Node ID",
    cell: (info) => (
      <span style={{ fontFamily: "monospace", color: "var(--accent)" }}>
        {info.getValue()}
      </span>
    ),
    size: 200,
  }),
  nodeHelper.accessor("kind", {
    header: "Type",
    cell: (info) => (
      <span className={`kg-badge kg-badge--${info.getValue()}`}>{info.getValue()}</span>
    ),
    size: 100,
  }),
  nodeHelper.accessor("degree", {
    header: "Degree",
    cell: (info) => info.getValue(),
    size: 80,
  }),
  nodeHelper.accessor("updated_at", {
    header: "Updated",
    cell: (info) => {
      const v = info.getValue();
      return v ? new Date(v).toLocaleDateString() : "—";
    },
    size: 120,
  }),
];

// ── Edge columns ─────────────────────────────────────────────────────────────

const edgeHelper = createColumnHelper<GraphEdge>();

const edgeColumns = [
  edgeHelper.accessor("source", {
    header: "Source",
    cell: (info) => (
      <span style={{ fontFamily: "monospace", color: "var(--accent)" }}>
        {info.getValue()}
      </span>
    ),
    size: 180,
  }),
  edgeHelper.accessor("kind", {
    header: "Relationship",
    cell: (info) => (
      <span className={`kg-badge kg-badge--${info.getValue()}`}>{info.getValue()}</span>
    ),
    size: 140,
  }),
  edgeHelper.accessor("target", {
    header: "Target",
    cell: (info) => (
      <span style={{ fontFamily: "monospace" }}>{info.getValue()}</span>
    ),
    size: 180,
  }),
];

// ── Node table ────────────────────────────────────────────────────────────────

interface NodeTableProps {
  search: string;
  kindFilter: NodeKind | "";
  onExpandEgo: (ticker: string) => void;
}

function NodeTable({ search, kindFilter, onExpandEgo }: NodeTableProps) {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [sorting, setSorting] = useState<SortingState>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetchGraphNodes({
        kind: kindFilter || undefined,
        search: search || undefined,
        offset: 0,
        limit: 500,
      });
      setNodes(result.items);
      setTotal(result.total);
    } catch {
      setNodes([]);
    } finally {
      setLoading(false);
    }
  }, [search, kindFilter]);

  useEffect(() => {
    const id = setTimeout(load, 200);
    return () => clearTimeout(id);
  }, [load]);

  const table = useReactTable({
    data: nodes,
    columns: nodeColumns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const rows = table.getRowModel().rows;

  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 36,
    overscan: 15,
  });

  const virtualItems = rowVirtualizer.getVirtualItems();
  const totalHeight = rowVirtualizer.getTotalSize();
  const paddingTop = virtualItems.length > 0 ? virtualItems[0].start : 0;
  const paddingBottom =
    virtualItems.length > 0
      ? totalHeight - virtualItems[virtualItems.length - 1].end
      : 0;

  return (
    <div className="kg-table-container">
      {loading && (
        <div style={{ padding: "6px 14px", fontSize: 11, color: "var(--text-muted)" }}>
          Loading…
        </div>
      )}
      {!loading && (
        <div style={{ padding: "4px 14px", fontSize: 11, color: "var(--text-muted)" }}>
          {total.toLocaleString()} nodes{total > 500 ? " (showing first 500)" : ""}
        </div>
      )}
      <div className="kg-table-scroll" ref={scrollRef}>
        <table className="kg-table">
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => (
                  <th
                    key={h.id}
                    style={{ width: h.getSize() }}
                    onClick={h.column.getToggleSortingHandler()}
                  >
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {h.column.getIsSorted() === "asc" ? " ↑" : h.column.getIsSorted() === "desc" ? " ↓" : ""}
                  </th>
                ))}
                <th style={{ width: 80 }}>Action</th>
              </tr>
            ))}
          </thead>
          <tbody>
            {paddingTop > 0 && (
              <tr><td colSpan={nodeColumns.length + 1} style={{ height: paddingTop }} /></tr>
            )}
            {virtualItems.map((vi) => {
              const row = rows[vi.index];
              return (
                <tr key={row.id} data-index={vi.index} ref={rowVirtualizer.measureElement}>
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                  <td>
                    {row.original.kind === "ticker" && (
                      <button
                        className="kg-table-action"
                        onClick={() => onExpandEgo(row.original.id)}
                      >
                        Expand
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
            {paddingBottom > 0 && (
              <tr><td colSpan={nodeColumns.length + 1} style={{ height: paddingBottom }} /></tr>
            )}
          </tbody>
        </table>
        {!loading && nodes.length === 0 && (
          <div className="kg-empty" style={{ height: 200 }}>
            No nodes match the current filter.
          </div>
        )}
      </div>
    </div>
  );
}

// ── Edge table ────────────────────────────────────────────────────────────────

interface EdgeTableProps {
  edgeKindFilter: EdgeKind | "";
}

function EdgeTable({ edgeKindFilter }: EdgeTableProps) {
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [sorting, setSorting] = useState<SortingState>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLoading(true);
    fetchGraphEdges({
      kind: edgeKindFilter || undefined,
      offset: 0,
      limit: 500,
    })
      .then((r) => { setEdges(r.items); setTotal(r.total); })
      .catch(() => setEdges([]))
      .finally(() => setLoading(false));
  }, [edgeKindFilter]);

  const table = useReactTable({
    data: edges,
    columns: edgeColumns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const rows = table.getRowModel().rows;

  const rowVirtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 36,
    overscan: 15,
  });

  const virtualItems = rowVirtualizer.getVirtualItems();
  const totalHeight = rowVirtualizer.getTotalSize();
  const paddingTop = virtualItems.length > 0 ? virtualItems[0].start : 0;
  const paddingBottom =
    virtualItems.length > 0
      ? totalHeight - virtualItems[virtualItems.length - 1].end
      : 0;

  return (
    <div className="kg-table-container">
      {loading && (
        <div style={{ padding: "6px 14px", fontSize: 11, color: "var(--text-muted)" }}>
          Loading…
        </div>
      )}
      {!loading && (
        <div style={{ padding: "4px 14px", fontSize: 11, color: "var(--text-muted)" }}>
          {total.toLocaleString()} edges{total > 500 ? " (showing first 500)" : ""}
        </div>
      )}
      <div className="kg-table-scroll" ref={scrollRef}>
        <table className="kg-table">
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => (
                  <th
                    key={h.id}
                    style={{ width: h.getSize() }}
                    onClick={h.column.getToggleSortingHandler()}
                  >
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {h.column.getIsSorted() === "asc" ? " ↑" : h.column.getIsSorted() === "desc" ? " ↓" : ""}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {paddingTop > 0 && (
              <tr><td colSpan={edgeColumns.length} style={{ height: paddingTop }} /></tr>
            )}
            {virtualItems.map((vi) => {
              const row = rows[vi.index];
              return (
                <tr key={row.id} data-index={vi.index} ref={rowVirtualizer.measureElement}>
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
            {paddingBottom > 0 && (
              <tr><td colSpan={edgeColumns.length} style={{ height: paddingBottom }} /></tr>
            )}
          </tbody>
        </table>
        {!loading && edges.length === 0 && (
          <div className="kg-empty" style={{ height: 200 }}>
            No edges found.
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main table view (tabs: Nodes / Edges) ─────────────────────────────────────

type TableTab = "nodes" | "edges";

interface KGTableViewProps {
  search: string;
  kindFilter: NodeKind | "";
  onExpandEgo: (ticker: string) => void;
}

export function KGTableView({ search, kindFilter, onExpandEgo }: KGTableViewProps) {
  const [tableTab, setTableTab] = useState<TableTab>("nodes");
  const [edgeKindFilter, setEdgeKindFilter] = useState<EdgeKind | "">("");

  return (
    <div className="kg-table-container">
      {/* Sub-tabs */}
      <div className="kg-table-tabs">
        <button
          className={`kg-table-tab${tableTab === "nodes" ? " active" : ""}`}
          onClick={() => setTableTab("nodes")}
        >
          Nodes
        </button>
        <button
          className={`kg-table-tab${tableTab === "edges" ? " active" : ""}`}
          onClick={() => setTableTab("edges")}
        >
          Edges
        </button>
        {tableTab === "edges" && (
          <select
            className="kg-select"
            style={{ alignSelf: "center", marginLeft: 12, fontSize: 12, padding: "3px 6px" }}
            value={edgeKindFilter}
            onChange={(e) => setEdgeKindFilter(e.target.value as EdgeKind | "")}
          >
            <option value="">All relationships</option>
            <option value="IN_SECTOR">IN_SECTOR</option>
            <option value="IN_INDUSTRY">IN_INDUSTRY</option>
            <option value="CO_MENTION">CO_MENTION</option>
          </select>
        )}
      </div>

      {tableTab === "nodes" ? (
        <NodeTable search={search} kindFilter={kindFilter} onExpandEgo={onExpandEgo} />
      ) : (
        <EdgeTable edgeKindFilter={edgeKindFilter} />
      )}
    </div>
  );
}
