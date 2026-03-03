import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  createColumnHelper,
  type SortingState,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { fetchGraphNodes, fetchGraphEdges } from "../../api";
import type { GraphNode, GraphEdge, NodeKind, EdgeKind, NodeQueryParams } from "../../types";

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
  nodeHelper.accessor((row) => row.in_sector_count ?? 0, {
    id: "in_sector_count",
    header: "IN_SECTOR",
    cell: (info) => info.getValue(),
    size: 90,
  }),
  nodeHelper.accessor((row) => row.in_industry_count ?? 0, {
    id: "in_industry_count",
    header: "IN_INDUSTRY",
    cell: (info) => info.getValue(),
    size: 100,
  }),
  nodeHelper.accessor((row) => row.co_mention_count ?? 0, {
    id: "co_mention_count",
    header: "CO_MENTION",
    cell: (info) => info.getValue(),
    size: 98,
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
  edgeHelper.accessor((row) => row.weight ?? 0, {
    id: "weight",
    header: "Weight",
    cell: (info) => info.getValue(),
    size: 88,
  }),
];

// ── Node table ────────────────────────────────────────────────────────────────

interface NodeTableProps {
  search: string;
  visibleKinds: NodeKind[];
  onSelectTicker: (ticker: string) => void;
  onExpandEgo: (ticker: string) => void;
}

function mapSortingToQuery(sorting: SortingState): Pick<NodeQueryParams, "sort_by" | "sort_dir"> {
  if (sorting.length === 0) return {};
  const active = sorting[0];
  const map: Record<string, NodeQueryParams["sort_by"]> = {
    id: "id",
    kind: "kind",
    degree: "degree",
    updated_at: "updated_at",
  };
  const sortBy = map[active.id];
  if (!sortBy) return {};
  return { sort_by: sortBy, sort_dir: active.desc ? "desc" : "asc" };
}

function NodeTable({ search, visibleKinds, onSelectTicker, onExpandEgo }: NodeTableProps) {
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [sorting, setSorting] = useState<SortingState>([{ id: "degree", desc: true }]);
  const [idContains, setIdContains] = useState("");
  const [minDegreeInput, setMinDegreeInput] = useState("0");
  const [kindFilter, setKindFilter] = useState<NodeKind | "">("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setKindFilter((prev) => (prev && !visibleKinds.includes(prev) ? "" : prev));
  }, [visibleKinds]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const selectedKinds = kindFilter ? [kindFilter] : visibleKinds;
      const effectiveSearch = idContains.trim() || search || undefined;
      const minDegree = Number.isFinite(Number(minDegreeInput)) ? Math.max(0, Number(minDegreeInput) || 0) : 0;
      const apiKind = selectedKinds.length === 1 ? selectedKinds[0] : undefined;
      const sortQuery = mapSortingToQuery(sorting);

      const result = await fetchGraphNodes({
        kind: apiKind,
        search: effectiveSearch,
        min_degree: minDegree,
        ...sortQuery,
        offset: 0,
        limit: 500,
      });

      const filtered = apiKind
        ? result.items
        : result.items.filter((item) => selectedKinds.includes(item.kind));

      setNodes(filtered);
      setTotal(apiKind ? result.total : filtered.length);
    } catch {
      setNodes([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [sorting, kindFilter, visibleKinds, idContains, minDegreeInput, search]);

  useEffect(() => {
    const id = setTimeout(load, 200);
    return () => clearTimeout(id);
  }, [load]);

  const table = useReactTable({
    data: nodes,
    columns: useMemo(() => nodeColumns, []),
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    manualSorting: true,
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
      <div className="kg-table-filters">
        <input
          className="kg-search"
          style={{ width: 220 }}
          placeholder="Node id contains..."
          value={idContains}
          onChange={(e) => setIdContains(e.target.value)}
        />
        <input
          className="kg-search"
          style={{ width: 110 }}
          type="number"
          min={0}
          value={minDegreeInput}
          onChange={(e) => setMinDegreeInput(e.target.value)}
          placeholder="Min degree"
        />
        <select
          className="kg-select"
          value={kindFilter}
          onChange={(e) => setKindFilter(e.target.value as NodeKind | "")}
        >
          <option value="">All visible types</option>
          {visibleKinds.includes("ticker") && <option value="ticker">ticker</option>}
          {visibleKinds.includes("sector") && <option value="sector">sector</option>}
          {visibleKinds.includes("industry") && <option value="industry">industry</option>}
        </select>
      </div>

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
                      <div style={{ display: "flex", gap: 6 }}>
                        <button
                          className="kg-table-action"
                          onClick={() => onSelectTicker(row.original.id)}
                        >
                          Open
                        </button>
                        <button
                          className="kg-table-action"
                          onClick={() => onExpandEgo(row.original.id)}
                        >
                          Expand
                        </button>
                      </div>
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
    getCoreRowModel: getCoreRowModel(),
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
  visibleKinds: NodeKind[];
  onSelectTicker: (ticker: string) => void;
  onExpandEgo: (ticker: string) => void;
}

export function KGTableView({ search, visibleKinds, onSelectTicker, onExpandEgo }: KGTableViewProps) {
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
        <NodeTable
          search={search}
          visibleKinds={visibleKinds}
          onSelectTicker={onSelectTicker}
          onExpandEgo={onExpandEgo}
        />
      ) : (
        <EdgeTable edgeKindFilter={edgeKindFilter} />
      )}
    </div>
  );
}
