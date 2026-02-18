"""
Metadata utilities: normalization and YAML front matter building.
"""
from datetime import datetime, timezone
from typing import Dict, Any, List


def to_iso8601(dt_str: str) -> str:
    try:
        # Handle compact yyyymmdd format from yt-dlp
        s = str(dt_str).strip()
        if len(s) == 8 and s.isdigit():
            dt = datetime(int(s[0:4]), int(s[4:6]), int(s[6:8]), tzinfo=timezone.utc)
        else:
            # Best-effort parse common ISO formats
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        # Fallback to now if unknown
        dt = datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_tags(tags: List[str] | None) -> List[str]:
    if not tags:
        return []
    out = []
    seen = set()
    for t in tags:
        if not isinstance(t, str):
            continue
        k = t.strip().lower().replace(" ", "-")
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out[:5]


def normalize_metadata(source_type: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    meta = {
        "title": str(raw.get("title") or raw.get("name") or "Untitled").strip(),
        "source_type": source_type,
        "source_url": raw.get("source_url") or raw.get("url") or "",
        "author": raw.get("author") or raw.get("channel") or "",
        # allow podcast field names
        "published_at": raw.get("published_at") or raw.get("upload_date") or raw.get("pub_date") or "",
        "language": raw.get("language") or "",
        "description": (raw.get("description") or "").strip(),
        "tags": raw.get("tags") or [],
        "duration": raw.get("duration") or "",
        "fetched_at": raw.get("fetched_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "pipeline_version": raw.get("pipeline_version") or "bodhiflow-0.2",
    }
    if meta["published_at"]:
        meta["published_at"] = to_iso8601(str(meta["published_at"]))
    meta["fetched_at"] = to_iso8601(str(meta["fetched_at"]))
    meta["tags"] = normalize_tags(meta["tags"])
    # Normalize duration: if seconds (int/float), convert to HH:MM:SS
    dur = meta.get("duration")
    try:
        if isinstance(dur, (int, float)) or (isinstance(dur, str) and dur.isdigit()):
            total = int(float(dur))
            hours = total // 3600
            minutes = (total % 3600) // 60
            seconds = total % 60
            meta["duration"] = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except Exception:
        pass
    return meta


def _yaml_escape(value: str) -> str:
    # Simple safe scalar: quote only if needed
    if value is None:
        return ""  # treat as empty
    s = str(value)
    if any(c in s for c in [":", "\n", "#", "-", "\"", "'"]):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def build_yaml_front_matter(metadata: Dict[str, Any]) -> str:
    lines = ["---"]
    def put(k: str, v: Any):
        if v is None or v == "":
            return
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                lines.append(f"  - {_yaml_escape(item)}")
        else:
            lines.append(f"{k}: {_yaml_escape(v)}")

    put("title", metadata.get("title"))
    put("source_type", metadata.get("source_type"))
    put("source_url", metadata.get("source_url"))
    put("author", metadata.get("author"))
    put("published_at", metadata.get("published_at"))
    put("fetched_at", metadata.get("fetched_at"))
    put("language", metadata.get("language"))
    put("style", metadata.get("style"))
    put("description", metadata.get("description"))
    put("tags", metadata.get("tags"))
    put("duration", metadata.get("duration"))
    put("transcript_chars", metadata.get("transcript_chars"))
    put("model_used", metadata.get("model_used"))
    put("pipeline_version", metadata.get("pipeline_version"))
    lines.append("---\n")
    return "\n".join(lines)


