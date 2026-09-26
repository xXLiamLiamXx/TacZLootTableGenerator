"""展开allow的#tag/直接id；通用slot tag递归；adapter/单独；ammo_mod单独。
严格=仅allow展开(含#tag)；启发式=allow+gun data allow_attachment_types反查同type；
跨包=反查/裸id/裸tag可跨ns；弹药=gun.ammo直取，空则caliber/关键字模糊(非严格)。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Set

from models import PackModel, GunInfo, ScanConfig

logger = logging.getLogger("tacz_link")

AMMO_MOD_PREFIX = ("ammo_mod",)
ADAPTER_MARK = "adapter/"


def _norm_tag(ref: str):
    r = ref.strip()
    if r.startswith("#"):
        r = r[1:]
    if ":" in r:
        ns, _, name = r.partition(":")
        return ns.strip(), name.strip().lower()
    return None, r.strip().lower()


def _slot_norm(t: str) -> str:
    return {
        "scope": "scope", "sight": "scope", "red_dot": "scope", "reddot": "scope",
        "scope_scope": "scope", "scope_sight": "scope",
        "muzzle": "muzzle", "silencer": "muzzle", "suppressor": "muzzle", "compensator": "muzzle", "brake": "muzzle",
        "stock": "stock", "grip": "grip", "foregrip": "grip", "vertical_grip": "grip", "angled_grip": "grip",
        "laser": "laser", "laser_sight": "laser",
        "extended_mag": "extended_mag", "mag": "extended_mag", "magazine": "extended_mag", "misc": "misc",
    }.get(t.lower(), t.lower())


def _att_id_exists(packs: dict, fid: str) -> bool:
    for p in packs.values():
        if fid in p.attachments:
            return True
    return False


def _find_att_by_base(packs: dict, rid: str, home_ns: str):
    _ns, _, base = rid.partition(":")
    if not base:
        return None
    pm = packs.get(home_ns)
    if pm:
        if rid in pm.attachments:
            return rid
        for fid in pm.attachments:
            if fid.split(":", 1)[-1] == base:
                return fid
    for ns, pm in packs.items():
        if ns == home_ns:
            continue
        cand = "%s:%s" % (ns, base)
        if cand in pm.attachments:
            return cand
        for fid in pm.attachments:
            if fid.split(":", 1)[-1] == base:
                return fid
    return None


class _Tagger:
    def __init__(self, packs: Dict[str, PackModel]):
        self.packs = packs

    def slot(self, ns: str, name: str):
        pm = self.packs.get(ns)
        if pm:
            store = pm.__dict__.get("_raw_slot_tags", {})
            if name in store:
                return store[name]
            if f"{ns}:{name}" in store:
                return store[f"{ns}:{name}"]
        for p in self.packs.values():
            store = p.__dict__.get("_raw_slot_tags", {})
            if name in store:
                return store[name]
        return None

    def adapter(self, ns: str, name: str):
        pm = self.packs.get(ns)
        if pm:
            store = pm.__dict__.get("_raw_adapter_tags", {})
            if name in store:
                return store[name]
        for p in self.packs.values():
            store = p.__dict__.get("_raw_adapter_tags", {})
            if name in store:
                return store[name]
        return None

    def ammod(self, ns: str, name: str):
        pm = self.packs.get(ns)
        if pm:
            store = pm.__dict__.get("_raw_ammo_tags", {})
            if name in store:
                return store[name]
        return None


def _expand(refs: List[str], home_ns: str, tagger: _Tagger,
            att: Set[str], ammod: Set[str], adap: Set[str], seen: Set, depth: int, cfg=None):
    if depth > 20:
        return
    for ref in refs or []:
        r = (ref or "").strip()
        if not r:
            continue
        low = r.lower()
        if low.startswith("#") and ADAPTER_MARK in low:
            body = r[1:]
            ns, _, rest = body.partition(":")
            ns = ns or home_ns
            aname = rest.split("/", 1)[-1] if "/" in rest else rest
            key = ("adapter", ns, aname)
            if key in seen:
                continue
            seen.add(key)
            raw = tagger.adapter(ns, aname) or []
            for x in raw:
                if x.startswith("#"):
                    _expand([x], ns, tagger, att, ammod, adap, seen, depth + 1, cfg)
                else:
                    adap.add(x if ":" in x else f"{ns}:{x}")
            adap.add(f"{ns}:{aname}")
            continue
        if r.startswith("#"):
            tns, tname = _norm_tag(r)
            tns = tns or home_ns
            key = ("tag", tns, tname)
            if key in seen:
                continue
            seen.add(key)
            raw = tagger.slot(tns, tname)
            if not raw and tns != home_ns:
                raw = tagger.slot(home_ns, tname)
            if not raw and cfg and getattr(cfg, "cross_pack", False):
                for p in tagger.packs.values():
                    store = p.__dict__.get("_raw_slot_tags", {})
                    if tname in store:
                        raw = store[tname]
                        break
            if not raw:
                if tname.startswith(AMMO_MOD_PREFIX):
                    ammod.add(r)
                else:
                    logger.debug("未解析tag %s (home=%s)", r, home_ns)
                continue
            _expand(raw, tns, tagger, att, ammod, adap, seen, depth + 1, cfg)
            continue
        # 直接 id：跨包时裸 id 回退其他包
        rid = r if ":" in r else f"{home_ns}:{r}"
        if cfg and getattr(cfg, "cross_pack", False) and not _att_id_exists(tagger.packs, rid):
            alt = _find_att_by_base(tagger.packs, rid, home_ns)
            if alt:
                rid = alt
        base = rid.split(":", 1)[-1].lower()
        if base.startswith(AMMO_MOD_PREFIX):
            ammod.add(rid)
            continue
        att.add(rid)


def _att_type(packs: Dict[str, PackModel], fid: str, home_ns: str, cross: bool) -> str:
    ns, _, _ = fid.partition(":")
    pm = packs.get(ns)
    if pm and fid in pm.attachments:
        return pm.attachments[fid].type
    if cross:
        for p in packs.values():
            if fid in p.attachments:
                return p.attachments[fid].type
    return ""


def _heuristic_by_types(g: GunInfo, pm: PackModel, packs: Dict[str, PackModel], cfg: ScanConfig, att: Set[str]):
    srcs = list(packs.values()) if cfg.cross_pack else [pm]
    for t in g.allow_attachment_types:
        want = _slot_norm(t)
        for sp in srcs:
            for fid, a in sp.attachments.items():
                at = a.type or ""
                if at == want or at == t:
                    if fid not in att:
                        att.add(fid)


def _ensure_extmag(g: GunInfo, pm: PackModel, packs: Dict[str, PackModel], cfg: ScanConfig, att: Set[str]):
    if not g.extended_mag_amount:
        return
    if any(_att_type(packs, x, g.ns, cfg.cross_pack) == "extended_mag" for x in att):
        return
    srcs = list(packs.values()) if cfg.cross_pack else [pm]
    for sp in srcs:
        for fid, a in sp.attachments.items():
            if a.type == "extended_mag":
                att.add(fid)
                return


def _resolve_ammo(g: GunInfo, pm: PackModel, cfg: ScanConfig, packs: Dict[str, PackModel]):
    if g.ammo_full_id:
        return
    if cfg.strict:
        return
    cands = list(g.calibers or [])
    hint = (g.gun_id or "").lower()
    pool = list(pm.ammos.values())
    if cfg.cross_pack:
        for p in packs.values():
            pool += list(p.ammos.values())

    def score(am):
        s = 0
        cal = (am.caliber or "").lower()
        aid = am.ammo_id.lower()
        for c in cands:
            c = c.lower()
            if c and (c in cal or c in aid or cal in c):
                s += 3
        kws = ["12g", "12ga", "9mm", "45acp", ".45", "556", "5.56", "762", "7.62", "762x39", "762x51", "50bmg", "shotgun", "rifle", "pistol"]
        for kw in kws:
            if kw in hint and (kw in aid or kw in cal):
                s += 2
        return s

    best = max(pool, key=score) if pool else None
    if best and score(best) > 0:
        g.ammo_full_id = best.full_id
        return
    if pm.ammos:
        g.ammo_full_id = next(iter(pm.ammos.values())).full_id


def link(packs: Dict[str, PackModel], cfg: ScanConfig) -> Dict[str, PackModel]:
    tagger = _Tagger(packs)
    for ns, pm in packs.items():
        for gname, g in pm.guns.items():
            att: Set[str] = set()
            ammod: Set[str] = set()
            adap: Set[str] = set()
            _expand(list(g.allow_list or []), ns, tagger, att, ammod, adap, set(), 0, cfg)

            for ex in g.exclusive.keys():
                att.add(ex)

            if not cfg.strict:
                if cfg.heuristic and g.allow_attachment_types:
                    _heuristic_by_types(g, pm, packs, cfg, att)
                _ensure_extmag(g, pm, packs, cfg, att)

            g.resolved_attachments = sorted(att)
            g.resolved_ammo_mods = sorted(ammod)
            g.resolved_adapters = sorted(adap)
            _resolve_ammo(g, pm, cfg, packs)
    return packs