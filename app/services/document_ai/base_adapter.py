from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseDocumentAIAdapter(ABC):
    """
    Abstract Base Class for Document AI / VLM Fallback adapters.
    Provides standard interface to run model extractions.
    """

    @abstractmethod
    async def extract_fields(
        self,
        raw_text: str,
        document_type: str,
        target_fields: list[str]
    ) -> Dict[str, Any]:
        """
        Execute model inference and return fields.
        Returns format matching:
        {
            "mapped_fields": {
                "field_name": {
                    "value": Any,
                    "confidence": float,
                    "source_evidence": str,
                    "page_number": Optional[int]
                }
            },
            "item_rows": List[Dict]
        }
        """
        pass
