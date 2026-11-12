# ============================================================================
# TerraTrace — Product Lifecycle Environmental Impact Calculator
# Theme: Field Ledger · Daylight (warm paper + evergreen instrument panels)
# ----------------------------------------------------------------------------
# Single-file Streamlit MVP. Cradle-to-grave GWP accounting from BOM, energy,
# transport and end-of-life assumptions, with import validation, scenario lab,
# Monte Carlo uncertainty, sensitivity analysis, role-based review/approval,
# audit trail, lineage and report exports. Local SQLite persistence.
#
# Run (Windows PowerShell):
#   python -m venv .venv
#   .\.venv\Scripts\Activate.ps1
#   pip install -r requirements.txt
#   streamlit run app.py
#
# IMPORTANT: ships with SYNTHETIC demo data and ILLUSTRATIVE emission factors.
# Decision support only — not a certified LCA / EPD. Replace factors with a
# licensed database (e.g. ecoinvent, GREET, DEFRA) before real reporting.
# ============================================================================

import copy
import difflib
import hashlib
import html as htmlmod
import inspect
import io
import json
import math
import random
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

APP_NAME = "TerraTrace"
THEME_NAME = "Field Ledger · Daylight"
ENGINE_VERSION = "1.3.0"
SCHEMA_VERSION = 1
DB_PATH = Path("data") / "terratrace.db"
UPLOAD_DIR = Path("data") / "uploads"
CREDIT_QUALITY = 0.9
AIR_KM = 2200
QUALITY_GATE = 0.60
MAX_UPLOAD_BYTES = 5_000_000

STAGES = ["Materials", "Manufacturing", "Distribution", "Use phase", "End-of-life", "Credits"]
STAGE_COLORS = {
    "Materials": "#0D9488", "Manufacturing": "#65A30D", "Distribution": "#2563EB",
    "Use phase": "#D97706", "End-of-life": "#7C3AED", "Credits": "#DB2777",
}
STATUS_LABEL = {
    "draft": "Draft", "validated": "Validated", "in_review": "In review",
    "approved": "Approved", "changes_requested": "Changes requested", "archived": "Archived",
}
EDITABLE = {"draft", "validated", "changes_requested"}
QW = {"measured": 1.0, "supplier": 0.8, "proxy": 0.55}

ROLES = ["Administrator", "Analyst", "Reviewer", "Stakeholder"]
ALL_ACTIONS = {"edit", "import", "validate", "submit", "approve", "reopen", "archive",
               "override", "delete", "factors", "reset", "scenario", "export"}
PERMS = {
    "Administrator": set(ALL_ACTIONS),
    "Analyst": {"edit", "import", "validate", "submit", "scenario", "export"},
    "Reviewer": {"approve", "reopen", "override", "export"},
    "Stakeholder": {"export"},
}
TRANSITIONS = {
    ("draft", "validated"): "validate",
    ("changes_requested", "validated"): "validate",
    ("validated", "in_review"): "submit",
    ("in_review", "approved"): "approve",
    ("in_review", "changes_requested"): "approve",
    ("approved", "draft"): "reopen",
    ("archived", "draft"): "reopen",
    ("approved", "archived"): "archive",
}

MATERIALS = {
    "steel":     {"name": "Steel, section & sheet",   "gwp": 2.46, "rec": 0.68, "unc": 25, "incin": 0.02, "land": 0.03, "recyclable": True},
    "stainless": {"name": "Stainless steel",          "gwp": 6.15, "rec": 1.80, "unc": 30, "incin": 0.02, "land": 0.03, "recyclable": True},
    "aluminium": {"name": "Aluminium, wrought",       "gwp": 12.5, "rec": 0.92, "unc": 30, "incin": 0.02, "land": 0.03, "recyclable": True},
    "copper":    {"name": "Copper, cathode/wire",     "gwp": 3.00, "rec": 0.60, "unc": 25, "incin": 0.02, "land": 0.03, "recyclable": True},
    "brass":     {"name": "Brass, machined",          "gwp": 4.50, "rec": 1.10, "unc": 35, "incin": 0.02, "land": 0.03, "recyclable": True},
    "abs":       {"name": "ABS plastic",              "gwp": 3.80, "rec": 1.20, "unc": 25, "incin": 2.90, "land": 0.05, "recyclable": True},
    "pp":        {"name": "Polypropylene",            "gwp": 1.95, "rec": 0.90, "unc": 25, "incin": 2.70, "land": 0.05, "recyclable": True},
    "pet":       {"name": "PET plastic",              "gwp": 2.70, "rec": 1.10, "unc": 25, "incin": 2.30, "land": 0.05, "recyclable": True},
    "rubber":    {"name": "Rubber, tyre compound",    "gwp": 3.20, "rec": 1.40, "unc": 30, "incin": 2.40, "land": 0.05, "recyclable": True},
    "glass":     {"name": "Glass, technical",         "gwp": 0.85, "rec": 0.55, "unc": 20, "incin": 0.01, "land": 0.02, "recyclable": True},
    "cardboard": {"name": "Cardboard packaging",      "gwp": 0.90, "rec": 0.60, "unc": 20, "incin": 0.10, "land": 0.60, "recyclable": True},
    "pcb":       {"name": "Printed circuit board",    "gwp": 28.0, "rec": None, "unc": 45, "incin": 0.80, "land": 0.04, "recyclable": False},
    "libatt":    {"name": "Li-ion battery cell",      "gwp": 7.50, "rec": None, "unc": 40, "incin": 0.60, "land": 0.05, "recyclable": False},
}
PROCESS = {
    "grid":      {"name": "Electricity, grid mix",     "unit": "kWh",  "gwp": 0.380, "unc": 15},
    "gas":       {"name": "Natural gas, process heat", "unit": "kWh",  "gwp": 0.202, "unc": 10},
    "truck":     {"name": "Road freight, HGV",         "unit": "t·km", "gwp": 0.110, "unc": 25},
    "rail":      {"name": "Rail freight",              "unit": "t·km", "gwp": 0.028, "unc": 30},
    "sea":       {"name": "Sea freight, container",    "unit": "t·km", "gwp": 0.016, "unc": 30},
    "air":       {"name": "Air freight, long-haul",    "unit": "t·km", "gwp": 0.600, "unc": 40},
    "reprocess": {"name": "Recycling reprocessing",    "unit": "kg",   "gwp": 0.080, "unc": 50},
}
FACTOR_VERSIONS = {
    "FCT-2026.1": {"label": "Current · demo baseline", "overrides": {}},
    "FCT-2025.2": {"label": "Previous · rollback candidate", "overrides": {
        "steel.gwp": 2.62, "aluminium.gwp": 13.4, "pcb.gwp": 24.0,
        "grid.gwp": 0.42, "truck.gwp": 0.121, "libatt.gwp": 8.4}},
}

DEFAULT_PARAMS = {
    "volume_mult": 1.0, "grid": 0.38, "use_mult": 1.0, "life_mult": 1.0,
    "dist_mult": 1.0, "air_share": 4, "recycled_content": 25, "eol_recycle": 55,
    "incin_share": 40, "scrap": 6, "mc_runs": 1500, "seed": 42,
}
PARAM_BOUNDS = {
    "volume_mult": (0.2, 3.0, 0.05), "grid": (0.02, 0.90, 0.01),
    "use_mult": (0.2, 2.0, 0.05), "life_mult": (0.5, 2.0, 0.05),
    "dist_mult": (0.2, 3.0, 0.05), "air_share": (0, 30, 1),
    "recycled_content": (0, 100, 5), "eol_recycle": (0, 95, 5),
    "incin_share": (0, 100, 5), "scrap": (0, 20, 1),
    "mc_runs": (200, 5000, 100), "seed": (0, 999999, 1),
}


def resolve_factors(version):
    m, p = copy.deepcopy(MATERIALS), copy.deepcopy(PROCESS)
    for key, val in FACTOR_VERSIONS.get(version, {}).get("overrides", {}).items():
        name, field = key.split(".")
        if name in m:
            m[name][field] = val
        elif name in p:
            p[name][field] = val
    return {"version": version, "materials": m, "process": p}


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ============================================================================
# DOMAIN ENGINE
# ============================================================================
def assess(prod, lines, legs, params, factors, overrides=None):
    overrides = overrides or []
    if prod["lifetime_yr"] <= 0:
        raise ValueError("lifetime_yr must be > 0")
    if prod["use_kwh_yr"] < 0 or prod["assembly_kwh"] < 0 or prod["gas_kwh"] < 0:
        raise ValueError("Energy inputs must be >= 0")
    if not (0 < params["grid"] <= 2):
        raise ValueError("Grid intensity out of range (0–2 kg CO2e/kWh)")
    mat, proc = factors["materials"], factors["process"]
    rows = []

    def add(stage, item, qty, qu, code, fv, fu, gwp, unc, quality, formula):
        rows.append({"stage": stage, "item": item, "qty": float(qty), "qty_unit": qu,
                     "code": code, "factor": float(fv), "factor_unit": fu,
                     "gwp": float(gwp), "unc_pct": float(unc), "quality": quality,
                     "formula": formula})

    rc = params["recycled_content"] / 100.0
    total_mass = mat_embodied = qsum = 0.0
    for ln in lines:
        code = str(ln["material_code"]).strip().lower()
        if code not in mat:
            raise ValueError(f"Unknown material code '{code}' — not in factor catalog")
        mass = float(ln["mass_kg"])
        if not math.isfinite(mass) or mass <= 0 or mass > 5000:
            raise ValueError(f"Line '{code}': mass_kg must be a number in (0, 5000]")
        m = mat[code]
        r = rc if (m["recyclable"] and m["rec"] is not None) else 0.0
        ef = m["gwp"] * (1 - r) + (m["rec"] if m["rec"] is not None else m["gwp"]) * r
        g = mass * ef
        total_mass += mass
        mat_embodied += g
        qsum += mass * QW.get(ln.get("quality", "proxy"), 0.55)
        add("Materials", m["name"], mass, "kg", code, ef, "kg CO₂e/kg", g, m["unc"],
            ln.get("quality", "proxy"),
            f"{mass:.3f} kg × {ef:.2f} kg/kg (recycled content {r*100:.0f}%)")

    scrap = min(params["scrap"] / 100.0, 0.95)
    extra = total_mass * scrap / (1 - scrap)
    avg_ef = mat_embodied / total_mass if total_mass else 0.0
    add("Manufacturing", "Process scrap (upstream burden)", extra, "kg", "mat_avg",
        avg_ef, "kg CO₂e/kg", extra * avg_ef, 30, "estimate",
        f"{total_mass:.2f} kg × {scrap:.0%} ÷ (1−{scrap:.0%}) × {avg_ef:.2f} kg/kg")
    grid, gu = params["grid"], proc["grid"]["unc"]
    add("Manufacturing", "Assembly electricity", prod["assembly_kwh"], "kWh", "grid",
        grid, "kg CO₂e/kWh", prod["assembly_kwh"] * grid, gu, "supplier",
        f"{prod['assembly_kwh']:.1f} kWh × {grid:.2f} kg/kWh")
    add("Manufacturing", "Process heat (natural gas)", prod["gas_kwh"], "kWh", "gas",
        proc["gas"]["gwp"], "kg CO₂e/kWh", prod["gas_kwh"] * proc["gas"]["gwp"],
        proc["gas"]["unc"], "supplier",
        f"{prod['gas_kwh']:.1f} kWh × {proc['gas']['gwp']:.3f} kg/kWh")

    mass_t = total_mass / 1000.0
    has_outbound = any(l["scope"] == "outbound" for l in legs)
    for leg in legs:
        mode = leg["mode"]
        if mode not in proc:
            raise ValueError(f"Unknown transport mode '{mode}'")
        km = float(leg["km"]) * params["dist_mult"]
        if km < 0 or km > 60000:
            raise ValueError("Transport distance out of range (0–60,000 km)")
        share = (1 - params["air_share"] / 100.0) if leg["scope"] == "outbound" else 1.0
        tk = mass_t * km * share
        add("Distribution", f"{leg['scope'].title()} · {proc[mode]['name']}", tk, "t·km",
            mode, proc[mode]["gwp"], "kg CO₂e/t·km", tk * proc[mode]["gwp"],
            proc[mode]["unc"], "estimate",
            f"{mass_t:.4f} t × {km:,.0f} km × {share:.0%} modal share")
    if has_outbound and params["air_share"] > 0:
        tk = mass_t * AIR_KM * params["dist_mult"] * params["air_share"] / 100.0
        add("Distribution", "Outbound · Air freight (expedited share)", tk, "t·km", "air",
            proc["air"]["gwp"], "kg CO₂e/t·km", tk * proc["air"]["gwp"], proc["air"]["unc"],
            "estimate", f"{mass_t:.4f} t × {AIR_KM:,} km × {params['air_share']}% air")

    life = prod["lifetime_yr"] * params["life_mult"]
    use_kwh = prod["use_kwh_yr"] * params["use_mult"] * life
    add("Use phase", "Operating electricity", use_kwh, "kWh", "grid", grid,
        "kg CO₂e/kWh", use_kwh * grid, gu, "estimate",
        f"{prod['use_kwh_yr']:,.1f} kWh/yr × {life:.1f} yr × {grid:.2f} kg/kWh")

    er, inc_s = params["eol_recycle"] / 100.0, params["incin_share"] / 100.0
    for ln in lines:
        m = mat[str(ln["material_code"]).strip().lower()]
        mass = float(ln["mass_kg"])
        r = er if m["recyclable"] else 0.0
        mr = mass * r
        rem = mass - mr
        inc = rem * inc_s
        land = rem - inc
        if mr > 0:
            add("End-of-life", f"Recycling reprocessing — {m['name']}", mr, "kg",
                "reprocess", proc["reprocess"]["gwp"], "kg CO₂e/kg",
                mr * proc["reprocess"]["gwp"], proc["reprocess"]["unc"], "estimate",
                f"{mr:.3f} kg × {proc['reprocess']['gwp']:.2f} kg/kg")
            if m["rec"] is not None:
                cf = -(m["gwp"] - m["rec"]) * CREDIT_QUALITY
                add("Credits", f"Avoided virgin material — {m['name']}", mr, "kg",
                    str(ln["material_code"]).strip().lower(), cf, "kg CO₂e/kg", mr * cf,
                    m["unc"], "estimate",
                    f"−{mr:.3f} kg × ({m['gwp']:.2f} − {m['rec']:.2f}) × {CREDIT_QUALITY}")
        if inc > 0:
            add("End-of-life", f"Incineration — {m['name']}", inc, "kg",
                str(ln["material_code"]).strip().lower(), m["incin"], "kg CO₂e/kg",
                inc * m["incin"], 30, "estimate", f"{inc:.3f} kg × {m['incin']:.2f} kg/kg")
        if land > 0:
            add("End-of-life", f"Landfill — {m['name']}", land, "kg",
                str(ln["material_code"]).strip().lower(), m["land"], "kg CO₂e/kg",
                land * m["land"], 40, "estimate", f"{land:.3f} kg × {m['land']:.2f} kg/kg")

    for o in overrides:
        add(o["stage"], f"Manual override — {o['reason'][:60]}", o["delta_kg"],
            "kg CO₂e", "override", 1.0, "adjustment", o["delta_kg"], 0, "override",
            f"Reviewer adjustment by {o['actor']} at {o['ts']}")

    df = pd.DataFrame(rows)
    stages = df.groupby("stage")["gwp"].sum().reindex(STAGES, fill_value=0.0).to_dict()
    total = float(df["gwp"].sum())
    quality = qsum / total_mass if total_mass else 0.0
    proxy_mass = sum(float(l["mass_kg"]) for l in lines if l.get("quality") == "proxy")
    flags = [("info", "Illustrative demo factor set — replace with a licensed database before external reporting.")]
    if total_mass and proxy_mass / total_mass > 0.25:
        flags.append(("warning", f"{proxy_mass/total_mass:.0%} of BOM mass uses proxy factors — request supplier declarations."))
    if any(mat[str(l['material_code']).strip().lower()]["unc"] >= 40 for l in lines):
        flags.append(("warning", "High-uncertainty inputs present (electronics / battery, ±40–45%)."))
    if any(not str(l.get("supplier", "")).strip() for l in lines):
        flags.append(("warning", "Some BOM lines are missing a supplier name — provenance incomplete."))
    if any(not mat[str(l['material_code']).strip().lower()]["recyclable"] for l in lines):
        flags.append(("info", "Electronics/battery recycling credits are not modeled in the MVP (conservative)."))
    volume = prod["base_volume"] * params["volume_mult"]
    return {"rows": df, "stages": stages, "total": total, "mass": total_mass,
            "quality": quality, "flags": flags, "volume": volume,
            "annual": total * volume, "proxy_share": (proxy_mass / total_mass) if total_mass else 0.0}


def monte_carlo(res, factors, n, seed):
    grp = res["rows"].groupby("code")["gwp"].sum()
    codes, base = list(grp.index), grp.values.astype(float)
    sig = []
    for c in codes:
        if c in factors["materials"]:
            u = factors["materials"][c]["unc"]
        elif c in factors["process"]:
            u = factors["process"][c]["unc"]
        elif c == "mat_avg":
            u = 30.0
        else:
            u = 0.0
        sig.append(u / 100.0 / 1.96)
    rng = np.random.default_rng(int(seed))
    draws = rng.lognormal(0.0, np.array(sig), size=(int(n), len(codes)))
    totals = draws @ base
    p5, p50, p95 = np.percentile(totals, [5, 50, 95])
    return {"samples": totals, "p5": float(p5), "p50": float(p50), "p95": float(p95),
            "mean": float(totals.mean()), "std": float(totals.std()), "n": int(n)}


def tornado(prod, lines, legs, params, factors, overrides):
    drivers = [("Grid carbon intensity", "grid", "rel", 0.2),
               ("Use-phase energy", "use_mult", "rel", 0.2),
               ("Service lifetime", "life_mult", "rel", 0.2),
               ("Transport distance", "dist_mult", "rel", 0.2),
               ("Recycled content", "recycled_content", "pp", 15),
               ("EoL recycling rate", "eol_recycle", "pp", 15),
               ("Air freight share", "air_share", "pp", 4),
               ("Manufacturing scrap", "scrap", "pp", 3)]
    base = assess(prod, lines, legs, params, factors, overrides)["total"]
    out = []
    for label, key, mode, amt in drivers:
        lo, hi, _ = PARAM_BOUNDS[key]
        v = params[key]
        vlo = v * (1 - amt) if mode == "rel" else v - amt
        vhi = v * (1 + amt) if mode == "rel" else v + amt
        plo, phi = dict(params), dict(params)
        plo[key] = max(lo, min(hi, vlo))
        phi[key] = max(lo, min(hi, vhi))
        out.append({"driver": label,
                    "low": assess(prod, lines, legs, plo, factors, overrides)["total"] - base,
                    "high": assess(prod, lines, legs, phi, factors, overrides)["total"] - base})
    df = pd.DataFrame(out)
    df["span"] = df[["low", "high"]].abs().max(axis=1)
    return df.sort_values("span"), base


def validate_product(prod, lines, legs, factors):
    checks = []

    def err(m): checks.append({"level": "error", "msg": m})
    def warn(m): checks.append({"level": "warning", "msg": m})
    def ok(m): checks.append({"level": "ok", "msg": m})

    if not lines:
        err("BOM is empty — add at least one material line.")
    seen = set()
    proxy = total = 0.0
    for ln in lines:
        code = str(ln["material_code"]).strip().lower()
        if code not in factors["materials"]:
            err(f"Unknown material '{code}' — not in factor catalog.")
        if code in seen:
            err(f"Duplicate BOM material '{code}'.")
        seen.add(code)
        try:
            mass = float(ln["mass_kg"])
            if not math.isfinite(mass) or mass <= 0 or mass > 5000:
                err(f"'{code}': mass must be in (0, 5000] kg.")
            else:
                total += mass
                if ln.get("quality") == "proxy":
                    proxy += mass
        except (TypeError, ValueError):
            err(f"'{code}': mass_kg is not numeric.")
        if ln.get("quality") not in QW:
            err(f"'{code}': quality must be measured / supplier / proxy.")
        if not str(ln.get("supplier", "")).strip():
            warn(f"'{code}': supplier missing — provenance incomplete.")
    if prod["lifetime_yr"] <= 0 or prod["lifetime_yr"] > 50:
        err("Lifetime must be in (0, 50] years.")
    if prod["use_kwh_yr"] < 0 or prod["use_kwh_yr"] > 100000:
        err("Use-phase energy out of range (0–100,000 kWh/yr).")
    if prod["base_volume"] < 1:
        err("Annual volume must be >= 1 unit.")
    for leg in legs:
        if leg["mode"] not in ("truck", "rail", "sea", "air"):
            err(f"Unknown transport mode '{leg['mode']}'.")
        if float(leg["km"]) < 0 or float(leg["km"]) > 40000:
            err("Transport distance out of range (0–40,000 km).")
    score = 0.0
    if total:
        score = sum(float(l["mass_kg"]) * QW.get(l.get("quality"), 0.55)
                    for l in lines if str(l.get("quality")) in QW) / total
    if total and proxy / total > 0.25:
        warn(f"{proxy/total:.0%} proxy mass — below supplier-data ambition.")
    errors = [c for c in checks if c["level"] == "error"]
    if not errors:
        ok(f"All structural checks passed · {len(lines)} BOM lines · {total:.2f} kg.")
    gate = (not errors) and score >= QUALITY_GATE
    if not errors and not gate:
        warn(f"Data-quality score {score*100:.0f} < gate {QUALITY_GATE*100:.0f} — submission requires a documented exception.")
    return checks, score, gate


IMPORT_COLS = ["material_code", "mass_kg", "supplier", "quality"]
TEMPLATE_CSV = ("material_code,mass_kg,supplier,quality\n"
                "aluminium,2.50,AluCycle SA,supplier\n"
                "steel,1.20,ArcelorDemo Mill,measured\n"
                "abs,0.40,PolyNord GmbH,proxy\n")
BAD_CSV = ("material_code,mass_kg,supplier,quality\n"
           "steel,1.20,ArcelorDemo Mill,measured\n"
           "unobtanium,0.50,Unknown Co,supplier\n"
           "aluminium,-2.0,AluCycle SA,supplier\n"
           "copper,abc,,supplier\n"
           "abs,0.40,PolyNord GmbH,guess\n"
           "steel,0.80,Duplicate Mill,measured\n"
           "pcb,0.05,,proxy\n"
           ",0.30,NoCode Ltd,measured\n")


def validate_import(df, existing_codes, mode, factors):
    out = df.copy()
    statuses, errors = [], []
    seen = set()
    for _, row in out.iterrows():
        errs = []
        code = str(row.get("material_code", "") or "").strip().lower()
        if not code or code == "nan":
            errs.append("material_code is required")
        elif code not in factors["materials"]:
            sug = difflib.get_close_matches(code, list(factors["materials"]), n=2)
            errs.append(f"unknown material '{code}'" + (f" — did you mean {sug}?" if sug else " — see factor catalog"))
        mass = pd.to_numeric(row.get("mass_kg"), errors="coerce")
        if pd.isna(mass):
            errs.append("mass_kg must be a number")
        elif mass <= 0:
            errs.append("mass_kg must be > 0")
        elif mass > 5000:
            errs.append("mass_kg out of range (0–5000]")
        q = str(row.get("quality", "") or "").strip().lower()
        if q not in QW:
            errs.append("quality must be measured / supplier / proxy")
        if code and code in seen:
            errs.append("duplicate material_code within file")
        if mode == "Append" and code and code in existing_codes:
            errs.append("material already in BOM — use Replace mode or edit the BOM")
        if code:
            seen.add(code)
        statuses.append("error" if errs else "valid")
        errors.append("; ".join(errs))
    out["_status"] = statuses
    out["_errors"] = errors
    return out


# ============================================================================
# DATA LAYER
# ============================================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS products(
  id TEXT PRIMARY KEY, name TEXT, category TEXT, description TEXT,
  functional_unit TEXT, status TEXT, lifetime_yr REAL, use_kwh_yr REAL,
  assembly_kwh REAL, gas_kwh REAL, base_volume INTEGER,
  created_at TEXT, updated_at TEXT, created_by TEXT, source TEXT, schema_version INTEGER);
CREATE TABLE IF NOT EXISTS bom_lines(
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT REFERENCES products(id) ON DELETE CASCADE,
  material_code TEXT, mass_kg REAL, supplier TEXT, quality TEXT,
  created_at TEXT, updated_at TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS transport_legs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT REFERENCES products(id) ON DELETE CASCADE,
  scope TEXT, mode TEXT, km REAL, created_at TEXT);
CREATE TABLE IF NOT EXISTS audit_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, role TEXT,
  event TEXT, entity TEXT, entity_id TEXT, details TEXT);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT, ts TEXT, actor TEXT, role TEXT,
  from_status TEXT, to_status TEXT, comment TEXT);
CREATE TABLE IF NOT EXISTS scenarios(
  id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, params_json TEXT,
  created_at TEXT, actor TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS imports(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, product_id TEXT, filename TEXT,
  sha256 TEXT, mode TEXT, rows_total INTEGER, rows_ok INTEGER, rows_err INTEGER, actor TEXT);
CREATE TABLE IF NOT EXISTS overrides(
  id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT, stage TEXT, delta_kg REAL,
  reason TEXT, actor TEXT, role TEXT, ts TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def seed(c):
    now = datetime.now(timezone.utc)
    ts = lambda d=0: (now - timedelta(days=d)).isoformat(timespec="seconds")
    prods = [
        ("aqua-m2", "AquaSense M2 — Smart Water Meter", "Municipal IoT",
         "Ultrasonic residential water meter with LoRa telemetry, battery powered.",
         "1 metering unit · 10-year service life", "approved", 10, 0.06, 1.4, 0.3, 24000, ts(21), ts(2), "demo.seed", "synthetic://fixtures/v1"),
        ("therma-200", "ThermaCore 200 — Heat-Pump Water Heater", "Building equipment",
         "200 L integrated heat-pump water heater for residential retrofit programs.",
         "1 heater · 200 L storage · 12-year life", "in_review", 12, 940, 46, 12, 1500, ts(14), ts(3), "demo.seed", "synthetic://fixtures/v1"),
        ("city-ebike", "CityFlow E3 — Urban E-Bike", "Mobility",
         "Step-through commuter e-bike, 500 Wh battery, belt drive, fleet-ready.",
         "1 e-bike · 8-year / 12,000 km service", "draft", 8, 62, 18, 4, 5200, ts(1), ts(1), "demo.seed", "synthetic://fixtures/v1"),
    ]
    for p in prods:
        c.execute("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (*p, SCHEMA_VERSION))
    boms = {
        "aqua-m2": [("abs", 0.42, "PolyNord GmbH", "supplier"), ("pcb", 0.058, "", "proxy"),
                    ("libatt", 0.092, "CellTech Ltd", "supplier"), ("copper", 0.048, "CuWire BV", "supplier"),
                    ("brass", 0.185, "MetalWorks NV", "supplier"), ("stainless", 0.12, "MetalWorks NV", "supplier"),
                    ("glass", 0.032, "", "proxy"), ("cardboard", 0.16, "LocalPack Co", "measured")],
        "therma-200": [("steel", 38.5, "ArcelorDemo Mill", "measured"), ("stainless", 4.2, "MetalWorks NV", "supplier"),
                       ("copper", 6.4, "CuWire BV", "supplier"), ("aluminium", 3.1, "AluCycle SA", "supplier"),
                       ("abs", 2.6, "PolyNord GmbH", "supplier"), ("pp", 1.8, "", "proxy"),
                       ("rubber", 0.9, "TyreTech Oy", "supplier"), ("glass", 0.2, "", "proxy"),
                       ("cardboard", 2.4, "LocalPack Co", "measured")],
        "city-ebike": [("aluminium", 9.6, "AluCycle SA", "supplier"), ("steel", 2.3, "ArcelorDemo Mill", "supplier"),
                       ("libatt", 3.4, "CellTech Ltd", "supplier"), ("copper", 0.9, "", "proxy"),
                       ("rubber", 1.9, "TyreTech Oy", "supplier"), ("abs", 1.1, "PolyNord GmbH", "supplier"),
                       ("pp", 0.6, "", "proxy"), ("cardboard", 1.2, "LocalPack Co", "measured")],
    }
    for pid, lines in boms.items():
        for code, mass, sup, q in lines:
            c.execute("INSERT INTO bom_lines(product_id,material_code,mass_kg,supplier,quality,created_at,updated_at,source) VALUES (?,?,?,?,?,?,?,?)",
                      (pid, code, mass, sup, q, ts(14), ts(1), "synthetic://fixtures/v1"))
    legs = {
        "aqua-m2": [("inbound", "sea", 8600), ("inbound", "truck", 420), ("outbound", "truck", 650), ("outbound", "rail", 180)],
        "therma-200": [("inbound", "sea", 11200), ("inbound", "truck", 380), ("outbound", "truck", 900)],
        "city-ebike": [("inbound", "sea", 14500), ("inbound", "truck", 520), ("outbound", "truck", 780), ("outbound", "rail", 260)],
    }
    for pid, ll in legs.items():
        for scope, mode, km in ll:
            c.execute("INSERT INTO transport_legs(product_id,scope,mode,km,created_at) VALUES (?,?,?,?,?)", (pid, scope, mode, km, ts(14)))
    events = [
        (ts(21), "demo.seed", "Administrator", "created", "product", "aqua-m2", "Synthetic fixture loaded"),
        (ts(18), "Mira Chen", "Analyst", "validated", "product", "aqua-m2", "Structural checks passed"),
        (ts(16), "Mira Chen", "Analyst", "submitted", "product", "aqua-m2", "Submitted for review"),
        (ts(12), "Jonas Weber", "Reviewer", "approved", "product", "aqua-m2", "Approved for pilot reporting"),
        (ts(14), "demo.seed", "Administrator", "created", "product", "therma-200", "Synthetic fixture loaded"),
        (ts(3), "Mira Chen", "Analyst", "submitted", "product", "therma-200", "Submitted for review"),
        (ts(1), "demo.seed", "Administrator", "created", "product", "city-ebike", "Synthetic fixture loaded"),
    ]
    c.executemany("INSERT INTO audit_events(ts,actor,role,event,entity,entity_id,details) VALUES (?,?,?,?,?,?,?)", events)
    c.executemany("INSERT INTO approvals(product_id,ts,actor,role,from_status,to_status,comment) VALUES (?,?,?,?,?,?,?)", [
        ("aqua-m2", ts(16), "Mira Chen", "Analyst", "validated", "in_review", "Submitted for review"),
        ("aqua-m2", ts(12), "Jonas Weber", "Reviewer", "in_review", "approved", "Approved for pilot reporting"),
        ("therma-200", ts(3), "Mira Chen", "Analyst", "validated", "in_review", "Submitted for review"),
    ])
    green = dict(DEFAULT_PARAMS, grid=0.12, recycled_content=60, eol_recycle=75, air_share=0)
    c.execute("INSERT INTO scenarios(name,params_json,created_at,actor,note) VALUES (?,?,?,?,?)",
              ("Green grid 2030", json.dumps(green), ts(5), "Mira Chen (Analyst)", "Decarbonized grid + circular materials"))
    c.execute("INSERT OR IGNORE INTO settings VALUES ('factor_version','FCT-2026.1')")


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with closing(db()) as c, c:
        c.executescript(SCHEMA)
        if c.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0:
            seed(c)


def qdf(sql, args=()):
    with closing(db()) as c:
        return pd.read_sql_query(sql, c, params=args)


def log_event(c, actor, role, event, entity, entity_id, details):
    c.execute("INSERT INTO audit_events(ts,actor,role,event,entity,entity_id,details) VALUES (?,?,?,?,?,?,?)",
              (utcnow(), actor, role, event, entity, entity_id, details))


def get_setting(key, default=None):
    with closing(db()) as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r else default


def require(role, action):
    if action not in PERMS.get(role, set()):
        raise PermissionError(f"Role '{role}' is not permitted to '{action}'.")


def set_status(pid, to, actor, role, comment):
    with closing(db()) as c, c:
        cur = c.execute("SELECT status FROM products WHERE id=?", (pid,)).fetchone()
        if cur is None:
            raise ValueError("Product not found.")
        cur = cur[0]
        key = (cur, to)
        if key not in TRANSITIONS:
            raise ValueError(f"Invalid transition {cur} → {to}.")
        require(role, TRANSITIONS[key])
        c.execute("UPDATE products SET status=?, updated_at=? WHERE id=?", (to, utcnow(), pid))
        c.execute("INSERT INTO approvals(product_id,ts,actor,role,from_status,to_status,comment) VALUES (?,?,?,?,?,?,?)",
                  (pid, utcnow(), actor, role, cur, to, comment))
        log_event(c, actor, role, to, "product", pid, comment or f"{cur} → {to}")


def replace_bom(pid, rows, actor, role, source):
    require(role, "edit")
    with closing(db()) as c, c:
        st_row = c.execute("SELECT status FROM products WHERE id=?", (pid,)).fetchone()[0]
        if st_row not in EDITABLE:
            raise ValueError("Record is locked — reopen it before editing.")
        c.execute("DELETE FROM bom_lines WHERE product_id=?", (pid,))
        for r in rows:
            c.execute("INSERT INTO bom_lines(product_id,material_code,mass_kg,supplier,quality,created_at,updated_at,source) VALUES (?,?,?,?,?,?,?,?)",
                      (pid, r["material_code"], r["mass_kg"], r["supplier"], r["quality"], utcnow(), utcnow(), source))
        new_status = "draft" if st_row in ("validated", "changes_requested") else st_row
        c.execute("UPDATE products SET status=?, updated_at=? WHERE id=?", (new_status, utcnow(), pid))
        log_event(c, actor, role, "bom_updated", "product", pid, f"{len(rows)} lines · source={source}")


def replace_legs(pid, rows, actor, role):
    require(role, "edit")
    with closing(db()) as c, c:
        c.execute("DELETE FROM transport_legs WHERE product_id=?", (pid,))
        for r in rows:
            c.execute("INSERT INTO transport_legs(product_id,scope,mode,km,created_at) VALUES (?,?,?,?,?)",
                      (pid, r["scope"], r["mode"], r["km"], utcnow()))
        c.execute("UPDATE products SET updated_at=? WHERE id=?", (utcnow(), pid))
        log_event(c, actor, role, "transport_updated", "product", pid, f"{len(rows)} legs")


# ============================================================================
# THEME — Field Ledger · Daylight
# ============================================================================
CSS = """
@import url('https://fonts.googleapis.com/css2?family=Fraunces:wght@500;600;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@500;600&display=swap');
:root{
--paper:#F7F2E7;--paper2:#EFE7D6;--card:#FFFDF8;--overlay:#FFFDF8;
--ink:#16281F;--ink2:#31473B;--muted:#68796E;--line:#D9CFBA;--line2:#C7BBA0;
--evergreen:#123B2E;--evergreen2:#0C2A20;--leaf:#65A30D;--leafbright:#A8E63C;
--teal:#0D9488;--sky:#2563EB;--amber:#D97706;--red:#DC2626;--violet:#7C3AED;--pink:#DB2777;
}
*{scrollbar-width:thin;scrollbar-color:#2E5C49 #E8DFC9;}
*::-webkit-scrollbar{width:11px;height:11px;}
*::-webkit-scrollbar-track{background:#E8DFC9;}
*::-webkit-scrollbar-thumb{background:#2E5C49;border-radius:8px;border:2px solid #E8DFC9;}
*::-webkit-scrollbar-thumb:hover{background:var(--leaf);}
.stApp{background-color:var(--paper);
background-image:url('data:image/svg+xml;utf8,%3Csvg xmlns=%22http://www.w3.org/2000/svg%22 width=%22160%22 height=%22160%22%3E%3Cfilter id=%22n%22%3E%3CfeTurbulence type=%22fractalNoise%22 baseFrequency=%220.9%22 numOctaves=%222%22/%3E%3CfeColorMatrix type=%22saturate%22 values=%220%22/%3E%3C/filter%3E%3Crect width=%22160%22 height=%22160%22 filter=%22url(%23n)%22 opacity=%220.04%22/%3E%3C/svg%3E'),
radial-gradient(900px 520px at 92% -10%,rgba(13,148,136,.16),transparent 62%),
radial-gradient(820px 500px at -8% 14%,rgba(101,163,13,.13),transparent 58%),
radial-gradient(900px 720px at 55% 112%,rgba(217,119,6,.11),transparent 62%);
background-size:160px 160px,auto,auto,auto;background-repeat:repeat,no-repeat,no-repeat,no-repeat;
color:var(--ink);font-family:Inter,system-ui,sans-serif;}
.block-container{padding-top:1.1rem;padding-bottom:3rem;max-width:1440px;}
#MainMenu,footer,[data-testid="stToolbar"]{visibility:hidden;}
[data-testid="stHeader"]{background:transparent;}
h1,h2,h3,h4{font-family:Fraunces,Georgia,serif;letter-spacing:-.015em;color:var(--ink);}
[data-testid="stSidebar"]{background:linear-gradient(180deg,#0C2A20 0%,#123B2E 62%,#0E3327 100%);border-right:3px solid var(--leaf);}
[data-testid="stSidebar"] *{color:#F3EEDF;}
[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3{color:#F7F2E7;}
[data-testid="stSidebar"] [data-testid="stCaptionContainer"]{color:#B9CDBF;}
[data-testid="stCaptionContainer"]{color:var(--muted);}
hr{border-color:var(--line);}
.stButton>button,.stDownloadButton>button,[data-testid="stBaseButton-secondary"]{
background:var(--card);border:1.5px solid var(--ink);color:var(--ink);border-radius:9px;
font-weight:600;box-shadow:2px 2px 0 rgba(22,40,31,.13);transition:all .15s ease;}
.stButton>button:hover,.stDownloadButton>button:hover,[data-testid="stBaseButton-secondary"]:hover{
transform:translate(-1px,-1px);box-shadow:4px 4px 0 rgba(22,40,31,.17);border-color:var(--evergreen);color:var(--evergreen);}
.stButton>button[kind="primary"],[data-testid="stBaseButton-primary"],
button[kind="primaryFormSubmit"],[data-testid="stBaseButton-primaryFormSubmit"],
button[kind="secondaryFormSubmit"]{
background:var(--evergreen)!important;color:#F7F2E7!important;border-color:var(--evergreen)!important;font-weight:700;}
.stButton>button[kind="primary"]:hover,[data-testid="stBaseButton-primary"]:hover,
button[kind="primaryFormSubmit"]:hover{background:#1C5240!important;color:#F7F2E7!important;}
.stButton>button:disabled,.stDownloadButton>button:disabled{opacity:.42;transform:none;box-shadow:2px 2px 0 rgba(22,40,31,.08);}
[data-baseweb="select"]>div,[data-baseweb="input"],[data-baseweb="textarea"]{
background:var(--card)!important;border-color:var(--line2)!important;}
[data-baseweb="select"] *,[data-baseweb="input"] input,[data-baseweb="textarea"] textarea{color:var(--ink)!important;}
[data-baseweb="popover"] li{background:var(--card);color:var(--ink);}
[data-baseweb="slider"] div[role="slider"]{background-color:var(--leaf)!important;box-shadow:0 0 0 4px rgba(101,163,13,.16);}
div[style*="rgb(255, 75, 75)"],div[style*="rgb(255,75,75)"]{background-color:var(--leaf)!important;}
input[type="checkbox"],input[type="radio"]{accent-color:var(--leaf);}
label[data-baseweb="radio"] div[aria-checked="true"]{background:rgba(101,163,13,.14);}
[data-baseweb="tab"]{color:var(--muted);} [data-baseweb="tab"][aria-selected="true"]{color:var(--evergreen)!important;}
[data-baseweb="tab-highlight"]{background-color:var(--leaf)!important;}
[data-baseweb="tab-border"]{background-color:var(--line2)!important;}
[data-testid="stExpander"]{border:1.5px solid var(--line2);border-radius:12px;background:rgba(255,253,248,.75);}
[data-testid="stExpander"] summary{color:var(--ink);}
[data-testid="stAlert"]{border-radius:12px;}
[data-testid="stDataFrame"],[data-testid="stDataEditor"]{background:var(--card);border-radius:12px;padding:6px;border:1.5px solid var(--line2);box-shadow:3px 3px 0 rgba(22,40,31,.06);}
[data-testid="stSidebar"] [data-baseweb="select"]>div,
[data-testid="stSidebar"] [data-baseweb="input"],
[data-testid="stSidebar"] [data-baseweb="textarea"]{background:#0F2F24!important;border-color:#2E5C49!important;}
[data-testid="stSidebar"] [data-baseweb="select"] *,
[data-testid="stSidebar"] [data-baseweb="input"] input,
[data-testid="stSidebar"] [data-baseweb="textarea"] textarea{color:#F3EEDF!important;}
[data-testid="stSidebar"] .stButton>button{background:rgba(168,230,60,.10);border:1px solid #3E6B54;color:#F3EEDF;box-shadow:none;}
[data-testid="stSidebar"] .stButton>button:hover{border-color:var(--leafbright);color:var(--leafbright);box-shadow:0 0 0 3px rgba(168,230,60,.12);}
[data-testid="stSidebar"] [data-baseweb="slider"] div[role="slider"]{background-color:var(--leafbright)!important;}
[data-testid="stSidebar"] div[style*="rgb(255, 75, 75)"],[data-testid="stSidebar"] div[style*="rgb(255,75,75)"]{background-color:var(--leafbright)!important;}
.tt-brand{display:flex;gap:10px;align-items:center;margin-bottom:6px;}
.tt-logo{width:38px;height:38px;border-radius:11px;background:linear-gradient(135deg,#A8E63C,#0D9488);
display:flex;align-items:center;justify-content:center;font-size:1.2rem;color:#0C2A20;box-shadow:0 0 0 3px rgba(168,230,60,.15);}
.tt-brand b{font-family:Fraunces,Georgia,serif;font-size:1.22rem;color:#F7F2E7;}
.tt-brand small{display:block;color:#A8C4B0;font-size:.64rem;letter-spacing:.16em;text-transform:uppercase;}
.tt-side-h{font:700 .66rem 'JetBrains Mono',monospace;letter-spacing:.2em;color:var(--leafbright);text-transform:uppercase;margin:14px 0 2px;}
.tt-strip{display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between;
padding:10px 15px;border:1px solid #0A211A;border-radius:14px;color:#F3EEDF;
background:linear-gradient(90deg,var(--evergreen2),var(--evergreen));box-shadow:4px 4px 0 rgba(22,40,31,.16);margin-bottom:18px;}
.tt-strip .chip{border-color:rgba(243,238,223,.28);color:#CFE0D2;background:rgba(255,255,255,.04);}
.tt-strip .chip.hot{color:#A8E63C;border-color:rgba(168,230,60,.55);}
.chip{font-size:.68rem;padding:3px 10px;border-radius:999px;border:1px solid var(--line2);color:var(--muted);background:var(--card);letter-spacing:.04em;}
.chip.hot{color:var(--leaf);border-color:rgba(101,163,13,.5);}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#A8E63C;margin-right:6px;animation:pulse 2s infinite;}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(168,230,60,.55)}70%{box-shadow:0 0 0 9px rgba(168,230,60,0)}100%{box-shadow:0 0 0 0 rgba(168,230,60,0)}}
.tt-kicker{color:var(--teal);font:600 .7rem 'JetBrains Mono',monospace;letter-spacing:.24em;text-transform:uppercase;}
.tt-hero{border-bottom:2px solid var(--line);padding-bottom:13px;animation:rise .4s ease both;}
.tt-hero h1{font-size:2.25rem;margin:3px 0 7px;font-weight:600;}
.tt-hero p{color:var(--muted);margin:0 0 10px;max-width:840px;font-size:.95rem;}
@keyframes rise{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
.tt-kpi-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:16px 0 8px;}
@media(max-width:1100px){.tt-kpi-grid{grid-template-columns:repeat(2,1fr);}}
@media(max-width:640px){.tt-kpi-grid{grid-template-columns:1fr;}}
.tt-kpi{position:relative;border:1.5px solid var(--line2);border-radius:14px;padding:16px 18px 14px;
background:var(--card);overflow:hidden;box-shadow:4px 4px 0 rgba(22,40,31,.08);animation:rise .45s ease both;}
.tt-kpi:nth-child(2){animation-delay:.05s}.tt-kpi:nth-child(3){animation-delay:.1s}.tt-kpi:nth-child(4){animation-delay:.15s}
.tt-kpi::before{content:"";position:absolute;top:0;left:0;right:0;height:4px;background:var(--leaf);}
.tt-kpi.a-teal::before{background:var(--teal);}.tt-kpi.a-amber::before{background:var(--amber);}.tt-kpi.a-sky::before{background:var(--sky);}
.tt-kpi .l{font-size:.66rem;text-transform:uppercase;letter-spacing:.14em;color:var(--muted);font-family:'JetBrains Mono',monospace;}
.tt-kpi .v{font:600 1.9rem Fraunces,Georgia,serif;color:var(--ink);margin:5px 0 3px;}
.tt-kpi .s{font-size:.78rem;color:var(--ink2);}
.b{padding:3px 10px;border-radius:999px;font-size:.66rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;border:1px solid;white-space:nowrap;}
.b-draft{color:#5C6F63;border-color:#B9C4B4;background:#EEF1EA;}
.b-validated{color:#1D4ED8;border-color:#BBD0F7;background:#EAF1FE;}
.b-in_review{color:#B45309;border-color:#F3D19B;background:#FDF3E2;}
.b-approved{color:#3F7A0E;border-color:#C4E39B;background:#F0F8E4;}
.b-changes_requested{color:#B91C1C;border-color:#F5B5B5;background:#FDECEC;}
.b-archived{color:#6D28D9;border-color:#D6C2F7;background:#F3EDFE;}
.b-ok{color:#3F7A0E;border-color:#C4E39B;background:#F0F8E4;}
.b-error{color:#B91C1C;border-color:#F5B5B5;background:#FDECEC;}
.b-warning{color:#B45309;border-color:#F3D19B;background:#FDF3E2;}
.b-info{color:#1D4ED8;border-color:#BBD0F7;background:#EAF1FE;}
.tt-tablewrap{overflow:auto;border:1.5px solid var(--line2);border-radius:12px;margin:6px 0 12px;background:var(--card);box-shadow:3px 3px 0 rgba(22,40,31,.05);}
table.tt{width:100%;border-collapse:collapse;font-size:.83rem;}
table.tt th{position:sticky;top:0;background:var(--evergreen);color:#F3EEDF;text-transform:uppercase;
font-size:.62rem;letter-spacing:.1em;padding:9px 12px;text-align:left;border-bottom:1px solid #0A211A;z-index:2;font-family:'JetBrains Mono',monospace;}
table.tt td{padding:7px 12px;border-bottom:1px solid #E7DECB;color:var(--ink2);}
table.tt tr:hover td{background:#F4F8EC;}
table.tt td.num{text-align:right;font-variant-numeric:tabular-nums;}
.tt-feed{list-style:none;padding:0;margin:0;}
.tt-feed li{padding:9px 12px;border-left:3px solid var(--leaf);margin-bottom:7px;font-size:.82rem;color:var(--ink2);background:var(--card);border-radius:0 9px 9px 0;box-shadow:2px 2px 0 rgba(22,40,31,.05);}
.tt-feed li b{color:var(--evergreen);} .tt-feed small{color:var(--muted);display:block;margin-top:2px;}
.tt-note{border:1.5px dashed var(--line2);border-radius:12px;padding:11px 15px;color:var(--ink2);font-size:.83rem;margin:8px 0;background:#FBF7EC;}
.delta-up{color:var(--red);font-weight:700;} .delta-down{color:var(--leaf);font-weight:700;}
"""


def width_kwargs(fn):
    try:
        if "width" in inspect.signature(fn).parameters:
            return {"width": "stretch"}
    except (TypeError, ValueError):
        pass
    return {"use_container_width": True}


def show_fig(fig, h=340, title=None, legend=True):
    fig.update_layout(
        height=h, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(255,253,248,.55)",
        font=dict(family="Inter, sans-serif", color="#31473B", size=12),
        margin=dict(l=10, r=10, t=46 if title else 16, b=10),
        title=dict(text=title, font=dict(family="Fraunces, Georgia, serif", size=16, color="#16281F")) if title else None,
        showlegend=legend, legend=dict(orientation="h", y=-0.16, font=dict(size=11, color="#31473B")),
        hoverlabel=dict(bgcolor="#FFFDF8", font_color="#16281F", bordercolor="#123B2E"))
    fig.update_xaxes(gridcolor="rgba(22,40,31,.10)", zerolinecolor="rgba(22,40,31,.25)", tickfont=dict(size=11, color="#5C6F63"))
    fig.update_yaxes(gridcolor="rgba(22,40,31,.10)", zerolinecolor="rgba(22,40,31,.25)", tickfont=dict(size=11, color="#5C6F63"))
    st.plotly_chart(fig, config={"displayModeBar": False}, **width_kwargs(st.plotly_chart))


def badge(status):
    return f'<span class="b b-{status}">{STATUS_LABEL.get(status, status)}</span>'


def html_table(df, badge_col=None, num_cols=(), max_h=420):
    thead = "".join(f"<th>{htmlmod.escape(str(c))}</th>" for c in df.columns)
    trs = []
    for _, r in df.iterrows():
        tds = []
        for c in df.columns:
            v = r[c]
            if badge_col and c == badge_col:
                cell = badge(str(v))
            else:
                txt = f"{v:,.2f}" if isinstance(v, float) else str(v)
                cell = htmlmod.escape(txt)
            cls = ' class="num"' if c in num_cols else ""
            tds.append(f"<td{cls}>{cell}</td>")
        trs.append("<tr>" + "".join(tds) + "</tr>")
    return (f'<div class="tt-tablewrap" style="max-height:{max_h}px"><table class="tt">'
            f'<thead><tr>{thead}</tr></thead><tbody>{"".join(trs)}</tbody></table></div>')


def kpi(label, value, sub, accent="lime"):
    return (f'<div class="tt-kpi a-{accent}"><div class="l">{label}</div>'
            f'<div class="v">{value}</div><div class="s">{sub}</div></div>')


def sec(kicker, title, sub=None):
    st.markdown(f'<div class="tt-kicker">{kicker}</div>', unsafe_allow_html=True)
    st.markdown(f"## {title}")
    if sub:
        st.caption(sub)


def flash(kind, msg):
    st.session_state["flash"] = (kind, msg)


def can(action):
    return action in PERMS.get(st.session_state.get("role", "Stakeholder"), set())


def grade(score):
    return "A" if score >= .85 else "B" if score >= .70 else "C" if score >= .60 else "D"


def strip(ctx):
    t = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    st.markdown(
        f'<div class="tt-strip"><div><span class="dot"></span>'
        f'<span class="chip hot">ENGINE v{ENGINE_VERSION}</span> '
        f'<span class="chip">FACTORS {ctx["version"]}</span> '
        f'<span class="chip">RUN {ctx["run_id"]}</span> '
        f'<span class="chip">THEME {THEME_NAME}</span></div>'
        f'<div><span class="chip">SYNTHETIC DEMO DATA</span> '
        f'<span class="chip">{t}</span></div></div>', unsafe_allow_html=True)


# ============================================================================
# SESSION / SIDEBAR
# ============================================================================
def ensure_defaults(products):
    ss = st.session_state
    ss.setdefault("role", "Analyst")
    ss.setdefault("actor_name", "Alex Rivera")
    ss.setdefault("nav", "Overview")
    ss.setdefault("bom_rev", 0)
    ss.setdefault("leg_rev", 0)
    ids = [p["id"] for p in products]
    pending = ss.pop("pending_product", None)
    if pending in ids:
        ss["selected_product"] = pending
    if ss.get("selected_product") not in ids:
        ss["selected_product"] = ids[0]
    for k, v in DEFAULT_PARAMS.items():
        ss.setdefault("p_" + k, v)


def reset_params():
    for k, v in DEFAULT_PARAMS.items():
        st.session_state["p_" + k] = v


def randomize_params():
    for k, (lo, hi, step) in PARAM_BOUNDS.items():
        if k == "seed":
            st.session_state["p_seed"] = random.randint(0, 999999)
        elif float(step).is_integer():
            st.session_state["p_" + k] = random.randrange(int(lo), int(hi) + 1, int(step))
        else:
            v = random.uniform(lo, hi)
            st.session_state["p_" + k] = round(round(v / step) * step, 2)


def read_params():
    return {k: st.session_state["p_" + k] for k in DEFAULT_PARAMS}


def build_sidebar(products, version):
    ss = st.session_state
    sb = st.sidebar
    sb.markdown('<div class="tt-brand"><div class="tt-logo">🌿</div><div><b>TerraTrace</b>'
                '<small>Lifecycle Impact Studio</small></div></div>', unsafe_allow_html=True)
    sb.markdown('<div class="tt-side-h">Workspace</div>', unsafe_allow_html=True)
    sb.radio("Navigation", ["Overview", "Products & BOM", "Import data", "Analysis",
                            "Review & approvals", "Reports", "Governance"],
             key="nav", label_visibility="collapsed")
    c1, c2 = sb.columns(2)
    c1.selectbox("Role (demo auth)", ROLES, key="role")
    c2.text_input("Your name", key="actor_name")
    names = {p["id"]: p["name"] for p in products}
    sb.selectbox("Active product", list(names), key="selected_product",
                 format_func=lambda i: names[i])
    sb.markdown('<div class="tt-side-h">Scenario lab — applied live</div>', unsafe_allow_html=True)
    sb.slider("Production volume ×", 0.2, 3.0, step=0.05, format="%.2f×", key="p_volume_mult",
              help="Scales annual program volume for every product.")
    sb.slider("Grid intensity (kg CO₂e/kWh)", 0.02, 0.90, step=0.01, format="%.2f", key="p_grid",
              help="Electricity carbon intensity used in manufacturing and use phase.")
    sb.slider("Use-phase energy ×", 0.2, 2.0, step=0.05, format="%.2f×", key="p_use_mult")
    sb.slider("Service lifetime ×", 0.5, 2.0, step=0.05, format="%.2f×", key="p_life_mult")
    sb.slider("Transport distance ×", 0.2, 3.0, step=0.05, format="%.2f×", key="p_dist_mult")
    sb.slider("Air freight share (outbound)", 0, 30, step=1, format="%d%%", key="p_air_share")
    sb.slider("Recycled content (metals/plastics)", 0, 100, step=5, format="%d%%", key="p_recycled_content")
    sb.slider("EoL recycling rate", 0, 95, step=5, format="%d%%", key="p_eol_recycle")
    sb.slider("Incineration of remainder", 0, 100, step=5, format="%d%%", key="p_incin_share")
    sb.slider("Manufacturing scrap rate", 0, 20, step=1, format="%d%%", key="p_scrap")
    sb.slider("Monte Carlo runs", 200, 5000, step=100, key="p_mc_runs")
    sb.number_input("Random seed (reproducibility)", 0, 999999, step=1, key="p_seed")
    b1, b2 = sb.columns(2)
    b1.button("↺ Reset", on_click=reset_params, **width_kwargs(st.button))
    b2.button("🎲 Randomize", on_click=randomize_params, **width_kwargs(st.button))
    sb.markdown('<div class="tt-side-h">Save scenario</div>', unsafe_allow_html=True)
    sb.text_input("Scenario name", key="scen_name", placeholder="e.g. Circular 2028",
                  label_visibility="collapsed")
    if sb.button("💾 Save current scenario", disabled=not can("scenario"),
                 help="Analyst or Administrator role required", **width_kwargs(st.button)):
        name = ss.get("scen_name", "").strip()
        if not name:
            sb.warning("Give the scenario a name first.")
        else:
            actor = f'{ss["actor_name"]} ({ss["role"]})'
            with closing(db()) as c, c:
                c.execute("INSERT INTO scenarios(name,params_json,created_at,actor,note) VALUES (?,?,?,?,?)",
                          (name, json.dumps(read_params()), utcnow(), actor, "Saved from scenario lab"))
                log_event(c, ss["actor_name"], ss["role"], "scenario_saved", "scenario", name, "Params snapshot")
            flash("success", f"Scenario “{name}” saved — compare it on the Analysis page.")
            st.rerun()
    sb.markdown("---")
    sb.caption(f"Engine v{ENGINE_VERSION} · Factors {version} · SQLite local\n\n"
               f"Theme: {THEME_NAME}. Demo roles are simulated; all data is synthetic.")
    return ss["selected_product"], ss["role"], ss["actor_name"]


# ============================================================================
# PAGE — OVERVIEW
# ============================================================================
def page_overview(ctx):
    strip(ctx)
    prod, res, mc = ctx["prod"], ctx["res"], ctx["mc"]
    base_total = ctx["baseline"][prod["id"]]["total"]
    delta = (res["total"] - base_total) / base_total * 100 if base_total else 0.0
    dcls = "delta-down" if delta <= 0 else "delta-up"
    darrow = "▼" if delta <= 0 else "▲"
    st.markdown(
        f'<div class="tt-hero"><div class="tt-kicker">Product lifecycle impact · cradle-to-grave · GWP100</div>'
        f'<h1>{htmlmod.escape(prod["name"])}</h1>'
        f'<p>{htmlmod.escape(prod["description"])}</p>'
        f'{badge(prod["status"])} &nbsp;<span class="chip">{htmlmod.escape(prod["category"])}</span> '
        f'<span class="chip">{htmlmod.escape(prod["functional_unit"])}</span> '
        f'<span class="chip">scenario vs baseline: <span class="{dcls}">{darrow} {abs(delta):.1f}%</span></span>'
        f'</div>', unsafe_allow_html=True)

    gross = {s: v for s, v in res["stages"].items() if s != "Credits" and v > 0}
    dom = max(gross, key=gross.get) if gross else "—"
    dom_share = gross[dom] / sum(gross.values()) * 100 if gross else 0
    st.markdown('<div class="tt-kpi-grid">' + "".join([
        kpi("Per-unit net footprint", f'{res["total"]:,.1f} <small style="font-size:.9rem">kg CO₂e</small>',
            f'P5–P95: {mc["p5"]:,.1f} – {mc["p95"]:,.1f} · {mc["n"]:,} MC runs', "lime"),
        kpi("Annual program", f'{res["annual"]/1000:,.1f} <small style="font-size:.9rem">t CO₂e</small>',
            f'{res["volume"]:,.0f} units/yr under current scenario', "teal"),
        kpi("Dominant stage", dom, f'{dom_share:.0f}% of gross impact — target here first', "amber"),
        kpi("Data quality", f'{res["quality"]*100:.0f}<small style="font-size:.9rem">/100 · {grade(res["quality"])}</small>',
            f'{res["proxy_share"]:.0%} proxy mass · gate {QUALITY_GATE*100:.0f}', "sky"),
    ]) + "</div>", unsafe_allow_html=True)

    c1, c2 = st.columns([3, 2])
    with c1:
        fig = go.Figure()
        for s in STAGES:
            fig.add_bar(x=[p["name"].split("—")[0].strip() for p in ctx["products"]],
                        y=[ctx["results"][p["id"]]["stages"][s] for p in ctx["products"]],
                        name=s, marker_color=STAGE_COLORS[s],
                        hovertemplate="%{x}<br>" + s + ": %{y:.1f} kg CO₂e<extra></extra>")
        fig.update_layout(barmode="relative")
        show_fig(fig, 360, "Portfolio · per-unit footprint by stage (kg CO₂e)")
    with c2:
        if gross:
            fig = go.Figure(go.Pie(labels=list(gross), values=list(gross.values()), hole=.62,
                                   marker=dict(colors=[STAGE_COLORS[s] for s in gross],
                                               line=dict(color="#F7F2E7", width=2)),
                                   textinfo="label+percent", textfont=dict(size=11, color="#FFFDF8"),
                                   hovertemplate="%{label}: %{value:.1f} kg CO₂e<extra></extra>"))
            fig.update_layout(annotations=[dict(text=f'<b>{res["total"]:,.0f}</b><br>kg net',
                                                showarrow=False, font=dict(size=17, color="#16281F"))])
            show_fig(fig, 360, "Selected product · gross stage mix", legend=False)
            st.caption(f'Credits from recycling: {res["stages"]["Credits"]:,.1f} kg CO₂e (already netted above).')
        else:
            st.info("No positive contributions yet — add BOM lines on Products & BOM.")

    pos = res["rows"][res["rows"]["gwp"] > 0]
    if not pos.empty:
        ids, labels, parents, values, colors = [], [], [], [], []
        for s in STAGES:
            sub = pos[pos["stage"] == s]
            if sub.empty:
                continue
            ids.append(s); labels.append(s); parents.append(""); values.append(float(sub["gwp"].sum()))
            colors.append(STAGE_COLORS[s])
            for i, r in sub.iterrows():
                ids.append(f"{s}/{i}"); labels.append(r["item"]); parents.append(s)
                values.append(float(r["gwp"])); colors.append(STAGE_COLORS[s])
        fig = go.Figure(go.Treemap(ids=ids, labels=labels, parents=parents, values=values,
                                   branchvalues="total", marker=dict(colors=colors, line=dict(width=2, color="#F7F2E7")),
                                   texttemplate="%{label}<br>%{value:.1f}", textfont=dict(size=12, color="#FFFDF8"),
                                   hovertemplate="%{label}<br>%{value:.2f} kg CO₂e<extra></extra>"))
        show_fig(fig, 400, "Hotspot map · contribution treemap (positive burdens, kg CO₂e)", legend=False)

    c3, c4 = st.columns([3, 2])
    with c3:
        if not pos.empty:
            top = pos.sort_values("gwp", ascending=False).head(10).copy()
            cum = top["gwp"].cumsum() / pos["gwp"].sum() * 100
            fig = go.Figure()
            fig.add_bar(x=top["item"], y=top["gwp"],
                        marker_color=[STAGE_COLORS[s] for s in top["stage"]], name="Contribution")
            fig.add_scatter(x=top["item"], y=cum, yaxis="y2", mode="lines+markers",
                            line=dict(color="#DB2777", width=2.5), name="Cumulative %")
            fig.update_layout(yaxis2=dict(overlaying="y", side="right", range=[0, 105],
                                          showgrid=False, ticksuffix="%", tickfont=dict(color="#DB2777")),
                              xaxis=dict(tickangle=-28, tickfont=dict(size=10)))
            show_fig(fig, 380, "Pareto · top contributors")
    with c4:
        st.markdown("#### Recent activity")
        aud = qdf("SELECT * FROM audit_events ORDER BY id DESC LIMIT 7")
        icons = {"created": "✚", "bom_updated": "✎", "import": "⇪", "approved": "✔",
                 "in_review": "➤", "override_added": "⚠", "export": "⤓", "factors_changed": "⟳"}
        items = []
        for _, r in aud.iterrows():
            ic = icons.get(r["event"], "•")
            items.append(f'<li>{ic} <b>{htmlmod.escape(r["event"])}</b> · {htmlmod.escape(r["entity_id"] or "")}'
                         f'<small>{htmlmod.escape(r["actor"])} ({htmlmod.escape(r["role"])}) · {r["ts"][:16]}'
                         f' · {htmlmod.escape(r["details"] or "")}</small></li>')
        st.markdown('<ul class="tt-feed">' + "".join(items) + "</ul>", unsafe_allow_html=True)
        with st.expander("Data quality flags"):
            for lvl, msg in res["flags"]:
                st.markdown(f'<span class="b b-{lvl}">{lvl}</span> {htmlmod.escape(msg)}', unsafe_allow_html=True)
    st.caption(f'Run {ctx["run_id"]} · engine v{ENGINE_VERSION} · factors {ctx["version"]} · '
               "Monte Carlo is seeded and reproducible. Synthetic demo data — decision support only.")


# ============================================================================
# PAGE — PRODUCTS & BOM
# ============================================================================
def page_products(ctx):
    strip(ctx)
    sec("Registry", "Products & bill of materials",
        "Create, edit and validate product records. Edits are audited; imported source files are preserved.")
    prods = ctx["products"]

    f1, f2, f3 = st.columns([2, 2, 1])
    search = f1.text_input("Search registry", key="reg_search", placeholder="Name or category…").strip().lower()
    stat = f2.multiselect("Filter status", list(STATUS_LABEL), format_func=lambda s: STATUS_LABEL[s], key="reg_status")
    sort_by = f3.selectbox("Sort by", ["Updated", "Name", "Footprint ↑", "Footprint ↓"], key="reg_sort")
    view = [p for p in prods if (not search or search in p["name"].lower() or search in p["category"].lower())
            and (not stat or p["status"] in stat)]
    if sort_by == "Name":
        view = sorted(view, key=lambda p: p["name"].lower())
    elif sort_by == "Footprint ↑":
        view = sorted(view, key=lambda p: ctx["results"][p["id"]]["total"])
    elif sort_by == "Footprint ↓":
        view = sorted(view, key=lambda p: ctx["results"][p["id"]]["total"], reverse=True)
    else:
        view = sorted(view, key=lambda p: p["updated_at"], reverse=True)
    reg = pd.DataFrame([{
        "Product": p["name"], "Category": p["category"], "Status": p["status"],
        "Per-unit kg CO₂e": round(ctx["results"][p["id"]]["total"], 1),
        "Annual t CO₂e": round(ctx["results"][p["id"]]["annual"] / 1000, 1),
        "Quality": f'{ctx["results"][p["id"]]["quality"]*100:.0f}',
        "Updated": p["updated_at"][:16],
    } for p in view], columns=["Product", "Category", "Status", "Per-unit kg CO₂e", "Annual t CO₂e", "Quality", "Updated"])
    st.caption(f"{len(view)} of {len(prods)} products shown · search, filter and sort are live (table only).")
    st.markdown(html_table(reg, badge_col="Status", num_cols=("Per-unit kg CO₂e", "Annual t CO₂e")), unsafe_allow_html=True)
    if not view:
        st.info("No products match the current filters.")

    with st.expander("➕ New product (Analyst / Administrator)"):
        with st.form("new_product"):
            n1, n2 = st.columns(2)
            name = n1.text_input("Name*", placeholder="SolarSense P1 — PV Microinverter")
            cat = n2.text_input("Category", value="Municipal IoT")
            desc = st.text_area("Description", value="Synthetic demo record — describe the product and its context.")
            fu = st.text_input("Functional unit", value="1 unit · 10-year service life")
            f1, f2, f3, f4, f5 = st.columns(5)
            life = f1.number_input("Lifetime (yr)", 0.5, 50.0, 10.0)
            use = f2.number_input("Use energy (kWh/yr)", 0.0, 100000.0, 5.0)
            asm = f3.number_input("Assembly (kWh/unit)", 0.0, 10000.0, 2.0)
            gas = f4.number_input("Process heat (kWh/unit)", 0.0, 10000.0, 0.5)
            vol = f5.number_input("Annual volume", 1, 10_000_000, 1000)
            ok = st.form_submit_button("Create draft", disabled=not can("edit"), type="primary")
        if ok:
            if not name.strip():
                st.error("Name is required.")
            else:
                pid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] + "-" + uuid.uuid4().hex[:4]
                with closing(db()) as c, c:
                    c.execute("INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (pid, name.strip(), cat, desc, fu, "draft", life, use, asm, gas, vol,
                               utcnow(), utcnow(), ctx["actor"], "manual://entry", SCHEMA_VERSION))
                    for scope, mode, km in [("inbound", "sea", 8000), ("inbound", "truck", 400), ("outbound", "truck", 600)]:
                        c.execute("INSERT INTO transport_legs(product_id,scope,mode,km,created_at) VALUES (?,?,?,?,?)",
                                  (pid, scope, mode, km, utcnow()))
                    log_event(c, ctx["actor"], ctx["role"], "created", "product", pid, "Manual entry")
                st.session_state["pending_product"] = pid
                flash("success", f"Draft “{name}” created — add BOM lines below.")
                st.rerun()

    prod = ctx["prod"]
    locked = prod["status"] not in EDITABLE or not can("edit")
    st.markdown(f'### {htmlmod.escape(prod["name"])} &nbsp; {badge(prod["status"])}', unsafe_allow_html=True)
    if locked:
        lock_reason = " / read-only role" if not can("edit") else ""
        st.markdown(
            '<div class="tt-note">🔒 Record is locked ('
            + STATUS_LABEL[prod["status"]] + lock_reason
            + '). Reopen it on Review & approvals to edit.</div>',
            unsafe_allow_html=True)

    with st.expander("Product metadata & energy assumptions", expanded=False):
        with st.form("meta_form"):
            m1, m2 = st.columns(2)
            name = m1.text_input("Name", value=prod["name"], disabled=locked)
            cat = m2.text_input("Category", value=prod["category"], disabled=locked)
            desc = st.text_area("Description", value=prod["description"], disabled=locked)
            fu = st.text_input("Functional unit", value=prod["functional_unit"], disabled=locked)
            f1, f2, f3, f4, f5 = st.columns(5)
            life = f1.number_input("Lifetime (yr)", 0.5, 50.0, float(prod["lifetime_yr"]), disabled=locked)
            use = f2.number_input("Use energy (kWh/yr)", 0.0, 100000.0, float(prod["use_kwh_yr"]), disabled=locked)
            asm = f3.number_input("Assembly (kWh/unit)", 0.0, 10000.0, float(prod["assembly_kwh"]), disabled=locked)
            gas = f4.number_input("Process heat (kWh/unit)", 0.0, 10000.0, float(prod["gas_kwh"]), disabled=locked)
            vol = f5.number_input("Annual volume", 1, 10_000_000, int(prod["base_volume"]), disabled=locked)
            saved = st.form_submit_button("Save metadata", disabled=locked, type="primary")
        if saved:
            with closing(db()) as c, c:
                new_status = "draft" if prod["status"] in ("validated", "changes_requested") else prod["status"]
                c.execute("UPDATE products SET name=?,category=?,description=?,functional_unit=?,lifetime_yr=?,"
                          "use_kwh_yr=?,assembly_kwh=?,gas_kwh=?,base_volume=?,status=?,updated_at=? WHERE id=?",
                          (name, cat, desc, fu, life, use, asm, gas, vol, new_status, utcnow(), prod["id"]))
                log_event(c, ctx["actor"], ctx["role"], "metadata_updated", "product", prod["id"], "Metadata edited")
            flash("success", "Metadata saved and audited.")
            st.rerun()

    st.markdown("#### Bill of materials")
    mat_codes = list(ctx["factors"]["materials"])
    bom_df = pd.DataFrame(ctx["lines"])[["material_code", "mass_kg", "supplier", "quality"]] \
        if ctx["lines"] else pd.DataFrame(columns=["material_code", "mass_kg", "supplier", "quality"])
    edited = st.data_editor(
        bom_df, num_rows="dynamic", disabled=locked, hide_index=True,
        key=f'bom_{prod["id"]}_{st.session_state["bom_rev"]}',
        column_config={
            "material_code": st.column_config.SelectboxColumn("Material", options=mat_codes, required=True),
            "mass_kg": st.column_config.NumberColumn("Mass (kg)", min_value=0.0, max_value=5000.0, step=0.01, format="%.3f"),
            "supplier": st.column_config.TextColumn("Supplier"),
            "quality": st.column_config.SelectboxColumn("Data quality", options=list(QW), required=True),
        }, **width_kwargs(st.data_editor))
    clean = edited.dropna(subset=["material_code", "mass_kg"])
    clean = clean[clean["material_code"].astype(str).str.len() > 0]
    if not clean.empty:
        try:
            preview = assess(prod, clean.to_dict("records"), ctx["legs"], ctx["params"], ctx["factors"], ctx["overrides"])
            st.caption(f'Live preview from editor: **{preview["mass"]:.2f} kg** BOM mass · '
                       f'**{preview["total"]:,.1f} kg CO₂e** per unit (unsaved).')
        except ValueError as e:
            st.warning(f"Preview unavailable: {e}")
    if st.button("💾 Save BOM", disabled=locked, type="primary", key="save_bom"):
        rows, errs = [], []
        seen = set()
        for _, r in clean.iterrows():
            code = str(r["material_code"]).strip().lower()
            q = str(r.get("quality") or "proxy")
            try:
                mass = float(r["mass_kg"])
            except (TypeError, ValueError):
                errs.append(f"{code}: mass is not numeric"); continue
            if code not in mat_codes:
                errs.append(f"{code}: unknown material")
            if code in seen:
                errs.append(f"{code}: duplicate line")
            if not (0 < mass <= 5000):
                errs.append(f"{code}: mass out of range (0–5000]")
            if q not in QW:
                errs.append(f"{code}: quality must be measured/supplier/proxy")
            seen.add(code)
            rows.append({"material_code": code, "mass_kg": mass, "supplier": str(r.get("supplier") or ""), "quality": q})
        if errs:
            st.error("BOM not saved:\n- " + "\n- ".join(errs))
        elif not rows:
            st.error("BOM cannot be empty.")
        else:
            try:
                replace_bom(prod["id"], rows, ctx["actor"], ctx["role"], "manual://editor")
                st.session_state["bom_rev"] += 1
                flash("success", f"BOM saved — {len(rows)} lines, audited.")
                st.rerun()
            except (PermissionError, ValueError) as e:
                st.error(str(e))

    st.markdown("#### Transport legs")
    leg_df = pd.DataFrame(ctx["legs"])[["scope", "mode", "km"]] \
        if ctx["legs"] else pd.DataFrame(columns=["scope", "mode", "km"])
    ledit = st.data_editor(
        leg_df, num_rows="dynamic", disabled=locked, hide_index=True,
        key=f'legs_{prod["id"]}_{st.session_state["leg_rev"]}',
        column_config={
            "scope": st.column_config.SelectboxColumn("Scope", options=["inbound", "outbound"], required=True),
            "mode": st.column_config.SelectboxColumn("Mode", options=["truck", "rail", "sea", "air"], required=True),
            "km": st.column_config.NumberColumn("Distance (km)", min_value=0.0, max_value=40000.0, step=50.0, format="%.0f"),
        }, **width_kwargs(st.data_editor))
    if st.button("💾 Save transport", disabled=locked, key="save_legs"):
        lrows = ledit.dropna(subset=["scope", "mode", "km"]).to_dict("records")
        try:
            replace_legs(prod["id"], lrows, ctx["actor"], ctx["role"])
            st.session_state["leg_rev"] += 1
            flash("success", f"Transport saved — {len(lrows)} legs.")
            st.rerun()
        except (PermissionError, ValueError) as e:
            st.error(str(e))

    with st.expander("⚠ Danger zone — delete product (Administrator)"):
        confirm = st.checkbox("I understand this deletes the BOM, legs and overrides (audit history is kept).")
        last = len(prods) == 1
        if st.button("🗑 Delete product", disabled=not can("delete") or not confirm or last,
                     help="Requires Administrator + confirmation; the last demo product cannot be deleted."):
            with closing(db()) as c, c:
                c.execute("DELETE FROM overrides WHERE product_id=?", (prod["id"],))
                c.execute("DELETE FROM products WHERE id=?", (prod["id"],))
                log_event(c, ctx["actor"], ctx["role"], "deleted", "product", prod["id"], prod["name"])
            flash("warning", f'Product “{prod["name"]}” deleted. Audit history retained.')
            st.rerun()


# ============================================================================
# PAGE — IMPORT
# ============================================================================
def page_import(ctx):
    strip(ctx)
    sec("Ingestion", "Import BOM data — CSV / Excel",
        "Preview before commit · row-level validation · downloadable error report · original files preserved with checksums.")
    prod = ctx["prod"]
    c1, c2 = st.columns([2, 1])
    with c1:
        up = st.file_uploader("Upload CSV or XLSX", type=["csv", "xlsx"], key="imp_file")
    with c2:
        st.download_button("⬇ Download CSV template", TEMPLATE_CSV, "terratrace_bom_template.csv",
                           "text/csv", **width_kwargs(st.download_button))
        demo = st.toggle("Use malformed demo file (shows rejection)", key="imp_demo")
    mode = st.radio("Import mode", ["Append", "Replace"], horizontal=True, key="imp_mode",
                    help="Append rejects materials already in the BOM. Replace overwrites the BOM (audited).")
    confirm_replace = True
    if mode == "Replace":
        confirm_replace = st.checkbox("Confirm: replace the existing BOM (original import files stay preserved).", key="imp_confirm")

    raw, fname = None, None
    if demo:
        raw, fname = BAD_CSV.encode("utf-8"), "malformed_demo.csv"
        st.info("Using the built-in malformed sample — 8 rows, several deliberate defects.")
    elif up is not None:
        raw, fname = up.getvalue(), up.name
    if raw is None:
        st.markdown('<div class="tt-note">Upload a file or flip the malformed-demo toggle. Required columns: '
                    '<b>material_code, mass_kg, supplier, quality</b>. Allowed materials are listed in Governance → Factor catalog.</div>',
                    unsafe_allow_html=True)
    else:
        if len(raw) > MAX_UPLOAD_BYTES:
            st.error("File exceeds the 5 MB upload limit.")
            st.stop()
        try:
            df = pd.read_csv(io.BytesIO(raw)) if fname.lower().endswith(".csv") else pd.read_excel(io.BytesIO(raw))
        except Exception as e:
            st.error(f"Could not parse file: {e}")
            st.stop()
        if df.empty:
            st.error("File contains no data rows.")
            st.stop()
        missing = [c for c in IMPORT_COLS if c not in df.columns]
        if missing:
            st.error(f"Missing required columns: {missing}. Download the template for the expected schema.")
            st.stop()
        extra = [c for c in df.columns if c not in IMPORT_COLS]
        if extra:
            st.warning(f"Ignoring extra columns: {extra}")
        df = df[IMPORT_COLS]
        existing = {str(l["material_code"]).lower() for l in ctx["lines"]}
        out = validate_import(df, existing, mode, ctx["factors"])
        n_ok = int((out["_status"] == "valid").sum())
        n_err = int((out["_status"] == "error").sum())
        k1, k2, k3 = st.columns(3)
        k1.markdown(kpi("Rows", str(len(out)), "parsed from source", "sky"), unsafe_allow_html=True)
        k2.markdown(kpi("Valid", str(n_ok), "ready to commit", "lime"), unsafe_allow_html=True)
        k3.markdown(kpi("Rejected", str(n_err), "actionable errors below", "amber"), unsafe_allow_html=True)
        prev = out.copy()
        prev["status"] = prev["_status"].map({"valid": "ok", "error": "error"})
        st.markdown(html_table(prev[IMPORT_COLS + ["status", "_errors"]].rename(columns={"_errors": "errors"}),
                               badge_col="status", max_h=320), unsafe_allow_html=True)
        if n_err:
            err_csv = out[out["_status"] == "error"].to_csv(index=False)
            st.download_button("⬇ Download error report (CSV)", err_csv,
                               f"errors_{Path(fname).stem}.csv", "text/csv", key="imp_err")
        disabled = (n_ok == 0) or (not can("import")) or (mode == "Replace" and not confirm_replace)
        if st.button(f"✅ Commit {n_ok} valid rows → {prod['name']}", disabled=disabled, type="primary", key="imp_commit",
                     help="Analyst / Administrator role required; record must be editable."):
            if prod["status"] not in EDITABLE:
                st.error("Target record is locked — reopen it before importing.")
                st.stop()
            sha = hashlib.sha256(raw).hexdigest()
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(fname).name)
            (UPLOAD_DIR / f"{sha[:16]}_{safe}").write_bytes(raw)
            good = out[out["_status"] == "valid"]
            rows = [{"material_code": str(r["material_code"]).strip().lower(), "mass_kg": float(r["mass_kg"]),
                     "supplier": str(r.get("supplier") or ""), "quality": str(r["quality"]).strip().lower()}
                    for _, r in good.iterrows()]
            try:
                if mode == "Replace":
                    replace_bom(prod["id"], rows, ctx["actor"], ctx["role"], f"import://{safe}")
                else:
                    merged = [{"material_code": l["material_code"], "mass_kg": l["mass_kg"],
                               "supplier": l["supplier"], "quality": l["quality"]} for l in ctx["lines"]] + rows
                    replace_bom(prod["id"], merged, ctx["actor"], ctx["role"], f"import://{safe}")
                with closing(db()) as c, c:
                    c.execute("INSERT INTO imports(ts,product_id,filename,sha256,mode,rows_total,rows_ok,rows_err,actor) VALUES (?,?,?,?,?,?,?,?,?)",
                              (utcnow(), prod["id"], safe, sha, mode, len(out), n_ok, n_err, ctx["actor"]))
                    log_event(c, ctx["actor"], ctx["role"], "import", "product", prod["id"],
                              f"{safe} · {n_ok}/{len(out)} rows · sha256={sha[:12]}…")
                st.session_state["bom_rev"] += 1
                flash("success", f"Imported {n_ok} rows ({n_err} rejected). Source preserved as {sha[:16]}_{safe}.")
                st.rerun()
            except (PermissionError, ValueError) as e:
                st.error(str(e))
    st.markdown("#### Import history (lineage)")
    hist = qdf("SELECT ts, product_id, filename, mode, rows_total, rows_ok, rows_err, actor, sha256 FROM imports ORDER BY id DESC LIMIT 20")
    if hist.empty:
        st.caption("No imports yet — history will appear here with checksums for full lineage.")
    else:
        hist["sha256"] = hist["sha256"].str[:16] + "…"
        st.markdown(html_table(hist, num_cols=("rows_total", "rows_ok", "rows_err")), unsafe_allow_html=True)


# ============================================================================
# PAGE — ANALYSIS
# ============================================================================
def page_analysis(ctx):
    strip(ctx)
    prod, res, mc = ctx["prod"], ctx["res"], ctx["mc"]
    sec("Analytics", f'Impact analysis — {prod["name"]}',
        "Deterministic accounting first; uncertainty and sensitivity are layered on top, never hidden.")

    st.markdown("### Uncertainty · Monte Carlo")
    u1, u2, u3, u4 = st.columns(4)
    u1.markdown(kpi("P5", f'{mc["p5"]:,.1f}', "kg CO₂e / unit", "sky"), unsafe_allow_html=True)
    u2.markdown(kpi("P50 (median)", f'{mc["p50"]:,.1f}', "kg CO₂e / unit", "lime"), unsafe_allow_html=True)
    u3.markdown(kpi("P95", f'{mc["p95"]:,.1f}', "kg CO₂e / unit", "amber"), unsafe_allow_html=True)
    u4.markdown(kpi("Spread", f'±{mc["std"]:,.1f}', f'std dev · {mc["n"]:,} draws · seed {ctx["params"]["seed"]}', "teal"), unsafe_allow_html=True)
    fig = go.Figure(go.Histogram(x=mc["samples"], nbinsx=45, marker_color="#65A30D",
                                 marker_line=dict(width=0), opacity=.88,
                                 hovertemplate="%{x:.1f} kg CO₂e<br>%{y} runs<extra></extra>"))
    for val, lab, col, dash in [(mc["p5"], "P5", "#2563EB", "dash"), (mc["p50"], "P50", "#16281F", "solid"),
                                (mc["p95"], "P95", "#D97706", "dash")]:
        fig.add_vline(x=val, line_width=1.8, line_dash=dash, line_color=col,
                      annotation_text=lab, annotation_font_color=col)
    show_fig(fig, 330, "Distribution of per-unit footprint (kg CO₂e)", legend=False)
    st.caption("Lognormal factor sampling; catalog uncertainty treated as a 95% half-width. "
               "Same seed ⇒ identical result. Correlations between factors are not modeled (MVP limitation).")

    st.markdown("### Sensitivity · one-at-a-time tornado")
    tdf, base = tornado(prod, ctx["lines"], ctx["legs"], ctx["params"], ctx["factors"], ctx["overrides"])
    tdf = tdf.sort_values("span")
    fig = go.Figure()
    fig.add_bar(y=tdf["driver"], x=tdf["low"], orientation="h", name="−20% / −15pp", marker_color="#2563EB")
    fig.add_bar(y=tdf["driver"], x=tdf["high"], orientation="h", name="+20% / +15pp", marker_color="#D97706")
    fig.update_layout(barmode="relative")
    show_fig(fig, 360, "Change in per-unit footprint vs current scenario (kg CO₂e)")
    top_driver = tdf.iloc[-1]["driver"]
    st.caption(f"Largest lever right now: **{top_driver}**. Deterministic base = {base:,.1f} kg CO₂e.")

    st.markdown("### Scenario comparison")
    scens = qdf("SELECT * FROM scenarios ORDER BY id")
    names, totals, colors = ["Baseline", "Current"], [ctx["baseline"][prod["id"]]["total"], res["total"]], ["#8A9A8E", "#65A30D"]
    rows = [{"Scenario": "Baseline", "Per-unit kg CO₂e": round(totals[0], 1), "Δ vs baseline": "—", "Actor": "engine defaults"},
            {"Scenario": "Current", "Per-unit kg CO₂e": round(totals[1], 1),
             "Δ vs baseline": f'{(totals[1]-totals[0])/totals[0]*100:+.1f}%' if totals[0] else "—", "Actor": "scenario lab"}]
    palette = ["#0D9488", "#2563EB", "#D97706", "#7C3AED", "#DB2777"]
    for i, (_, s) in enumerate(scens.iterrows()):
        p = json.loads(s["params_json"])
        t = assess(prod, ctx["lines"], ctx["legs"], p, ctx["factors"], ctx["overrides"])["total"]
        names.append(s["name"]); totals.append(t); colors.append(palette[i % len(palette)])
        rows.append({"Scenario": s["name"], "Per-unit kg CO₂e": round(t, 1),
                     "Δ vs baseline": f'{(t-totals[0])/totals[0]*100:+.1f}%' if totals[0] else "—",
                     "Actor": s["actor"]})
    fig = go.Figure(go.Bar(x=names, y=totals, marker_color=colors,
                           text=[f"{t:,.0f}" for t in totals], textposition="outside",
                           hovertemplate="%{x}: %{y:.1f} kg CO₂e<extra></extra>"))
    show_fig(fig, 330, "Saved scenarios · per-unit footprint (kg CO₂e)", legend=False)
    st.markdown(html_table(pd.DataFrame(rows), num_cols=("Per-unit kg CO₂e",)), unsafe_allow_html=True)
    if not scens.empty:
        d1, d2 = st.columns([3, 1])
        target = d1.selectbox("Delete a saved scenario", scens["id"].tolist(),
                              format_func=lambda i: scens.loc[scens["id"] == i, "name"].iloc[0], key="scen_del")
        if d2.button("🗑 Delete", disabled=not can("scenario"), key="scen_del_btn"):
            with closing(db()) as c, c:
                nm = scens.loc[scens["id"] == target, "name"].iloc[0]
                c.execute("DELETE FROM scenarios WHERE id=?", (int(target),))
                log_event(c, ctx["actor"], ctx["role"], "scenario_deleted", "scenario", nm, "Removed")
            flash("info", "Scenario deleted.")
            st.rerun()

    st.markdown("### Full contribution ledger (traceable)")
    led = res["rows"][["stage", "item", "qty", "qty_unit", "factor", "factor_unit", "gwp", "unc_pct", "quality", "formula"]]
    st.markdown(html_table(led.rename(columns={"gwp": "kg CO₂e", "unc_pct": "±%"}),
                           num_cols=("qty", "factor", "kg CO₂e", "±%"), max_h=440), unsafe_allow_html=True)
    st.download_button("⬇ Contributions CSV", res["rows"].to_csv(index=False).encode(),
                       f"contributions_{prod['id']}.csv", "text/csv", key="an_csv")

    with st.expander("📐 Methodology, boundaries & why no ML yet"):
        st.latex(r"E_{\text{stage}}=\sum_i m_i \cdot EF_i \qquad E_{\text{use}}=P_{\text{yr}}\cdot L\cdot EF_{\text{grid}}")
        st.latex(r"\text{credit}=-\,m_{\text{recycled}}\,(EF_{\text{virgin}}-EF_{\text{recycled}})\cdot q,\quad q=0.9")
        st.markdown(
            "- **Boundary:** cradle-to-grave; avoided-burden (credit) approach for recycling; single-product allocation; no cut-off applied.\n"
            "- **Units:** GWP100, kg CO₂e per functional unit; transport in tonne-km; energy in kWh.\n"
            "- **Scrap:** extra upstream input = mass × s/(1−s).\n"
            "- **Factors:** illustrative demo set, versioned with rollback. Production requires licensed, dated factors (ecoinvent / GREET / DEFRA).\n"
            "- **Why deterministic first:** lifecycle accounting is rule-based arithmetic — auditable and hand-checkable. "
            "ML (e.g. anomaly detection on supplier declarations) is post-MVP and must be trained on real, permissioned data, "
            "split by supplier/time, and compared against this baseline. Synthetic data validates plumbing only, never predictive claims.")


# ============================================================================
# PAGE — REVIEW & APPROVALS
# ============================================================================
def page_review(ctx):
    strip(ctx)
    prod, res = ctx["prod"], ctx["res"]
    sec("Workflow", "Review & approvals",
        "Draft → validation → review → approval → archive, with a documented exception path and full audit.")
    checks, score, gate = validate_product(prod, ctx["lines"], ctx["legs"], ctx["factors"])
    cur = prod["status"]
    hl = {"draft": "#8A9A8E", "validated": "#2563EB", "in_review": "#D97706",
          "approved": "#65A30D", "changes_requested": "#DC2626", "archived": "#7C3AED"}
    nodes = []
    for s in ["draft", "validated", "in_review", "approved", "archived"]:
        fill = hl[s] if s == cur else "#FFFDF8"
        font = "#FFFDF8" if s == cur else "#16281F"
        nodes.append(f'"{s}" [label="{STATUS_LABEL[s]}", style="filled", fillcolor="{fill}", fontcolor="{font}"]')
    dot = ('digraph G { rankdir=LR; bgcolor="transparent"; node [shape=box, style="rounded,filled", '
           'fillcolor="#FFFDF8", fontcolor="#16281F", color="#C7BBA0", fontname="Inter", fontsize=11]; '
           'edge [color="#7A8B7E", fontcolor="#5C6F63", fontname="Inter", fontsize=9]; '
           + "; ".join(nodes) +
           '; draft->validated [label="Analyst: validate"]; '
           'validated->in_review [label="Analyst: submit"]; '
           'in_review->approved [label="Reviewer: approve"]; '
           'in_review->changes_requested [label="Reviewer: request changes"]; '
           'changes_requested->validated [label="correct + revalidate"]; '
           'approved->draft [label="reopen"]; approved->archived [label="Admin: archive"]; '
           'archived->draft [label="Admin: unarchive"]; '
           'exception [label="Exception path\\nquality gate failed", shape=note, fillcolor="#FDF3E2", fontcolor="#B45309", color="#D97706"]; '
           'validated->exception [style=dashed, label="score < gate"]; '
           'exception->in_review [style=dashed, label="reason + audit"]; }')
    st.graphviz_chart(dot, **width_kwargs(st.graphviz_chart))

    c1, c2 = st.columns([2, 3])
    with c1:
        st.markdown(f'#### {htmlmod.escape(prod["name"])}')
        st.markdown(f'{badge(cur)} &nbsp; <span class="chip">quality {score*100:.0f}/100 · {grade(score)}</span> '
                    f'<span class="chip">gate {"PASS" if gate else "FAIL"}</span>', unsafe_allow_html=True)
        st.markdown(kpi("Per-unit result under review", f'{res["total"]:,.1f} kg CO₂e',
                        f'factors {ctx["version"]} · run {ctx["run_id"]}', "lime"), unsafe_allow_html=True)
    with c2:
        st.markdown("#### Validation checks")
        items = []
        icons = {"ok": "✔", "warning": "⚠", "error": "✖"}
        for ck in checks:
            items.append(f'<li><span class="b b-{ck["level"]}">{icons[ck["level"]]} {ck["level"]}</span> '
                         f'{htmlmod.escape(ck["msg"])}</li>')
        st.markdown('<ul class="tt-feed">' + "".join(items) + "</ul>", unsafe_allow_html=True)

    st.markdown("#### Actions")
    has_errors = any(c["level"] == "error" for c in checks)
    comment = st.text_input("Reviewer comment / exception reason", key="rev_comment",
                            placeholder="Required for request-changes and quality-gate exceptions.")
    a1, a2, a3, a4 = st.columns(4)
    if cur in ("draft", "changes_requested"):
        if a1.button("✔ Validate & mark", disabled=not can("validate") or has_errors, type="primary", key="act_val"):
            try:
                set_status(prod["id"], "validated", ctx["actor"], ctx["role"], "Structural checks passed")
                flash("success", "Record validated.")
                st.rerun()
            except (PermissionError, ValueError) as e:
                st.error(str(e))
    if cur == "validated":
        need_exception = not gate and not has_errors
        ok = can("submit") and not has_errors and (gate or (need_exception and bool(comment.strip())))
        if a2.button("➤ Submit for review", disabled=not ok, type="primary", key="act_sub",
                     help="Gate failed: provide an exception reason." if need_exception else None):
            try:
                note = comment.strip() if need_exception else (comment.strip() or "Submitted for review")
                set_status(prod["id"], "in_review", ctx["actor"], ctx["role"],
                           ("EXCEPTION: " + note) if need_exception else note)
                if need_exception:
                    with closing(db()) as c, c:
                        log_event(c, ctx["actor"], ctx["role"], "exception_submit", "product", prod["id"], note)
                flash("success", "Submitted for review." + (" Exception documented." if need_exception else ""))
                st.rerun()
            except (PermissionError, ValueError) as e:
                st.error(str(e))
        if need_exception:
            st.warning(f"Quality gate failed ({score*100:.0f} < {QUALITY_GATE*100:.0f}). "
                       "Enter an exception reason above to submit — it will be audited and shown to the reviewer.")
    if cur == "in_review":
        if a2.button("✔ Approve", disabled=not can("approve"), type="primary", key="act_app"):
            try:
                set_status(prod["id"], "approved", ctx["actor"], ctx["role"], comment.strip() or "Approved")
                flash("success", "Record approved.")
                st.rerun()
            except (PermissionError, ValueError) as e:
                st.error(str(e))
        if a3.button("↩ Request changes", disabled=not can("approve") or not comment.strip(), key="act_chg",
                     help="A comment is required."):
            try:
                set_status(prod["id"], "changes_requested", ctx["actor"], ctx["role"], comment.strip())
                flash("warning", "Changes requested — record returned for correction.")
                st.rerun()
            except (PermissionError, ValueError) as e:
                st.error(str(e))
    if cur in ("approved", "archived"):
        if a3.button("🔓 Reopen", disabled=not can("reopen"), key="act_reopen"):
            try:
                set_status(prod["id"], "draft", ctx["actor"], ctx["role"], comment.strip() or "Reopened for correction")
                flash("info", "Record reopened as draft.")
                st.rerun()
            except (PermissionError, ValueError) as e:
                st.error(str(e))
    if cur == "approved":
        arch_ok = st.checkbox("Confirm archive", key="arch_conf")
        if a4.button("🗄 Archive", disabled=not can("archive") or not arch_ok, key="act_arch"):
            try:
                set_status(prod["id"], "archived", ctx["actor"], ctx["role"], "Archived")
                flash("info", "Record archived (read-only).")
                st.rerun()
            except (PermissionError, ValueError) as e:
                st.error(str(e))

    st.markdown("#### Manual overrides (Reviewer / Administrator)")
    o1, o2, o3 = st.columns([2, 1, 2])
    o_stage = o1.selectbox("Stage", STAGES, key="ov_stage")
    o_delta = o2.number_input("Adjustment (kg CO₂e)", -1000.0, 1000.0, 0.0, step=0.5, key="ov_delta")
    o_reason = o3.text_input("Reason (required, audited)", key="ov_reason")
    if st.button("⚠ Apply override", disabled=not can("override") or not o_reason.strip() or o_delta == 0, key="ov_add"):
        with closing(db()) as c, c:
            c.execute("INSERT INTO overrides(product_id,stage,delta_kg,reason,actor,role,ts,active) VALUES (?,?,?,?,?,?,?,1)",
                      (prod["id"], o_stage, o_delta, o_reason.strip(), ctx["actor"], ctx["role"], utcnow()))
            log_event(c, ctx["actor"], ctx["role"], "override_added", "product", prod["id"],
                      f"{o_stage} {o_delta:+.1f} kg — {o_reason.strip()}")
        flash("warning", f"Override applied: {o_stage} {o_delta:+.1f} kg CO₂e. Included in all outputs and reports.")
        st.rerun()
    ovs = qdf("SELECT * FROM overrides WHERE product_id=? AND active=1 ORDER BY id DESC", (prod["id"],))
    if ovs.empty:
        st.caption("No active overrides — the deterministic result stands.")
    else:
        for _, o in ovs.iterrows():
            r1, r2 = st.columns([5, 1])
            r1.markdown(f'<span class="b b-warning">override</span> **{o["stage"]} {o["delta_kg"]:+.1f} kg** — '
                        f'{htmlmod.escape(o["reason"])} · {htmlmod.escape(o["actor"])} · {o["ts"][:16]}',
                        unsafe_allow_html=True)
            if r2.button("Revoke", key=f"ov_rev_{o['id']}", disabled=not can("override")):
                with closing(db()) as c, c:
                    c.execute("UPDATE overrides SET active=0 WHERE id=?", (int(o["id"]),))
                    log_event(c, ctx["actor"], ctx["role"], "override_revoked", "product", prod["id"], o["reason"])
                flash("info", "Override revoked.")
                st.rerun()

    st.markdown("#### Approval history")
    hist = qdf("SELECT ts, actor, role, from_status, to_status, comment FROM approvals WHERE product_id=? ORDER BY id DESC",
               (prod["id"],))
    if hist.empty:
        st.caption("No transitions recorded yet.")
    else:
        st.markdown(html_table(hist), unsafe_allow_html=True)

    with st.expander("🔍 Result explanation panel — inputs, rules, lineage, reviewer status"):
        e1, e2 = st.columns(2)
        with e1:
            st.markdown("**Inputs applied**")
            inp = pd.DataFrame([
                {"Input": "Lifetime (yr)", "Value": f'{prod["lifetime_yr"]} × {ctx["params"]["life_mult"]:.2f}'},
                {"Input": "Use energy (kWh/yr)", "Value": f'{prod["use_kwh_yr"]} × {ctx["params"]["use_mult"]:.2f}'},
                {"Input": "Grid (kg/kWh)", "Value": ctx["params"]["grid"]},
                {"Input": "Recycled content", "Value": f'{ctx["params"]["recycled_content"]}%'},
                {"Input": "EoL recycling / incineration", "Value": f'{ctx["params"]["eol_recycle"]}% / {ctx["params"]["incin_share"]}%'},
                {"Input": "Scrap rate", "Value": f'{ctx["params"]["scrap"]}%'},
                {"Input": "Rules / factor version", "Value": f'engine v{ENGINE_VERSION} · {ctx["version"]}'},
            ])
            st.markdown(html_table(inp, max_h=300), unsafe_allow_html=True)
        with e2:
            st.markdown("**Key contributors & uncertainty**")
            top = res["rows"].reindex(res["rows"]["gwp"].abs().sort_values(ascending=False).index).head(5)
            st.markdown(html_table(top[["item", "gwp", "unc_pct", "formula"]].rename(columns={"gwp": "kg CO₂e", "unc_pct": "±%"}),
                                   num_cols=("kg CO₂e", "±%"), max_h=300), unsafe_allow_html=True)
            st.caption(f'Reviewer status: {STATUS_LABEL[cur]} · quality {score*100:.0f}/100 · '
                       f'MC P5–P95 {ctx["mc"]["p5"]:,.1f}–{ctx["mc"]["p95"]:,.1f} kg.')
        st.markdown(f'<div class="tt-note"><b>Lineage:</b> synthetic://fixtures/v1 → {htmlmod.escape(prod["source"])} → '
                    f'engine v{ENGINE_VERSION} → factors {ctx["version"]} → run {ctx["run_id"]}. '
                    f'Every contribution row carries its formula, factor and quality tag.</div>', unsafe_allow_html=True)


# ============================================================================
# PAGE — REPORTS
# ============================================================================
REPORT_CSS = ("@import url('https://fonts.googleapis.com/css2?family=Fraunces:wght@600;700&family=Inter:wght@400;600&display=swap');"
              "body{font-family:Inter,Arial,sans-serif;margin:40px auto;max-width:920px;color:#16281F;background:#F7F2E7;}"
              "h1{font-family:Fraunces,Georgia,serif;font-size:26px;margin:0;} h2{font-family:Fraunces,Georgia,serif;font-size:17px;margin:26px 0 8px;border-bottom:2px solid #123B2E;padding-bottom:4px;}"
              ".brand{color:#3F7A0E;font-weight:700;letter-spacing:.12em;font-size:12px;text-transform:uppercase;}"
              ".meta{color:#68796E;font-size:12px;margin:6px 0 18px;}"
              "table{border-collapse:collapse;width:100%;margin:8px 0;background:#FFFDF8;} th,td{border:1px solid #D9CFBA;padding:6px 10px;font-size:12px;text-align:left;}"
              "th{background:#123B2E;color:#F7F2E7;} .num{text-align:right;}"
              ".kpis{display:flex;gap:12px;margin:14px 0;} .k{flex:1;border:1.5px solid #C7BBA0;border-radius:10px;padding:12px;background:#FFFDF8;box-shadow:3px 3px 0 rgba(22,40,31,.07);}"
              ".k b{display:block;font-size:20px;font-family:Fraunces,Georgia,serif;} .k span{font-size:11px;color:#68796E;text-transform:uppercase;letter-spacing:.08em;}"
              ".warn{background:#FDF3E2;border:1px solid #F3D19B;border-radius:8px;padding:10px;font-size:12px;margin:8px 0;}"
              "footer{margin-top:30px;font-size:11px;color:#68796E;border-top:1px solid #D9CFBA;padding-top:10px;}")


def build_report(ctx, include):
    prod, res, mc = ctx["prod"], ctx["res"], ctx["mc"]
    checks, score, gate = validate_product(prod, ctx["lines"], ctx["legs"], ctx["factors"])
    approvals = qdf("SELECT ts,actor,role,from_status,to_status,comment FROM approvals WHERE product_id=? ORDER BY id", (prod["id"],))
    meta = {"product": prod["name"], "product_id": prod["id"], "functional_unit": prod["functional_unit"],
            "status": STATUS_LABEL[prod["status"]], "run_id": ctx["run_id"], "engine": ENGINE_VERSION,
            "factors": ctx["version"], "generated": utcnow(), "generated_by": f'{ctx["actor"]} ({ctx["role"]})',
            "data_label": "SYNTHETIC DEMO DATA"}
    bundle = {"meta": meta, "params": ctx["params"],
              "stages": {k: round(v, 3) for k, v in res["stages"].items()},
              "total_kg_co2e": round(res["total"], 3), "annual_t_co2e": round(res["annual"] / 1000, 3),
              "quality_score": round(score, 3), "gate_pass": gate,
              "flags": [f"{l}: {m}" for l, m in res["flags"]]}
    if include["contributions"]:
        bundle["contributions"] = json.loads(res["rows"].to_json(orient="records"))
    if include["uncertainty"]:
        unc = {k: round(mc[k], 3) for k in ("p5", "p50", "p95", "mean", "std")}
        unc.update({"n": mc["n"], "seed": ctx["params"]["seed"]})
        bundle["uncertainty"] = unc
    if include["approvals"]:
        bundle["approvals"] = json.loads(approvals.to_json(orient="records"))
        bundle["overrides"] = json.loads(qdf("SELECT stage,delta_kg,reason,actor,ts FROM overrides WHERE product_id=? AND active=1",
                                             (prod["id"],)).to_json(orient="records"))
    if include["audit"]:
        bundle["audit_tail"] = json.loads(qdf("SELECT ts,actor,role,event,details FROM audit_events ORDER BY id DESC LIMIT 25").to_json(orient="records"))

    def tbl(df, num=()):
        th = "".join(f"<th>{htmlmod.escape(str(c))}</th>" for c in df.columns)
        trs = []
        for _, r in df.iterrows():
            cells = []
            for c, v in r.items():
                txt = f"{v:,.2f}" if isinstance(v, float) else str(v)
                cls = "num" if c in num else ""
                cells.append(f'<td class="{cls}">{htmlmod.escape(txt)}</td>')
            trs.append("<tr>" + "".join(cells) + "</tr>")
        return f"<table><thead><tr>{th}</tr></thead><tbody>{''.join(trs)}</tbody></table>"

    stage_df = pd.DataFrame([{"Stage": s, "kg CO₂e": round(v, 2)} for s, v in res["stages"].items()])
    parts = [f'<!doctype html><html><head><meta charset="utf-8"><title>TerraTrace report — {htmlmod.escape(prod["name"])}</title>'
             f'<style>{REPORT_CSS}</style></head><body>',
             '<div class="brand">🌿 TerraTrace · Lifecycle Impact Studio</div>',
             f'<h1>{htmlmod.escape(prod["name"])}</h1>',
             f'<div class="meta">Run {meta["run_id"]} · engine v{ENGINE_VERSION} · factors {ctx["version"]} · '
             f'{meta["generated"]} · {htmlmod.escape(meta["generated_by"])} · status: {meta["status"]} · <b>SYNTHETIC DEMO DATA</b></div>',
             '<div class="kpis">'
             f'<div class="k"><span>Per-unit net</span><b>{res["total"]:,.1f} kg CO₂e</b></div>'
             f'<div class="k"><span>Annual program</span><b>{res["annual"]/1000:,.1f} t CO₂e</b></div>'
             f'<div class="k"><span>P5–P95</span><b>{mc["p5"]:,.1f} – {mc["p95"]:,.1f}</b></div>'
             f'<div class="k"><span>Data quality</span><b>{score*100:.0f}/100 · {grade(score)}</b></div></div>',
             "<h2>Lifecycle stage summary</h2>" + tbl(stage_df, num=("kg CO₂e",)),
             "<h2>Scenario assumptions</h2>" + tbl(pd.DataFrame([{"Parameter": k, "Value": v} for k, v in ctx["params"].items()])),
             "<h2>Data quality & limitations</h2>" + "".join(f'<div class="warn">{htmlmod.escape(l.upper())}: {htmlmod.escape(m)}</div>' for l, m in res["flags"])]
    if include["contributions"]:
        parts.append("<h2>Contribution ledger (traceable formulas)</h2>" +
                     tbl(res["rows"][["stage", "item", "qty", "qty_unit", "factor", "gwp", "quality", "formula"]]
                         .rename(columns={"gwp": "kg CO₂e"}), num=("qty", "factor", "kg CO₂e")))
    if include["approvals"] and not approvals.empty:
        parts.append("<h2>Approval chain</h2>" + tbl(approvals))
    if include["audit"] and "audit_tail" in bundle:
        parts.append("<h2>Audit trail (latest 25 events)</h2>" +
                     tbl(pd.DataFrame(bundle["audit_tail"])))
    parts.append("<footer>Decision-support estimate produced with illustrative, versioned demo factors and synthetic activity data. "
                 "Not a certified LCA, EPD or regulatory filing. Boundary: cradle-to-grave, avoided-burden recycling credits (q=0.9), "
                 "GWP100. Replace factors with a licensed database and obtain qualified review before external use.</footer></body></html>")
    return bundle, "".join(parts)


def _log_export(pid, fmt):
    ss = st.session_state
    with closing(db()) as c, c:
        log_event(c, ss.get("actor_name", "?"), ss.get("role", "?"), "export", "product", pid, f"Format: {fmt}")


def page_reports(ctx):
    strip(ctx)
    sec("Export", "Reports & exports",
        "Every export is logged to the audit trail and carries run ID, factor version, assumptions and limitations.")
    inc = {
        "contributions": st.checkbox("Contribution ledger (formulas)", True, key="rp_c"),
        "uncertainty": st.checkbox("Monte Carlo uncertainty", True, key="rp_u"),
        "approvals": st.checkbox("Approvals & overrides", True, key="rp_a"),
        "audit": st.checkbox("Audit trail (latest 25)", False, key="rp_t"),
    }
    if st.button("⚙ Generate report", type="primary", disabled=not can("export"), key="rp_gen"):
        st.session_state["report"] = build_report(ctx, inc)
    if "report" not in st.session_state:
        st.markdown('<div class="tt-note">Click <b>Generate report</b> — the current scenario, uncertainty run and approval '
                    'state are bundled with full traceability.</div>', unsafe_allow_html=True)
    else:
        bundle, html_doc = st.session_state["report"]
        r = ctx["res"]
        st.markdown('<div class="tt-kpi-grid">' + "".join([
            kpi("Per-unit", f'{r["total"]:,.1f} kg', "net CO₂e", "lime"),
            kpi("Annual", f'{r["annual"]/1000:,.1f} t', "program CO₂e", "teal"),
            kpi("Run", bundle["meta"]["run_id"], f'factors {ctx["version"]}', "sky"),
            kpi("Status", STATUS_LABEL[ctx["prod"]["status"]], bundle["meta"]["generated"][:16], "amber"),
        ]) + "</div>", unsafe_allow_html=True)
        pid = ctx["prod"]["id"]
        d1, d2, d3 = st.columns(3)
        d1.download_button("⬇ HTML report", html_doc, f"terratrace_{pid}.html", "text/html",
                           on_click=_log_export, args=(pid, "HTML"), type="primary", **width_kwargs(st.download_button))
        d2.download_button("⬇ CSV contributions", r["rows"].to_csv(index=False).encode(),
                           f"terratrace_{pid}.csv", "text/csv",
                           on_click=_log_export, args=(pid, "CSV"), **width_kwargs(st.download_button))
        d3.download_button("⬇ JSON bundle", json.dumps(bundle, indent=2),
                           f"terratrace_{pid}.json", "application/json",
                           on_click=_log_export, args=(pid, "JSON"), **width_kwargs(st.download_button))
        with st.expander("Preview stage summary"):
            st.markdown(html_table(pd.DataFrame([{"Stage": s, "kg CO₂e": round(v, 2)} for s, v in r["stages"].items()]),
                                   num_cols=("kg CO₂e",)), unsafe_allow_html=True)
    st.markdown("#### Portfolio export")
    port = pd.DataFrame([{
        "product_id": p["id"], "name": p["name"], "status": p["status"],
        "per_unit_kg_co2e": round(ctx["results"][p["id"]]["total"], 2),
        "annual_t_co2e": round(ctx["results"][p["id"]]["annual"] / 1000, 2),
        "quality": round(ctx["results"][p["id"]]["quality"], 2),
        **{f"stage_{s.lower().replace(' ', '_').replace('-', '_')}": round(ctx["results"][p["id"]]["stages"][s], 2) for s in STAGES},
    } for p in ctx["products"]])
    st.markdown(html_table(port, num_cols=tuple(c for c in port.columns if c not in ("product_id", "name", "status"))),
                unsafe_allow_html=True)
    st.download_button("⬇ Portfolio CSV", port.to_csv(index=False).encode(), "terratrace_portfolio.csv",
                       "text/csv", on_click=_log_export, args=("portfolio", "CSV"), key="rp_port",
                       disabled=not can("export"), **width_kwargs(st.download_button))


# ============================================================================
# PAGE — GOVERNANCE
# ============================================================================
def run_self_tests():
    results = []
    F = resolve_factors("FCT-2026.1")
    P = dict(DEFAULT_PARAMS, scrap=0, recycled_content=0, eol_recycle=0, incin_share=0)
    mini = {"lifetime_yr": 10, "use_kwh_yr": 100, "assembly_kwh": 0, "gas_kwh": 0, "base_volume": 1}
    lines = [{"material_code": "steel", "mass_kg": 2.0, "supplier": "T", "quality": "measured"}]

    def check(name, fn):
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"raised {type(e).__name__}: {e}"
        results.append({"Test": name, "Result": "PASS" if ok else "FAIL", "Detail": detail})

    def t1():
        r = assess(mini, lines, [], P, F)
        v = r["stages"]["Materials"]
        return abs(v - 4.92) < 1e-9, f"2 kg × 2.46 = {v:.4f} (expected 4.92)"

    def t2():
        r = assess(mini, lines, [], P, F)
        v = r["stages"]["Use phase"]
        return abs(v - 380.0) < 1e-9, f"100 kWh/yr × 10 yr × 0.38 = {v:.2f} (expected 380)"

    def t3():
        r = assess(mini, lines, [], dict(P, eol_recycle=80), F)
        return r["stages"]["Credits"] < 0, f'credit = {r["stages"]["Credits"]:.3f} kg (must be negative)'

    def t4():
        r = assess(mini, lines, [], P, F)
        a, b = monte_carlo(r, F, 400, 42), monte_carlo(r, F, 400, 42)
        return a["p50"] == b["p50"], f'seeded P50 {a["p50"]:.4f} == {b["p50"]:.4f}'

    def t5():
        bad = pd.read_csv(io.StringIO(BAD_CSV))
        out = validate_import(bad, set(), "Append", F)
        n = int((out["_status"] == "error").sum())
        return n >= 6, f"{n} malformed rows rejected with messages (expected ≥6)"

    def t6():
        good = pd.read_csv(io.StringIO(TEMPLATE_CSV))
        out = validate_import(good, set(), "Append", F)
        return bool((out["_status"] == "valid").all()), "template file: all rows valid"

    def t7():
        try:
            assess(mini, [{"material_code": "steel", "mass_kg": -1, "supplier": "", "quality": "proxy"}], [], P, F)
            return False, "negative mass was accepted"
        except ValueError as e:
            return True, f"ValueError raised: {e}"

    def t8():
        return "approve" not in PERMS["Analyst"] and "approve" in PERMS["Reviewer"], \
            "Analyst cannot approve; Reviewer can (server-side rule)"

    def t9():
        r = assess(mini, lines, [], dict(P, eol_recycle=60, incin_share=50), F)
        eol = r["rows"]
        mr = eol[eol["item"].str.startswith("Recycling reprocessing")]["qty"].sum()
        inc = eol[eol["item"].str.startswith("Incineration")]["qty"].sum()
        land = eol[eol["item"].str.startswith("Landfill")]["qty"].sum()
        return abs(mr + inc + land - 2.0) < 1e-9, f"mass balance {mr+inc+land:.4f} = 2.0 kg"

    for name, fn in [("Hand-check: materials 2 kg steel → 4.92 kg", t1),
                     ("Hand-check: use phase → 380 kg", t2),
                     ("Recycling credit is negative", t3),
                     ("Monte Carlo reproducible with same seed", t4),
                     ("Malformed import rows rejected (≥6)", t5),
                     ("Documented template imports cleanly", t6),
                     ("Negative mass raises ValueError", t7),
                     ("Authorization: analyst cannot approve", t8),
                     ("End-of-life mass balance closes", t9)]:
        check(name, fn)
    return pd.DataFrame(results)


def page_governance(ctx):
    strip(ctx)
    sec("Governance", "Data, factors, audit & reliability",
        "Versioned reference data with rollback · lineage · audit · backup · observable self-tests.")
    tabs = st.tabs(["Factor catalog", "Data dictionary", "Audit log", "Data management", "Self-tests", "Architecture & security"])

    with tabs[0]:
        v = ctx["version"]
        opts = list(FACTOR_VERSIONS)
        sel = st.selectbox("Active factor version", opts, index=opts.index(v),
                           format_func=lambda k: f"{k} — {FACTOR_VERSIONS[k]['label']}", key="fv_sel")
        if st.button("⟳ Apply version (rollback supported)", disabled=not can("factors") or sel == v,
                     help="Administrator only. Results recompute immediately; change is audited."):
            with closing(db()) as c, c:
                c.execute("INSERT OR REPLACE INTO settings VALUES ('factor_version',?)", (sel,))
                log_event(c, ctx["actor"], ctx["role"], "factors_changed", "reference", sel, f"{v} → {sel}")
            flash("success", f"Factor set switched {v} → {sel}. All results recomputed.")
            st.rerun()
        mrows = [{"Code": k, "Material": m["name"], "Virgin kg/kg": m["gwp"],
                  "Recycled kg/kg": m["rec"] if m["rec"] is not None else "n/a",
                  "±% (95%)": m["unc"], "Recyclable": "yes" if m["recyclable"] else "no",
                  "Incineration kg/kg": m["incin"], "Landfill kg/kg": m["land"]}
                 for k, m in ctx["factors"]["materials"].items()]
        st.markdown(html_table(pd.DataFrame(mrows), num_cols=("Virgin kg/kg", "±% (95%)", "Incineration kg/kg", "Landfill kg/kg")),
                    unsafe_allow_html=True)
        prows = [{"Code": k, "Process": p["name"], "Unit": p["unit"], "Factor": p["gwp"], "±% (95%)": p["unc"]}
                 for k, p in ctx["factors"]["process"].items()]
        st.markdown(html_table(pd.DataFrame(prows), num_cols=("Factor", "±% (95%)")), unsafe_allow_html=True)
        st.markdown('<div class="tt-note"><b>License & provenance:</b> these are illustrative synthetic values for software '
                    'validation only. Before external use, load a licensed database (ecoinvent, GREET, DEFRA, Exiobase), confirm '
                    'commercial-use and redistribution terms, record update frequency, and have a qualified practitioner review boundaries.</div>',
                    unsafe_allow_html=True)

    with tabs[1]:
        dd = [
            ("products", "id", "TEXT PK", "—", "Stable product identifier", "slug+uuid", "internal", "unique"),
            ("products", "status", "TEXT", "—", "Workflow state", "draft/validated/in_review/approved/changes_requested/archived", "internal", "transition rules"),
            ("products", "lifetime_yr", "REAL", "years", "Service life", ">0, ≤50", "manual/import", "range check"),
            ("products", "use_kwh_yr", "REAL", "kWh/yr", "Operating electricity", "≥0, ≤100000", "manual/import", "range check"),
            ("products", "base_volume", "INTEGER", "units/yr", "Annual program volume", "≥1", "manual", "range check"),
            ("bom_lines", "material_code", "TEXT FK", "—", "Reference catalog key", "catalog codes", "import/manual", "referential integrity"),
            ("bom_lines", "mass_kg", "REAL", "kg", "Mass per functional unit", "(0, 5000]", "import/manual", "type+range"),
            ("bom_lines", "quality", "TEXT", "—", "Data pedigree tag", "measured/supplier/proxy", "import/manual", "enum"),
            ("bom_lines", "source", "TEXT", "—", "Provenance of the row", "synthetic://, import://, manual://", "system", "required"),
            ("transport_legs", "mode", "TEXT", "—", "Freight mode", "truck/rail/sea/air", "manual", "enum"),
            ("transport_legs", "km", "REAL", "km", "Leg distance", "[0, 40000]", "manual", "range check"),
            ("imports", "sha256", "TEXT", "—", "Checksum of preserved source file", "hex", "system", "required"),
            ("audit_events", "event", "TEXT", "—", "Material change type", "created/import/approved/override/export/…", "system", "append-only"),
            ("overrides", "delta_kg", "REAL", "kg CO₂e", "Reviewer adjustment", "[-1000, 1000], reason required", "reviewer", "audited"),
            ("overrides", "reason", "TEXT", "—", "Justification", "non-empty", "reviewer", "required"),
            ("scenarios", "params_json", "TEXT", "—", "Frozen parameter snapshot", "valid JSON", "analyst", "schema keys"),
            ("settings", "factor_version", "TEXT", "—", "Active reference-data version", "known versions", "admin", "audited switch"),
        ]
        st.markdown(html_table(pd.DataFrame(dd, columns=["Entity", "Field", "Type", "Unit", "Meaning", "Allowed values", "Source", "Validation"]),
                               max_h=520), unsafe_allow_html=True)
        st.caption("All records carry created/updated timestamps, source provenance and schema version. "
                   "Sensitivity: everything in the demo is synthetic/public; production supplier declarations would be 'confidential'.")

    with tabs[2]:
        aud = qdf("SELECT * FROM audit_events ORDER BY id DESC")
        types = sorted(aud["event"].unique().tolist())
        pick = st.multiselect("Filter event types", types, default=[], key="aud_filter")
        if pick:
            aud = aud[aud["event"].isin(pick)]
        st.markdown(html_table(aud[["ts", "actor", "role", "event", "entity", "entity_id", "details"]].head(200), max_h=480),
                    unsafe_allow_html=True)
        st.download_button("⬇ Audit CSV", aud.to_csv(index=False).encode(), "terratrace_audit.csv", "text/csv",
                           key="aud_dl", disabled=not can("export"), **width_kwargs(st.download_button))

    with tabs[3]:
        g1, g2 = st.columns(2)
        with g1:
            st.markdown("#### Backup")
            st.download_button("⬇ Download database backup (.db)", DB_PATH.read_bytes(),
                               f"terratrace_backup_{datetime.now(timezone.utc).strftime('%Y%m%d')}.db",
                               "application/octet-stream", on_click=_log_export, args=("database", "BACKUP"),
                               key="bk_dl", disabled=not can("export"), **width_kwargs(st.download_button))
            st.markdown("**Restore (PowerShell):**")
            st.code("Stop-Process -Name streamlit -ErrorAction SilentlyContinue\n"
                    "Copy-Item .\\terratrace_backup_YYYYMMDD.db .\\data\\terratrace.db -Force\n"
                    "streamlit run app.py", language="powershell")
            st.caption("Retention policy (demo): audit 7 years, preserved uploads 24 months, then purge via scheduled job. "
                       "Deletion: admin product delete cascades BOM/legs/overrides; audit history is retained.")
        with g2:
            st.markdown("#### Preserved source files")
            files = sorted(UPLOAD_DIR.glob("*"))
            if files:
                st.markdown(html_table(pd.DataFrame([{"File": f.name, "Bytes": f.stat().st_size} for f in files]),
                                       num_cols=("Bytes",)), unsafe_allow_html=True)
            else:
                st.caption("No imports committed yet — original files will be stored here with checksum names.")
            st.markdown("#### Reset demo")
            conf = st.checkbox("Wipe all records and reseed the synthetic fixtures.", key="reset_conf")
            if st.button("♻ Reset demo database", disabled=not can("reset") or not conf, key="reset_btn"):
                with closing(db()) as c, c:
                    for t in ["bom_lines", "transport_legs", "approvals", "overrides", "scenarios", "imports", "audit_events", "products", "settings"]:
                        c.execute(f"DELETE FROM {t}")
                    seed(c)
                st.session_state["bom_rev"] += 1
                st.session_state["leg_rev"] += 1
                st.session_state.pop("report", None)
                flash("success", "Database reset — synthetic fixtures reloaded. Demo is repeatable from a clean state.")
                st.rerun()
        st.markdown("#### PostgreSQL migration path (team edition)")
        st.markdown("- Swap the connection layer for SQLAlchemy + Alembic; schema is already normalized and versioned.\n"
                    "- Types map 1:1 (TEXT→TEXT, REAL→DOUBLE PRECISION, AUTOINCREMENT→IDENTITY).\n"
                    "- Bulk-move with `pgloader data/terratrace.db postgresql://…`, then enable row-level auth, "
                    "server-side sessions and concurrent transaction tests before multi-user rollout.")

    with tabs[4]:
        st.markdown("#### Observable acceptance self-tests")
        st.caption("Hand-checkable arithmetic, validation, reproducibility, authorization and mass-balance invariants. "
                   "Run them any time — results are real, computed live, and logged.")
        if st.button("▶ Run self-tests", type="primary", key="st_run"):
            st.session_state["selftests"] = run_self_tests()
            with closing(db()) as c, c:
                n_fail = int((st.session_state["selftests"]["Result"] == "FAIL").sum())
                log_event(c, ctx["actor"], ctx["role"], "selftest", "engine", ENGINE_VERSION,
                          f'{len(st.session_state["selftests"])} tests, {n_fail} failures')
        if "selftests" in st.session_state:
            t = st.session_state["selftests"]
            n_pass = int((t["Result"] == "PASS").sum())
            if n_pass == len(t):
                st.success(f"All {len(t)} acceptance checks passed.")
            else:
                st.error(f"{len(t) - n_pass} of {len(t)} checks failed — investigate before release.")
            tt = t.copy()
            tt["Result"] = tt["Result"].map({"PASS": "ok", "FAIL": "error"})
            st.markdown(html_table(tt, badge_col="Result", max_h=420), unsafe_allow_html=True)

    with tabs[5]:
        erd = ('digraph ERD { rankdir=LR; bgcolor="transparent"; '
               'node [shape=record, style="filled", fillcolor="#FFFDF8", fontcolor="#16281F", color="#C7BBA0", fontname="Inter", fontsize=10]; '
               'edge [color="#7A8B7E", fontname="Inter", fontsize=9, fontcolor="#5C6F63"]; '
               'products [label="{products|id · name · status\\llifetime · energy · volume\\lsource · schema_version\\l}"]; '
               'bom [label="{bom_lines|product_id · material_code\\lmass_kg · quality · source\\l}"]; '
               'legs [label="{transport_legs|product_id · scope\\lmode · km\\l}"]; '
               'appr [label="{approvals|product_id · from→to\\lactor · role · comment\\l}"]; '
               'ovr [label="{overrides|product_id · stage\\ldelta_kg · reason · active\\l}"]; '
               'imp [label="{imports|filename · sha256\\lrows ok/err · actor\\l}"]; '
               'aud [label="{audit_events|ts · actor · event\\lentity · details\\l}"]; '
               'scn [label="{scenarios|name · params_json\\lactor · created_at\\l}"]; '
               'set [label="{settings|factor_version\\l}"]; '
               'products->bom [label="1:N"]; products->legs [label="1:N"]; products->appr [label="1:N"]; '
               'products->ovr [label="1:N"]; products->imp [label="1:N"]; }')
        st.graphviz_chart(erd, **width_kwargs(st.graphviz_chart))
        roles_df = pd.DataFrame([{"Action": a, **{r: ("✔" if a in PERMS[r] else "—") for r in ROLES}} for a in sorted(ALL_ACTIONS)])
        st.markdown(html_table(roles_df, max_h=380), unsafe_allow_html=True)
        st.markdown(
            "**Threat model & honest boundaries**\n"
            "- *Demo auth:* role switching is a client-side simulation. Production requires server-side authentication "
            "(e.g. FastAPI + argon2 password hashing), HTTPS and secrets in environment variables.\n"
            "- *Tampering:* imports are checksummed and preserved; every material change, approval, override and export is audited.\n"
            "- *Injection:* all SQL is parameterized; uploads are type-, size- (5 MB) and schema-validated.\n"
            "- *Disclosure:* demo data is synthetic; production supplier declarations must be classified confidential and access-scoped.\n"
            "- *Reliability:* short-lived transactions, deterministic seeded engine, backup/restore above, pinned dependency list, "
            "structured audit log. Multi-user concurrency and rate limiting arrive with the PostgreSQL team edition.\n\n"
            "**Release tiers:** (1) Local MVP — this app, one operator, free demo. (2) Team — shared PostgreSQL, real roles/approvals, "
            "per-seat pricing hypothesis. (3) Enterprise — SSO, audit exports, deployment controls, SLAs. "
            "No compliance or certification is claimed; qualified domain review is required for regulated use.")


# ============================================================================
# MAIN
# ============================================================================
def main():
    st.set_page_config(page_title="TerraTrace — Lifecycle Impact Studio", page_icon="🌿", layout="wide")
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)
    try:
        init_db()
    except sqlite3.Error as e:
        st.error(f"Database initialization failed: {e}")
        st.stop()
    products = qdf("SELECT * FROM products ORDER BY created_at").to_dict("records")
    if not products:
        with closing(db()) as c, c:
            seed(c)
        products = qdf("SELECT * FROM products ORDER BY created_at").to_dict("records")
    ensure_defaults(products)
    version = get_setting("factor_version", "FCT-2026.1")
    factors = resolve_factors(version)
    pid, role, actor_name = build_sidebar(products, version)
    actor = actor_name.strip() or "Anonymous"
    params = read_params()

    lines_by = {p["id"]: qdf("SELECT * FROM bom_lines WHERE product_id=? ORDER BY id", (p["id"],)).to_dict("records") for p in products}
    legs_by = {p["id"]: qdf("SELECT * FROM transport_legs WHERE product_id=? ORDER BY id", (p["id"],)).to_dict("records") for p in products}
    ov_by = {p["id"]: qdf("SELECT * FROM overrides WHERE product_id=? AND active=1", (p["id"],)).to_dict("records") for p in products}
    results, baseline = {}, {}
    for p in products:
        try:
            results[p["id"]] = assess(p, lines_by[p["id"]], legs_by[p["id"]], params, factors, ov_by[p["id"]])
            baseline[p["id"]] = assess(p, lines_by[p["id"]], legs_by[p["id"]], DEFAULT_PARAMS, factors, ov_by[p["id"]])
        except ValueError as e:
            st.error(f"Engine validation error for {p['name']}: {e}")
            st.stop()
    mc = monte_carlo(results[pid], factors, params["mc_runs"], params["seed"])
    prod = next(p for p in products if p["id"] == pid)
    run_id = hashlib.sha1(json.dumps({"pid": pid, "params": params, "fv": version, "ev": ENGINE_VERSION,
                                      "upd": prod["updated_at"]}, sort_keys=True).encode()).hexdigest()[:10]
    ctx = {"products": products, "prod": prod, "pid": pid, "role": role, "actor": actor,
           "params": params, "factors": factors, "version": version, "run_id": run_id,
           "lines": lines_by[pid], "legs": legs_by[pid], "overrides": ov_by[pid],
           "res": results[pid], "results": results, "baseline": baseline, "mc": mc}

    if st.session_state.get("flash"):
        kind, msg = st.session_state.pop("flash")
        getattr(st, kind)(msg)

    {"Overview": page_overview, "Products & BOM": page_products, "Import data": page_import,
     "Analysis": page_analysis, "Review & approvals": page_review, "Reports": page_reports,
     "Governance": page_governance}[st.session_state["nav"]](ctx)


if __name__ == "__main__":
    main()