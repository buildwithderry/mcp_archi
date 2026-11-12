# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2.0,<2"]
# ///
"""MCP server for Archi (ArchiMate modelling tool) on macOS.

Works by reading and writing Archi's native .archimate XML files directly, and
drives Archi's own command line for reports/exports. Archi does not reload a model
that changed on disk, so close the model in Archi before writing to it (or use
`open_in_archi` afterwards and choose to reload).
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ARCHI_APP = Path(os.environ.get("ARCHI_APP", "/Applications/Archi.app"))
ARCHI_BIN = ARCHI_APP / "Contents/MacOS/Archi"

NS_ARCHI = "http://www.archimatetool.com/archimate"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
XSI_TYPE = f"{{{NS_XSI}}}type"
ET.register_namespace("archimate", NS_ARCHI)
ET.register_namespace("xsi", NS_XSI)

FOLDER_TYPES = {
    "strategy": ["Resource", "Capability", "ValueStream", "CourseOfAction"],
    "business": [
        "BusinessActor", "BusinessRole", "BusinessCollaboration", "BusinessInterface",
        "BusinessProcess", "BusinessFunction", "BusinessInteraction", "BusinessEvent",
        "BusinessService", "BusinessObject", "Contract", "Representation", "Product",
    ],
    "application": [
        "ApplicationComponent", "ApplicationCollaboration", "ApplicationInterface",
        "ApplicationFunction", "ApplicationInteraction", "ApplicationProcess",
        "ApplicationEvent", "ApplicationService", "DataObject",
    ],
    "technology": [
        "Node", "Device", "SystemSoftware", "TechnologyCollaboration", "TechnologyInterface",
        "Path", "CommunicationNetwork", "TechnologyFunction", "TechnologyProcess",
        "TechnologyInteraction", "TechnologyEvent", "TechnologyService", "Artifact",
        "Equipment", "Facility", "DistributionNetwork", "Material",
    ],
    "motivation": [
        "Stakeholder", "Driver", "Assessment", "Goal", "Outcome", "Principle",
        "Requirement", "Constraint", "Meaning", "Value",
    ],
    "implementation_migration": ["WorkPackage", "Deliverable", "ImplementationEvent", "Plateau", "Gap"],
    "other": ["Location", "Grouping", "Junction"],
}
FOLDER_NAMES = {
    "strategy": "Strategy", "business": "Business", "application": "Application",
    "technology": "Technology & Physical", "motivation": "Motivation",
    "implementation_migration": "Implementation & Migration", "other": "Other",
    "relations": "Relations", "diagrams": "Views",
}
ELEMENT_FOLDER = {t: f for f, types in FOLDER_TYPES.items() for t in types}
RELATIONSHIP_TYPES = [
    "CompositionRelationship", "AggregationRelationship", "AssignmentRelationship",
    "RealizationRelationship", "ServingRelationship", "AccessRelationship",
    "InfluenceRelationship", "TriggeringRelationship", "FlowRelationship",
    "SpecializationRelationship", "AssociationRelationship",
]
ACCESS_TYPES = {"write": None, "read": "1", "access": "2", "readwrite": "3"}

mcp = FastMCP("archi")


# ---------------------------------------------------------------- helpers

def _new_id() -> str:
    return "id-" + uuid.uuid4().hex


def _short(xsi: str | None) -> str:
    return (xsi or "").split(":")[-1]


def _norm_type(t: str, allowed: list[str]) -> str:
    """Accept 'business-actor', 'Business Actor', 'businessactor', 'serving' etc."""
    key = t.replace("-", "").replace("_", "").replace(" ", "").lower()
    for a in allowed:
        k = a.lower()
        if key == k or key + "relationship" == k:
            return a
    raise ValueError(f"Unknown type '{t}'. Valid: {', '.join(allowed)}")


def _path(model_path: str) -> Path:
    p = Path(model_path).expanduser().resolve()
    if p.suffix != ".archimate":
        raise ValueError("model_path must point to a .archimate file")
    return p


def _load(model_path: str) -> tuple[Path, ET.ElementTree]:
    p = _path(model_path)
    if not p.exists():
        raise FileNotFoundError(p)
    return p, ET.parse(p)


def _save(p: Path, tree: ET.ElementTree) -> None:
    if p.exists():
        shutil.copy2(p, p.with_suffix(".archimate.bak"))
    ET.indent(tree, space="  ")
    tree.write(p, encoding="UTF-8", xml_declaration=True)


def _folder(root: ET.Element, ftype: str) -> ET.Element:
    for f in root.findall("folder"):
        if f.get("type") == ftype:
            return f
    f = ET.SubElement(root, "folder", {"name": FOLDER_NAMES[ftype], "id": _new_id(), "type": ftype})
    return f


def _concepts(root: ET.Element) -> dict[str, ET.Element]:
    return {e.get("id"): e for e in root.iter("element") if e.get("id")}


def _get(root: ET.Element, cid: str) -> ET.Element:
    e = _concepts(root).get(cid)
    if e is None:
        raise ValueError(f"No element/relationship/view with id '{cid}'")
    return e


def _is_rel(e: ET.Element) -> bool:
    return _short(e.get(XSI_TYPE)).endswith("Relationship")


def _is_view(e: ET.Element) -> bool:
    return _short(e.get(XSI_TYPE)) in ("ArchimateDiagramModel", "SketchModel", "CanvasModel")


def _doc(e: ET.Element) -> str | None:
    d = e.find("documentation")
    return d.text if d is not None else None


def _props(e: ET.Element) -> dict[str, str]:
    return {p.get("key"): p.get("value", "") for p in e.findall("property")}


def _set_doc_props(e: ET.Element, documentation: str | None, properties: dict[str, str] | None) -> None:
    if documentation is not None:
        d = e.find("documentation")
        if d is None:
            d = ET.Element("documentation")
            # Archi expects documentation before properties / children
            e.insert(0, d)
        d.text = documentation
    if properties:
        existing = {p.get("key"): p for p in e.findall("property")}
        for k, v in properties.items():
            if k in existing:
                if v is None:
                    e.remove(existing[k])
                else:
                    existing[k].set("value", str(v))
            elif v is not None:
                ET.SubElement(e, "property", {"key": k, "value": str(v)})


def _summary(e: ET.Element, names: dict[str, str] | None = None) -> dict:
    out = {"id": e.get("id"), "type": _short(e.get(XSI_TYPE)), "name": e.get("name", "")}
    if _is_rel(e):
        out["source"] = e.get("source")
        out["target"] = e.get("target")
        if names:
            out["source_name"] = names.get(e.get("source"), "")
            out["target_name"] = names.get(e.get("target"), "")
    return out


_RULES: dict[tuple[str, str], set[str]] | None = None


def _rules() -> dict[tuple[str, str], set[str]]:
    """ArchiMate relationship rules, read from Archi's own relationships.xml."""
    global _RULES
    if _RULES is None:
        _RULES = {}
        found = glob.glob(str(ARCHI_APP / "Contents/Eclipse/plugins/com.archimatetool.model_*/model"))
        if found:
            d = Path(sorted(found)[-1])
            keys = {k.get("char"): k.get("relationship")
                    for k in ET.parse(d / "relationships-keys.xml").getroot()}
            for s in ET.parse(d / "relationships.xml").getroot():
                for t in s:
                    _RULES[(s.get("concept"), t.get("concept"))] = {
                        keys[c] for c in t.get("relations", "") if c in keys}
    return _RULES


def _diagram_objects(view: ET.Element) -> list[ET.Element]:
    return [c for c in view.iter("child") if _short(c.get(XSI_TYPE)) == "DiagramObject"]


# ---------------------------------------------------------------- tools: discovery

@mcp.tool()
def list_models(search_dirs: list[str] | None = None) -> dict:
    """List .archimate model files: ones Archi has open/recent plus files found in
    ~/Documents, ~/Downloads, ~/Desktop (or the given directories)."""
    found: set[str] = set()
    mx = Path.home() / "Library/Application Support/Archi/models.xml"
    if mx.exists():
        try:
            for m in ET.parse(mx).getroot().iter():
                f = m.get("file")
                if f and f.endswith(".archimate"):
                    found.add(f)
        except ET.ParseError:
            pass
    dirs = search_dirs or [str(Path.home() / d) for d in ("Documents", "Downloads", "Desktop")]
    for d in dirs:
        for f in glob.glob(os.path.join(os.path.expanduser(d), "**", "*.archimate"), recursive=True):
            found.add(f)
    models = []
    for f in sorted(found):
        try:
            name = ET.parse(f).getroot().get("name")
        except Exception:
            name = None
        models.append({"path": f, "name": name, "exists": os.path.exists(f)})
    return {"models": models}


@mcp.tool()
def get_model_summary(model_path: str) -> dict:
    """Model name, purpose and counts of elements per type, relationships and views."""
    _, tree = _load(model_path)
    root = tree.getroot()
    counts: dict[str, int] = {}
    rels = views = 0
    for e in root.iter("element"):
        if _is_rel(e):
            rels += 1
        elif _is_view(e):
            views += 1
        else:
            t = _short(e.get(XSI_TYPE))
            counts[t] = counts.get(t, 0) + 1
    purpose = root.find("purpose")
    return {
        "name": root.get("name"), "id": root.get("id"),
        "purpose": purpose.text if purpose is not None else None,
        "element_counts": counts, "element_total": sum(counts.values()),
        "relationship_total": rels, "view_total": views,
    }


@mcp.tool()
def list_elements(model_path: str, element_type: str | None = None, name_contains: str | None = None) -> dict:
    """List ArchiMate elements (not relationships or views). Optionally filter by type
    (e.g. 'BusinessActor') and/or a case-insensitive name substring."""
    _, tree = _load(model_path)
    t = _norm_type(element_type, list(ELEMENT_FOLDER)) if element_type else None
    out = []
    for e in tree.getroot().iter("element"):
        if _is_rel(e) or _is_view(e):
            continue
        if t and _short(e.get(XSI_TYPE)) != t:
            continue
        if name_contains and name_contains.lower() not in e.get("name", "").lower():
            continue
        out.append(_summary(e))
    return {"count": len(out), "elements": out}


@mcp.tool()
def list_relationships(model_path: str, element_id: str | None = None, relationship_type: str | None = None) -> dict:
    """List relationships, optionally only those touching element_id and/or of one type."""
    _, tree = _load(model_path)
    root = tree.getroot()
    names = {k: v.get("name", "") for k, v in _concepts(root).items()}
    t = _norm_type(relationship_type, RELATIONSHIP_TYPES) if relationship_type else None
    out = []
    for e in root.iter("element"):
        if not _is_rel(e):
            continue
        if t and _short(e.get(XSI_TYPE)) != t:
            continue
        if element_id and element_id not in (e.get("source"), e.get("target")):
            continue
        out.append(_summary(e, names))
    return {"count": len(out), "relationships": out}


@mcp.tool()
def get_concept(model_path: str, concept_id: str) -> dict:
    """Full details of an element or relationship: documentation, properties,
    relationships in/out and the views it appears on."""
    _, tree = _load(model_path)
    root = tree.getroot()
    e = _get(root, concept_id)
    names = {k: v.get("name", "") for k, v in _concepts(root).items()}
    out = _summary(e, names)
    out["documentation"] = _doc(e)
    out["properties"] = _props(e)
    for attr in ("accessType", "strength", "directed", "type"):
        if e.get(attr) is not None:
            out[attr] = e.get(attr)
    if not _is_rel(e):
        out["relationships"] = [
            _summary(r, names) for r in root.iter("element")
            if _is_rel(r) and concept_id in (r.get("source"), r.get("target"))
        ]
    out["views"] = [
        {"id": v.get("id"), "name": v.get("name")} for v in root.iter("element") if _is_view(v)
        and any(c.get("archimateElement") == concept_id or c.get("archimateRelationship") == concept_id
                for c in v.iter())
    ]
    return out


@mcp.tool()
def list_views(model_path: str) -> dict:
    """List views (diagrams) with how many elements each shows."""
    _, tree = _load(model_path)
    out = []
    for v in tree.getroot().iter("element"):
        if _is_view(v):
            out.append({"id": v.get("id"), "name": v.get("name"), "type": _short(v.get(XSI_TYPE)),
                        "element_count": len(_diagram_objects(v))})
    return {"views": out}


@mcp.tool()
def get_view(model_path: str, view_id: str) -> dict:
    """Elements (with positions) and connections shown on a view."""
    _, tree = _load(model_path)
    root = tree.getroot()
    v = _get(root, view_id)
    concepts = _concepts(root)
    nodes, conns = [], []
    for c in _diagram_objects(v):
        el = concepts.get(c.get("archimateElement"))
        b = c.find("bounds")
        nodes.append({
            "diagram_object_id": c.get("id"), "element_id": c.get("archimateElement"),
            "type": _short(el.get(XSI_TYPE)) if el is not None else None,
            "name": el.get("name") if el is not None else None,
            "bounds": {k: int(b.get(k, -1)) for k in ("x", "y", "width", "height")} if b is not None else None,
        })
        for sc in c.findall("sourceConnection"):
            r = concepts.get(sc.get("archimateRelationship"))
            conns.append({"relationship_id": sc.get("archimateRelationship"),
                          "type": _short(r.get(XSI_TYPE)) if r is not None else None,
                          "source": sc.get("source"), "target": sc.get("target")})
    return {"id": view_id, "name": v.get("name"), "documentation": _doc(v), "elements": nodes, "connections": conns}


@mcp.tool()
def list_valid_relationships(source_type: str, target_type: str) -> dict:
    """Which ArchiMate relationship types are allowed from source_type to target_type
    (rules taken from the installed Archi)."""
    s = _norm_type(source_type, list(ELEMENT_FOLDER))
    t = _norm_type(target_type, list(ELEMENT_FOLDER))
    return {"source": s, "target": t, "allowed": sorted(_rules().get((s, t), set()))}


@mcp.tool()
def list_types() -> dict:
    """All ArchiMate element types (grouped by layer) and relationship types."""
    return {"elements_by_layer": FOLDER_TYPES, "relationships": RELATIONSHIP_TYPES}


# ---------------------------------------------------------------- tools: editing

@mcp.tool()
def create_model(model_path: str, name: str, purpose: str | None = None, overwrite: bool = False) -> dict:
    """Create a new, empty .archimate model with the standard Archi folders."""
    p = _path(model_path)
    if p.exists() and not overwrite:
        raise FileExistsError(f"{p} exists; pass overwrite=true to replace it")
    root = ET.Element(f"{{{NS_ARCHI}}}model", {"name": name, "id": _new_id(), "version": "5.0.0"})
    for ftype in FOLDER_NAMES:
        ET.SubElement(root, "folder", {"name": FOLDER_NAMES[ftype], "id": _new_id(), "type": ftype})
    if purpose:
        ET.SubElement(root, "purpose").text = purpose
    p.parent.mkdir(parents=True, exist_ok=True)
    _save(p, ET.ElementTree(root))
    return {"path": str(p), "id": root.get("id")}


@mcp.tool()
def add_element(model_path: str, element_type: str, name: str, documentation: str | None = None,
                properties: dict[str, str] | None = None, junction_type: str | None = None) -> dict:
    """Add an element, e.g. element_type='BusinessActor'. junction_type 'and'/'or' only for Junction."""
    p, tree = _load(model_path)
    t = _norm_type(element_type, list(ELEMENT_FOLDER))
    e = ET.SubElement(_folder(tree.getroot(), ELEMENT_FOLDER[t]), "element",
                      {XSI_TYPE: f"archimate:{t}", "name": name, "id": _new_id()})
    if t == "Junction" and junction_type == "or":
        e.set("type", "or")
    _set_doc_props(e, documentation, properties)
    _save(p, tree)
    return _summary(e)


@mcp.tool()
def add_elements(model_path: str, elements: list[dict]) -> dict:
    """Bulk add. Each item: {"type": "...", "name": "...", "documentation"?: "...", "properties"?: {...}}."""
    p, tree = _load(model_path)
    root = tree.getroot()
    created = []
    for it in elements:
        t = _norm_type(it["type"], list(ELEMENT_FOLDER))
        e = ET.SubElement(_folder(root, ELEMENT_FOLDER[t]), "element",
                          {XSI_TYPE: f"archimate:{t}", "name": it["name"], "id": _new_id()})
        _set_doc_props(e, it.get("documentation"), it.get("properties"))
        created.append(_summary(e))
    _save(p, tree)
    return {"created": created}


@mcp.tool()
def update_concept(model_path: str, concept_id: str, name: str | None = None, documentation: str | None = None,
                   properties: dict[str, str | None] | None = None) -> dict:
    """Rename / set documentation / set properties on an element, relationship or view.
    A property value of null removes that property."""
    p, tree = _load(model_path)
    e = _get(tree.getroot(), concept_id)
    if name is not None:
        e.set("name", name)
    _set_doc_props(e, documentation, properties)
    _save(p, tree)
    return _summary(e)


@mcp.tool()
def add_relationship(model_path: str, relationship_type: str, source_id: str, target_id: str,
                     name: str | None = None, documentation: str | None = None,
                     access_type: str | None = None, influence_strength: str | None = None,
                     directed: bool | None = None, force: bool = False) -> dict:
    """Add a relationship, e.g. 'Serving' from an ApplicationService to a BusinessProcess.
    Validated against ArchiMate rules unless force=true.
    access_type: write|read|access|readwrite (Access only). influence_strength e.g. '+', '++' (Influence only).
    directed: Association only."""
    p, tree = _load(model_path)
    root = tree.getroot()
    rt = _norm_type(relationship_type, RELATIONSHIP_TYPES)
    s, t = _get(root, source_id), _get(root, target_id)
    st, tt = _short(s.get(XSI_TYPE)), _short(t.get(XSI_TYPE))
    rules = _rules()
    if not force and rules and not _is_rel(s) and not _is_rel(t):
        allowed = rules.get((st, tt), set())
        if rt not in allowed:
            raise ValueError(f"{rt} is not allowed from {st} to {tt} in ArchiMate. "
                             f"Allowed: {sorted(allowed) or 'none'}. Pass force=true to override.")
    attrs = {XSI_TYPE: f"archimate:{rt}", "id": _new_id(), "source": source_id, "target": target_id}
    if name:
        attrs["name"] = name
    r = ET.SubElement(_folder(root, "relations"), "element", attrs)
    if rt == "AccessRelationship" and access_type:
        code = ACCESS_TYPES[access_type.lower()]
        if code:
            r.set("accessType", code)
    if rt == "InfluenceRelationship" and influence_strength:
        r.set("strength", influence_strength)
    if rt == "AssociationRelationship" and directed:
        r.set("directed", "true")
    _set_doc_props(r, documentation, None)
    _save(p, tree)
    return _summary(r, {source_id: s.get("name", ""), target_id: t.get("name", "")})


@mcp.tool()
def delete_concept(model_path: str, concept_id: str) -> dict:
    """Delete an element, relationship or view. Deleting an element also deletes its
    relationships and removes it from all views (as Archi does). A .bak copy is kept."""
    p, tree = _load(model_path)
    root = tree.getroot()
    target = _get(root, concept_id)
    doomed = {concept_id}
    if not _is_rel(target) and not _is_view(target):
        # relationships attached to the element, including relationships on relationships
        changed = True
        while changed:
            changed = False
            for r in root.iter("element"):
                if _is_rel(r) and r.get("id") not in doomed and (r.get("source") in doomed or r.get("target") in doomed):
                    doomed.add(r.get("id"))
                    changed = True
    parents = {c: par for par in root.iter() for c in par}
    removed_dobjs: set[str] = set()
    removed_conns: set[str] = set()
    for v in [x for x in root.iter("element") if _is_view(x)]:
        for c in list(v.iter()):
            if c.get("archimateElement") in doomed or c.get("archimateRelationship") in doomed:
                removed_dobjs.add(c.get("id"))
                if c.tag == "sourceConnection":
                    removed_conns.add(c.get("id"))
                parents[c].remove(c)
    if removed_dobjs:
        # connections whose diagram endpoint vanished
        for v in [x for x in root.iter("element") if _is_view(x)]:
            for c in list(v.iter("sourceConnection")):
                if c.get("source") in removed_dobjs or c.get("target") in removed_dobjs:
                    removed_conns.add(c.get("id"))
                    parents[c].remove(c)
            for c in v.iter():
                tc = c.get("targetConnections")
                if tc:
                    keep = [x for x in tc.split() if x not in removed_conns]
                    if keep:
                        c.set("targetConnections", " ".join(keep))
                    else:
                        del c.attrib["targetConnections"]
    for e in list(root.iter("element")):
        if e.get("id") in doomed:
            parents[e].remove(e)
    _save(p, tree)
    return {"deleted": sorted(doomed), "removed_from_views": len(removed_dobjs)}


def _layout(n: int, cols: int, start: int, w: int, h: int, gap_x: int, gap_y: int):
    for i in range(n):
        yield start + (i % cols) * (w + gap_x), start + (i // cols) * (h + gap_y)


_LAYER_ORDER = ["strategy", "motivation", "business", "application", "technology", "implementation_migration", "other"]


def _populate_view(root: ET.Element, v: ET.Element, element_ids: list[str], connect: bool) -> dict:
    concepts = _concepts(root)
    existing = {c.get("archimateElement"): c for c in _diagram_objects(v)}
    maxy = max([int(c.find("bounds").get("y", 0)) + int(c.find("bounds").get("height", 55))
                for c in existing.values() if c.find("bounds") is not None] or [0])
    new_ids = [i for i in dict.fromkeys(element_ids) if i not in existing]
    for i in new_ids:
        e = concepts.get(i)
        if e is None or _is_rel(e) or _is_view(e):
            raise ValueError(f"'{i}' is not an element id")
    # one row band per ArchiMate layer, top to bottom
    by_layer: dict[str, list[str]] = {}
    for i in new_ids:
        by_layer.setdefault(ELEMENT_FOLDER.get(_short(concepts[i].get(XSI_TYPE)), "other"), []).append(i)
    y = maxy + 24 if existing else 24
    W, H, GX, GY, COLS = 120, 55, 40, 40, 6
    for layer in _LAYER_ORDER:
        ids = by_layer.get(layer, [])
        for idx, i in enumerate(ids):
            x = 24 + (idx % COLS) * (W + GX)
            yy = y + (idx // COLS) * (H + GY)
            d = ET.SubElement(v, "child", {XSI_TYPE: "archimate:DiagramObject", "id": _new_id(), "archimateElement": i})
            ET.SubElement(d, "bounds", {"x": str(x), "y": str(yy), "width": str(W), "height": str(H)})
            existing[i] = d
        if ids:
            y += ((len(ids) - 1) // COLS + 1) * (H + GY)
    added_conns = 0
    if connect:
        shown = {c.get("archimateRelationship") for c in v.iter("sourceConnection")}
        for r in root.iter("element"):
            if not _is_rel(r) or r.get("id") in shown:
                continue
            s, t = existing.get(r.get("source")), existing.get(r.get("target"))
            if s is None or t is None:
                continue
            cid = _new_id()
            ET.SubElement(s, "sourceConnection", {
                XSI_TYPE: "archimate:Connection", "id": cid,
                "source": s.get("id"), "target": t.get("id"), "archimateRelationship": r.get("id")})
            # Archi resolves connection endpoints through the target's targetConnections list
            t.set("targetConnections", " ".join(filter(None, [t.get("targetConnections"), cid])))
            added_conns += 1
    return {"elements_added": len(new_ids), "connections_added": added_conns}


@mcp.tool()
def create_view(model_path: str, name: str, element_ids: list[str] | None = None,
                connect_relationships: bool = True, documentation: str | None = None) -> dict:
    """Create an ArchiMate view with the given elements laid out in rows per layer
    (strategy/motivation on top, technology at the bottom). Relationships between the
    elements are drawn automatically when connect_relationships is true."""
    p, tree = _load(model_path)
    root = tree.getroot()
    v = ET.SubElement(_folder(root, "diagrams"), "element",
                      {XSI_TYPE: "archimate:ArchimateDiagramModel", "name": name, "id": _new_id()})
    _set_doc_props(v, documentation, None)
    res = _populate_view(root, v, element_ids or [], connect_relationships)
    _save(p, tree)
    return {"id": v.get("id"), "name": name, **res}


@mcp.tool()
def add_to_view(model_path: str, view_id: str, element_ids: list[str], connect_relationships: bool = True) -> dict:
    """Add existing elements to an existing view (placed below what is already there)."""
    p, tree = _load(model_path)
    root = tree.getroot()
    v = _get(root, view_id)
    if not _is_view(v):
        raise ValueError(f"'{view_id}' is not a view")
    res = _populate_view(root, v, element_ids, connect_relationships)
    _save(p, tree)
    return res


# ---------------------------------------------------------------- tools: Archi app / CLI

def _run_cli(args: list[str], timeout: int = 300) -> dict:
    if not ARCHI_BIN.exists():
        raise FileNotFoundError(f"Archi not found at {ARCHI_APP}")
    cmd = [str(ARCHI_BIN), "-application", "com.archimatetool.commandline.app",
           "-consoleLog", "-nosplash", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    tail = "\n".join((r.stdout + r.stderr).strip().splitlines()[-30:])
    return {"ok": r.returncode == 0, "exit_code": r.returncode, "log": tail}


@mcp.tool()
def open_in_archi(model_path: str) -> dict:
    """Open the model in the Archi app (launches Archi if it isn't running)."""
    p = _path(model_path)
    subprocess.run(["open", "-a", str(ARCHI_APP), str(p)], check=True)
    return {"opened": str(p), "note": "If the model was already open in Archi, close it and reopen to see changes."}


@mcp.tool()
def export_html_report(model_path: str, output_dir: str) -> dict:
    """Generate Archi's HTML report (with view images) via the Archi command line."""
    out = Path(output_dir).expanduser().resolve()
    res = _run_cli(["--loadModel", str(_path(model_path)), "--html.createReport", str(out)])
    res["index"] = str(out / "index.html")
    return res


@mcp.tool()
def export_csv(model_path: str, output_dir: str) -> dict:
    """Export elements, relations and properties to CSV via the Archi command line."""
    out = Path(output_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    return _run_cli(["--loadModel", str(_path(model_path)), "--csv.export", str(out)])


@mcp.tool()
def export_open_exchange(model_path: str, output_file: str) -> dict:
    """Export to the Open Group ArchiMate Exchange Format (.xml) via the Archi command line."""
    out = Path(output_file).expanduser().resolve()
    return _run_cli(["--loadModel", str(_path(model_path)), "--xmlexchange.export", str(out)])


@mcp.tool()
def export_view_images(model_path: str, output_dir: str) -> dict:
    """Render every view to PNG by generating the HTML report and collecting its images,
    so Claude (or you) can look at the diagrams."""
    out = Path(output_dir).expanduser().resolve()
    tmp = out / "_report"
    res = _run_cli(["--loadModel", str(_path(model_path)), "--html.createReport", str(tmp)])
    images = []
    for f in glob.glob(str(tmp / "**" / "images" / "*.png"), recursive=True):
        dst = out / Path(f).name
        shutil.copy2(f, dst)
        images.append(str(dst))
    res["images"] = sorted(images)
    return res


if __name__ == "__main__":
    mcp.run()
