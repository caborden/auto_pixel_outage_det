"""Read-only local review of delayed watcher logs, state and evidence."""
import argparse
from collections import deque
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import re
import shutil
import time
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("America/Chicago")
PNG = re.compile(r'iog_[1-8]/[0-9]+-(?:T[1-8]|reconfig)\.png')


def when(stamp):
    return datetime.fromtimestamp(stamp, LOCAL).strftime("%b %d %H:%M:%S %Z") if stamp else "unknown"


def evidence(root, group, event):
    name = f'iog_{group}/' + Path(event.get("image", "")).name
    if PNG.fullmatch(name) and (root / name).is_file():
        return f'<a href="/{name}"><img loading="lazy" src="/{name}" alt="View maps"></a>'
    return ""


def page(root, query):
    def number(key, default, limit):
        return min(max(int(query.get(key, [default])[0]), 0), limit)
    group = number("group", 0, 8)
    tile = number("tile", 0, 8)
    hours = max(1, number("hours", 24, 168))
    end_text = query.get("end", [""])[0]
    end = datetime.fromisoformat(end_text).replace(tzinfo=LOCAL).timestamp() if end_text else time.time()
    start = end - 3600 * hours
    events, audit, active = [], deque(maxlen=80), []
    holds, legacy = {}, {}
    def record(stamp, g, t, label, info="", image=None):
        if not tile or not t or t == tile:
            events.append((stamp, g, t, label, info, image or {}))
    for path in sorted(root.glob("watch-*.log")):
        if path.stat().st_mtime < start:
            continue
        with path.open(errors="replace") as stream:
            for line in stream:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                g = r.get("io_group")
                stamp = r.get("stamp") or r.get("observed", 0)
                if not g or group and group != g or not start <= stamp <= end:
                    continue
                status = r.get("status")
                if status == "likely_reconfiguration" and not holds.get(g):
                    record(stamp, g, 0, "Likely reconfiguration · first seen", str(r.get("iog_mean_ratio")), r)
                    holds[g] = True
                elif status == "reconfiguration_cleared":
                    record(stamp, g, 0, "Reconfiguration cleared")
                    holds[g] = False
                for e in r.get("events", []):
                    record(stamp, g, e["tile"], e.get("kind", "localized") + " " + e["event"],
                           str(e.get("chips") or e.get("retained") or ""), e)
                if status == "evaluated" and "tiles" not in r:
                    current = {b["tile"] for b in r.get("broad_tile_losses", [])}
                    for t in current - legacy.get(g, set()):
                        record(stamp, g, t, "Historical broad loss · first seen")
                    for t in legacy.get(g, set()) - current:
                        record(stamp, g, t, "Historical broad flag ended")
                    legacy[g] = current
                if group and tile:
                    d = next((a for a in r.get("tiles", []) if a["tile"] == tile), None)
                    audit.append((stamp, status, d or r.get("reason") or r.get("tile_mean_ratios"),
                                  [c for c in r.get("candidates", []) if c["tile"] == tile]))
    for g in range(1, 9):
        path = root / f"iog_{g}" / "state.json"
        if (group and g != group) or not path.exists():
            continue
        s = json.loads(path.read_text())
        incident_list = []
        for t in s.get("active", []):
            if tile and t != tile:
                continue
            event = s.get("incidents", {}).get(str(t), {})
            incident_list.append(f'T{t} {escape(event.get("kind","localized"))} since {when(event.get("since"))} '
                                 + evidence(root, g, event))
        if s.get("reconfig_anchor") is not None:
            incident_list.append("Likely reconfiguration")
        active.append(f'<tr><td>{g}</td><td>{when(s["stamp"])}</td><td>{" · ".join(incident_list) or "—"}</td></tr>')
    def select(key, selected):
        options = ''.join(f'<option value="{n}" {"selected" if n == selected else ""}>{n or "All"}</option>' for n in range(9))
        return f'<label>{key.capitalize()} <select name="{key}">{options}</select></label>'
    table = []
    for stamp,g,t,label,info,e in sorted(events, key=lambda row:row[0], reverse=True):
        table.append(f'<tr><td>{when(stamp)}</td><td>{g}</td><td>{t or "—"}</td><td>{escape(label)}</td>'
                     f'<td>{escape(info)}</td><td>{evidence(root,g,e)}</td></tr>')
    traces = ''.join(f'<details><summary>{when(ts)} · {escape(str(status))}</summary><pre>{escape(json.dumps([d,c], indent=2))}</pre></details>'
                     for ts,status,d,c in reversed(audit))
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Pixel outage review</title><style>body{{font:16px system-ui;color:#193448;max-width:1100px;margin:32px auto;padding:0 16px}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:8px;text-align:left;border-bottom:1px solid #ddd}}
img{{height:80px}}label{{display:inline-block;margin:4px 12px 4px 0}}summary{{cursor:pointer}}</style>
<h1>Pixel outage review</h1><form>{select("group",group)}{select("tile",tile)}
<label>Hours <input type="number" min="1" max="168" name="hours" value="{hours}"></label>
<label>Through (Chicago) <input type="datetime-local" name="end" value="{escape(end_text,quote=True)}"></label>
<button>Show results</button></form><h2>Current watcher state</h2>
<table><tr><th>IOG</th><th>Last processed</th><th>Active incidents or hold</th></tr>{''.join(active)}</table>
<h2>Timeline</h2><p>{when(start)} – {when(end)}</p>
<table><tr><th>Time</th><th>IOG</th><th>Tile</th><th>Event</th><th>Chips or ratios</th><th>Maps</th></tr>
{''.join(table)}</table>{'' if table else '<p>No recorded transitions in this selection.</p>'}
<h2>Decision audit</h2>{traces or 'Select an IO group and tile to inspect up to 80 recent records in this interval.'}"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, default=Path("runs/delayed"))
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            if url.path == "/":
                try:
                    content = page(args.work, parse_qs(url.query)).encode()
                except (ValueError, OverflowError):
                    self.send_error(400, "Invalid time or filter")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(content)
            elif PNG.fullmatch(url.path[1:]) and (args.work / url.path[1:]).is_file():
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                with (args.work / url.path[1:]).open("rb") as stream:
                    shutil.copyfileobj(stream, self.wfile)
            else:
                self.send_error(404)
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
