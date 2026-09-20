#!/usr/bin/env python3
"""노션 페이지(+하위 블록·하위 페이지·이미지)를 Markdown으로 내려받는다. 토큰은 secrets.yaml 의 notion_token."""
import json, sys, re, pathlib, urllib.request, urllib.error
ROOT = pathlib.Path("/mnt/ssd/icepredict"); OUT = ROOT / "dataset/team/notion"; OUT.mkdir(parents=True, exist_ok=True)
tok = next(l.split(":", 1)[1].strip() for l in open(ROOT / "secrets.yaml") if l.startswith("notion_token:"))
H = {"Authorization": f"Bearer {tok}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"}
def api(path):
    req = urllib.request.Request("https://api.notion.com/v1" + path, headers=H)
    with urllib.request.urlopen(req, timeout=30) as r: return json.load(r)
def rich(rt): return "".join(t.get("plain_text", "") for t in rt)
def children(bid):
    out, cur = [], None
    while True:
        d = api(f"/blocks/{bid}/children?page_size=100" + (f"&start_cursor={cur}" if cur else ""))
        out += d["results"]
        if not d.get("has_more"): return out
        cur = d["next_cursor"]
imgs = 0
def walk(bid, depth=0, lines=None):
    global imgs
    lines = [] if lines is None else lines
    for b in children(bid):
        t = b["type"]; v = b.get(t, {}); ind = "  " * depth
        txt = rich(v.get("rich_text", [])) if isinstance(v, dict) else ""
        if t.startswith("heading_"): lines.append(f"\n{'#' * int(t[-1])} {txt}")
        elif t == "paragraph": lines.append(f"{ind}{txt}")
        elif t in ("bulleted_list_item", "numbered_list_item"): lines.append(f"{ind}- {txt}")
        elif t == "to_do": lines.append(f"{ind}- [{'x' if v.get('checked') else ' '}] {txt}")
        elif t == "toggle": lines.append(f"{ind}▸ {txt}")
        elif t == "quote": lines.append(f"{ind}> {txt}")
        elif t == "callout": lines.append(f"{ind}💡 {txt}")
        elif t == "code": lines.append(f"```{v.get('language','')}\n{txt}\n```")
        elif t == "divider": lines.append("---")
        elif t == "table":
            rows = children(b["id"])
            for i, r in enumerate(rows):
                cells = [rich(c) for c in r["table_row"]["cells"]]; lines.append("| " + " | ".join(cells) + " |")
                if i == 0: lines.append("|" + "---|" * len(cells))
            continue
        elif t == "image":
            src = v.get("file", v.get("external", {})).get("url", ""); imgs += 1
            name = f"img_{imgs:03d}" + (re.search(r"\.(png|jpe?g|gif|webp)", src.split("?")[0], re.I).group(0).lower() if re.search(r"\.(png|jpe?g|gif|webp)", src.split("?")[0], re.I) else ".png")
            try: urllib.request.urlretrieve(src, OUT / name)
            except Exception as e: name += f" (다운로드 실패 {e})"
            lines.append(f"{ind}![{rich(v.get('caption', []))}]({name})")
        elif t == "child_page":
            lines.append(f"\n{'#' * (depth + 2)} [하위 페이지] {v.get('title')}"); walk(b["id"], depth + 1, lines); continue
        elif t == "child_database":
            lines.append(f"{ind}[데이터베이스] {v.get('title')}")
        elif t in ("file", "pdf", "video", "embed", "bookmark", "link_preview"):
            src = v.get("file", v.get("external", {})).get("url", v.get("url", "")); lines.append(f"{ind}[{t}] {src.split('?')[0]}")
        elif t == "column_list" or t == "column":
            walk(b["id"], depth, lines); continue
        elif t == "synced_block":
            walk(b["id"], depth, lines); continue
        else:
            lines.append(f"{ind}[{t}] {txt}")
        if b.get("has_children") and t not in ("child_page", "table"):
            walk(b["id"], depth + 1, lines)
    return lines
pid = sys.argv[1]
page = api(f"/pages/{pid}")
title = ""
for k, pv in page.get("properties", {}).items():
    if pv.get("type") == "title": title = rich(pv["title"])
lines = [f"# {title}", ""] + walk(pid)
md = "\n".join(lines); (OUT / "page.md").write_text(md)
print(f"제목: {title}\n블록 줄 수: {len(lines)}  이미지: {imgs}장  → {OUT}/page.md")
