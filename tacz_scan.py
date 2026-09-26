"""五阶段只登记原始表；tag不在此展开，交tacz_link。
支持目录：
 index: data/<ns>/index/{guns,attachments,ammo}/*.json
 data : data/<ns>/data/{guns,attachments,ammo}/*_data.json 或同名
 allow: data/<ns>/tacz_tags/attachments/allow_attachments/*.json
        data/<ns>/tags/attachments/allow_attachments/*.json
 slot : data/<ns>/tacz_tags/attachments/*.json
        data/<ns>/tags/attachments/*.json   (通用配件tag，可再引用#tag)
 adapter: data/<ns>/tacz_tags/attachments/adapter/*.json 或 tags/.../adapter/*
 ammod: data/<ns>/tacz_tags/attachments/ammo_mod* 或 tags/.../ammo_mod*
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Iterable, List

from models import PackModel, GunInfo, AttachmentInfo, AmmoInfo, ScanConfig

logger = logging.getLogger("tacz_scan")

ALLOW_DIRS = ("tacz_tags/attachments/allow_attachments", "tags/attachments/allow_attachments")
SLOT_DIRS = ("tacz_tags/attachments", "tags/attachments")

SLOT_NORM = {
    "scope": "scope", "sight": "scope", "red_dot": "scope", "reddot": "scope", "scope_scope": "scope", "scope_sight": "scope",
    "muzzle": "muzzle", "silencer": "muzzle", "suppressor": "muzzle", "compensator": "muzzle", "brake": "muzzle",
    "stock": "stock",
    "grip": "grip", "foregrip": "grip", "vertical_grip": "grip", "angled_grip": "grip",
    "laser": "laser", "laser_sight": "laser",
    "extended_mag": "extended_mag", "mag": "extended_mag", "magazine": "extended_mag",
    "misc": "misc",
}


def _load(text: str):
    if not text or not text.strip():
        return None
    try:
        return json.loads(text)
    except Exception:
        try:
            from tacz_io import strip_json_comments
            return json.loads(strip_json_comments(text))
        except Exception as e:
            logger.debug("parse fail: %r", e)
            return None


def _raw_list(obj) -> List[str]:
    if isinstance(obj, list):
        return [str(x) for x in obj if isinstance(x, str)]
    if isinstance(obj, dict):
        vals = obj.get("values")
        if isinstance(vals, list):
            out = []
            for v in vals:
                if isinstance(v, str):
                    out.append(v)
                elif isinstance(v, dict) and v.get("id"):
                    out.append(str(v["id"]))
            return out
    return []


def scan_records(records: Iterable[dict]) -> Dict[str, PackModel]:
    packs: Dict[str, PackModel] = {}
    for rec in records:
        rel = (rec.get("rel") or "").replace("\\", "/").lower()
        if not rel.startswith("data/"):
            continue
        ns = rec.get("ns") or ""
        if not ns:
            continue
        pm = packs.setdefault(ns, PackModel(ns=ns, source_label=rec.get("source_label", "")))
        stage = (rec.get("stage") or "").lower()
        sub = [s.lower() for s in (rec.get("sub") or [])]
        name = rec.get("name") or ""
        obj = _load(rec.get("text", ""))

        # index
        if stage == "index" and sub:
            kind = sub[0]
            if kind == "guns" and obj:
                g = GunInfo(gun_id=name, full_id=f"{ns}:{name}", ns=ns, index_raw=obj or {})
                pm.guns[name] = g
                pm._raw_index_gun[name] = obj or {}
            elif kind == "attachments" and obj:
                a = AttachmentInfo(attachment_id=name, full_id=f"{ns}:{name}", ns=ns, index_raw=obj or {})
                _fill_att_index(a, obj)
                pm.attachments[a.full_id] = a
                pm._raw_index_att[a.full_id] = obj or {}
            elif kind == "ammo" and obj:
                am = AmmoInfo(ammo_id=name, full_id=f"{ns}:{name}", ns=ns, index_raw=obj or {})
                _fill_ammo_index(am, obj)
                pm.ammos[am.full_id] = am
                pm._raw_index_ammo[full_id_safe(am)] = obj or {}
        # data
        elif stage == "data" and sub:
            kind = sub[0]
            if obj is None:
                continue
            if kind == "guns":
                g = pm.guns.get(name) or GunInfo(gun_id=name, full_id=f"{ns}:{name}", ns=ns)
                pm.guns[name] = g
                _fill_gun_data(g, obj, ns)
                pm._raw_data_gun[name] = obj
            elif kind == "attachments":
                base = name[:-5] if name.endswith("_data") else name
                fid = f"{ns}:{base}"
                a = pm.attachments.get(fid) or AttachmentInfo(attachment_id=base, full_id=fid, ns=ns)
                _fill_att_data(a, obj)
                pm.attachments[fid] = a
                pm._raw_data_att[fid] = obj
            elif kind == "ammo":
                base = name[:-5] if name.endswith("_data") else name
                fid = f"{ns}:{base}"
                am = pm.ammos.get(fid) or AmmoInfo(ammo_id=base, full_id=fid, ns=ns)
                _fill_ammo_data(am, obj)
                pm.ammos[fid] = am
                pm._raw_data_ammo[fid] = obj
        # allow
        elif any(d in rel for d in ALLOW_DIRS) and "allow_attachments" in sub:
            arr = _raw_list(obj)
            g = pm.guns.get(name) or GunInfo(gun_id=name, full_id=f"{ns}:{name}", ns=ns)
            pm.guns[name] = g
            g.allow_list = arr
            pm._raw_allow[name] = arr
        # 通用slot/adapter/ammod tag
        elif any(d in rel for d in SLOT_DIRS):
            if "allow_attachments" in sub:
                continue
            arr = _raw_list(obj)
            if "adapter" in sub:
                pm._raw_adapter_tags[name] = arr
            elif name.startswith("ammo_mod") or "ammo_mod" in rel:
                pm._raw_ammo_tags[name] = arr
            else:
                pm._raw_slot_tags[name] = arr
                pm._raw_slot_tags[f"{ns}:{name}"] = arr
    return packs


def full_id_safe(am: AmmoInfo) -> str:
    return am.full_id or f"{am.ns}:{am.ammo_id}"


def _fill_att_index(a: AttachmentInfo, obj: dict):
    t = obj.get("type") or ""
    a.type = SLOT_NORM.get(t.lower(), t.lower()) if t else a.type
    if isinstance(obj.get("weight"), (int, float)) and not a.weight:
        a.weight = float(obj["weight"])
    if isinstance(obj.get("sort"), (int, float)):
        a.sort = float(obj["sort"])
    if isinstance(obj.get("data"), str):
        a.data_ref = obj["data"].split(":", 1)[-1]


def _fill_att_data(a: AttachmentInfo, obj: dict):
    if isinstance(obj.get("weight"), (int, float)):
        a.weight = float(obj["weight"])
    if obj.get("type") and not a.type:
        a.type = SLOT_NORM.get(str(obj["type"]).lower(), str(obj["type"]).lower())
    a.data_raw = obj


def _fill_ammo_index(am: AmmoInfo, obj: dict):
    if isinstance(obj.get("type"), str):
        am.type = obj["type"]
    if isinstance(obj.get("data"), str):
        am.data_ref = obj["data"].split(":", 1)[-1]
    for k in ("caliber", "ammo_caliber"):
        if obj.get(k) and not am.caliber:
            am.caliber = str(obj[k])


def _fill_ammo_data(am: AmmoInfo, obj: dict):
    for k in ("caliber", "ammo_caliber"):
        if obj.get(k) and not am.caliber:
            am.caliber = str(obj[k])
    if isinstance(obj.get("type"), str) and not am.type:
        am.type = obj["type"]
    for k in ("name", "display_name"):
        if isinstance(obj.get(k), str):
            am.display_name = str(obj[k])
            break
    am.data_raw = obj


def _fill_gun_data(g: GunInfo, obj: dict, ns: str):
    if isinstance(obj.get("ammo"), str):
        g.ammo_full_id = obj["ammo"] if ":" in obj["ammo"] else f"{ns}:{obj['ammo']}"
    if isinstance(obj.get("ammo_amount"), int):
        g.ammo_amount = obj["ammo_amount"]
    if isinstance(obj.get("extended_mag_ammo_amount"), list):
        g.extended_mag_amount = [int(x) for x in obj["extended_mag_ammo_amount"] if isinstance(x, (int, float))]
    fm = obj.get("fire_mode")
    if isinstance(fm, list) and fm:
        g.fire_mode = str(fm[0]).upper()
    elif isinstance(fm, str):
        g.fire_mode = fm.upper()
    if isinstance(obj.get("allow_attachment_types"), list):
        g.allow_attachment_types = [str(x).lower() for x in obj["allow_attachment_types"]]
    if isinstance(obj.get("exclusive_attachments"), dict):
        for k, v in obj["exclusive_attachments"].items():
            g.exclusive[k if ":" in k else f"{ns}:{k}"] = v if isinstance(v, dict) else {}
    if not g.ammo_full_id and obj.get("caliber"):
        g.calibers = [str(obj["caliber"])]
    elif obj.get("caliber"):
        g.calibers = g.calibers or []
        if str(obj["caliber"]) not in g.calibers:
            g.calibers.append(str(obj["caliber"]))
    g.data_raw = obj