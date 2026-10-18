import io
import re
import subprocess

import matplotlib
from matplotlib.figure import Figure

_EXTERNAL = re.compile(r'["\']?\b(image|shapefile|fontpath|imagepath)\b["\']?\s*=|<\s*img\b', re.I)


_FONT = "DejaVu Sans"
_DEFAULTS = (
    f'graph [fontname="{_FONT}", fontsize=18, nodesep=0.5, ranksep=0.6, pad=0.3, bgcolor="white"]; '
    f'node [shape=box, style="rounded,filled", fillcolor="#FFF6CC", color="#16325C", fontname="{_FONT}", '
    'fontsize=18, penwidth=1.5, margin="0.25,0.12"]; '
    f'edge [color="#16325C", penwidth=1.5, fontname="{_FONT}", fontsize=14]; '
)
_OPEN = re.compile(r"^(\s*(?:strict\s+)?(?:di)?graph\b[^{]*\{)", re.I)
_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')
_ATTRS = re.compile(r"\[[^\]]*\]")
_EDGE = re.compile(r"-[->]")


def _max_out(code: str) -> int:
    """Largest number of edges leaving one node, over `a -> b`, `a -- b` and `a -> {b c d}` forms."""
    names: dict[str, str] = {}
    flat = _QUOTED.sub(lambda m: names.setdefault(m.group(0), f"q{len(names)}"), code)
    flat = _ATTRS.sub("", flat)
    out: dict[str, int] = {}
    for stmt in re.split(r"[\n;]", flat):
        parts = _EDGE.split(stmt)
        if len(parts) < 2:
            continue
        groups = [re.findall(r"[\w.]+", p) for p in parts]
        for src, dst in zip(groups, groups[1:]):
            for a in src:
                out[a] = out.get(a, 0) + len(dst)
    return max(out.values(), default=0)


def _styled(code: str) -> str:
    extra = _DEFAULTS
    if _max_out(code) > 3 and not re.search(r"\brankdir\s*=", code):
        extra += "rankdir=LR; "
    # shortcut: only the first graph gets defaults; a later `subgraph`/second graph keeps dot's own
    return _OPEN.sub(lambda m: m.group(1) + " " + extra, code, count=1)


def svg_size(svg: bytes) -> tuple[float, float]:
    """(width, height) of an SVG from its root width/height, else its viewBox; (0, 0) if unknown."""
    root = re.search(rb"<svg\b[^>]*>", svg)
    tag = root.group(0).decode(errors="replace") if root else ""
    w, h = (re.search(rf'\b{k}="([\d.]+)', tag) for k in ("width", "height"))
    if not (w and h) and (vb := re.search(r'viewBox="[\d.\-]+[ ,]+[\d.\-]+[ ,]+([\d.]+)[ ,]+([\d.]+)"', tag)):
        return float(vb.group(1)), float(vb.group(2))
    return (float(w.group(1)), float(h.group(1))) if w and h else (0.0, 0.0)


def render_dot(code: str) -> tuple[bytes | None, str]:
    """DOT -> SVG bytes, or (None, the exact dot stderr / a clear error)."""
    if _EXTERNAL.search(code):
        return None, "external files not allowed (image, shapefile, fontpath, imagepath, <img>)"
    try:
        p = subprocess.run(["dot", "-Tsvg"], input=_styled(code).encode(), capture_output=True, timeout=30)
    except FileNotFoundError:
        return None, "Graphviz 'dot' is not installed on this host"
    except subprocess.TimeoutExpired:
        return None, "dot timed out after 30s"
    if p.returncode != 0 or not p.stdout:
        return None, (p.stderr.decode(errors="replace").strip() or "dot failed")[:600]
    return p.stdout, ""


def render_chart(spec: dict) -> tuple[bytes | None, str]:
    """Chart spec -> SVG bytes, or (None, validation error text)."""
    if not isinstance(spec, dict) or spec.get("type") not in ("bar", "line"):
        return None, 'spec must be an object with "type": "bar" or "line"'
    x, series = spec.get("x"), spec.get("series")
    if not (isinstance(x, list) and 1 <= len(x) <= 12):
        return None, '"x" must be a list of 1-12 labels'
    if not (isinstance(series, list) and 1 <= len(series) <= 4 and all(isinstance(s, dict) for s in series)):
        return None, '"series" must be a list of 1-4 objects {"name", "values"}'
    for s in series:
        v = s.get("values")
        if not (isinstance(v, list) and len(v) == len(x) and all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in v)):
            return None, f'series {s.get("name")!r}: "values" must be {len(x)} numbers, one per x label'
    x = [str(i) for i in x]
    with matplotlib.rc_context({"font.family": _FONT, "font.size": 14, "axes.titlesize": 16}):
        return _draw_chart(spec, x, series)


def _draw_chart(spec: dict, x: list, series: list) -> tuple[bytes, str]:
    fig = Figure(figsize=(8, 4.5))
    ax = fig.subplots()
    w = 0.8 / len(series)
    for i, s in enumerate(series):
        name = str(s.get("name", ""))
        if spec["type"] == "bar":
            ax.bar([j + i * w - 0.4 + w / 2 for j in range(len(x))], s["values"], w, label=name)
        else:
            ax.plot(range(len(x)), s["values"], marker="o", label=name)
    ax.set_xticks(range(len(x)), x)
    ax.set_title(str(spec.get("title", "")))
    ax.set_ylabel(str(spec.get("y_label", "")))
    ax.spines[["top", "right"]].set_visible(False)
    if len(series) > 1:
        ax.legend(frameon=False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="svg", transparent=True)
    return buf.getvalue(), ""
