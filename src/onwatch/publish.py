from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .io import write_json


def rebuild_feed(root: Path, status: dict[str, Any] | None = None) -> dict[str, Any]:
    event_dir = root / "events"
    events = []
    if event_dir.exists():
        for path in event_dir.glob("*.json"):
            try:
                event = json.loads(path.read_text("utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise ValueError(f"cannot read stored event {path}: {error}") from error
            if (
                event.get("schema") != "our-notes-update-event/1"
                or event.get("id") != path.stem
                or not re.fullmatch(r"[A-Za-z0-9._-]+", str(event.get("id", "")))
            ):
                raise ValueError(f"stored event metadata does not match {path}")
            events.append(event)
    events.sort(key=lambda item: (item.get("detectedAt", ""), item.get("id", "")), reverse=True)
    index = {
        "schema": "our-notes-event-index/1",
        "count": len(events),
        "events": [
            {
                "id": item["id"],
                "server": item["server"],
                "detectedAt": item["detectedAt"],
                "severity": item["severity"],
                "summary": item["summary"],
                "url": f"events/{item['id']}.json",
            }
            for item in events
        ],
    }
    latest = (
        {"latest": events[0]["id"], "url": f"events/{events[0]['id']}.json"}
        if events
        else {"latest": None, "url": None}
    )
    site_index = dict(index)
    site_index["events"] = [
        {**item, "url": f"{item['id']}.json"} for item in index["events"]
    ]
    status_value = status or {"lastProbe": None, "lastSuccessfulScan": None, "latestEvent": latest["latest"], "source": {}}
    status_value = dict(status_value)
    status_value["latestEvent"] = latest["latest"]

    feed_root = root / "feed"
    feed_root.mkdir(parents=True, exist_ok=True)
    write_json(feed_root / "index.json", index)
    write_json(feed_root / "latest.json", latest)
    for event in events:
        write_json(feed_root / "events" / f"{event['id']}.json", event)

    site_api = root / "site" / "api"
    write_json(site_api / "latest.json", latest)
    write_json(site_api / "events" / "index.json", site_index)
    write_json(site_api / "status.json", status_value)
    for event in events:
        write_json(site_api / "events" / f"{event['id']}.json", event)
        report = root / "reports" / f"{event['id']}.md"
        if report.is_file():
            report_target = root / "site" / "reports" / report.name
            report_target.parent.mkdir(parents=True, exist_ok=True)
            report_target.write_bytes(report.read_bytes())
        page = root / "site" / "events" / event["id"] / "index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(_event_html(event["id"]), encoding="utf-8")
    (root / "site").mkdir(parents=True, exist_ok=True)
    (root / "site" / "index.html").write_text(_index_html(), encoding="utf-8")
    atom = _atom_feed(events, os.environ.get("ONWATCH_PUBLIC_BASE_URL", ""))
    (root / "feed.xml").write_bytes(atom)
    (root / "site" / "feed.xml").write_bytes(atom)
    write_json(root / "state" / "status.json", status_value)
    return {"index": index, "latest": latest, "status": status_value}


def _index_html() -> str:
    return """<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Our Notes Update Feed</title><link rel="alternate" type="application/atom+xml" href="feed.xml">
<main><h1>Our Notes Update Feed</h1><p id="latest">Loading latest update…</p><p><a href="api/events/index.json">Machine-readable event index</a> · <a href="feed.xml">Atom feed</a></p><h2>Timeline</h2><ol id="timeline"></ol></main>
<script>
Promise.all([fetch('api/latest.json').then(r=>r.json()),fetch('api/events/index.json').then(r=>r.json())]).then(([x,index])=>{
  const p=document.querySelector('#latest');
  p.textContent=x.latest ? `Latest update: ${x.latest}` : 'No update events yet.';
  if(x.url){const a=document.createElement('a');a.href=`api/${x.url}`;a.textContent=' Open event';p.append(a);}
  const timeline=document.querySelector('#timeline');
  for(const event of index.events){const li=document.createElement('li');const a=document.createElement('a');a.href=`events/${encodeURIComponent(event.id)}/`;a.textContent=`${event.detectedAt} · ${event.severity} · ${event.id}`;li.append(a);timeline.append(li);}
}).catch(()=>{document.querySelector('#latest').textContent='Update feed is temporarily unavailable.'});
</script></html>
"""


def _event_html(event_id: str) -> str:
    return f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Our Notes update</title><main><p><a href="../../">All updates</a></p><h1 id="title">Loading update…</h1><p id="summary"></p><p><a href="../../api/events/{event_id}.json">Event JSON</a> · <a href="../../reports/{event_id}.md">Markdown report</a></p></main>
<script>
fetch('../../api/events/{event_id}.json').then(r=>r.json()).then(e=>{{
  document.querySelector('#title').textContent=`${{e.server}} update · ${{e.severity}}`;
  document.querySelector('#summary').textContent=`${{e.detectedAt}} · assets +${{e.summary.assetsAdded}} ~${{e.summary.assetsChanged}} -${{e.summary.assetsRemoved}} · charts +${{e.summary.chartsAdded}} ~${{e.summary.chartsChanged}} -${{e.summary.chartsRemoved}}`;
}}).catch(()=>{{document.querySelector('#title').textContent='Update details are temporarily unavailable.'}});
</script></html>
"""


def _atom_feed(events: list[dict[str, Any]], base_url: str) -> bytes:
    namespace = "http://www.w3.org/2005/Atom"
    ET.register_namespace("", namespace)
    root = ET.Element(f"{{{namespace}}}feed")
    ET.SubElement(root, f"{{{namespace}}}id").text = "urn:our-notes-update-feed"
    ET.SubElement(root, f"{{{namespace}}}title").text = "Our Notes Update Feed"
    updated = events[0]["detectedAt"] if events else "1970-01-01T00:00:00Z"
    ET.SubElement(root, f"{{{namespace}}}updated").text = updated
    if base_url:
        ET.SubElement(
            root,
            f"{{{namespace}}}link",
            {"rel": "self", "href": f"{base_url.rstrip('/')}/feed.xml"},
        )
    for event in events:
        entry = ET.SubElement(root, f"{{{namespace}}}entry")
        ET.SubElement(entry, f"{{{namespace}}}id").text = f"urn:our-notes-update:{event['id']}"
        ET.SubElement(entry, f"{{{namespace}}}title").text = (
            f"Our Notes data update · {event['server']} · {event['severity']}"
        )
        ET.SubElement(entry, f"{{{namespace}}}updated").text = event["detectedAt"]
        ET.SubElement(entry, f"{{{namespace}}}summary").text = _event_summary(event)
        path = f"api/events/{event['id']}.json"
        href = f"{base_url.rstrip('/')}/{path}" if base_url else path
        ET.SubElement(entry, f"{{{namespace}}}link", {"rel": "alternate", "href": href})
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _event_summary(event: dict[str, Any]) -> str:
    counts = event["summary"]
    return (
        f"Assets +{counts['assetsAdded']} ~{counts['assetsChanged']} -{counts['assetsRemoved']}; "
        f"Master +{counts['masterAdded']} ~{counts['masterChanged']} -{counts['masterRemoved']}; "
        f"charts +{counts['chartsAdded']} ~{counts['chartsChanged']} -{counts['chartsRemoved']}"
    )
