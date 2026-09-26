"""文件夹/zip枚举json；去//注释；按 data/<ns>/... 定位 stage。
提供: strip_json_comments, iter_folder_json, iter_zip_json, discover_game_root
record格式: {ns, rel, stage, name, text, source_label}
  rel: 相对包根 linux斜杠, 例如 data/tacz/tacz_tags/attachments/allow_attachments/aa12.json
  stage: index/data/tacz_tags/tags/其他
  name: 文件名无后缀
"""
from __future__ import annotations
import json
import re
import zipfile
from pathlib import Path
from typing import Iterable, Iterator, List, Tuple

_COMMENT_RE = re.compile(r"^\s*//.*$|(?<=^)\s*//.*", flags=re.MULTILINE)


def strip_json_comments(text: str) -> str:
    if text is None:
        return "{}"
    # 去掉整行 // 注释；不处理字符串内的 //
    out_lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("//"):
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def _read_json_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except Exception:
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""


def _classify(rel: str):
    """返回 (ns, stage, subdirs[], name) ；只处理 data/ 下"""
    p = Path(rel.replace("\\", "/"))
    parts = [x.lower() for x in p.parts]
    if not parts or parts[0] != "data" or len(parts) < 2:
        return None
    ns = parts[1]
    rest = parts[2:]
    if not rest:
        return None
    stage = rest[0]
    sub = rest[1:-1]
    name = p.stem
    return ns, stage, sub, name


def _emit_json_file(path: Path, rel_root_len: int, source_label: str) -> dict | None:
    rel = str(path).replace("\\", "/")
    # 仅data下
    marker = "/data/"
    idx = rel.lower().find(marker)
    if idx < 0:
        return None
    rel_after = rel[idx + len(marker):]
    cls = _classify("data/" + rel_after)
    if not cls:
        return None
    ns, stage, sub, name = cls
    text = _read_json_text(path)
    if not text.strip():
        return None
    return {"ns": ns, "rel": "data/" + rel_after, "stage": stage,
            "sub": sub, "name": name, "text": text, "source_label": source_label}


def iter_folder_json(folder: Path) -> Iterator[dict]:
    folder = Path(folder)
    for path in folder.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".json":
            rec = _emit_json_file(path, 0, folder.name)
            if rec:
                yield rec


def iter_zip_json(zippath: Path) -> Iterator[dict]:
    zippath = Path(zippath)
    try:
        zf = zipfile.ZipFile(zippath)
    except Exception:
        return
    with zf:
        for name in zf.namelist():
            if not name.lower().endswith(".json"):
                continue
            low = name.lower()
            if "/data/" not in low:
                continue
            rel_after = name[low.index("/data/") + len("/data/"):]
            cls = _classify("data/" + rel_after)
            if not cls:
                continue
            ns, stage, sub, fname = cls
            try:
                data = zf.read(name)
                text = data.decode("utf-8-sig", errors="ignore")
            except Exception:
                continue
            if not text.strip():
                continue
            yield {"ns": ns, "rel": "data/" + rel_after, "stage": stage,
                   "sub": sub, "name": fname, "text": text, "source_label": zippath.stem}


def discover_game_root(game_root: Path) -> List[Path]:
    """游戏根/版本根：返回各枪包根。支持
    - <root>/tacz/<packfolder>
    - <root>/tacz/<pack>.zip
    - 直接给到 <root> 且内含 data/<ns> 的文件夹
    """
    game_root = Path(game_root)
    out: List[Path] = []
    tacz_dir = None
    for cand in [game_root / "tacz", game_root]:
        if cand.is_dir():
            tacz_dir = cand
            break
    if tacz_dir is None:
        return out
    # 文件夹子包
    for child in sorted(tacz_dir.iterdir()):
        if child.is_dir():
            # 含 data/<任何ns> 视为包根
            if any(child.rglob("gunpack.meta.json")) or any((child / "data").rglob("index")):
                out.append(child)
        elif child.suffix.lower() == ".zip":
            out.append(child)
    # 若game_root本身就是包根
    if not out and (any(game_root.rglob("gunpack.meta.json")) or (game_root / "data").exists()):
        out.append(game_root)
    return out