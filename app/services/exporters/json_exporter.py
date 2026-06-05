from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from loguru import logger
from app.core.config import get_settings

settings = get_settings()


class JSONExporter:
    def export(
        self,
        fields:       Dict[str, Any],
        line_items:   List[Dict[str, Any]],
        job_id:       uuid.UUID,
        metadata:     Optional[Dict[str, Any]] = None,
    ) -> Path:
        output = {
            "job_id":    str(job_id),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "fields":    fields,
            "line_items": line_items,
        }
        if metadata:
            output["metadata"] = metadata

        dest = settings.output_path / f"{job_id}.json"
        dest.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
        logger.info(f"JSON export: {dest}")
        return dest
