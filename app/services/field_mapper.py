"""
Maps raw extracted_fields (from ExtractedData) through a template's
mapping_config + overrides to produce a flat {field: value} dict
ready for export engines.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from loguru import logger


class FieldMapper:
    def map(
        self,
        extracted_fields: Dict[str, Any],
        mapped_output:    Dict[str, Any],
        template_schema:  Dict[str, Any],
        mapping_config:   Dict[str, Any],
        overrides:        Dict[str, Any],
    ) -> Tuple[Dict[str, Any], List[str]]:
        """
        Returns (final_values, warnings).

        Priority: override > mapped_output > direct extracted match > alias match > default
        """
        final:    Dict[str, Any] = {}
        warnings: List[str]     = []

        # Build alias → canonical lookup
        alias_map: Dict[str, str] = {}
        for fname, fdef in template_schema.items():
            if isinstance(fdef, dict):
                for alias in fdef.get("aliases", []):
                    alias_map[alias.lower()] = fname

        for field_name, field_def in template_schema.items():
            fdef = field_def if isinstance(field_def, dict) else {}

            # 1. Manual override
            if field_name in overrides:
                final[field_name] = overrides[field_name]
                continue

            # 2. Pre-mapped output (already processed by extractor)
            if field_name in mapped_output:
                final[field_name] = mapped_output[field_name]
                continue

            # 3. Custom mapping config
            src = mapping_config.get(field_name)
            if src and src in extracted_fields:
                fv = extracted_fields[src]
                final[field_name] = fv.get("value", fv) if isinstance(fv, dict) else fv
                continue

            # 4. Direct name match in extracted_fields
            if field_name in extracted_fields:
                fv = extracted_fields[field_name]
                final[field_name] = fv.get("value", fv) if isinstance(fv, dict) else fv
                continue

            # 5. Alias match
            matched = False
            for ext_key, ext_val in extracted_fields.items():
                if alias_map.get(ext_key.lower()) == field_name:
                    final[field_name] = ext_val.get("value", ext_val) if isinstance(ext_val, dict) else ext_val
                    matched = True
                    break

            if matched:
                continue

            # 6. Default value
            if "default" in fdef:
                final[field_name] = fdef["default"]
                continue

            # 7. Required but missing
            if fdef.get("required"):
                warnings.append(f"Required field '{field_name}' not found in extracted data")

        # Merge any extracted fields NOT in the schema (passthrough)
        for ext_key, ext_val in extracted_fields.items():
            if ext_key not in final and ext_key != "line_items":
                val = ext_val.get("value", ext_val) if isinstance(ext_val, dict) else ext_val
                final[ext_key] = val

        logger.debug(f"FieldMapper: {len(final)} fields mapped, {len(warnings)} warnings")
        return final, warnings
