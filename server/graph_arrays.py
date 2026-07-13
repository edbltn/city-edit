"""
Compact-array walk-graph artifact (walk_graph_arrays.npz).

The runtime no longer unpickles the networkx walk graph — that decode transiently
inflates to several GB of boxed Python objects per city (the prod OOM loop) and
takes minutes. Instead, build time converts the pickle once into flat numpy
arrays, and the runtime memory-maps those in ~1s with no networkx anywhere.

Edge ORDER (and therefore every stored vote's edge id) is preserved exactly:
`convert()` iterates `graph.edges(data=True)` with the same skip rule the old
runtime loader used, so the arrays are a bit-faithful projection of the pickle.

Layout (all little-endian, aligned with python_router's runtime needs):
  coords       float64 [n, 2]   (lat, lon) indexed by graph index
  node_osmid   int64   [n]      graph index -> OSM node id
  edge_u/v     int32   [e]      endpoint graph indices
  edge_len     float32 [e]      edge length (m)
  edge_name    uint32  [e]      index into meta["names"]
  edge_highway uint32  [e]      index into meta["highways"]
  meta         uint8   [...]    UTF-8 JSON: {"version", "names", "highways"}

Adjacency is NOT stored — it derives from edge_u/edge_v in vectorized numpy at
load time (see build_csr_adjacency), which is faster than reading it from disk.
"""
import json
import logging
import pickle
import sys
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

ARRAYS_FILENAME = "walk_graph_arrays.npz"


def _first_str(val) -> str:
    """OSM tags can be a string or a list of strings (multi-valued); take one."""
    if isinstance(val, list):
        return val[0] if val else ""
    return val or ""


def convert(data_dir: str | Path) -> Path:
    """Convert <data_dir>/walk_graph.pkl into <data_dir>/walk_graph_arrays.npz.

    Iterates edges in the exact order the legacy runtime loader did, so edge ids
    (positions) are identical to what baked blocks and stored votes reference.
    """
    data_dir = Path(data_dir)
    pkl_path = data_dir / "walk_graph.pkl"
    if not pkl_path.exists():
        raise FileNotFoundError(f"{pkl_path} not found — build the graph first")

    logger.info(f"[ARRAYS] Converting {pkl_path} ...")
    with open(pkl_path, "rb") as f:
        data = pickle.load(f)

    nx_graph = data["graph"]
    node_index = data["node_index"]        # osm id -> graph index
    index_to_node = data["index_to_node"]  # graph index -> osm id
    version = data.get("version", "unknown")
    data = None

    n = len(node_index)
    coords = np.zeros((n, 2), dtype=np.float64)
    node_osmid = np.zeros(n, dtype=np.int64)
    for osmid, idx in node_index.items():
        nd = nx_graph.nodes[osmid]
        coords[idx, 0] = nd["y"]
        coords[idx, 1] = nd["x"]
        node_osmid[idx] = osmid
    # index_to_node is the same mapping; assert consistency rather than trust it.
    if len(index_to_node) != n:
        raise ValueError("node_index / index_to_node length mismatch")

    edge_u: list[int] = []
    edge_v: list[int] = []
    edge_len: list[float] = []
    name_codes: list[int] = []
    highway_codes: list[int] = []
    names: list[str] = []
    highways: list[str] = []
    name_idx: dict[str, int] = {}
    highway_idx: dict[str, int] = {}

    def code(table: list[str], idx: dict[str, int], s: str) -> int:
        i = idx.get(s)
        if i is None:
            i = len(table)
            idx[s] = i
            table.append(s)
        return i

    # EXACT legacy iteration: edges(data=True), skip edges whose endpoints are
    # missing from node_index. Positions in these lists ARE the edge ids.
    for u, v, ed in nx_graph.edges(data=True):
        ui = node_index.get(u)
        vi = node_index.get(v)
        if ui is None or vi is None:
            continue
        edge_u.append(ui)
        edge_v.append(vi)
        edge_len.append(float(ed.get("length", 1.0)))
        name_codes.append(code(names, name_idx, str(_first_str(ed.get("name", "")))))
        highway_codes.append(
            code(highways, highway_idx, str(_first_str(ed.get("highway", "")))))

    meta = json.dumps(
        {"version": version, "names": names, "highways": highways},
        ensure_ascii=False,
    ).encode("utf-8")

    out_path = data_dir / ARRAYS_FILENAME
    tmp_path = data_dir / (ARRAYS_FILENAME + ".tmp")
    with open(tmp_path, "wb") as f:
        np.savez_compressed(
            f,
            coords=coords,
            node_osmid=node_osmid,
            edge_u=np.array(edge_u, dtype=np.int32),
            edge_v=np.array(edge_v, dtype=np.int32),
            edge_len=np.array(edge_len, dtype=np.float32),
            edge_name=np.array(name_codes, dtype=np.uint32),
            edge_highway=np.array(highway_codes, dtype=np.uint32),
            meta=np.frombuffer(meta, dtype=np.uint8),
        )
    tmp_path.replace(out_path)
    logger.info(
        f"[ARRAYS] Wrote {out_path} ({out_path.stat().st_size / 1e6:.1f} MB, "
        f"{n} nodes, {len(edge_u)} edges, version {version})"
    )
    return out_path


def load(data_dir: str | Path) -> dict:
    """Load the arrays artifact. Returns a dict of arrays + decoded meta."""
    path = Path(data_dir) / ARRAYS_FILENAME
    if not path.exists():
        raise RuntimeError(
            f"Graph arrays not found: {path}. "
            "Run: python graph_arrays.py --city <city> (after building the graph)"
        )
    with np.load(path) as z:
        arrays = {k: z[k] for k in z.files}
    meta = json.loads(bytes(arrays.pop("meta")).decode("utf-8"))
    arrays["version"] = meta["version"]
    arrays["names"] = meta["names"]
    arrays["highways"] = meta["highways"]
    return arrays


def build_csr_adjacency(edge_u: np.ndarray, edge_v: np.ndarray, n_nodes: int):
    """Vectorized CSR adjacency: node index -> incident edge positions.

    Both endpoints are recorded per edge (matching the legacy per-node adj
    lists), so self-edges appear twice — same as before.
    Returns (indptr int64 [n+1], indices int32 [2e]).
    """
    endpoints = np.concatenate([edge_u, edge_v]).astype(np.int64)
    edge_pos = np.tile(np.arange(len(edge_u), dtype=np.int32), 2)
    # lexsort: group by node, ascending edge id within each node — the same
    # order the legacy per-edge append loop produced.
    order = np.lexsort((edge_pos, endpoints))
    counts = np.bincount(endpoints, minlength=n_nodes)
    indptr = np.zeros(n_nodes + 1, dtype=np.int64)
    np.cumsum(counts, out=indptr[1:])
    return indptr, edge_pos[order]


def main(argv: list[str]) -> int:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    p = argparse.ArgumentParser(description="Build walk_graph_arrays.npz from walk_graph.pkl")
    p.add_argument("--city", action="append", default=[],
                   help="city id under osm_data/ (repeatable)")
    p.add_argument("--all", action="store_true",
                   help="convert every osm_data/<city>/ that has a walk_graph.pkl")
    p.add_argument("--data-root", default=str(Path(__file__).parent / "osm_data"))
    args = p.parse_args(argv)

    root = Path(args.data_root)
    if args.all:
        dirs = sorted(d for d in root.iterdir()
                      if (d / "walk_graph.pkl").exists())
    else:
        dirs = [root / c for c in args.city]
    if not dirs:
        p.error("nothing to convert (use --city or --all)")
    for d in dirs:
        convert(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
