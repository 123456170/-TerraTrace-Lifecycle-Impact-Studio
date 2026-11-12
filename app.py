"""
EcoLedger LCA — Product Lifecycle Environmental Impact Calculator (Project 014)
Single-file Streamlit application. No API keys. All data is SYNTHETIC and clearly labeled.

Run on Windows (PowerShell):
    python -m venv .venv
    .\\.venv\\Scripts\\Activate.ps1
    pip install -r requirements.txt
    streamlit run app.py

The app auto-loads a synthetic demo portfolio, auto-runs the Monte Carlo simulation,
and is fully interactive on first paint. Roles: Administrator / Analyst / Reviewer /
Stakeholder (switch in the sidebar to test server-side-style permission guards).
"""
from __future__ import annotations

import base64
import copy
import hashlib
import html as html_lib
import inspect
import io
import json
import math
import random
import time
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit.components.v1 import html as components_html

# ══════════════════════════════ CONSTANTS & PALETTE ══════════════════════════════
APP_NAME = "EcoLedger LCA"
ENGINE_VERSION = "1.0.0"
SCHEMA_VERSION = 1
BASE_FACTOR_VERSION = "SYN-2026.1"

FOREST, LEAF, LIME = "#0E3B2E", "#2E7D5B", "#A8E10C"
AMBER, SKY, TERRA = "#F2A007", "#2D9CDB", "#E4572E"
PURPLE, MAGENTA, INK = "#7D4FBF", "#D81E5B", "#14231C"
PAPER, SURFACE = "#F6F5EF", "#FFFFFF"

STAGES = ["Raw materials", "Manufacturing", "Transport", "Use phase", "End of life"]
STAGE_COLORS = {"Raw materials": LEAF, "Manufacturing": AMBER, "Transport": SKY,
                "Use phase": PURPLE, "End of life": TERRA}
SERIES = [FOREST, LEAF, SKY, AMBER, MAGENTA, PURPLE, TERRA, "#5B8C5A"]

STATUSES = ["Draft", "Validated", "In Review", "Approved", "Changes Requested", "Archived"]
STATUS_COLORS = {"Draft": "#5C665F", "Validated": SKY, "In Review": AMBER,
                 "Approved": LEAF, "Changes Requested": TERRA, "Archived": "#6B705C"}
QUALITY_SIGMA = {"measured": 0.05, "estimated": 0.15, "proxy": 0.30}
QUALITY_SCORE = {"measured": 1.0, "estimated": 0.7, "proxy": 0.4}
FACTOR_SIGMA = 0.10
EOL_RECOVERY = 0.90
KWH_TO_MJ = 3.6  # documented physical conversion (electricity CED uses grid primary factors)

USERS = {"Administrator": "Morgan Diaz", "Analyst": "Ava Chen",
         "Reviewer": "Dr. Ravi Patel", "Stakeholder": "Sam Lee"}
PERMS = {
    "Administrator": {"view", "edit", "create", "delete", "submit", "approve", "override",
                      "archive", "export", "factors", "restore", "reset"},
    "Analyst": {"view", "edit", "create", "delete", "submit", "export"},
    "Reviewer": {"view", "approve", "override", "export"},
    "Stakeholder": {"view", "export_approved"},
}
TRANSITIONS = {"Draft": ["Validated"], "Validated": ["In Review", "Draft"],
               "In Review": ["Approved", "Changes Requested"],
               "Changes Requested": ["Draft", "Validated"],
               "Approved": ["Archived", "Draft"], "Archived": ["Draft"]}
ACTION_PERM = {"Validated": "submit", "In Review": "submit", "Approved": "approve",
               "Changes Requested": "approve", "Archived": "archive", "Draft": "edit"}
ACTION_VERB = {"Validated": "validate", "In Review": "submit", "Approved": "approve",
               "Changes Requested": "request_changes", "Archived": "archive", "Draft": "reopen"}

# ══════════════════════════════ REFERENCE FACTORS (SYNTHETIC) ══════════════════════════════
def base_factors() -> dict:
    """Illustrative synthetic factors — NOT a licensed LCA database. ghg: kg CO2e/unit,
    mj: MJ/unit. ghg_rec == ghg means no established recycling credit pathway."""
    return {
        "materials": {
            "Steel":                  {"ghg": 2.10, "ghg_rec": 0.70, "mj": 25.0, "mj_rec": 9.0,  "incin": 0.0, "landfill": 0.02},
            "Aluminium":              {"ghg": 11.0, "ghg_rec": 1.20, "mj": 155.0,"mj_rec": 18.0, "incin": 0.0, "landfill": 0.02},
            "Copper":                 {"ghg": 3.20, "ghg_rec": 0.90, "mj": 45.0, "mj_rec": 12.0, "incin": 0.0, "landfill": 0.02},
            "Brass":                  {"ghg": 3.50, "ghg_rec": 1.10, "mj": 50.0, "mj_rec": 16.0, "incin": 0.0, "landfill": 0.02},
            "Glass":                  {"ghg": 1.10, "ghg_rec": 0.70, "mj": 15.0, "mj_rec": 10.0, "incin": 0.0, "landfill": 0.02},
            "ABS plastic":            {"ghg": 3.40, "ghg_rec": 1.10, "mj": 80.0, "mj_rec": 30.0, "incin": 2.9, "landfill": 0.01},
            "PET":                    {"ghg": 2.80, "ghg_rec": 1.00, "mj": 70.0, "mj_rec": 28.0, "incin": 2.3, "landfill": 0.01},
            "PUR foam":               {"ghg": 4.20, "ghg_rec": 4.20, "mj": 95.0, "mj_rec": 95.0, "incin": 2.6, "landfill": 0.02},
            "Rubber":                 {"ghg": 2.90, "ghg_rec": 1.40, "mj": 60.0, "mj_rec": 25.0, "incin": 2.8, "landfill": 0.02},
            "Cardboard":              {"ghg": 0.90, "ghg_rec": 0.50, "mj": 18.0, "mj_rec": 10.0, "incin": 1.1, "landfill": 0.90},
            "Concrete":               {"ghg": 0.13, "ghg_rec": 0.10, "mj": 1.2,  "mj_rec": 1.0,  "incin": 0.0, "landfill": 0.01},
            "Li-ion battery":         {"ghg": 12.5, "ghg_rec": 8.00, "mj": 160.0,"mj_rec": 100.0,"incin": 1.0, "landfill": 0.10},
            "PCB / electronics":      {"ghg": 38.0, "ghg_rec": 38.0, "mj": 400.0,"mj_rec": 400.0,"incin": 1.2, "landfill": 0.05},
            "LED module":             {"ghg": 28.0, "ghg_rec": 28.0, "mj": 320.0,"mj_rec": 320.0,"incin": 1.2, "landfill": 0.05},
            "Recycled plastic lumber":{"ghg": 1.10, "ghg_rec": 0.90, "mj": 20.0, "mj_rec": 16.0, "incin": 2.3, "landfill": 0.01},
        },
        "grids": {
            "EU average":   {"ghg": 0.380, "mj": 9.5},
            "US average":   {"ghg": 0.420, "mj": 10.2},
            "China":        {"ghg": 0.580, "mj": 13.0},
            "Nordic hydro": {"ghg": 0.060, "mj": 1.5},
            "On-site solar":{"ghg": 0.045, "mj": 1.2},
        },
        "transport": {  # per tonne-km
            "Sea freight": {"ghg": 0.016, "mj": 0.22},
            "Rail":        {"ghg": 0.028, "mj": 0.38},
            "Truck (HGV)": {"ghg": 0.110, "mj": 1.50},
            "Air freight": {"ghg": 0.600, "mj": 8.50},
        },
    }

MATERIAL_NAMES = list(base_factors()["materials"])
GRID_NAMES = list(base_factors()["grids"])
TRANSPORT_NAMES = list(base_factors()["transport"])

# ══════════════════════════════ SMALL UTILITIES ══════════════════════════════
def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def days_ago(n: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat(timespec="seconds")

def uid() -> str:
    return uuid.uuid4().hex[:8].upper()

def sha256_text(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8", "replace")).hexdigest()

def _num(x, default=0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default

def fmt(x) -> str:  return f"{_num(x):,.1f}"
def fmt0(x) -> str: return f"{_num(x):,.0f}"

def _w(fn) -> dict:
    """Width kwarg compatible across Streamlit versions."""
    try:
        if "width" in inspect.signature(fn).parameters:
            return {"width": "stretch"}
    except (TypeError, ValueError):
        pass
    return {"use_container_width": True}

def fig_layout(fig, height=340, title=None, **kw):
    fig.update_layout(
        template="plotly_white", height=height,
        margin=dict(l=10, r=10, t=44 if title else 14, b=10),
        font=dict(family="Inter, Segoe UI, sans-serif", color=INK, size=12),
        title=dict(text=title, font=dict(family="Space Grotesk, Segoe UI, sans-serif", size=15)) if title else None,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        legend=dict(orientation="h", y=-0.22), **kw)
    return fig

def pill(status: str) -> str:
    return (f"<span style='background:{STATUS_COLORS.get(status, '#5C665F')};color:#fff;"
            f"padding:3px 11px;border-radius:999px;font-size:12px;font-weight:700;"
            f"white-space:nowrap'>{status}</span>")

def role_can(role: str, perm: str) -> bool:
    return perm in PERMS.get(role, set())

def can(perm: str) -> bool:
    return role_can(st.session_state.role, perm)

def guard(perm: str):
    if not can(perm):
        raise PermissionError(f"Role '{st.session_state.role}' is not allowed to '{perm}'. Switch roles in the sidebar.")

def current_user() -> str:
    return USERS[st.session_state.role]

def make_event(actor, role, action, entity, entity_id, details="", ts=None) -> dict:
    return {"ts": ts or utcnow(), "actor": actor, "role": role, "action": action,
            "entity": entity, "entity_id": entity_id, "details": details}

def log_event(action, entity, entity_id, details=""):
    st.session_state.db["audit"].append(
        make_event(current_user(), st.session_state.role, action, entity, entity_id, details))

def current_factors() -> dict:
    db = st.session_state.db
    return db["factor_versions"][db["factor_version"]]

def active_products() -> list:
    return [p for p in st.session_state.db["products"] if p["status"] != "Archived"]

def product_by_id(pid):
    return next((p for p in st.session_state.db["products"] if p["id"] == pid), None)

def result_digest(r: dict) -> str:
    return hashlib.sha256(json.dumps(r["lines"], sort_keys=True).encode()).hexdigest()[:16]

def safe_results(p, factors):
    try:
        return compute_impacts(p, factors), None
    except (ValueError, KeyError) as ex:
        return None, str(ex)

# ══════════════════════════════ DOMAIN ENGINE (UI-independent, deterministic) ══════════════════════════════
def combine_sigma(q: str) -> float:
    return math.sqrt(QUALITY_SIGMA.get(q, 0.30) ** 2 + FACTOR_SIGMA ** 2)

def mk_line(stage, item, qty, unit, factor_id, factor_val, ghg, mj, quality, formula):
    return {"stage": stage, "item": item, "qty": round(_num(qty), 6), "unit": unit,
            "factor_id": factor_id, "factor": round(_num(factor_val), 6),
            "ghg": float(ghg), "mj": float(mj), "quality": quality,
            "sigma": combine_sigma(quality), "formula": formula}

def compute_impacts(p: dict, factors: dict, mods: dict | None = None) -> dict:
    """Deterministic attributional footprint.
    Materials : m × (virgin·(1−r) + recycled·r)
    Mfg / Use : kWh × grid factor
    Transport : (Σmass/1000) t × km × mode factor
    EoL       : m·recycle·(rec−virgin)·0.90  +  m·incin·f_incin  +  m·landfill·f_landfill
    """
    mods = mods or {}
    lines = []
    mass = 0.0
    for row in p.get("bom", []):
        mat = row.get("material")
        if mat not in factors["materials"]:
            raise ValueError(f"Unknown material '{mat}' in BOM — fix the line or extend the reference catalog.")
        f = factors["materials"][mat]
        m, r = _num(row.get("mass_kg")), _num(row.get("recycled_pct")) / 100.0
        gf = f["ghg"] * (1 - r) + f["ghg_rec"] * r
        mf = f["mj"] * (1 - r) + f["mj_rec"] * r
        mass += m
        lines.append(mk_line("Raw materials", row.get("component", "?"), m, "kg",
                             f"MAT:{mat}", gf, m * gf, m * mf, row.get("quality", "proxy"),
                             f"{m:g} kg × blended {gf:.3f} kgCO₂e/kg (recycled content {r:.0%})"))
    mfg = p.get("manufacturing", {})
    g = factors["grids"].get(mfg.get("grid"))
    if g is None:
        raise ValueError(f"Unknown manufacturing grid '{mfg.get('grid')}'.")
    k = _num(mfg.get("kwh"))
    lines.append(mk_line("Manufacturing", "Assembly & fabrication", k, "kWh",
                         f"GRID:{mfg.get('grid')}", g["ghg"], k * g["ghg"], k * g["mj"],
                         mfg.get("quality", "estimated"),
                         f"{k:g} kWh × {g['ghg']:.3f} kgCO₂e/kWh ({mfg.get('grid')})"))
    t = mass / 1000.0
    for leg in p.get("transport", []):
        tf = factors["transport"].get(leg.get("mode"))
        if tf is None:
            raise ValueError(f"Unknown transport mode '{leg.get('mode')}'.")
        d = _num(leg.get("distance_km"))
        tkm = t * d
        lines.append(mk_line("Transport", leg["mode"], tkm, "t·km", f"TRN:{leg['mode']}",
                             tf["ghg"], tkm * tf["ghg"], tkm * tf["mj"], "estimated",
                             f"{t:.4f} t × {d:g} km × {tf['ghg']:.3f} kgCO₂e/t·km"))
    use = p.get("use", {})
    ug = factors["grids"].get(use.get("grid"))
    if ug is None:
        raise ValueError(f"Unknown use-phase grid '{use.get('grid')}'.")
    lt = _num(p.get("lifetime_years"))
    uk = _num(use.get("kwh_per_year")) * lt
    um = _num(mods.get("use_grid_mult"), 1.0)
    lines.append(mk_line("Use phase", f"{lt:g}-year operation", uk, "kWh",
                         f"GRID:{use.get('grid')}", ug["ghg"] * um, uk * ug["ghg"] * um,
                         uk * ug["mj"], use.get("quality", "estimated"),
                         f"{_num(use.get('kwh_per_year')):g} kWh/y × {lt:g} y × {ug['ghg']:.3f} kgCO₂e/kWh"
                         + (f" × grid multiplier {um:.2f}" if um != 1.0 else "")))
    e = p.get("eol", {})
    rr, ir, lr = _num(e.get("recycle_pct")) / 100, _num(e.get("incinerate_pct")) / 100, _num(e.get("landfill_pct")) / 100
    for row in p.get("bom", []):
        f = factors["materials"][row["material"]]
        m = _num(row.get("mass_kg"))
        credit = m * rr * (f["ghg_rec"] - f["ghg"]) * EOL_RECOVERY
        incin = m * ir * f["incin"]
        land = m * lr * f["landfill"]
        mj_credit = m * rr * (f["mj_rec"] - f["mj"]) * EOL_RECOVERY
        net = credit + incin + land
        lines.append(mk_line("End of life", row.get("component", "?"), m, "kg",
                             f"EOL:{row['material']}", net / m if m else 0.0, net, mj_credit,
                             e.get("quality", "proxy"),
                             f"credit {credit:.2f} + incineration {incin:.2f} + landfill {land:.2f} kgCO₂e "
                             f"(shares {rr:.0%}/{ir:.0%}/{lr:.0%}, recovery {EOL_RECOVERY:.0%})"))
    stages = {s: {"ghg": sum(l["ghg"] for l in lines if l["stage"] == s),
                  "mj": sum(l["mj"] for l in lines if l["stage"] == s)} for s in STAGES}
    total = sum(l["ghg"] for l in lines)
    return {"lines": lines, "stages": stages, "total_ghg": total,
            "total_mj": sum(l["mj"] for l in lines), "mass_kg": mass,
            "per_year_ghg": total / lt if lt > 0 else float("nan")}

def validate_product(p: dict) -> list:
    issues = []
    err = lambda f, m: issues.append({"severity": "error", "field": f, "message": m})
    warn = lambda f, m: issues.append({"severity": "warning", "field": f, "message": m})
    if not str(p.get("name", "")).strip(): err("name", "Product name is required")
    lt = _num(p.get("lifetime_years"))
    if not 0 < lt <= 100: err("lifetime_years", "Lifetime must be in (0, 100] years")
    elif lt > 30: warn("lifetime_years", "Lifetime > 30 y — verify the assumption")
    if not p.get("bom"): err("bom", "At least one BOM line is required")
    comps = set()
    for j, row in enumerate(p.get("bom", [])):
        f = f"bom[{j}]"
        m = _num(row.get("mass_kg"))
        if m <= 0: err(f, f"'{row.get('component','?')}': mass_kg must be > 0")
        elif m > 5000: warn(f, f"mass {m:g} kg — verify scale")
        if row.get("material") not in MATERIAL_NAMES: err(f, f"unknown material '{row.get('material')}'")
        if not 0 <= _num(row.get("recycled_pct"), -1) <= 100: err(f, "recycled_pct must be 0–100")
        if row.get("quality") not in QUALITY_SIGMA: err(f, "quality must be measured / estimated / proxy")
        c = str(row.get("component", "")).strip()
        if not c: err(f, "component name required")
        elif c in comps: warn(f, f"duplicate component name '{c}'")
        comps.add(c)
    for sect, key in [("manufacturing", "kwh"), ("use", "kwh_per_year")]:
        s = p.get(sect, {})
        if _num(s.get(key), -1) < 0: err(sect, "energy must be ≥ 0")
        if s.get("grid") not in GRID_NAMES: err(sect, f"unknown grid '{s.get('grid')}'")
    if not p.get("transport"): warn("transport", "No transport legs — assumes zero inbound logistics")
    for j, leg in enumerate(p.get("transport", [])):
        if leg.get("mode") not in TRANSPORT_NAMES: err(f"transport[{j}]", f"unknown mode '{leg.get('mode')}'")
        d = _num(leg.get("distance_km"), -1)
        if not 0 <= d <= 100000: err(f"transport[{j}]", "distance must be 0–100,000 km")
        elif leg.get("mode") == "Air freight" and d > 8000: warn(f"transport[{j}]", "air leg > 8,000 km — confirm modal choice")
    e = p.get("eol", {})
    shares = [_num(e.get(k), -1) for k in ("recycle_pct", "incinerate_pct", "landfill_pct")]
    if any(not 0 <= v <= 100 for v in shares): err("eol", "each end-of-life share must be 0–100%")
    tot = sum(shares)
    if abs(tot - 100) > 0.5: err("eol", f"End-of-life shares sum to {tot:g}% — must equal 100%")
    if p.get("bom") and all(r.get("quality") == "proxy" for r in p["bom"]):
        warn("quality", "All BOM lines use proxy data — expect wide uncertainty")
    return issues

def mc_draws(lines: list, n: int, seed: int) -> np.ndarray:
    base = np.array([l["ghg"] for l in lines], dtype=float)
    sig = np.array([l["sigma"] for l in lines], dtype=float)
    if base.size == 0:
        return np.zeros(n)
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n, base.size))
    return (base * np.clip(1.0 + sig * z, 0.0, None)).sum(axis=1)

@st.cache_data(show_spinner=False)
def cached_mc(p_json: str, f_json: str, n: int, seed: int) -> dict:
    r = compute_impacts(json.loads(p_json), json.loads(f_json))
    t = mc_draws(r["lines"], n, seed)
    return {"totals": t, "p5": float(np.percentile(t, 5)), "p50": float(np.percentile(t, 50)),
            "p95": float(np.percentile(t, 95)), "mean": float(t.mean()), "std": float(t.std())}

@st.cache_data(show_spinner=False)
def cached_portfolio_mc(prods_json: str, f_json: str, n: int, seed: int) -> dict:
    f = json.loads(f_json)
    lines = []
    for p in json.loads(prods_json):
        try:
            lines.extend(compute_impacts(p, f)["lines"])
        except (ValueError, KeyError):
            continue
    t = mc_draws(lines, n, seed)
    return {"totals": t, "p5": float(np.percentile(t, 5)), "p50": float(np.percentile(t, 50)),
            "p95": float(np.percentile(t, 95)), "mean": float(t.mean()), "std": float(t.std())}

def scenario_defaults(p: dict) -> dict:
    return {"recycled_boost": 0, "use_eff": 0, "lifetime_mult": 1.0, "transport_cut": 0,
            "sea_to_rail": False, "use_grid_mult": 1.0,
            "mfg_grid": p["manufacturing"]["grid"],
            "eol_target": int(round(_num(p["eol"]["recycle_pct"])))}

def apply_scenario(p: dict, s: dict, factors: dict):
    q = copy.deepcopy(p)
    for row in q["bom"]:
        row["recycled_pct"] = min(100.0, _num(row["recycled_pct"]) + s["recycled_boost"])
    q["use"]["kwh_per_year"] = _num(q["use"]["kwh_per_year"]) * (1 - s["use_eff"] / 100.0)
    q["lifetime_years"] = _num(q["lifetime_years"]) * s["lifetime_mult"]
    q["transport"] = [{"mode": ("Rail" if s["sea_to_rail"] and l["mode"] == "Sea freight" else l["mode"]),
                       "distance_km": _num(l["distance_km"]) * (1 - s["transport_cut"] / 100.0)}
                      for l in q["transport"]]
    q["manufacturing"]["grid"] = s["mfg_grid"]
    t = float(s["eol_target"]); rest = 100.0 - t
    oi, ol = _num(q["eol"]["incinerate_pct"]), _num(q["eol"]["landfill_pct"])
    b = oi + ol
    q["eol"] = {"recycle_pct": t,
                "incinerate_pct": rest * oi / b if b > 0 else rest / 2,
                "landfill_pct": rest * ol / b if b > 0 else rest / 2,
                "quality": q["eol"].get("quality", "proxy")}
    return q, compute_impacts(q, factors, mods={"use_grid_mult": s["use_grid_mult"]})

# ══════════════════════════════ IMPORT PIPELINE (pure + testable) ══════════════════════════════
IMPORT_COLS = ["product_id", "product_name", "category", "functional_unit", "lifetime_years",
               "component", "material", "mass_kg", "recycled_pct", "data_quality",
               "mfg_kwh", "mfg_grid", "use_kwh_per_year", "use_grid",
               "transport_mode", "transport_km",
               "eol_recycle_pct", "eol_incinerate_pct", "eol_landfill_pct"]
NUM_COLS = ["lifetime_years", "mass_kg", "recycled_pct", "mfg_kwh", "use_kwh_per_year",
            "transport_km", "eol_recycle_pct", "eol_incinerate_pct", "eol_landfill_pct"]
STR_COLS = ["product_id", "product_name", "category", "functional_unit", "component",
            "material", "data_quality", "mfg_grid", "use_grid", "transport_mode"]
PROD_LEVEL = ["product_name", "category", "functional_unit", "lifetime_years", "mfg_kwh",
              "mfg_grid", "use_kwh_per_year", "use_grid",
              "eol_recycle_pct", "eol_incinerate_pct", "eol_landfill_pct"]

def product_from_rows(rows: pd.DataFrame) -> dict:
    r0 = rows.iloc[0]
    bom = [{"component": r["component"], "material": r["material"], "mass_kg": float(r["mass_kg"]),
            "recycled_pct": float(r["recycled_pct"]), "quality": r["data_quality"]}
           for _, r in rows.iterrows()]
    legs, seen = [], set()
    for _, r in rows.iterrows():
        key = (r["transport_mode"], float(r["transport_km"]))
        if key not in seen:
            seen.add(key); legs.append({"mode": key[0], "distance_km": key[1]})
    now = utcnow()
    return {"id": r0["product_id"], "name": r0["product_name"],
            "category": r0["category"] or "General equipment",
            "functional_unit": r0["functional_unit"] or f"1 unit · {float(r0['lifetime_years']):g} y service",
            "status": "Draft", "schema_version": SCHEMA_VERSION,
            "created_utc": now, "updated_utc": now, "lifetime_years": float(r0["lifetime_years"]),
            "bom": bom,
            "manufacturing": {"kwh": float(r0["mfg_kwh"]), "grid": r0["mfg_grid"], "quality": "estimated"},
            "use": {"kwh_per_year": float(r0["use_kwh_per_year"]), "grid": r0["use_grid"], "quality": "estimated"},
            "transport": legs,
            "eol": {"recycle_pct": float(r0["eol_recycle_pct"]), "incinerate_pct": float(r0["eol_incinerate_pct"]),
                    "landfill_pct": float(r0["eol_landfill_pct"]), "quality": "proxy"},
            "override": None, "notes": "", "source": {"kind": "import"}}

def parse_import(df: pd.DataFrame, existing_ids: set, factors: dict) -> dict:
    res = {"missing_cols": [c for c in IMPORT_COLS if c not in df.columns], "row_errors": [],
           "preview": None, "valid_products": [], "rejected_groups": {}, "n_rows": int(len(df))}
    if res["missing_cols"]:
        return res
    d = df[IMPORT_COLS].copy().reset_index(drop=True)
    for c in NUM_COLS:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    for c in STR_COLS:
        d[c] = d[c].where(d[c].notna(), "").astype(str).str.strip()
    errs = [[] for _ in range(len(d))]
    def bad(i, msg):
        if msg not in errs[i]:
            errs[i].append(msg)
    for i in range(len(d)):
        r = d.iloc[i]
        for c in ["product_id", "product_name", "component", "material", "data_quality",
                  "mfg_grid", "use_grid", "transport_mode"]:
            if r[c] == "": bad(i, f"'{c}' is required")
        for c in NUM_COLS:
            if pd.isna(r[c]): bad(i, f"'{c}' must be a number")
        if not pd.isna(r["mass_kg"]):
            if r["mass_kg"] <= 0: bad(i, "mass_kg must be > 0")
            elif r["mass_kg"] > 100000: bad(i, "mass_kg out of range (> 100,000)")
        if not pd.isna(r["recycled_pct"]) and not 0 <= r["recycled_pct"] <= 100:
            bad(i, "recycled_pct must be 0–100")
        if not pd.isna(r["lifetime_years"]) and not 0 < r["lifetime_years"] <= 100:
            bad(i, "lifetime_years must be in (0, 100]")
        for c in ["mfg_kwh", "use_kwh_per_year", "transport_km"]:
            if not pd.isna(r[c]) and r[c] < 0: bad(i, f"{c} must be ≥ 0")
        if r["material"] and r["material"] not in factors["materials"]:
            bad(i, f"unknown material '{r['material']}' — see Reference factors")
        if r["transport_mode"] and r["transport_mode"] not in factors["transport"]:
            bad(i, f"unknown transport mode '{r['transport_mode']}'")
        for c in ["mfg_grid", "use_grid"]:
            if r[c] and r[c] not in factors["grids"]: bad(i, f"unknown grid '{r[c]}'")
        if r["data_quality"] and r["data_quality"] not in QUALITY_SIGMA:
            bad(i, "data_quality must be measured / estimated / proxy")
    seen = {}
    for i in range(len(d)):
        pid, comp = d.iloc[i]["product_id"], d.iloc[i]["component"]
        if pid and comp:
            key = (pid, comp)
            if key in seen:
                bad(i, f"duplicate key ({pid}, {comp}) — also file row {seen[key] + 2}")
                bad(seen[key], f"duplicate key ({pid}, {comp}) — also file row {i + 2}")
            else:
                seen[key] = i
    groups = {}
    for i in range(len(d)):
        groups.setdefault(d.iloc[i]["product_id"] or "(missing id)", []).append(i)
    valid, rejected = [], {}
    for pid, idxs in groups.items():
        gmsgs = [m for i in idxs for m in errs[i]]
        r0 = d.iloc[idxs[0]]
        for c in PROD_LEVEL:
            vals = {str(d.iloc[i][c]) for i in idxs}
            if len(vals) > 1:
                gmsgs.append(f"conflicting '{c}' across rows of {pid}: {sorted(vals)}")
        if pid in existing_ids:
            gmsgs.append(f"product_id '{pid}' already exists — use a new id (validated records are never overwritten)")
        eol_vals = [r0[c] for c in ("eol_recycle_pct", "eol_incinerate_pct", "eol_landfill_pct")]
        if not any(pd.isna(v) for v in eol_vals):
            s = float(sum(eol_vals))
            if abs(s - 100) > 0.5:
                gmsgs.append(f"end-of-life shares sum to {s:g}% (must be 100%)")
        if not gmsgs:
            p = product_from_rows(d.iloc[idxs])
            gmsgs = [x["message"] for x in validate_product(p) if x["severity"] == "error"]
        if gmsgs:
            rejected[pid] = sorted(set(gmsgs))
            for i in idxs:
                for m in gmsgs: bad(i, m)
        else:
            valid.append(product_from_rows(d.iloc[idxs]))
    preview = d.copy()
    preview.insert(0, "import_status", ["❌ rejected" if errs[i] else "✅ valid" for i in range(len(d))])
    preview["errors"] = ["; ".join(e) for e in errs]
    res.update(row_errors=errs, preview=preview, valid_products=valid, rejected_groups=rejected)
    return res

VALID_SAMPLE = """product_id,product_name,category,functional_unit,lifetime_years,component,material,mass_kg,recycled_pct,data_quality,mfg_kwh,mfg_grid,use_kwh_per_year,use_grid,transport_mode,transport_km,eol_recycle_pct,eol_incinerate_pct,eol_landfill_pct
P-SOL-01,Solar Bollard Light,Municipal lighting,1 bollard · 10 y service,10,Housing,Aluminium,3.4,40,estimated,6.5,China,0,EU average,Sea freight,16000,65,20,15
P-SOL-01,Solar Bollard Light,Municipal lighting,1 bollard · 10 y service,10,Battery pack,Li-ion battery,0.9,0,estimated,6.5,China,0,EU average,Sea freight,16000,65,20,15
P-SOL-01,Solar Bollard Light,Municipal lighting,1 bollard · 10 y service,10,PV panel,PCB / electronics,0.6,0,proxy,6.5,China,0,EU average,Truck (HGV),300,65,20,15
P-BIN-01,Smart Waste Bin,Municipal equipment,1 bin · 12 y service,12,Shell,Steel,18,60,measured,14,EU average,1.8,EU average,Rail,900,75,15,10
P-BIN-01,Smart Waste Bin,Municipal equipment,1 bin · 12 y service,12,Lid,ABS plastic,2.6,25,estimated,14,EU average,1.8,EU average,Rail,900,75,15,10
P-BIN-01,Smart Waste Bin,Municipal equipment,1 bin · 12 y service,12,Sensor unit,PCB / electronics,0.35,0,proxy,14,EU average,1.8,EU average,Truck (HGV),120,75,15,10
"""

BROKEN_SAMPLE = """product_id,product_name,category,functional_unit,lifetime_years,component,material,mass_kg,recycled_pct,data_quality,mfg_kwh,mfg_grid,use_kwh_per_year,use_grid,transport_mode,transport_km,eol_recycle_pct,eol_incinerate_pct,eol_landfill_pct
P-LED-001,Clone attempt,Municipal lighting,1 unit,15,Housing,Aluminium,6.2,30,estimated,18,China,378,EU average,Sea freight,18500,70,20,10
P-BAD-01,Broken Widget,General equipment,1 unit,10,Frame,Unobtainium,5,0,estimated,5,Mars,10,EU average,Teleport,500,80,30,20
P-BAD-01,Broken Widget,General equipment,1 unit,12,Frame,Steel,-2,0,estimated,5,Mars,10,EU average,Teleport,500,80,30,20
P-BAD-01,Broken Widget,General equipment,1 unit,10,,Glass,1.2,0,estimated,5,Mars,10,EU average,Teleport,500,80,30,20
P-BAD-01,Broken Widget,General equipment,1 unit,10,Panel,Glass,1.2,150,estimated,5,Mars,10,EU average,Teleport,500,80,30,20
"""

# ══════════════════════════════ SYNTHETIC SEED DATA ══════════════════════════════
def _mk(pid, name, category, fu, lifetime, bom, mfg, use, transport, eol, status, age, notes=""):
    ts = days_ago(age)
    return {"id": pid, "name": name, "category": category, "functional_unit": fu,
            "status": status, "schema_version": SCHEMA_VERSION,
            "created_utc": ts, "updated_utc": ts, "lifetime_years": float(lifetime),
            "bom": [{"component": c, "material": m, "mass_kg": float(k), "recycled_pct": float(r), "quality": q}
                    for c, m, k, r, q in bom],
            "manufacturing": {"kwh": float(mfg[0]), "grid": mfg[1], "quality": "estimated"},
            "use": {"kwh_per_year": float(use[0]), "grid": use[1], "quality": "estimated"},
            "transport": [{"mode": m, "distance_km": float(d)} for m, d in transport],
            "eol": {"recycle_pct": float(eol[0]), "incinerate_pct": float(eol[1]),
                    "landfill_pct": float(eol[2]), "quality": "proxy"},
            "override": None, "notes": notes, "source": {"kind": "demo", "file": "synthetic_seed", "batch_id": "B-DEMO"}}

def demo_products() -> list:
    return [
        _mk("P-LED-001", "LED Streetlight SL-90", "Municipal lighting", "1 luminaire · 15 y service", 15,
            [("Housing", "Aluminium", 6.2, 30, "estimated"), ("Bracket", "Steel", 3.1, 40, "measured"),
             ("LED module", "LED module", 0.45, 0, "proxy"), ("Lens", "Glass", 0.8, 0, "estimated"),
             ("Wiring", "Copper", 0.35, 20, "estimated"), ("Driver", "PCB / electronics", 0.22, 0, "proxy")],
            (18, "China"), (378, "EU average"),
            [("Sea freight", 18500), ("Truck (HGV)", 420)], (70, 20, 10), "Approved", 32,
            "90 W · 4,200 h/y. Supplier declaration on file (synthetic)."),
        _mk("P-EBK-002", "Municipal E-Bike Fleet Unit", "Mobility", "1 e-bike · 8 y service", 8,
            [("Frame", "Aluminium", 9.0, 30, "estimated"), ("Battery", "Li-ion battery", 3.2, 0, "estimated"),
             ("Fork", "Steel", 4.0, 50, "measured"), ("Tyres", "Rubber", 1.6, 10, "proxy"),
             ("Motor windings", "Copper", 0.9, 15, "estimated"), ("Body panels", "ABS plastic", 1.2, 20, "estimated")],
            (26, "China"), (72, "EU average"),
            [("Sea freight", 15000), ("Truck (HGV)", 350)], (65, 20, 15), "In Review", 21,
            "6,000 km/y fleet duty cycle · 12 Wh/km."),
        _mk("P-WMT-003", "Smart Water Meter WM-2", "Water infrastructure", "1 meter · 12 y service", 12,
            [("Body", "Brass", 0.4, 35, "measured"), ("Shell", "ABS plastic", 0.35, 25, "estimated"),
             ("Electronics", "PCB / electronics", 0.12, 0, "proxy"), ("Battery", "Li-ion battery", 0.08, 0, "proxy"),
             ("Window", "Glass", 0.05, 0, "estimated")],
            (1.2, "EU average"), (0, "EU average"),
            [("Truck (HGV)", 600)], (60, 25, 15), "Draft", 9,
            "Battery-powered LoRa unit — materials-dominated footprint."),
        _mk("P-CHR-004", "ErgoPro Task Chair", "Workplace furniture", "1 chair · 10 y service", 10,
            [("Base", "Steel", 8.0, 40, "measured"), ("Gas column", "Aluminium", 3.0, 50, "estimated"),
             ("Seat foam", "PUR foam", 2.2, 0, "proxy"), ("Fabric", "PET", 1.4, 60, "estimated"),
             ("Castors", "ABS plastic", 0.9, 20, "estimated"), ("Packaging", "Cardboard", 1.1, 80, "measured")],
            (12, "EU average"), (0, "EU average"),
            [("Rail", 400), ("Truck (HGV)", 120)], (55, 30, 15), "Approved", 26,
            "Regional manufacturing; take-back scheme pilot."),
        _mk("P-HPT-005", "Rooftop Heat Pump HP-8", "HVAC equipment", "1 heat pump · 15 y service", 15,
            [("Casing", "Steel", 42, 35, "measured"), ("Coil", "Copper", 6.5, 40, "estimated"),
             ("Fins", "Aluminium", 4.2, 30, "estimated"), ("Compressor", "Steel", 12, 20, "estimated"),
             ("Controls", "PCB / electronics", 1.1, 0, "proxy"), ("Fan", "ABS plastic", 1.8, 15, "estimated")],
            (95, "EU average"), (2100, "EU average"),
            [("Rail", 900), ("Truck (HGV)", 150)], (75, 15, 10), "In Review", 14,
            "SCOP assumed 3.0 → 2,100 kWh electricity/y for 6.3 MWh heat."),
        _mk("P-BEN-006", "CityLoop Recycled Bench", "Public realm", "1 bench · 20 y service", 20,
            [("Slats", "Recycled plastic lumber", 24, 95, "measured"), ("Legs", "Steel", 9, 60, "measured"),
             ("Fasteners", "Steel", 0.6, 60, "measured"), ("Plaque", "Aluminium", 0.2, 80, "proxy")],
            (4, "EU average"), (0, "EU average"),
            [("Truck (HGV)", 250)], (85, 10, 5), "Changes Requested", 18,
            "Circular-economy showcase. EoL evidence pending."),
    ]

def build_demo_db() -> dict:
    prods = demo_products()
    audit = []
    for p in prods:
        audit.append(make_event("Ava Chen", "Analyst", "create", "product", p["id"],
                                "Seeded synthetic demo record", ts=p["created_utc"]))
    for pid, act, actor, role, age, det in [
        ("P-LED-001", "validate", "Ava Chen", "Analyst", 30, "0 errors, 1 warning"),
        ("P-LED-001", "submit", "Ava Chen", "Analyst", 29, "Submitted with supplier declaration"),
        ("P-LED-001", "approve", "Dr. Ravi Patel", "Reviewer", 27, "Factors consistent with declaration (synthetic)"),
        ("P-CHR-004", "approve", "Dr. Ravi Patel", "Reviewer", 24, "Take-back scheme documented"),
        ("P-BEN-006", "request_changes", "Dr. Ravi Patel", "Reviewer", 16, "EoL recycling rate lacks a waste-manifest reference — attach evidence"),
        ("P-HPT-005", "submit", "Ava Chen", "Analyst", 13, "SCOP assumption flagged for review"),
        ("P-EBK-002", "submit", "Ava Chen", "Analyst", 12, "Fleet duty cycle from telematics sample"),
    ]:
        audit.append(make_event(actor, role, act, "product", pid, det, ts=days_ago(age)))
    audit.sort(key=lambda a: a["ts"])
    return {"schema_version": SCHEMA_VERSION,
            "products": prods, "audit": audit, "scenarios": [],
            "imports": [{"id": "B-DEMO", "file": "synthetic_seed (embedded)", "sha256": sha256_text("synthetic-seed"),
                         "ts": days_ago(32), "actor": "System", "rows": sum(len(p["bom"]) for p in prods),
                         "imported": len(prods), "rejected": 0, "format": "seed",
                         "raw_text": "(embedded synthetic seed — labeled demo data)"}],
            "factor_versions": {BASE_FACTOR_VERSION: base_factors()},
            "factor_version": BASE_FACTOR_VERSION,
            "saved_views": [{"name": "Approved records", "q": "", "status": ["Approved"], "cat": [], "sort": "Name A–Z"}],
            "trash": [], "settings": {"mc_n": 4000, "mc_seed": 42}}

def fixture_product() -> dict:
    """Hand-checkable unit-test fixture (see self-checks)."""
    now = utcnow()
    return {"id": "T-001", "name": "Test widget", "category": "Test", "functional_unit": "1 unit · 10 y",
            "status": "Draft", "schema_version": 1, "created_utc": now, "updated_utc": now,
            "lifetime_years": 10.0,
            "bom": [{"component": "Body", "material": "Steel", "mass_kg": 2.0, "recycled_pct": 0.0, "quality": "measured"}],
            "manufacturing": {"kwh": 0.0, "grid": "EU average", "quality": "measured"},
            "use": {"kwh_per_year": 100.0, "grid": "EU average", "quality": "measured"},
            "transport": [{"mode": "Truck (HGV)", "distance_km": 100.0}],
            "eol": {"recycle_pct": 80.0, "incinerate_pct": 10.0, "landfill_pct": 10.0, "quality": "measured"},
            "override": None, "notes": "", "source": {"kind": "manual"}}

# ══════════════════════════════ MUTATIONS (guarded, audited) ══════════════════════════════
def transition(p: dict, to: str, note: str = ""):
    guard(ACTION_PERM[to])
    if to not in TRANSITIONS.get(p["status"], []):
        raise ValueError(f"Illegal transition {p['status']} → {to}.")
    if to in ("Validated", "Approved"):
        errors = [i for i in validate_product(p) if i["severity"] == "error"]
        if errors:
            raise ValueError(f"Blocked: {len(errors)} validation error(s) — fix data before '{ACTION_VERB[to]}'.")
    if to == "Changes Requested" and not note.strip():
        raise ValueError("A reviewer note is required when requesting changes.")
    frm = p["status"]
    p["status"] = to
    p["updated_utc"] = utcnow()
    if to in ("Approved", "Changes Requested"):
        p["review"] = {"by": current_user(), "role": st.session_state.role, "note": note, "ts": utcnow()}
    log_event(ACTION_VERB[to], "product", p["id"], f"{frm} → {to}" + (f" · {note}" if note else ""))

def record_locked(p: dict) -> bool:
    return (not can("edit")) or p["status"] in ("Approved", "Archived")

def update_product(p: dict, mutator, detail: str):
    guard("edit")
    if p["status"] in ("Approved", "Archived"):
        raise PermissionError("Record is locked — an Administrator must reopen it first.")
    mutator(p)
    if p["status"] in ("Validated", "In Review", "Changes Requested"):
        p["status"] = "Draft"
        log_event("invalidate", "product", p["id"], "Edited after validation → returned to Draft")
    p["updated_utc"] = utcnow()
    log_event("update", "product", p["id"], detail)

def delete_products(ids: list) -> str:
    guard("delete")
    db = st.session_state.db
    removed, skipped = [], []
    for pid in ids:
        p = product_by_id(pid)
        if p is None:
            continue
        if p["status"] == "Approved" and st.session_state.role != "Administrator":
            skipped.append(pid); continue
        idx = db["products"].index(p)
        removed.append({"p": p, "idx": idx})
    for item in sorted(removed, key=lambda x: -x["idx"]):
        db["products"].pop(item["idx"])
        log_event("delete", "product", item["p"]["id"], "Soft delete — undoable until next deletion")
    if removed:
        db["trash"] = removed  # previous trash purged (documented retention rule)
    msg = f"Deleted {len(removed)} record(s) — Undo available."
    if skipped:
        msg += f" Skipped approved (admin only): {', '.join(skipped)}."
    return msg

def undo_delete():
    db = st.session_state.db
    for item in sorted(db["trash"], key=lambda x: x["idx"]):
        db["products"].insert(min(item["idx"], len(db["products"])), item["p"])
        log_event("restore_deleted", "product", item["p"]["id"], "Undo of soft delete")
    n = len(db["trash"])
    db["trash"] = []
    return f"Restored {n} record(s)."

def duplicate_product(p: dict) -> str:
    guard("create")
    q = copy.deepcopy(p)
    q["id"] = f"P-{uid()[:6]}"
    q["name"] = p["name"] + " (copy)"
    q["status"] = "Draft"
    q["override"] = None
    q.pop("review", None)
    q["created_utc"] = q["updated_utc"] = utcnow()
    q["source"] = {"kind": "manual", "derived_from": p["id"]}
    st.session_state.db["products"].append(q)
    log_event("create", "product", q["id"], f"Duplicated from {p['id']}")
    return q["id"]

# ══════════════════════════════ SELF-CHECK SUITE (real, runnable) ══════════════════════════════
def run_self_checks() -> list:
    F = base_factors()
    fx = fixture_product()
    r = compute_impacts(fx, F)
    close = math.isclose
    def t(name, fn):
        try:
            ok, detail = fn()
        except Exception as ex:
            ok, detail = False, f"{type(ex).__name__}: {ex}"
        return {"check": name, "passed": bool(ok), "detail": detail}
    def materials():
        v = r["stages"]["Raw materials"]["ghg"]
        return close(v, 4.2, rel_tol=1e-9), f"2 kg steel × 2.10 = {v:.3f} kg (expect 4.200)"
    def use():
        v = r["stages"]["Use phase"]["ghg"]
        return close(v, 380.0, rel_tol=1e-9), f"100 kWh/y × 10 y × 0.38 = {v:.2f} (expect 380.00)"
    def transport():
        v = r["stages"]["Transport"]["ghg"]
        return close(v, 0.022, rel_tol=1e-9), f"0.002 t × 100 km × 0.11 = {v:.4f} (expect 0.0220)"
    def eol():
        v = r["stages"]["End of life"]["ghg"]
        return close(v, -2.012, rel_tol=1e-9), f"credit −2.016 + landfill 0.004 = {v:.4f} (expect −2.0120)"
    def total():
        return close(r["total_ghg"], 382.21, rel_tol=1e-9), f"hand total 382.21 vs engine {r['total_ghg']:.4f}"
    def blend():
        q = copy.deepcopy(fx)
        q["bom"] = [{"component": "X", "material": "Aluminium", "mass_kg": 10, "recycled_pct": 50, "quality": "measured"}]
        v = compute_impacts(q, F)["stages"]["Raw materials"]["ghg"]
        return close(v, 61.0, rel_tol=1e-9), f"10 kg Al @50% recycled = {v:.2f} (expect 61.00)"
    def energy():
        v = r["stages"]["Use phase"]["mj"]
        return close(v, 9500.0, rel_tol=1e-9), f"1,000 kWh × 9.5 MJ/kWh = {v:.0f} MJ (expect 9,500)"
    def eol_sum():
        q = copy.deepcopy(fx); q["eol"]["recycle_pct"] = 110
        return any(i["field"] == "eol" and i["severity"] == "error" for i in validate_product(q)), "130% EoL sum rejected"
    def neg_mass():
        q = copy.deepcopy(fx); q["bom"][0]["mass_kg"] = -1
        return any(i["severity"] == "error" for i in validate_product(q)), "negative mass rejected"
    def unknown_mat():
        q = copy.deepcopy(fx); q["bom"][0]["material"] = "Unobtainium"
        return any("unknown material" in i["message"] for i in validate_product(q)), "unknown material rejected"
    def zero_life():
        q = copy.deepcopy(fx); q["lifetime_years"] = 0
        return any(i["field"] == "lifetime_years" for i in validate_product(q)), "zero lifetime rejected"
    def repro():
        d1, d2 = result_digest(r), result_digest(compute_impacts(fixture_product(), base_factors()))
        return d1 == d2, f"digest {d1} == {d2}"
    def mc_seed():
        a, b, c = mc_draws(r["lines"], 512, 7), mc_draws(r["lines"], 512, 7), mc_draws(r["lines"], 512, 8)
        ok = np.allclose(a, b) and not np.allclose(np.percentile(a, 50), np.percentile(c, 50))
        return ok, f"seed 7 P50 {np.percentile(a,50):.2f} reproducible; seed 8 differs"
    def perms():
        ok = (not role_can("Stakeholder", "approve") and not role_can("Analyst", "approve")
              and role_can("Reviewer", "approve") and role_can("Administrator", "restore"))
        return ok, "stakeholder/analyst cannot approve; reviewer can; admin can restore"
    def imp_valid():
        res = parse_import(pd.read_csv(io.StringIO(VALID_SAMPLE)), set(), F)
        ok = len(res["valid_products"]) == 2 and all(
            not [i for i in validate_product(p) if i["severity"] == "error"] for p in res["valid_products"])
        return ok, f"{len(res['valid_products'])} valid products, 0 errors"
    def imp_broken():
        res = parse_import(pd.read_csv(io.StringIO(BROKEN_SAMPLE)), {"P-LED-001"}, F)
        flat = " | ".join(m for e in res["row_errors"] for m in e)
        ok = ("P-BAD-01" in res["rejected_groups"] and "P-LED-001" in res["rejected_groups"]
              and "duplicate" in flat and "unknown material" in flat and "already exists" in flat)
        return ok, f"{len(res['rejected_groups'])} groups rejected with actionable row errors"
    def scen_renorm():
        s = scenario_defaults(fx); s["eol_target"] = 90
        q, _ = apply_scenario(fx, s, F)
        tot = q["eol"]["recycle_pct"] + q["eol"]["incinerate_pct"] + q["eol"]["landfill_pct"]
        return close(tot, 100.0, abs_tol=1e-6), f"scenario EoL renormalized to {tot:.2f}%"
    def audit_ev():
        ev = make_event("A", "Analyst", "create", "product", "T-1", "x")
        return all(k in ev for k in ("ts", "actor", "role", "action", "entity_id")), "audit event carries full provenance"
    return [t("Materials — hand example", materials), t("Use phase — hand example", use),
            t("Transport — hand example", transport), t("End-of-life credit — hand example", eol),
            t("End-to-end total 382.21 kg", total), t("Recycled-content blend", blend),
            t("Energy accounting (MJ)", energy), t("Validation: EoL shares ≠ 100%", eol_sum),
            t("Validation: negative mass", neg_mass), t("Validation: unknown material", unknown_mat),
            t("Validation: zero lifetime", zero_life), t("Deterministic reproducibility", repro),
            t("Monte Carlo seed reproducibility", mc_seed), t("Role permission matrix", perms),
            t("Import: valid sample accepted", imp_valid), t("Import: broken sample rejected", imp_broken),
            t("Scenario EoL renormalization", scen_renorm), t("Audit event provenance", audit_ev)]

# ══════════════════════════════ CSS / THEME ══════════════════════════════
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;600;700&display=swap');
html, body, [class*="css"] { font-family: 'Inter', 'Segoe UI', sans-serif; }
h1, h2, h3 { font-family: 'Space Grotesk', 'Segoe UI', sans-serif !important; color: #0E3B2E; letter-spacing: -0.3px; }
.stApp { background: #F6F5EF; }
#MainMenu, footer { visibility: hidden; }
[data-testid="stHeader"] { background: transparent; }
*::-webkit-scrollbar { width: 10px; height: 10px; }
*::-webkit-scrollbar-track { background: #E9EEE6; border-radius: 8px; }
*::-webkit-scrollbar-thumb { background: #2E7D5B; border-radius: 8px; border: 2px solid #E9EEE6; }
*::-webkit-scrollbar-thumb:hover { background: #3FA372; }
* { scrollbar-width: thin; scrollbar-color: #2E7D5B #E9EEE6; }
[data-testid="stSidebar"] { background: linear-gradient(175deg, #0B3B2E 0%, #124D3B 60%, #17604A 100%); }
[data-testid="stSidebar"] p, [data-testid="stSidebar"] span, [data-testid="stSidebar"] label,
[data-testid="stSidebar"] .stMarkdown, [data-testid="stSidebar"] caption { color: #DFF0E4 !important; }
[data-testid="stSidebar"] h1, [data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 { color: #FFFFFF !important; }
[data-testid="stSidebar"] hr { border-color: rgba(255,255,255,0.18); }
[data-testid="stMetric"] { background: #FFFFFF; border: 1px solid #E1E8DD; border-left: 5px solid #2E7D5B;
  border-radius: 12px; padding: 12px 16px; box-shadow: 0 1px 4px rgba(14,59,46,0.07); }
[data-testid="stMetricLabel"] { color: #4A5A50; font-weight: 600; }
[data-testid="stMetricValue"] { color: #0E3B2E; }
.stButton > button, .stDownloadButton > button { border-radius: 10px; border: 1px solid #C8DCC9;
  font-weight: 600; color: #14231C; background: #FFFFFF; transition: all .15s ease; }
.stButton > button:hover, .stDownloadButton > button:hover { border-color: #2E7D5B; color: #0E3B2E;
  box-shadow: 0 2px 10px rgba(46,125,91,0.25); transform: translateY(-1px); }
button[kind="primary"], [data-testid="stBaseButton-primary"] { background: linear-gradient(135deg, #1E6B4F, #2E8B62) !important;
  color: #FFFFFF !important; border: none !important; }
button[kind="primary"]:hover, [data-testid="stBaseButton-primary"]:hover { filter: brightness(1.1); }
.stTabs [data-baseweb="tab-list"] { gap: 6px; }
.stTabs [data-baseweb="tab"] { font-weight: 600; border-radius: 8px 8px 0 0; }
.stTabs [aria-selected="true"] { color: #0E3B2E !important; }
.eco-hero { background: linear-gradient(120deg, #0B3B2E 0%, #14614A 42%, #1E7A55 72%, #4C9A44 100%);
  border-radius: 18px; padding: 26px 30px; color: #F2F8EF; position: relative; overflow: hidden; margin-bottom: 14px; }
.eco-hero::after { content: ''; position: absolute; right: -70px; top: -70px; width: 260px; height: 260px;
  background: radial-gradient(circle, rgba(168,225,12,0.35) 0%, rgba(168,225,12,0) 70%); }
.eco-hero::before { content: ''; position: absolute; left: 30%; bottom: -90px; width: 300px; height: 200px;
  background: radial-gradient(circle, rgba(45,156,219,0.25) 0%, rgba(45,156,219,0) 70%); }
.eco-hero h1 { color: #FFFFFF !important; margin: 0 0 6px 0; font-size: 30px; }
.eco-hero p { color: #CFE8D2; margin: 0; font-size: 14px; max-width: 760px; }
.eco-chip { display: inline-block; background: rgba(255,255,255,0.14); border: 1px solid rgba(255,255,255,0.25);
  color: #EAF6E6; padding: 3px 12px; border-radius: 999px; font-size: 12px; font-weight: 600; margin: 10px 6px 0 0; }
.eco-callout { background: linear-gradient(100deg, #FFFFFF, #F2F8EC); border: 1px solid #D8E6CF;
  border-left: 5px solid #A8E10C; border-radius: 12px; padding: 14px 18px; margin: 8px 0; }
.eco-warnchip { background: #FFF3CD; color: #7A5A00; border: 1px solid #F2A007; padding: 3px 12px;
  border-radius: 999px; font-size: 12px; font-weight: 700; }
div[data-testid="stExpander"] { background: #FFFFFF; border: 1px solid #E1E8DD; border-radius: 12px; }
</style>
"""

WORKFLOW_DOT = """
digraph G {
  rankdir=LR; bgcolor="transparent";
  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11];
  Draft [fillcolor="#E8ECE7"]; Validated [fillcolor="#D6EAF8"];
  "In Review" [fillcolor="#FDEBD0"]; Approved [fillcolor="#D5F5E3"];
  "Changes Requested" [fillcolor="#FADBD8"]; Archived [fillcolor="#E5E7E9"];
  Exception [fillcolor="#FFF3CD", shape=octagon];
  Draft -> Validated [label="Analyst: validate (0 errors)"];
  Validated -> "In Review" [label="Analyst: submit"];
  "In Review" -> Approved [label="Reviewer: approve"];
  "In Review" -> "Changes Requested" [label="Reviewer: note required"];
  "Changes Requested" -> Draft [label="Analyst: correct"];
  Draft -> Exception [label="missing / conflicting / out-of-range", color="#E4572E"];
  Exception -> Draft [label="fix & revalidate", color="#E4572E"];
  Approved -> Archived [label="Admin"];
  Approved -> Draft [label="Admin: reopen"];
}"""

ERD_DOT = """
digraph E {
  rankdir=LR; bgcolor="transparent"; node [shape=record, fontname="Helvetica", fontsize=10];
  products [label="{products|id (PK)\\lname / category\\lstatus / lifetime_years\\lschema_version\\lcreated / updated_utc\\lsource (provenance)\\l}"];
  bom_lines [label="{bom_lines|product_id (FK)\\lcomponent / material\\lmass_kg / recycled_pct\\lquality pedigree\\l}"];
  transport_legs [label="{transport_legs|product_id (FK)\\lmode / distance_km\\l}"];
  factor_versions [label="{factor_versions|version (PK)\\lmaterials / grids / transport\\lpublished_by / ts\\l}"];
  import_batches [label="{import_batches|id (PK)\\lfile / sha256\\lraw_text preserved\\lrow counts\\l}"];
  audit_events [label="{audit_events|ts / actor / role\\laction / entity_id\\ldetails\\l}"];
  scenarios [label="{scenarios|id (PK)\\lproduct_id (FK)\\lparams / delta_ghg\\l}"];
  users [label="{users (roles)|Administrator\\lAnalyst / Reviewer\\lStakeholder\\l}"];
  products -> bom_lines; products -> transport_legs; products -> scenarios;
  import_batches -> products [label="lineage"];
  factor_versions -> products [label="calc version"];
  users -> audit_events; products -> audit_events;
}"""

# ══════════════════════════════ STATE & SIDEBAR ══════════════════════════════
def init_state():
    if "db" not in st.session_state:
        st.session_state.db = build_demo_db()
    st.session_state.setdefault("role", "Administrator")
    st.session_state.setdefault("intro_done", False)
    st.session_state.setdefault("selected_product", None)
    st.session_state.setdefault("qa_results", None)
    st.session_state.setdefault("import_payload", None)
    st.session_state.setdefault("report_cache", None)

NAV = ["🏠 Overview", "📦 Products", "📊 Impact Analysis", "🔀 Scenario Lab",
       "📥 Import & Validate", "✅ Review & Approvals", "📄 Reports & Export", "🛡️ Governance & QA"]

def sidebar():
    with st.sidebar:
        st.markdown("## 🌿 EcoLedger LCA")
        st.caption("Lifecycle impact, end to end — decision support")
        st.selectbox("Active role", list(USERS), key="role",
                     help="Permissions are enforced in every mutation handler, not only in the UI.")
        st.caption(f"Signed in as **{current_user()}**")
        st.divider()
        choice = st.radio("Navigation", NAV, key="nav", label_visibility="collapsed")
        st.divider()
        db = st.session_state.db
        prods = db["products"]
        appr = sum(p["status"] == "Approved" for p in prods)
        st.caption(f"📦 {len(prods)} records · ✅ {appr} approved")
        st.caption(f"🧮 Engine {ENGINE_VERSION} · Factors {db['factor_version']}")
        st.markdown("<span class='eco-warnchip'>SYNTHETIC DEMO DATA</span>", unsafe_allow_html=True)
        st.caption("Illustrative factors — not for regulatory reporting or certified EPDs.")
    return choice

def page_header(title, sub):
    st.markdown(f"## {title}")
    st.caption(sub)

# ══════════════════════════════ PAGE: OVERVIEW ══════════════════════════════
def page_overview():
    db = st.session_state.db
    factors = current_factors()
    prods = active_products()
    st.markdown(f"""
<div class="eco-hero">
  <h1>🌿 Product Lifecycle Environmental Impact Calculator</h1>
  <p>Bill of materials → manufacturing energy → transport → use phase → end of life.
     Deterministic engine, seeded Monte Carlo uncertainty, role-based review, full lineage and one-click reports.</p>
  <span class="eco-chip">Engine {ENGINE_VERSION}</span>
  <span class="eco-chip">Factors {db['factor_version']}</span>
  <span class="eco-chip">Schema v{SCHEMA_VERSION}</span>
  <span class="eco-chip">No API keys · 100% local</span>
</div>""", unsafe_allow_html=True)
    if not prods:
        st.info("No active records. Load the demo portfolio from Governance & QA → Backup & restore → Reset demo, or import a file.")
        return
    results, errs = {}, []
    for p in prods:
        r, e = safe_results(p, factors)
        if r: results[p["id"]] = r
        else: errs.append(f"{p['id']}: {e}")
    for e in errs:
        st.warning(e)
    total = sum(r["total_ghg"] for r in results.values())
    pmc = cached_portfolio_mc(json.dumps(prods, sort_keys=True), json.dumps(factors, sort_keys=True),
                              db["settings"]["mc_n"], db["settings"]["mc_seed"])
    qscore = np.mean([QUALITY_SCORE.get(b["quality"], 0.4) for p in prods for b in p["bom"]] or [0]) * 100
    unc = (pmc["p95"] - pmc["p5"]) / 2 / max(abs(pmc["p50"]), 1e-9) * 100
    c = st.columns(5)
    c[0].metric("Active products", len(prods))
    c[1].metric("Portfolio footprint", f"{total/1000:,.2f} t CO₂e", help="Deterministic cradle-to-grave total")
    c[2].metric("Approved", f"{sum(p['status']=='Approved' for p in prods)} / {len(prods)}")
    c[3].metric("Data quality score", f"{qscore:.0f}%", help="measured=100 · estimated=70 · proxy=40")
    c[4].metric("Uncertainty (±)", f"{unc:.1f}%", help="Half-width of the P5–P95 interval, relative to P50")

    h1, h2 = st.columns([5, 1])
    h1.markdown("### 🎲 Live Monte Carlo simulation")
    if h2.button("▶ Replay", key="replay_conv"):
        st.session_state.intro_done = False
        st.rerun()
    ph = st.empty()
    totals = pmc["totals"]
    n = len(totals)
    checkpoints = sorted({int(x) for x in np.geomspace(25, n, 12)})
    if not st.session_state.intro_done:
        xs, ys = [], []
        for ck in checkpoints:
            xs.append(ck); ys.append(float(totals[:ck].mean()) / 1000)
            f = go.Figure(go.Scatter(x=xs, y=ys, mode="lines+markers",
                                     line=dict(color=LEAF, width=3), marker=dict(size=7, color=FOREST)))
            fig_layout(f, height=230, title=f"Portfolio estimate converging… {ck:,}/{n:,} draws")
            f.update_yaxes(title="t CO₂e")
            ph.plotly_chart(f, key=f"conv{ck}", **_w(st.plotly_chart))
            time.sleep(0.06)
        st.session_state.intro_done = True
    f = go.Figure()
    xs = checkpoints
    ys = [float(totals[:ck].mean()) / 1000 for ck in xs]
    f.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name="Running mean",
                           line=dict(color=LEAF, width=3), marker=dict(size=6, color=FOREST)))
    for val, lab, col, dash in [(pmc["p5"]/1000, "P5", TERRA, "dash"), (pmc["p95"]/1000, "P95", TERRA, "dash"),
                                (pmc["p50"]/1000, "P50", FOREST, "solid")]:
        f.add_hline(y=val, line_color=col, line_dash=dash, line_width=2,
                    annotation_text=lab, annotation_position="right")
    fig_layout(f, height=250, title=f"Converged · seed {db['settings']['mc_seed']} · {n:,} draws · fully reproducible")
    f.update_yaxes(title="t CO₂e")
    ph.plotly_chart(f, key="convfinal", **_w(st.plotly_chart))

    left, right = st.columns([3, 2])
    with left:
        f = go.Figure()
        names = [p["name"] for p in prods if p["id"] in results]
        for s in STAGES:
            f.add_trace(go.Bar(name=s, x=names,
                               y=[results[p["id"]]["stages"][s]["ghg"] for p in prods if p["id"] in results],
                               marker_color=STAGE_COLORS[s],
                               hovertemplate="%{x}<br>" + s + ": %{y:,.1f} kg CO₂e<extra></extra>"))
        f.update_layout(barmode="relative")
        fig_layout(f, height=380, title="Cradle-to-grave footprint by product & stage")
        f.update_yaxes(title="kg CO₂e")
        f.update_xaxes(tickangle=-12)
        st.plotly_chart(f, key="ov_stack", **_w(st.plotly_chart))
    with right:
        stage_tot = {s: sum(r["stages"][s]["ghg"] for r in results.values()) for s in STAGES}
        pos = {s: v for s, v in stage_tot.items() if v > 0}
        f = go.Figure(go.Pie(labels=list(pos), values=list(pos.values()), hole=0.62,
                             marker=dict(colors=[STAGE_COLORS[s] for s in pos]),
                             textinfo="label+percent"))
        fig_layout(f, height=380, title="Portfolio stage share (positive contributions)")
        f.add_annotation(text=f"{total/1000:,.1f}<br>t CO₂e", showarrow=False, font=dict(size=16, color=FOREST))
        st.plotly_chart(f, key="ov_donut", **_w(st.plotly_chart))

    hot = max(((p, l) for p in prods if p["id"] in results for l in results[p["id"]]["lines"]),
              key=lambda pl: pl[1]["ghg"], default=None)
    if hot and total > 0:
        p, l = hot
        st.markdown(f"""<div class="eco-callout">🎯 <b>Portfolio hotspot:</b>
        <b>{l['stage']} · {l['item']}</b> of <b>{html_lib.escape(p['name'])}</b> alone contributes
        <b>{l['ghg']:,.0f} kg CO₂e</b> ({l['ghg']/max(total,1e-9):.0%} of the portfolio).
        Open the <b>Scenario Lab</b> to test efficiency, grid-decarbonization and lifetime levers against it.</div>""",
                    unsafe_allow_html=True)
    chips = " ".join(f"{pill(s)} <b style='color:{INK}'>{sum(p['status']==s for p in db['products'])}</b>&nbsp;&nbsp;"
                     for s in STATUSES)
    st.markdown("**Lifecycle pipeline:** " + chips, unsafe_allow_html=True)
    q1, q2, q3 = st.columns(3)
    if q1.button("📥 Import a BOM file", key="go_import"):
        st.session_state["_nav_pending"] = NAV[4]; st.rerun()
    if q2.button("✅ Open review queue", key="go_review"):
        st.session_state["_nav_pending"] = NAV[5]; st.rerun()
    if q3.button("📄 Generate a report", key="go_report"):
        st.session_state["_nav_pending"] = NAV[6]; st.rerun()
    st.markdown("**Recent activity**")
    st.dataframe(pd.DataFrame(db["audit"][::-1][:6]), hide_index=True, height=220, **_w(st.dataframe))

# ══════════════════════════════ PAGE: PRODUCTS ══════════════════════════════
def _clean_editor_rows(df: pd.DataFrame, cols: list) -> list:
    rows = []
    for _, r in df.iterrows():
        if all(pd.isna(r[c]) or str(r[c]).strip() == "" for c in cols):
            continue
        rows.append({c: (None if pd.isna(r[c]) else r[c]) for c in cols})
    return rows

def page_products():
    db = st.session_state.db
    factors = current_factors()
    pend = st.session_state.pop("_view_pending", None)
    if pend:
        st.session_state.prod_search = pend["q"]
        st.session_state.prod_status = pend["status"]
        st.session_state.prod_cat = pend["cat"]
        st.session_state.prod_sort = pend["sort"]
    page_header("📦 Product records", "Create, edit, validate and steer every record through its lifecycle. All mutations are audited.")
    if db["trash"]:
        u1, u2 = st.columns([4, 1])
        u1.info(f"🗑️ {len(db['trash'])} deleted record(s) recoverable (until the next deletion).")
        if u2.button("↩️ Undo delete", key="undo_del"):
            st.toast(undo_delete(), icon="↩️"); st.rerun()
    sel = st.session_state.selected_product
    if sel and product_by_id(sel):
        render_product_detail(product_by_id(sel), factors)
        return
    st.session_state.selected_product = None

    c1, c2, c3, c4 = st.columns([2, 2, 2, 1.6])
    c1.text_input("🔎 Search", key="prod_search", placeholder="name, id, category…")
    c2.multiselect("Status", STATUSES, key="prod_status")
    cats = sorted({p["category"] for p in db["products"]})
    c3.multiselect("Category", cats, key="prod_cat")
    c4.selectbox("Sort", ["Name A–Z", "Footprint ↓", "Footprint ↑", "Updated ↓", "Status"], key="prod_sort")
    v1, v2, v3, v4 = st.columns([2, 1, 2, 1])
    view_name = v1.text_input("Save current filters as view", key="view_name", placeholder="e.g. Lighting drafts")
    if v2.button("💾 Save view", key="save_view") and view_name.strip():
        db["saved_views"].append({"name": view_name.strip(), "q": st.session_state.prod_search,
                                  "status": st.session_state.prod_status, "cat": st.session_state.prod_cat,
                                  "sort": st.session_state.prod_sort})
        log_event("save_view", "view", view_name.strip()); st.toast("View saved", icon="💾"); st.rerun()
    view_names = [v["name"] for v in db["saved_views"]]
    pick = v3.selectbox("Saved views", ["(none)"] + view_names, key="view_pick")
    if v4.button("📂 Apply", key="apply_view") and pick != "(none)":
        st.session_state["_view_pending"] = next(v for v in db["saved_views"] if v["name"] == pick)
        st.rerun()

    q = st.session_state.prod_search.lower().strip()
    rows = []
    for p in db["products"]:
        if q and q not in f"{p['id']} {p['name']} {p['category']}".lower(): continue
        if st.session_state.prod_status and p["status"] not in st.session_state.prod_status: continue
        if st.session_state.prod_cat and p["category"] not in st.session_state.prod_cat: continue
        r, e = safe_results(p, factors)
        rows.append((p, r["total_ghg"] if r else None, e))
    sort = st.session_state.prod_sort
    rows.sort(key=lambda t: {"Name A–Z": lambda: t[0]["name"].lower(),
                             "Footprint ↓": lambda: -(t[1] if t[1] is not None else -1e18),
                             "Footprint ↑": lambda: (t[1] if t[1] is not None else 1e18),
                             "Updated ↓": lambda: "", "Status": lambda: t[0]["status"]}[sort]()
              if sort != "Updated ↓" else "")
    if sort == "Updated ↓":
        rows.sort(key=lambda t: t[0]["updated_utc"], reverse=True)
    st.caption(f"{len(rows)} record(s) match · click **Open** for the detail drawer")
    hdr = st.columns([0.4, 1.1, 2.5, 1.4, 1.3, 1.2, 1.0, 0.8])
    for col, lab in zip(hdr, ["", "ID", "Product", "Category", "Status", "kg CO₂e", "Updated", ""]):
        col.caption(f"**{lab}**")
    for p, tot, e in rows:
        c = st.columns([0.4, 1.1, 2.5, 1.4, 1.3, 1.2, 1.0, 0.8])
        c[0].checkbox("select", key=f"sel_{p['id']}", label_visibility="collapsed")
        c[1].markdown(f"`{p['id']}`")
        c[2].markdown(f"**{html_lib.escape(p['name'])}**")
        c[3].caption(p["category"])
        c[4].markdown(pill(p["status"]), unsafe_allow_html=True)
        c[5].markdown(fmt(tot) if tot is not None else "⚠ data")
        c[6].caption(p["updated_utc"][:10])
        if c[7].button("Open", key=f"open_{p['id']}"):
            st.session_state.selected_product = p["id"]; st.rerun()
    selected_ids = [p["id"] for p, _, _ in rows if st.session_state.get(f"sel_{p['id']}")]
    if selected_ids:
        st.markdown(f"**Bulk actions — {len(selected_ids)} selected**")
        b1, b2, b3 = st.columns(3)
        if b1.button("📨 Submit selected (Validated only)", key="bulk_submit", disabled=not can("submit")):
            ok, skip = 0, 0
            for pid in selected_ids:
                p = product_by_id(pid)
                try:
                    if p["status"] == "Validated":
                        transition(p, "In Review", "Bulk submit"); ok += 1
                    else: skip += 1
                except (ValueError, PermissionError): skip += 1
            st.toast(f"Submitted {ok}, skipped {skip}", icon="📨")
            for k in [k for k in st.session_state if k.startswith("sel_")]: del st.session_state[k]
            st.rerun()
        if b2.button("🗄️ Archive selected (Approved)", key="bulk_archive", disabled=not can("archive")):
            ok = 0
            for pid in selected_ids:
                p = product_by_id(pid)
                try:
                    if p["status"] == "Approved":
                        transition(p, "Archived", "Bulk archive"); ok += 1
                except (ValueError, PermissionError): pass
            st.toast(f"Archived {ok}", icon="🗄️")
            for k in [k for k in st.session_state if k.startswith("sel_")]: del st.session_state[k]
            st.rerun()
        if b3.button("🗑️ Delete selected", key="bulk_delete", disabled=not can("delete")):
            st.toast(delete_products(selected_ids), icon="🗑️")
            for k in [k for k in st.session_state if k.startswith("sel_")]: del st.session_state[k]
            st.rerun()
    with st.expander("➕ New product", expanded=False):
        with st.form("new_product_form"):
            n1, n2, n3 = st.columns(3)
            name = n1.text_input("Name*", "New municipal asset")
            cat = n2.selectbox("Category", sorted(set(cats) | {"General equipment"}))
            life = n3.number_input("Lifetime (years)", 0.5, 100.0, 10.0, 0.5)
            m1, m2, m3, m4 = st.columns(4)
            mkwh = m1.number_input("Manufacturing kWh", 0.0, 1e7, 10.0, 0.5)
            mgrid = m2.selectbox("Manufacturing grid", GRID_NAMES)
            ukwh = m3.number_input("Use kWh / year", 0.0, 1e7, 0.0, 0.5)
            ugrid = m4.selectbox("Use grid", GRID_NAMES)
            t1, t2, e1, e2, e3 = st.columns(5)
            tmode = t1.selectbox("Transport mode", TRANSPORT_NAMES, index=2)
            tkm = t2.number_input("Distance km", 0.0, 100000.0, 500.0, 10.0)
            er = e1.number_input("EoL recycle %", 0.0, 100.0, 60.0, 5.0)
            ei = e2.number_input("EoL incinerate %", 0.0, 100.0, 25.0, 5.0)
            el = e3.number_input("EoL landfill %", 0.0, 100.0, 15.0, 5.0)
            go_btn = st.form_submit_button("Create draft record", type="primary", disabled=not can("create"),
                                           **_w(st.form_submit_button))
        if go_btn:
            try:
                guard("create")
                if not name.strip():
                    raise ValueError("Name is required.")
                now = utcnow()
                p = {"id": f"P-{uid()[:6]}", "name": name.strip(), "category": cat,
                     "functional_unit": f"1 unit · {life:g} y service", "status": "Draft",
                     "schema_version": SCHEMA_VERSION, "created_utc": now, "updated_utc": now,
                     "lifetime_years": life,
                     "bom": [{"component": "Main body", "material": "Steel", "mass_kg": 5.0,
                              "recycled_pct": 30.0, "quality": "estimated"}],
                     "manufacturing": {"kwh": mkwh, "grid": mgrid, "quality": "estimated"},
                     "use": {"kwh_per_year": ukwh, "grid": ugrid, "quality": "estimated"},
                     "transport": [{"mode": tmode, "distance_km": tkm}],
                     "eol": {"recycle_pct": er, "incinerate_pct": ei, "landfill_pct": el, "quality": "proxy"},
                     "override": None, "notes": "", "source": {"kind": "manual"}}
                db["products"].append(p)
                log_event("create", "product", p["id"], "Manual creation (starter BOM — edit next)")
                st.session_state.selected_product = p["id"]
                st.toast(f"Created {p['id']} — edit its BOM", icon="📦"); st.rerun()
            except (ValueError, PermissionError) as ex:
                st.error(str(ex))

def render_product_detail(p, factors):
    db = st.session_state.db
    locked = record_locked(p)
    top = st.columns([1, 4, 2])
    if top[0].button("← All products", key="back_list"):
        st.session_state.selected_product = None; st.rerun()
    top[1].markdown(f"### {html_lib.escape(p['name'])}")
    top[2].markdown(pill(p["status"]) + (
        " <span class='eco-warnchip'>MANUAL OVERRIDE</span>" if p.get("override") else ""),
        unsafe_allow_html=True)
    st.caption(f"`{p['id']}` · {p['category']} · {p['functional_unit']} · source: {p['source'].get('kind','?')}"
               + (f" ({p['source'].get('file','')})" if p['source'].get('file') else ""))
    r, e = safe_results(p, factors)
    if e: st.error(f"Engine: {e}")
    m = st.columns(4)
    m[0].metric("Total footprint", f"{fmt(r['total_ghg'])} kg CO₂e" if r else "—")
    m[1].metric("Per service year", f"{fmt(r['per_year_ghg'])} kg" if r and math.isfinite(r["per_year_ghg"]) else "—")
    m[2].metric("Product mass", f"{fmt(r['mass_kg'])} kg" if r else "—")
    m[3].metric("Energy (CED)", f"{fmt0(r['total_mj'])} MJ" if r else "—")
    issues = validate_product(p)
    errors = [i for i in issues if i["severity"] == "error"]
    warns = [i for i in issues if i["severity"] == "warning"]
    for i in errors: st.error(f"⛔ **{i['field']}** — {i['message']}")
    for i in warns: st.warning(f"⚠️ **{i['field']}** — {i['message']}")
    if not issues: st.success("✅ Record passes all validation rules.")
    if locked and can("edit"):
        st.info("🔒 Approved/Archived records are locked. An Administrator can reopen them below.")
    elif not can("edit"):
        st.info(f"👁️ Read-only for role '{st.session_state.role}'.")

    tab_def, tab_bom, tab_life = st.tabs(["🧾 Definition", "🧱 Bill of materials & transport", "🔄 Lifecycle & lineage"])
    with tab_def:
        with st.form(f"def_form_{p['id']}"):
            d1, d2, d3 = st.columns(3)
            name = d1.text_input("Name", p["name"], disabled=locked)
            cat = d2.text_input("Category", p["category"], disabled=locked)
            life = d3.number_input("Lifetime (y)", 0.5, 100.0, float(p["lifetime_years"]), 0.5, disabled=locked)
            fu = st.text_input("Functional unit", p["functional_unit"], disabled=locked)
            f1, f2, f3, f4 = st.columns(4)
            mkwh = f1.number_input("Mfg kWh", 0.0, 1e7, float(p["manufacturing"]["kwh"]), 0.5, disabled=locked)
            mgrid = f2.selectbox("Mfg grid", GRID_NAMES, index=GRID_NAMES.index(p["manufacturing"]["grid"])
                                 if p["manufacturing"]["grid"] in GRID_NAMES else 0, disabled=locked)
            ukwh = f3.number_input("Use kWh/y", 0.0, 1e7, float(p["use"]["kwh_per_year"]), 0.5, disabled=locked)
            ugrid = f4.selectbox("Use grid", GRID_NAMES, index=GRID_NAMES.index(p["use"]["grid"])
                                 if p["use"]["grid"] in GRID_NAMES else 0, disabled=locked)
            g1, g2, g3 = st.columns(3)
            er = g1.number_input("EoL recycle %", 0.0, 100.0, float(p["eol"]["recycle_pct"]), 5.0, disabled=locked)
            ei = g2.number_input("EoL incinerate %", 0.0, 100.0, float(p["eol"]["incinerate_pct"]), 5.0, disabled=locked)
            el = g3.number_input("EoL landfill %", 0.0, 100.0, float(p["eol"]["landfill_pct"]), 5.0, disabled=locked)
            notes = st.text_area("Assumptions & notes", p.get("notes", ""), disabled=locked, height=80)
            save = st.form_submit_button("💾 Save definition", type="primary", disabled=locked,
                                         **_w(st.form_submit_button))
        if save:
            def mut(q):
                q.update({"name": name.strip(), "category": cat.strip(), "lifetime_years": life,
                          "functional_unit": fu.strip(), "notes": notes})
                q["manufacturing"] = {"kwh": mkwh, "grid": mgrid, "quality": p["manufacturing"]["quality"]}
                q["use"] = {"kwh_per_year": ukwh, "grid": ugrid, "quality": p["use"]["quality"]}
                q["eol"] = {"recycle_pct": er, "incinerate_pct": ei, "landfill_pct": el, "quality": p["eol"]["quality"]}
            try:
                update_product(p, mut, "Definition edited"); st.toast("Definition saved", icon="💾"); st.rerun()
            except PermissionError as ex: st.error(str(ex))
    with tab_bom:
        st.caption("Add rows with the ➕ below the grid; delete via row selector. Save to apply & revalidate.")
        bom_df = pd.DataFrame(p["bom"] or [], columns=["component", "material", "mass_kg", "recycled_pct", "quality"])
        edited = st.data_editor(bom_df, num_rows="dynamic", hide_index=True, key=f"bom_ed_{p['id']}",
                                disabled=locked, **_w(st.data_editor),
                                column_config={
                                    "component": st.column_config.TextColumn("Component", required=True),
                                    "material": st.column_config.SelectboxColumn("Material", options=MATERIAL_NAMES, required=True),
                                    "mass_kg": st.column_config.NumberColumn("Mass (kg)", min_value=0.0, max_value=100000.0, step=0.1, format="%.3f"),
                                    "recycled_pct": st.column_config.NumberColumn("Recycled %", min_value=0.0, max_value=100.0, step=5.0),
                                    "quality": st.column_config.SelectboxColumn("Data quality", options=list(QUALITY_SIGMA), required=True)})
        cleaned = _clean_editor_rows(edited, ["component", "material", "mass_kg"])
        preview_p = copy.deepcopy(p)
        preview_p["bom"] = [{"component": str(x["component"] or "?"), "material": x["material"],
                             "mass_kg": _num(x["mass_kg"]), "recycled_pct": _num(x["recycled_pct"]),
                             "quality": x["quality"] or "proxy"} for x in cleaned]
        pr, pe = safe_results(preview_p, factors)
        if pr:
            st.caption(f"🔴 Live preview (unsaved): **{fmt(pr['total_ghg'])} kg CO₂e** · mass {fmt(pr['mass_kg'])} kg")
        elif pe:
            st.caption(f"Live preview unavailable: {pe}")
        if st.button("💾 Save BOM", key=f"bom_save_{p['id']}", disabled=locked, type="primary"):
            try:
                update_product(p, lambda q: q.update({"bom": preview_p["bom"]}),
                               f"BOM edited → {len(preview_p['bom'])} lines")
                st.toast("BOM saved & recalculated", icon="🧱"); st.rerun()
            except PermissionError as ex: st.error(str(ex))
        r1, r2, r3 = st.columns([2, 1, 1])
        move_pick = r1.selectbox("Reorder line", [b["component"] for b in p["bom"]] or ["(empty)"],
                                 key=f"mv_{p['id']}", disabled=locked)
        for col, delta, lab in [(r2, -1, "↑ Move up"), (r3, 1, "↓ Move down")]:
            if col.button(lab, key=f"mv{delta}_{p['id']}", disabled=locked):
                try:
                    idx = next(i for i, b in enumerate(p["bom"]) if b["component"] == move_pick)
                    j = idx + delta
                    if 0 <= j < len(p["bom"]):
                        def mut(q):
                            q["bom"][idx], q["bom"][j] = q["bom"][j], q["bom"][idx]
                        update_product(p, mut, f"Reordered BOM line '{move_pick}'")
                        st.rerun()
                    else:
                        st.toast("Already at the boundary", icon="ℹ️")
                except (StopIteration, PermissionError) as ex: st.error(str(ex))
        st.markdown("**Transport legs** (cargo mass auto-derived from BOM)")
        tr_df = pd.DataFrame(p["transport"] or [], columns=["mode", "distance_km"])
        tr_ed = st.data_editor(tr_df, num_rows="dynamic", hide_index=True, key=f"tr_ed_{p['id']}",
                               disabled=locked, **_w(st.data_editor),
                               column_config={"mode": st.column_config.SelectboxColumn("Mode", options=TRANSPORT_NAMES, required=True),
                                              "distance_km": st.column_config.NumberColumn("Distance (km)", min_value=0.0, max_value=100000.0, step=10.0)})
        if st.button("💾 Save transport", key=f"tr_save_{p['id']}", disabled=locked):
            legs = [{"mode": x["mode"], "distance_km": _num(x["distance_km"])}
                    for x in _clean_editor_rows(tr_ed, ["mode", "distance_km"])]
            try:
                update_product(p, lambda q: q.update({"transport": legs}), f"Transport edited → {len(legs)} legs")
                st.toast("Transport saved", icon="🚚"); st.rerun()
            except PermissionError as ex: st.error(str(ex))
    with tab_life:
        a = st.columns(5)
        acts = []
        if p["status"] in ("Draft", "Changes Requested"): acts.append(("✔️ Validate", "Validated", "submit"))
        if p["status"] == "Validated": acts.append(("📨 Submit for review", "In Review", "submit"))
        if p["status"] in ("Approved", "Archived"): acts.append(("🔓 Reopen (admin)", "Draft", "archive"))
        if p["status"] == "Approved": acts.append(("🗄️ Archive", "Archived", "archive"))
        for i, (lab, to, perm) in enumerate(acts):
            if a[i].button(lab, key=f"tr_{p['id']}_{to}", disabled=not can(perm)):
                try:
                    transition(p, to); st.toast(f"{p['id']} → {to}", icon="🔄"); st.rerun()
                except (ValueError, PermissionError) as ex: st.error(str(ex))
        if p["status"] == "In Review":
            a[0].info("Awaiting reviewer — decisions in **Review & Approvals**.")
        if a[3].button("🧬 Duplicate", key=f"dup_{p['id']}", disabled=not can("create")):
            try:
                nid = duplicate_product(p); st.session_state.selected_product = nid
                st.toast(f"Duplicated → {nid}", icon="🧬"); st.rerun()
            except PermissionError as ex: st.error(str(ex))
        del_ok = can("delete") and (p["status"] != "Approved" or st.session_state.role == "Administrator")
        if a[4].button("🗑️ Delete", key=f"del_{p['id']}", disabled=not del_ok):
            st.toast(delete_products([p["id"]]), icon="🗑️")
            st.session_state.selected_product = None; st.rerun()
        st.markdown("**Lineage**")
        l1, l2 = st.columns(2)
        l1.json(p["source"], expanded=True)
        l2.markdown(f"- Engine **{ENGINE_VERSION}**\n- Factors **{db['factor_version']}**\n"
                    f"- Schema **v{p['schema_version']}**\n- Created `{p['created_utc']}`\n- Updated `{p['updated_utc']}`")
        if r:
            st.caption(f"Result digest (reproducibility): `{result_digest(r)}`")
            st.dataframe(pd.DataFrame(r["lines"])[["stage", "item", "qty", "unit", "factor_id", "factor", "ghg", "quality"]],
                         hide_index=True, height=260, **_w(st.dataframe))
        if p.get("override"):
            st.warning(f"Manual override active: {fmt(p['override']['total_ghg'])} kg CO₂e — "
                       f"“{p['override']['reason']}” ({p['override']['by']}, {p['override']['ts'][:16]}). Engine value preserved above.")
        hist = [x for x in db["audit"] if x["entity_id"] == p["id"]]
        st.markdown("**Record history**")
        st.dataframe(pd.DataFrame(hist[::-1]), hide_index=True, height=220, **_w(st.dataframe))

# ══════════════════════════════ PAGE: IMPACT ANALYSIS ══════════════════════════════
def page_analysis():
    db = st.session_state.db
    factors = current_factors()
    prods = active_products()
    page_header("📊 Impact analysis", "Deterministic breakdown, hotspots, seeded Monte Carlo uncertainty and ±20% sensitivity — every number explainable.")
    if not prods:
        st.info("No active products."); return
    ids = [p["id"] for p in prods]
    default = st.session_state.get("analysis_pid", ids[0])
    idx = ids.index(default) if default in ids else 0
    pid = st.selectbox("Product", ids, index=idx, key="analysis_pick",
                       format_func=lambda i: f"{i} — {product_by_id(i)['name']}")
    st.session_state.analysis_pid = pid
    p = product_by_id(pid)
    r, e = safe_results(p, factors)
    if e:
        st.error(f"Engine cannot compute this record: {e}"); return
    eff_total = p["override"]["total_ghg"] if p.get("override") else r["total_ghg"]
    m = st.columns(4)
    m[0].metric("Total (effective)", f"{fmt(eff_total)} kg CO₂e",
                help="Manual override active — engine value preserved in lineage" if p.get("override") else "Deterministic engine result")
    m[1].metric("Per service year", f"{fmt(r['per_year_ghg'])} kg" if math.isfinite(r["per_year_ghg"]) else "—")
    m[2].metric("Embodied + operational energy", f"{fmt0(r['total_mj'])} MJ")
    m[3].metric("Result digest", result_digest(r), help="SHA-256 of line items — identical inputs & versions reproduce this exactly")
    left, right = st.columns(2)
    with left:
        vals = [r["stages"][s]["ghg"] for s in STAGES]
        f = go.Figure(go.Bar(x=STAGES, y=vals, marker_color=[STAGE_COLORS[s] for s in STAGES],
                             text=[fmt(v) for v in vals], textposition="outside"))
        fig_layout(f, height=360, title=f"Stage breakdown — net total {fmt(r['total_ghg'])} kg CO₂e")
        f.update_yaxes(title="kg CO₂e")
        st.plotly_chart(f, key="an_stages", **_w(st.plotly_chart))
    with right:
        top = sorted(r["lines"], key=lambda l: abs(l["ghg"]), reverse=True)[:8][::-1]
        f = go.Figure(go.Bar(x=[l["ghg"] for l in top],
                             y=[f"{l['stage']} · {l['item']}" for l in top], orientation="h",
                             marker_color=[STAGE_COLORS[l["stage"]] for l in top]))
        fig_layout(f, height=360, title="Top contributors (credits shown negative)")
        f.update_xaxes(title="kg CO₂e")
        st.plotly_chart(f, key="an_hot", **_w(st.plotly_chart))
    gross = sum(max(l["ghg"], 0) for l in r["lines"]) or 1.0
    hot_df = pd.DataFrame([{**l, "share_%": max(l["ghg"], 0) / gross * 100}
                           for l in sorted(r["lines"], key=lambda l: abs(l["ghg"]), reverse=True)[:10]])
    st.markdown("**Hotspot table**")
    st.dataframe(hot_df[["stage", "item", "qty", "unit", "factor_id", "ghg", "share_%", "quality", "sigma"]],
                 hide_index=True, **_w(st.dataframe),
                 column_config={"ghg": st.column_config.NumberColumn("kg CO₂e", format="%.2f"),
                                "share_%": st.column_config.ProgressColumn("Share of gross", min_value=0, max_value=100, format="%.1f%%")})
    with st.expander("🔍 Explanation panel — trace any line item"):
        labels = [f"{l['stage']} · {l['item']} ({fmt(l['ghg'])} kg)" for l in r["lines"]]
        li = st.selectbox("Line item", range(len(labels)), format_func=lambda i: labels[i], key="an_line")
        l = r["lines"][li]
        e1, e2 = st.columns(2)
        e1.markdown(f"- **Activity:** {l['qty']:g} {l['unit']}\n- **Factor:** `{l['factor_id']}` = {l['factor']:g}"
                    f"\n- **Factor set:** {db['factor_version']} · **Engine:** {ENGINE_VERSION}"
                    f"\n- **Quality:** {l['quality']} → σ = {l['sigma']:.2f}")
        e2.code(l["formula"], language="text")
        issues = [i for i in validate_product(p) if i["severity"] == "warning"]
        if issues:
            for i in issues: st.warning(f"{i['field']}: {i['message']}")
        st.caption(f"Reviewer status: {p['status']}" + (f" · override by {p['override']['by']}" if p.get("override") else ""))
    st.markdown("### 🎲 Uncertainty — Monte Carlo (data pedigree × factor uncertainty)")
    u1, u2, u3 = st.columns([2, 1, 1])
    n = u1.slider("Draws", 500, 20000, db["settings"]["mc_n"], 500, key="an_n")
    seed = u2.number_input("Seed", 0, 99999, db["settings"]["mc_seed"], key="an_seed")
    if u3.button("💾 Save as defaults", key="an_mc_save"):
        db["settings"] = {"mc_n": int(n), "mc_seed": int(seed)}
        st.toast("Simulation defaults saved", icon="🎲")
    mc = cached_mc(json.dumps(p, sort_keys=True), json.dumps(factors, sort_keys=True), int(n), int(seed))
    s = st.columns(4)
    s[0].metric("P5", f"{fmt(mc['p5'])} kg")
    s[1].metric("P50 (median)", f"{fmt(mc['p50'])} kg")
    s[2].metric("P95", f"{fmt(mc['p95'])} kg")
    s[3].metric("± interval", f"{(mc['p95']-mc['p5'])/2/max(abs(mc['p50']),1e-9)*100:.1f}%")
    f = go.Figure(go.Histogram(x=mc["totals"], nbinsx=60, marker_color=LEAF, opacity=0.85))
    for val, lab, col, dash in [(mc["p5"], "P5", TERRA, "dash"), (mc["p50"], "P50", FOREST, "solid"), (mc["p95"], "P95", TERRA, "dash")]:
        f.add_vline(x=val, line_color=col, line_dash=dash, line_width=2, annotation_text=lab, annotation_position="top")
    fig_layout(f, height=330, title=f"Total footprint distribution · seed {int(seed)} · {int(n):,} draws (reproducible)")
    f.update_xaxes(title="kg CO₂e"); f.update_yaxes(title="draws")
    st.plotly_chart(f, key="an_hist", **_w(st.plotly_chart))
    st.markdown("### 🌪️ Sensitivity — ±20% per stage (tornado)")
    sums = {s_: r["stages"][s_]["ghg"] for s_ in STAGES}
    order = sorted(STAGES, key=lambda s_: abs(sums[s_]))
    f = go.Figure()
    f.add_trace(go.Bar(name="−20%", y=order, x=[-0.2 * sums[s_] for s_ in order], orientation="h", marker_color=SKY))
    f.add_trace(go.Bar(name="+20%", y=order, x=[0.2 * sums[s_] for s_ in order], orientation="h", marker_color=AMBER))
    f.update_layout(barmode="relative")
    fig_layout(f, height=300, title="Change in total kg CO₂e (model is linear in each stage)")
    f.update_xaxes(title="Δ kg CO₂e", zeroline=True, zerolinecolor=INK)
    st.plotly_chart(f, key="an_tornado", **_w(st.plotly_chart))

# ══════════════════════════════ PAGE: SCENARIO LAB ══════════════════════════════
def page_scenarios():
    db = st.session_state.db
    factors = current_factors()
    prods = active_products()
    page_header("🔀 Scenario lab", "Eight live levers against the baseline — every slider recalculates instantly. Save, compare and export scenarios.")
    if not prods:
        st.info("No active products."); return
    pend = st.session_state.pop("_scen_pending", None)
    if pend:
        for k, v in pend.items():
            st.session_state[k] = v
    ids = [p["id"] for p in prods]
    pid = st.selectbox("Product", ids, key="sc_pid_pick",
                       format_func=lambda i: f"{i} — {product_by_id(i)['name']}")
    p = product_by_id(pid)
    base_r, e = safe_results(p, factors)
    if e:
        st.error(e); return
    d = scenario_defaults(p)
    ctl, out = st.columns([1, 2])
    with ctl:
        st.markdown("#### 🎛️ Live controls")
        s = {}
        s["recycled_boost"] = st.slider("Recycled content boost (+pp)", 0, 100, d["recycled_boost"], 5, key="sc_boost")
        s["use_eff"] = st.slider("Use-phase efficiency gain (%)", 0, 60, d["use_eff"], 5, key="sc_eff")
        s["lifetime_mult"] = st.slider("Lifetime multiplier (×)", 0.5, 2.0, d["lifetime_mult"], 0.1, key="sc_life")
        s["transport_cut"] = st.slider("Transport distance cut (%)", 0, 90, d["transport_cut"], 5, key="sc_cut")
        s["sea_to_rail"] = st.toggle("Shift sea freight → rail", d["sea_to_rail"], key="sc_rail")
        s["use_grid_mult"] = st.slider("Use-grid carbon intensity (×)", 0.05, 1.0, d["use_grid_mult"], 0.05, key="sc_grid")
        s["mfg_grid"] = st.selectbox("Manufacturing grid", GRID_NAMES,
                                     index=GRID_NAMES.index(d["mfg_grid"]) if d["mfg_grid"] in GRID_NAMES else 0, key="sc_mfg")
        s["eol_target"] = st.slider("EoL recycling target (%)", 0, 95, min(95, d["eol_target"]), 5, key="sc_eol")
        b1, b2 = st.columns(2)
        if b1.button("↺ Reset levers", key="sc_reset"):
            st.session_state["_scen_pending"] = {"sc_boost": d["recycled_boost"], "sc_eff": d["use_eff"],
                "sc_life": d["lifetime_mult"], "sc_cut": d["transport_cut"], "sc_rail": d["sea_to_rail"],
                "sc_grid": d["use_grid_mult"], "sc_mfg": d["mfg_grid"], "sc_eol": min(95, d["eol_target"])}
            st.rerun()
        if b2.button("🎲 Randomize", key="sc_rand"):
            st.session_state["_scen_pending"] = {"sc_boost": random.randrange(0, 65, 5),
                "sc_eff": random.randrange(0, 55, 5), "sc_life": round(random.uniform(0.8, 1.6), 1),
                "sc_cut": random.randrange(0, 75, 5), "sc_rail": random.choice([True, False]),
                "sc_grid": round(random.uniform(0.1, 1.0), 2), "sc_mfg": random.choice(GRID_NAMES),
                "sc_eol": random.randrange(50, 96, 5)}
            st.rerun()
        name = st.text_input("Scenario name", f"{p['name']} — improvement", key="sc_name")
        if st.button("💾 Save scenario", key="sc_save", type="primary", **_w(st.button)):
            _, sr = apply_scenario(p, s, factors)
            db["scenarios"].append({"id": uid(), "name": name.strip() or "Untitled", "pid": pid,
                                    "params": s, "delta_ghg": sr["total_ghg"] - base_r["total_ghg"],
                                    "created": utcnow(), "by": current_user()})
            log_event("save_scenario", "scenario", name, f"Δ {sr['total_ghg']-base_r['total_ghg']:,.1f} kg CO₂e")
            st.toast("Scenario saved", icon="💾"); st.rerun()
    q, sr = apply_scenario(p, s, factors)
    with out:
        delta = sr["total_ghg"] - base_r["total_ghg"]
        pct = delta / max(abs(base_r["total_ghg"]), 1e-9) * 100
        m = st.columns(3)
        m[0].metric("Baseline", f"{fmt(base_r['total_ghg'])} kg CO₂e")
        m[1].metric("Scenario", f"{fmt(sr['total_ghg'])} kg CO₂e", f"{delta:+,.1f} kg", delta_color="inverse")
        m[2].metric("Change", f"{pct:+.1f}%", help="Negative = improvement (green)")
        f = go.Figure()
        for stg in STAGES:
            f.add_trace(go.Bar(name=stg, x=["Baseline", "Scenario"],
                               y=[base_r["stages"][stg]["ghg"], sr["stages"][stg]["ghg"]],
                               marker_color=STAGE_COLORS[stg]))
        f.update_layout(barmode="relative")
        fig_layout(f, height=380, title="Baseline vs scenario — stage by stage")
        f.update_yaxes(title="kg CO₂e")
        st.plotly_chart(f, key="sc_compare", **_w(st.plotly_chart))
        cmp_df = pd.DataFrame([{"stage": stg, "baseline_kg": base_r["stages"][stg]["ghg"],
                                "scenario_kg": sr["stages"][stg]["ghg"],
                                "delta_kg": sr["stages"][stg]["ghg"] - base_r["stages"][stg]["ghg"]} for stg in STAGES])
        st.dataframe(cmp_df, hide_index=True, **_w(st.dataframe))
        st.download_button("⬇️ Export scenario comparison (CSV)", cmp_df.to_csv(index=False),
                           file_name=f"scenario_{pid}.csv", mime="text/csv", key="sc_export")
    saved = [x for x in db["scenarios"] if x["pid"] == pid]
    if saved:
        st.markdown("#### 📚 Saved scenarios for this product")
        totals = [("Baseline", base_r["total_ghg"])]
        for x in saved:
            try:
                _, xr = apply_scenario(p, x["params"], factors)
                totals.append((x["name"], xr["total_ghg"]))
            except (ValueError, KeyError):
                continue
        f = go.Figure(go.Bar(x=[t[0] for t in totals], y=[t[1] for t in totals],
                             marker_color=SERIES[:len(totals)], text=[fmt(t[1]) for t in totals], textposition="outside"))
        fig_layout(f, height=320, title="Saved scenario comparison (recomputed with current factor set)")
        f.update_yaxes(title="kg CO₂e"); f.update_xaxes(tickangle=-10)
        st.plotly_chart(f, key="sc_saved_chart", **_w(st.plotly_chart))
        for x in saved:
            c = st.columns([4, 2, 1, 1])
            c[0].markdown(f"**{html_lib.escape(x['name'])}** — Δ {x['delta_ghg']:+,.1f} kg · {x['by']} · {x['created'][:16]}")
            c[1].caption(" · ".join(f"{k}={v}" for k, v in list(x["params"].items())[:4]) + " …")
            if c[2].button("Load", key=f"sc_load_{x['id']}"):
                st.session_state["_scen_pending"] = {"sc_boost": x["params"]["recycled_boost"],
                    "sc_eff": x["params"]["use_eff"], "sc_life": x["params"]["lifetime_mult"],
                    "sc_cut": x["params"]["transport_cut"], "sc_rail": x["params"]["sea_to_rail"],
                    "sc_grid": x["params"]["use_grid_mult"], "sc_mfg": x["params"]["mfg_grid"],
                    "sc_eol": x["params"]["eol_target"]}
                st.rerun()
            if c[3].button("Delete", key=f"sc_del_{x['id']}"):
                db["scenarios"].remove(x)
                log_event("delete_scenario", "scenario", x["name"]); st.rerun()

# ══════════════════════════════ PAGE: IMPORT & VALIDATE ══════════════════════════════
def page_import():
    db = st.session_state.db
    factors = current_factors()
    page_header("📥 Import & validate", "CSV / XLSX BOM import with preview, row-level actionable errors, downloadable error report and a safe, never-overwriting commit.")
    left, right = st.columns([1, 2])
    with left:
        st.markdown("**1 · Get a template**")
        st.download_button("⬇️ Valid sample CSV (2 new products)", VALID_SAMPLE, "sample_valid.csv", "text/csv", key="dl_valid")
        st.download_button("⬇️ Malformed sample CSV (demo of rejections)", BROKEN_SAMPLE, "sample_broken.csv", "text/csv", key="dl_broken")
        st.markdown("**2 · Load a file**")
        up = st.file_uploader("Upload CSV or XLSX", type=["csv", "xlsx"], key="imp_up",
                              disabled=not can("create"))
        if st.button("🧪 Try built-in valid sample", key="try_valid", **_w(st.button)):
            st.session_state.import_payload = {"name": "sample_valid.csv", "bytes": VALID_SAMPLE.encode(), "fmt": "csv"}
            st.rerun()
        if st.button("🧪 Try built-in broken sample", key="try_broken", **_w(st.button)):
            st.session_state.import_payload = {"name": "sample_broken.csv", "bytes": BROKEN_SAMPLE.encode(), "fmt": "csv"}
            st.rerun()
        st.caption("One row per BOM component; product-level columns repeat and must be consistent. "
                   "Existing product IDs are rejected — validated source data is never silently overwritten. "
                   "Original files are preserved with a SHA-256 checksum.")
        with st.expander("📖 Column contract"):
            st.code(", ".join(IMPORT_COLS), language="text")
    payload = None
    if up is not None:
        payload = {"name": up.name, "bytes": up.getvalue(), "fmt": "xlsx" if up.name.lower().endswith("xlsx") else "csv"}
    elif st.session_state.import_payload:
        payload = st.session_state.import_payload
    with right:
        st.markdown("**3 · Preview & validate**")
        if payload is None:
            st.info("Upload a file or click a built-in sample — the validation preview appears here instantly.")
        else:
            raw = payload["bytes"]
            sha = hashlib.sha256(raw).hexdigest()
            try:
                if payload["fmt"] == "xlsx":
                    df = pd.read_excel(io.BytesIO(raw))
                    raw_text = base64.b64encode(raw).decode()
                else:
                    raw_text = raw.decode("utf-8-sig", "replace")
                    df = pd.read_csv(io.StringIO(raw_text))
            except ImportError:
                st.error("XLSX needs openpyxl — it is included in requirements.txt. Re-run pip install."); return
            except Exception as ex:
                st.error(f"Could not parse file: {ex}"); return
            st.caption(f"📄 **{payload['name']}** · {len(df)} rows · sha256 `{sha[:16]}…`")
            res = parse_import(df, {p["id"] for p in db["products"]}, factors)
            if res["missing_cols"]:
                st.error(f"Missing required columns: {', '.join(res['missing_cols'])} — download the template above.")
                return
            m = st.columns(3)
            m[0].metric("Rows", res["n_rows"])
            m[1].metric("Valid products", len(res["valid_products"]))
            m[2].metric("Rejected groups", len(res["rejected_groups"]))
            def row_style(row):
                bg = "background-color:#FDECEA" if str(row["import_status"]).startswith("❌") else "background-color:#EAF6EE"
                return [bg] * len(row)
            st.dataframe(res["preview"].style.apply(row_style, axis=1).hide(axis="index"),
                         height=300, **_w(st.dataframe))
            err_rows = res["preview"][res["preview"]["errors"] != ""]
            if len(err_rows):
                rep = pd.DataFrame({"file_row": err_rows.index + 2,
                                    "product_id": err_rows["product_id"],
                                    "component": err_rows["component"],
                                    "errors": err_rows["errors"]})
                st.download_button("⬇️ Download row-level error report (CSV)", rep.to_csv(index=False),
                                   file_name=f"import_errors_{payload['name']}.csv", mime="text/csv", key="dl_err")
                for pid, msgs in res["rejected_groups"].items():
                    st.error(f"**{pid}** rejected: " + " · ".join(msgs[:4]) + (" …" if len(msgs) > 4 else ""))
            if res["valid_products"]:
                st.success(f"✅ Ready to import: {', '.join(p['id'] for p in res['valid_products'])} (as Draft records)")
            if st.button(f"📥 Import {len(res['valid_products'])} valid product(s)", type="primary",
                         key="imp_commit", disabled=not (can("create") and res["valid_products"]),
                         **_w(st.button)):
                try:
                    guard("create")
                    existing = {p["id"] for p in db["products"]}
                    bid = f"B-{uid()[:6]}"
                    committed = 0
                    for p in res["valid_products"]:
                        if p["id"] in existing:
                            continue
                        p["source"] = {"kind": "import", "file": payload["name"], "sha256": sha, "batch_id": bid}
                        db["products"].append(p)
                        log_event("import", "product", p["id"], f"Batch {bid} · {payload['name']}")
                        committed += 1
                    db["imports"].append({"id": bid, "file": payload["name"], "sha256": sha, "ts": utcnow(),
                                          "actor": current_user(), "rows": res["n_rows"], "imported": committed,
                                          "rejected": len(res["rejected_groups"]), "format": payload["fmt"],
                                          "raw_text": raw_text})
                    st.toast(f"Imported {committed} product(s) — originals preserved in batch {bid}", icon="📥")
                    st.session_state.import_payload = None
                    st.rerun()
                except PermissionError as ex:
                    st.error(str(ex))
    st.markdown("#### 🧾 Import history (source files preserved with checksums)")
    hist = pd.DataFrame([{k: v for k, v in b.items() if k != "raw_text"} for b in db["imports"]][::-1])
    st.dataframe(hist, hide_index=True, **_w(st.dataframe))

# ══════════════════════════════ PAGE: REVIEW & APPROVALS ══════════════════════════════
def page_review():
    db = st.session_state.db
    factors = current_factors()
    page_header("✅ Review & approvals", "Reviewer decisions with mandatory notes on change requests, blocked approvals on validation errors, and audited manual overrides.")
    queue = [p for p in db["products"] if p["status"] == "In Review"]
    if not queue:
        st.success("🎉 Review queue is clear. Submit validated records from the Products page.")
    else:
        pid = st.selectbox("Queue item", [p["id"] for p in queue], key="rev_pick",
                           format_func=lambda i: f"{i} — {product_by_id(i)['name']}")
        p = product_by_id(pid)
        r, e = safe_results(p, factors)
        issues = validate_product(p)
        errors = [i for i in issues if i["severity"] == "error"]
        m = st.columns(3)
        m[0].metric("Engine total", f"{fmt(r['total_ghg'])} kg CO₂e" if r else "—")
        m[1].metric("Validation errors", len(errors))
        m[2].metric("Warnings", len(issues) - len(errors))
        for i in issues:
            (st.error if i["severity"] == "error" else st.warning)(f"**{i['field']}** — {i['message']}")
        if e: st.error(e)
        if r:
            with st.expander("Line items under review", expanded=False):
                st.dataframe(pd.DataFrame(r["lines"])[["stage", "item", "qty", "unit", "factor_id", "ghg", "quality"]],
                             hide_index=True, **_w(st.dataframe))
        note = st.text_area("Reviewer note (required for change requests)", key=f"rev_note_{pid}",
                            placeholder="Evidence checked, assumptions accepted because…")
        c1, c2 = st.columns(2)
        if c1.button("✅ Approve", type="primary", key=f"rev_ok_{pid}",
                     disabled=not can("approve") or bool(errors), **_w(st.button)):
            try:
                transition(p, "Approved", note); st.toast(f"{pid} approved", icon="✅"); st.rerun()
            except (ValueError, PermissionError) as ex: st.error(str(ex))
        if c2.button("↩️ Request changes", key=f"rev_no_{pid}", disabled=not can("approve"), **_w(st.button)):
            try:
                transition(p, "Changes Requested", note); st.toast("Changes requested", icon="↩️"); st.rerun()
            except (ValueError, PermissionError) as ex: st.error(str(ex))
        if not can("approve"):
            st.info(f"Role '{st.session_state.role}' can view but not decide. Switch to Reviewer or Administrator.")
    st.markdown("#### ✍️ Manual override (engine value always preserved)")
    over_candidates = [p for p in db["products"] if p["status"] in ("In Review", "Approved")]
    if over_candidates:
        opid = st.selectbox("Record", [p["id"] for p in over_candidates], key="ov_pick",
                            format_func=lambda i: f"{i} — {product_by_id(i)['name']}")
        op = product_by_id(opid)
        orr, _ = safe_results(op, factors)
        o1, o2, o3 = st.columns([1, 2, 1])
        new_total = o1.number_input("Override total (kg CO₂e)", 0.0, 1e9,
                                    float(op["override"]["total_ghg"] if op.get("override") else (orr["total_ghg"] if orr else 0.0)),
                                    1.0, key="ov_val", disabled=not can("override"))
        reason = o2.text_input("Reason (required, audited)", key="ov_reason",
                               value=op["override"]["reason"] if op.get("override") else "",
                               disabled=not can("override"))
        if o3.button("Apply override", key="ov_apply", disabled=not can("override")):
            if not reason.strip():
                st.warning("A reason is required for a manual override.")
            else:
                op["override"] = {"total_ghg": float(new_total), "reason": reason.strip(),
                                  "by": current_user(), "ts": utcnow()}
                op["updated_utc"] = utcnow()
                log_event("override", "product", opid,
                          f"{fmt(new_total)} kg — {reason.strip()} (engine {fmt(orr['total_ghg']) if orr else '?'} preserved)")
                st.toast("Override applied & audited", icon="✍️"); st.rerun()
        if op.get("override") and can("override"):
            if st.button("🧹 Clear override", key="ov_clear"):
                log_event("clear_override", "product", opid, f"Removed override of {fmt(op['override']['total_ghg'])} kg")
                op["override"] = None; st.toast("Override cleared", icon="🧹"); st.rerun()
    st.markdown("#### 🏛️ Approved register")
    approved = [p for p in db["products"] if p["status"] == "Approved"]
    if approved:
        st.dataframe(pd.DataFrame([{"id": p["id"], "name": p["name"],
                                    "approver": p.get("review", {}).get("by", "—"),
                                    "note": p.get("review", {}).get("note", ""),
                                    "approved_utc": p.get("review", {}).get("ts", p["updated_utc"])} for p in approved]),
                     hide_index=True, **_w(st.dataframe))
    with st.expander("🔐 Access-control matrix (enforced in handlers)"):
        actions = ["view", "edit", "create", "delete", "submit", "approve", "override", "archive", "export", "factors", "restore", "reset"]
        st.dataframe(pd.DataFrame([{"action": a, **{r: ("✅" if role_can(r, a) or (a == "export" and role_can(r, "export_approved")) else "—")
                                                   for r in USERS}} for a in actions]),
                     hide_index=True, **_w(st.dataframe))
        st.caption("Stakeholders may export **approved** reports only.")

# ══════════════════════════════ PAGE: REPORTS & EXPORT ══════════════════════════════
REPORT_CSS = """<style>
body{font-family:'Segoe UI',Inter,sans-serif;color:#14231C;margin:0;background:#F6F5EF}
.wrap{max-width:860px;margin:0 auto;padding:28px}
.hero{background:linear-gradient(120deg,#0B3B2E,#1E7A55 70%,#4C9A44);color:#fff;border-radius:14px;padding:22px 26px}
.hero h1{margin:0 0 4px;font-size:22px}.hero p{margin:0;color:#CFE8D2;font-size:13px}
.meta{display:flex;flex-wrap:wrap;gap:8px;margin:14px 0}
.chip{background:#fff;border:1px solid #D8E6CF;border-radius:999px;padding:4px 12px;font-size:12px;font-weight:600}
.kpis{display:flex;gap:12px;margin:14px 0;flex-wrap:wrap}
.kpi{flex:1;min-width:150px;background:#fff;border-left:5px solid #2E7D5B;border-radius:10px;padding:12px 14px;border-top:1px solid #E1E8DD;border-right:1px solid #E1E8DD;border-bottom:1px solid #E1E8DD}
.kpi b{display:block;font-size:20px;color:#0E3B2E}.kpi span{font-size:12px;color:#4A5A50}
h2{color:#0E3B2E;font-size:16px;margin:22px 0 8px;border-bottom:2px solid #A8E10C;padding-bottom:4px}
table{border-collapse:collapse;width:100%;background:#fff;font-size:12px}
th{background:#0E3B2E;color:#fff;padding:6px 8px;text-align:left}
td{border-bottom:1px solid #E1E8DD;padding:5px 8px}
.warn{background:#FFF3CD;border:1px solid #F2A007;border-radius:10px;padding:10px 14px;font-size:12px;margin:10px 0}
.draft{background:#FDECEA;border:2px solid #E4572E;color:#8C2B0E;border-radius:10px;padding:10px 14px;font-weight:700;margin:10px 0}
.foot{font-size:11px;color:#5C665F;margin-top:22px;border-top:1px solid #D8E6CF;padding-top:10px}
code{background:#EDF2EA;padding:1px 5px;border-radius:4px}
</style>"""

def html_report(p, r, issues, mc, fv, audit_rows) -> str:
    esc = html_lib.escape
    eff = p["override"]["total_ghg"] if p.get("override") else r["total_ghg"]
    stages_df = pd.DataFrame([{"Stage": s, "kg CO2e": round(r["stages"][s]["ghg"], 2),
                               "MJ": round(r["stages"][s]["mj"], 1)} for s in STAGES])
    hot_df = pd.DataFrame([{"Stage": l["stage"], "Item": l["item"], "kg CO2e": round(l["ghg"], 2),
                            "Factor": l["factor_id"], "Quality": l["quality"]}
                           for l in sorted(r["lines"], key=lambda x: abs(x["ghg"]), reverse=True)[:6]])
    aud_df = pd.DataFrame(audit_rows)[["ts", "actor", "action", "details"]] if audit_rows else pd.DataFrame()
    warn_html = "".join(f"<div class='warn'>⚠️ <b>{esc(i['field'])}</b>: {esc(i['message'])}</div>"
                        for i in issues) or "<p>✅ No validation issues.</p>"
    draft_html = "" if p["status"] == "Approved" else "<div class='draft'>DRAFT — NOT APPROVED · decision support only</div>"
    ov_html = (f"<div class='warn'>✍️ Manual override: <b>{eff:,.1f} kg</b> — “{esc(p['override']['reason'])}” "
               f"by {esc(p['override']['by'])}. Engine value {r['total_ghg']:,.1f} kg preserved.</div>"
               if p.get("override") else "")
    review = p.get("review", {})
    return REPORT_CSS + f"""<div class="wrap">
<div class="hero"><h1>🌿 Lifecycle Impact Report — {esc(p['name'])}</h1>
<p>{esc(p['functional_unit'])} · cradle-to-grave · attributional</p></div>
{draft_html}
<div class="meta"><span class="chip">ID {esc(p['id'])}</span><span class="chip">Status {esc(p['status'])}</span>
<span class="chip">Engine {ENGINE_VERSION}</span><span class="chip">Factors {esc(fv)}</span>
<span class="chip">Digest {result_digest(r)}</span><span class="chip">Generated {utcnow()}</span></div>
<div class="kpis">
<div class="kpi"><span>Total (effective)</span><b>{eff:,.1f} kg CO₂e</b></div>
<div class="kpi"><span>Per service year</span><b>{r['per_year_ghg']:,.1f} kg</b></div>
<div class="kpi"><span>Energy (CED)</span><b>{r['total_mj']:,.0f} MJ</b></div>
<div class="kpi"><span>Uncertainty P5–P95</span><b>{mc['p5']:,.0f} – {mc['p95']:,.0f} kg</b></div></div>
{ov_html}
<h2>Stage breakdown</h2>{stages_df.to_html(index=False, border=0)}
<h2>Top contributors</h2>{hot_df.to_html(index=False, border=0)}
<h2>Uncertainty</h2><p>Monte Carlo, {len(mc['totals']):,} draws, seed-fixed pedigree model
(σ: measured 5% · estimated 15% · proxy 30%, combined with 10% factor uncertainty).
P5 <b>{mc['p5']:,.1f}</b> · P50 <b>{mc['p50']:,.1f}</b> · P95 <b>{mc['p95']:,.1f}</b> kg CO₂e.</p>
<h2>Data quality & validation</h2>{warn_html}
<h2>Boundaries & assumptions</h2><ul>
<li>Materials: mass × blended virgin/recycled factor. Manufacturing & use: kWh × regional grid factor.</li>
<li>Transport: BOM mass (t) × distance × mode factor. EoL: recycling credit (90% recovery) + incineration + landfill.</li>
<li>Capital goods, refrigerant leakage and infrastructure excluded. Notes: {esc(p.get('notes','') or '—')}</li></ul>
<h2>Review & approval</h2><p>Status <b>{esc(p['status'])}</b> · Reviewer: {esc(review.get('by','—'))}
· Note: {esc(review.get('note','—'))} · {esc(review.get('ts','—'))}</p>
<h2>Lineage & audit trail (latest)</h2>
<p>Source: <code>{esc(json.dumps(p['source']))}</code></p>{aud_df.to_html(index=False, border=0) if len(aud_df) else ''}
<div class="foot">SYNTHETIC DEMO DATA with illustrative factors ({esc(fv)}). This report is decision support,
not a certified LCA, EPD or regulatory compliance claim. Qualified practitioner review and licensed
reference data are required before external use. © EcoLedger LCA demo.</div></div>"""

def portfolio_df(products, factors, approved_only=False) -> pd.DataFrame:
    rows = []
    for p in products:
        if approved_only and p["status"] != "Approved":
            continue
        r, e = safe_results(p, factors)
        row = {"id": p["id"], "name": p["name"], "category": p["category"], "status": p["status"],
               "functional_unit": p["functional_unit"]}
        if r:
            row.update({s: round(r["stages"][s]["ghg"], 2) for s in STAGES})
            row.update({"total_kg_co2e": round(r["total_ghg"], 2),
                        "per_year_kg": round(r["per_year_ghg"], 2) if math.isfinite(r["per_year_ghg"]) else None,
                        "total_mj": round(r["total_mj"], 1)})
        else:
            row["error"] = e
        rows.append(row)
    return pd.DataFrame(rows)

def page_reports():
    db = st.session_state.db
    factors = current_factors()
    page_header("📄 Reports & export", "Traceable outputs: styled HTML report, line-item CSV, full lineage JSON bundle and portfolio summary. Exports are audited.")
    stake = st.session_state.role == "Stakeholder"
    prods = [p for p in db["products"] if (p["status"] == "Approved" if stake else p["status"] != "Archived")]
    if stake:
        st.info("👁️ Stakeholder role: approved records only.")
    if not prods and not stake:
        st.info("No records available."); return
    fmt_pick = st.radio("Format", ["HTML report", "Line items (CSV)", "Lineage bundle (JSON)", "Portfolio summary (CSV)"],
                        horizontal=True, key="rpt_fmt")
    pid = None
    if fmt_pick != "Portfolio summary (CSV)":
        if not prods:
            st.warning("No approved records to export yet."); return
        pid = st.selectbox("Product", [p["id"] for p in prods], key="rpt_pick",
                           format_func=lambda i: f"{i} — {product_by_id(i)['name']} ({product_by_id(i)['status']})")
    if st.button("⚙️ Generate", type="primary", key="rpt_gen"):
        try:
            if stake:
                guard("export_approved")
            else:
                guard("export")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
            if fmt_pick == "Portfolio summary (CSV)":
                df = portfolio_df(db["products"], factors, approved_only=stake)
                st.session_state.report_cache = {"fmt": fmt_pick, "name": f"portfolio_summary_{stamp}.csv",
                                                 "data": df.to_csv(index=False), "mime": "text/csv", "df": df}
                log_event("export", "portfolio", "all", f"Portfolio CSV ({len(df)} rows)")
            else:
                p = product_by_id(pid)
                r, e = safe_results(p, factors)
                if e: raise ValueError(e)
                if fmt_pick == "HTML report":
                    mc = cached_mc(json.dumps(p, sort_keys=True), json.dumps(factors, sort_keys=True),
                                   2000, db["settings"]["mc_seed"])
                    audit_rows = [a for a in db["audit"] if a["entity_id"] == pid][-10:]
                    data = html_report(p, r, validate_product(p), mc, db["factor_version"], audit_rows)
                    st.session_state.report_cache = {"fmt": fmt_pick, "name": f"report_{pid}_{stamp}.html",
                                                     "data": data, "mime": "text/html", "pid": pid}
                elif fmt_pick == "Line items (CSV)":
                    df = pd.DataFrame(r["lines"])
                    st.session_state.report_cache = {"fmt": fmt_pick, "name": f"line_items_{pid}_{stamp}.csv",
                                                     "data": df.to_csv(index=False), "mime": "text/csv", "df": df}
                else:
                    mc = cached_mc(json.dumps(p, sort_keys=True), json.dumps(factors, sort_keys=True),
                                   2000, db["settings"]["mc_seed"])
                    bundle = {"product": p, "results": r, "validation": validate_product(p),
                              "factor_version": db["factor_version"], "factors": factors,
                              "engine_version": ENGINE_VERSION, "result_digest": result_digest(r),
                              "mc": {k: v for k, v in mc.items() if k != "totals"},
                              "audit": [a for a in db["audit"] if a["entity_id"] == pid],
                              "generated_utc": utcnow(), "synthetic_demo": True}
                    st.session_state.report_cache = {"fmt": fmt_pick, "name": f"lineage_{pid}_{stamp}.json",
                                                     "data": json.dumps(bundle, indent=2), "mime": "application/json"}
                log_event("export", "product", pid, fmt_pick)
            st.toast("Report generated & audit-logged", icon="📄")
        except (ValueError, PermissionError) as ex:
            st.error(str(ex))
    cache = st.session_state.report_cache
    if cache:
        st.download_button(f"⬇️ Download {cache['name']}", cache["data"], file_name=cache["name"],
                           mime=cache["mime"], key="rpt_dl", type="primary")
        if cache["fmt"] == "HTML report":
            p = product_by_id(cache.get("pid"))
            if p and p["status"] != "Approved":
                st.caption("🔴 Watermarked DRAFT — approval required before external sharing.")
            components_html(cache["data"], height=560, scrolling=True)
        elif "df" in cache:
            st.dataframe(cache["df"], hide_index=True, height=320, **_w(st.dataframe))
        else:
            st.code(cache["data"][:1500] + ("\n… (truncated preview — full file in download)" if len(cache["data"]) > 1500 else ""),
                    language="json")

# ══════════════════════════════ PAGE: GOVERNANCE & QA ══════════════════════════════
def page_governance():
    db = st.session_state.db
    factors = current_factors()
    page_header("🛡️ Governance & QA", "Audit trail, lineage, versioned reference data, data dictionary, schema, self-checks, backup/restore and product documentation.")
    tabs = st.tabs(["📜 Audit log", "🧬 Lineage", "📚 Reference factors", "🗂️ Data dictionary",
                    "🗺️ Schema & workflow", "🧪 Self-checks", "💾 Backup & restore", "📘 Product docs"])
    with tabs[0]:
        f1, f2 = st.columns(2)
        actions = sorted({a["action"] for a in db["audit"]})
        fa = f1.multiselect("Action", actions, key="aud_act")
        fp = f2.selectbox("Record", ["(all)"] + [p["id"] for p in db["products"]], key="aud_pid")
        rows = [a for a in db["audit"][::-1]
                if (not fa or a["action"] in fa) and (fp == "(all)" or a["entity_id"] == fp)]
        st.dataframe(pd.DataFrame(rows), hide_index=True, height=380, **_w(st.dataframe))
        st.download_button("⬇️ Export audit (CSV)", pd.DataFrame(rows).to_csv(index=False),
                           "audit_log.csv", "text/csv", key="aud_dl", disabled=not can("export"))
    with tabs[1]:
        prods = db["products"]
        if prods:
            pid = st.selectbox("Record", [p["id"] for p in prods], key="lin_pick",
                               format_func=lambda i: f"{i} — {product_by_id(i)['name']}")
            p = product_by_id(pid)
            r, e = safe_results(p, factors)
            src = p["source"]
            chain = (f"**{src.get('file', src.get('kind','manual'))}** → import/manual entry → "
                     f"validation rules v{SCHEMA_VERSION} → engine **{ENGINE_VERSION}** × factors "
                     f"**{db['factor_version']}** → result" + (f" `{result_digest(r)}`" if r else " (blocked)"))
            st.markdown(chain)
            st.json(src, expanded=False)
            if e: st.error(e)
            if r:
                st.dataframe(pd.DataFrame(r["lines"])[["stage", "item", "factor_id", "factor", "qty", "unit", "ghg", "mj", "quality", "sigma"]],
                             hide_index=True, height=320, **_w(st.dataframe))
            batch = next((b for b in db["imports"] if b["id"] == src.get("batch_id")), None)
            if batch:
                st.caption(f"Source batch {batch['id']} preserved · sha256 `{batch['sha256']}` · {batch['rows']} rows")
    with tabs[2]:
        versions = list(db["factor_versions"])
        v = st.selectbox("Inspect version", versions, index=versions.index(db["factor_version"]), key="fac_ver")
        fv = db["factor_versions"][v]
        c = st.columns(3)
        c[0].dataframe(pd.DataFrame([{"material": k, **vals} for k, vals in fv["materials"].items()]),
                       hide_index=True, height=300, **_w(st.dataframe))
        c[1].dataframe(pd.DataFrame([{"grid": k, **vals} for k, vals in fv["grids"].items()]),
                       hide_index=True, height=300, **_w(st.dataframe))
        c[2].dataframe(pd.DataFrame([{"mode": k, **vals} for k, vals in fv["transport"].items()]),
                       hide_index=True, height=300, **_w(st.dataframe))
        st.caption("Units — materials: kgCO₂e/kg & MJ/kg · grids: kgCO₂e/kWh & MJ/kWh · transport: per t·km. "
                   "Illustrative synthetic values; replace with a licensed database (e.g. ecoinvent) after license review.")
        a1, a2 = st.columns(2)
        if a1.button(f"⏪ Activate inspected version ({v})", key="fac_roll", disabled=not can("factors")):
            old = db["factor_version"]
            db["factor_version"] = v
            log_event("factor_rollback", "factors", v, f"{old} → {v}")
            st.toast(f"Factor set rolled back to {v}", icon="⏪"); st.rerun()
        with st.expander("✏️ Publish new grid-factor version (Administrator)"):
            new_vals = {}
            for i, (gname, g) in enumerate(factors["grids"].items()):
                new_vals[gname] = st.number_input(f"{gname} — kgCO₂e/kWh", 0.001, 2.0, float(g["ghg"]), 0.005,
                                                  key=f"fg_{i}", format="%.3f", disabled=not can("factors"))
            if st.button("🚀 Publish new version", key="fac_pub", disabled=not can("factors")):
                k = len(db["factor_versions"]) + 1
                nv = f"SYN-2026.{k}"
                while nv in db["factor_versions"]:
                    k += 1; nv = f"SYN-2026.{k}"
                nf = copy.deepcopy(factors)
                for gname, val in new_vals.items():
                    nf["grids"][gname]["ghg"] = float(val)
                db["factor_versions"][nv] = nf
                db["factor_version"] = nv
                log_event("factor_publish", "factors", nv, f"Grid factors updated from {st.session_state.db['factor_version']}")
                st.toast(f"Published {nv} — all results recompute against it", icon="🚀"); st.rerun()
    with tabs[3]:
        dd = [
            ("products.id", "text PK", "—", "Stable record identifier", "unique", "system", "internal", "required, unique"),
            ("products.status", "enum", "—", "Lifecycle state", ", ".join(STATUSES), "workflow", "internal", "transition matrix"),
            ("products.lifetime_years", "float", "years", "Service life", "(0, 100]", "analyst/import", "internal", ">0, ≤100; >30 warns"),
            ("bom.component", "text", "—", "Component name", "free", "analyst/import", "internal", "required; dup warns"),
            ("bom.material", "FK", "—", "Reference material", "catalog keys", "catalog", "public", "must exist in factor set"),
            ("bom.mass_kg", "float", "kg", "Component mass", ">0", "BOM/supplier", "commercial-sensitive", ">0; >5000 warns"),
            ("bom.recycled_pct", "float", "%", "Recycled content", "0–100", "supplier decl.", "commercial-sensitive", "range check"),
            ("bom.quality", "enum", "—", "Pedigree → MC sigma", "measured/estimated/proxy", "analyst", "internal", "enum check"),
            ("manufacturing.kwh", "float", "kWh", "Assembly electricity", "≥0", "meter/estimate", "commercial-sensitive", "≥0"),
            ("manufacturing.grid / use.grid", "FK", "kgCO₂e/kWh", "Electricity mix", "catalog keys", "catalog", "public", "must exist"),
            ("use.kwh_per_year", "float", "kWh/y", "Operational energy", "≥0", "telematics/estimate", "internal", "≥0"),
            ("transport.mode", "FK", "—", "Freight mode", "catalog keys", "logistics", "internal", "must exist"),
            ("transport.distance_km", "float", "km", "Leg distance", "0–100000", "logistics", "internal", "range; air>8000 warns"),
            ("eol.*_pct", "float", "%", "Fate shares", "0–100, Σ=100", "waste manifest", "internal", "sum 100 ±0.5"),
            ("override.total_ghg", "float", "kg CO₂e", "Manual override", "≥0 + reason", "reviewer", "restricted", "reason & audit required"),
            ("imports.sha256", "text", "—", "Source checksum", "hex-64", "system", "internal", "computed on upload"),
            ("audit.ts", "ISO-8601", "UTC", "Event time", "timezone-aware", "system", "restricted", "auto"),
            ("factor_versions.version", "text PK", "—", "Reference-data version", "SYN-YYYY.n", "admin", "public", "immutable snapshots"),
            ("scenarios.params", "json", "mixed", "Scenario levers", "schema-checked", "analyst", "internal", "validated on apply"),
            ("*.created/updated_utc", "ISO-8601", "UTC", "Timestamps", "timezone-aware", "system", "internal", "auto"),
        ]
        st.dataframe(pd.DataFrame(dd, columns=["field", "type", "unit", "meaning", "allowed", "source", "sensitivity", "validation"]),
                     hide_index=True, height=420, **_w(st.dataframe))
    with tabs[4]:
        st.markdown("**Record workflow (actors & exception path)**")
        st.graphviz_chart(WORKFLOW_DOT, **_w(st.graphviz_chart))
        st.markdown("**Normalized schema (SQLite MVP → PostgreSQL team edition)**")
        st.graphviz_chart(ERD_DOT, **_w(st.graphviz_chart))
        st.caption("MVP persists in-session with JSON backup/restore; the same entities map 1:1 to SQLite/PostgreSQL "
                   "tables (SQLAlchemy + Alembic migration path). Large originals live outside relational rows — "
                   "metadata + SHA-256 inside. Retention: soft-deleted records recoverable until the next deletion; "
                   "import originals retained in batch records; backups are operator-managed.")
    with tabs[5]:
        st.markdown("**Deterministic self-checks** — hand-checkable math, validation rules, import rejections, "
                    "reproducibility, permissions and audit provenance. Software correctness ≠ domain certification.")
        if st.button("▶ Run self-checks", type="primary", key="qa_run") or st.session_state.qa_results is None:
            with st.spinner("Running 18 checks…"):
                st.session_state.qa_results = run_self_checks()
                st.session_state.qa_ts = utcnow()
        res = st.session_state.qa_results
        passed = sum(x["passed"] for x in res)
        (st.success if passed == len(res) else st.error)(f"{passed}/{len(res)} checks passed · {st.session_state.qa_ts}")
        st.dataframe(pd.DataFrame([{"check": x["check"], "result": "✅ PASS" if x["passed"] else "❌ FAIL",
                                    "detail": x["detail"]} for x in res]),
                     hide_index=True, height=420, **_w(st.dataframe))
        st.download_button("⬇️ QA report (JSON)",
                           json.dumps({"ran_utc": st.session_state.qa_ts, "engine": ENGINE_VERSION,
                                       "factor_version": db["factor_version"],
                                       "summary": {"passed": passed, "total": len(res)}, "results": res}, indent=2),
                           "qa_report.json", "application/json", key="qa_dl")
        st.caption("Evaluation stance: the deterministic factor model **is** the baseline; Monte Carlo propagates "
                   "pedigree uncertainty. No ML is trained on synthetic data and no real-world predictive performance is claimed.")
    with tabs[6]:
        b1, b2 = st.columns(2)
        backup = json.dumps({"backup_version": 1, "schema_version": SCHEMA_VERSION,
                             "exported_utc": utcnow(), "db": db}, indent=2)
        b1.download_button("⬇️ Full backup (JSON)", backup,
                           f"ecoledger_backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json",
                           "application/json", key="bk_dl", disabled=not can("export"))
        b1.caption("Includes products, audit, factor versions, scenarios, import originals & checksums.")
        up = b2.file_uploader("Restore from backup (JSON)", type=["json"], key="rs_up",
                              disabled=not can("restore"))
        confirm = b2.checkbox("I understand this replaces the current workspace", key="rs_confirm",
                              disabled=not can("restore"))
        if b2.button("♻️ Restore backup", key="rs_go", disabled=not (can("restore") and up and confirm)):
            try:
                data = json.loads(up.getvalue().decode("utf-8"))
                nd = data["db"]
                assert isinstance(nd["products"], list) and isinstance(nd["audit"], list)
                assert nd["factor_version"] in nd["factor_versions"]
                st.session_state.db = nd
                st.session_state.selected_product = None
                st.session_state.report_cache = None
                log_event("restore", "workspace", "backup", f"{len(nd['products'])} products restored")
                st.toast("Workspace restored from backup", icon="♻️"); st.rerun()
            except Exception as ex:
                st.error(f"Restore rejected (current data untouched): {ex}")
        with st.expander("🧨 Danger zone — reset demo workspace"):
            rc = st.checkbox("Confirm full reset to synthetic demo seed", key="reset_confirm",
                             disabled=not can("reset"))
            if st.button("Reset demo data", key="reset_go", disabled=not (can("reset") and rc)):
                st.session_state.db = build_demo_db()
                st.session_state.selected_product = None
                st.session_state.intro_done = False
                st.session_state.qa_results = None
                st.session_state.report_cache = None
                log_event("reset", "workspace", "demo", "Reset to synthetic seed")
                st.toast("Demo workspace reset", icon="🧨"); st.rerun()
    with tabs[7]:
        st.markdown("""
**Problem.** Sustainability teams rebuild product footprints in fragile spreadsheets: inconsistent factors,
no review trail, no uncertainty, hours per study. **Buyer (pilot persona):** a municipal sustainability lead
commissioning comparable cradle-to-grave estimates for procured assets.

**Personas & roles.** Ava (Analyst) enters BOM/energy/logistics data · Dr. Patel (Reviewer) approves or requests
changes with notes · Morgan (Administrator) manages factors, archives, backups · Sam (Stakeholder) reads and
exports approved reports only.

**In scope (MVP).** Synthetic demo portfolio · CSV/XLSX import with row-level validation & error reports ·
deterministic 5-stage engine · seeded Monte Carlo · scenario lab · review/approval workflow with overrides ·
audit + lineage · HTML/CSV/JSON exports · backup/restore · factor versioning with rollback · this self-check suite.

**Out of scope (MVP).** Licensed LCA databases, certified EPDs, multi-user auth/SSO, ERP adapters, ML forecasts,
regulatory compliance claims. **Success metrics (pilot, to be measured with real users):** time-to-first-report,
% imports passing without manual fixes, reviewer turnaround, reproducibility digest match rate. No customer
impact is claimed until piloted.

| Acceptance criterion | Pass condition | Verify in |
|---|---|---|
| Sample import | 2 products imported from documented CSV | Import → built-in valid sample |
| Malformed rejection | Row-level actionable errors + downloadable report; nothing committed | Import → broken sample |
| Reproducibility | Same inputs/versions → identical digest & MC seed → identical draws | Analysis · Self-checks |
| Traceability | Every line shows factor ID, version, formula, quality | Analysis → explanation panel |
| Workflow | Draft→Validated→In Review→Approved with role gates & exception path | Products · Review |
| Export | Watermarked HTML report + CSV + lineage JSON downloaded | Reports |
| Automated checks | 18/18 self-checks pass on this machine | Self-checks tab |
| Clean restart | Reset demo reproduces the identical seed portfolio | Backup & restore |

| Risk | Mitigation |
|---|---|
| Domain validity (illustrative factors) | Synthetic labels everywhere; licensed-data gate before external use; practitioner review |
| Data quality | Pedigree sigmas, validation gates, warnings in every report |
| Security | Handler-level role guards, upload validation, no secrets, checksummed originals, audit |
| Adoption | One narrow workflow end-to-end; demo mode; saved views; undo |
| Operational failure | Backup/restore, never-overwrite imports, soft delete + undo, versioned factor rollback |

**Tiers.** Local MVP (1 operator, free eval) → Team (PostgreSQL, roles, approvals, per-seat) → Enterprise
(SSO, audit export, on-prem, SLA). Pricing hypothesis: per-seat + per-site license; validated via interviews
and a pilot, not assumed. **90-day roadmap:** weeks 1–4 pilot with 2 municipalities & weekly feedback;
weeks 5–8 SQLite persistence + licensed-factor evaluation; weeks 9–12 team edition beta + measured usage review.

**License inventory (commercial-compatible):** Streamlit (Apache-2.0) · pandas (BSD-3) · NumPy (BSD-3) ·
Plotly (MIT) · openpyxl (MIT). Dependencies unpinned here per request — pin via `pip freeze` for release SBOM.

**Human-approval boundary:** this software is decision support. Regulated or safety-sensitive use requires
qualified review, licensed reference data and authorized human approval. Measured vs estimated data are
distinguished via the pedigree field; no certification or compliance is claimed.
""")

# ══════════════════════════════ MAIN ══════════════════════════════
def main():
    st.set_page_config(page_title=f"{APP_NAME} — Lifecycle Impact Calculator", page_icon="🌿",
                       layout="wide", initial_sidebar_state="expanded")
    st.markdown(CSS, unsafe_allow_html=True)
    init_state()
    pend = st.session_state.pop("_nav_pending", None)
    if pend:
        st.session_state.nav = pend
    choice = sidebar()
    {NAV[0]: page_overview, NAV[1]: page_products, NAV[2]: page_analysis, NAV[3]: page_scenarios,
     NAV[4]: page_import, NAV[5]: page_review, NAV[6]: page_reports, NAV[7]: page_governance}[choice]()
    st.divider()
    st.caption(f"🌿 {APP_NAME} · Engine {ENGINE_VERSION} · Factors {st.session_state.db['factor_version']} · "
               "SYNTHETIC demo data with illustrative factors — decision support only, not a certified LCA/EPD. "
               "All mutations audited · no API keys · runs fully local.")

if __name__ == "__main__":
    main()