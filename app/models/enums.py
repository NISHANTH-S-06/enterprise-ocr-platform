import enum

class UserRole(str, enum.Enum):
    SUPERADMIN = "superadmin"; ADMIN = "admin"
    MANAGER = "manager";       OPERATOR = "operator"; REVIEWER = "reviewer"; VIEWER = "viewer"

class UserStatus(str, enum.Enum):
    ACTIVE = "active"; INACTIVE = "inactive"; SUSPENDED = "suspended"; PENDING = "pending"

class FileCategory(str, enum.Enum):
    PDF = "pdf"; IMAGE = "image"

class FileStatus(str, enum.Enum):
    PENDING = "pending"; VALIDATED = "validated"; PROCESSING = "processing"
    DONE = "done";       FAILED = "failed";       QUARANTINE = "quarantine"

class JobStatus(str, enum.Enum):
    QUEUED = "queued"; PROCESSING = "processing"; COMPLETED = "completed"
    FAILED = "failed"; CANCELLED = "cancelled";   RETRYING = "retrying"

class BatchStatus(str, enum.Enum):
    QUEUED = "queued"; PROCESSING = "processing"; COMPLETED = "completed"
    FAILED = "failed"; PARTIAL_FAILED = "partial_failed"; CANCELLED = "cancelled"

class ExtractionStatus(str, enum.Enum):
    PENDING = "pending"; EXTRACTED = "extracted"; VALIDATED = "validated"
    NEEDS_REVIEW = "needs_review"; REJECTED = "rejected"

class LogLevel(str, enum.Enum):
    DEBUG = "debug"; INFO = "info"; WARNING = "warning"; ERROR = "error"; CRITICAL = "critical"

class LogEventType(str, enum.Enum):
    AUTH = "auth"; UPLOAD = "upload"; OCR_JOB = "ocr_job"; EXTRACTION = "extraction"
    TEMPLATE = "template"; USER_MGMT = "user_mgmt"; SYSTEM = "system"
    WEBHOOK = "webhook"; FORM_FILL = "form_fill"; DATA_EXPORT = "data_export"

class TemplateStatus(str, enum.Enum):
    DRAFT = "draft"; ACTIVE = "active"; ARCHIVED = "archived"

class DocumentType(str, enum.Enum):
    INVOICE = "invoice"; PURCHASE_ORDER = "purchase_order"; SALES_ORDER = "sales_order"
    RECEIPT = "receipt"; CREDIT_NOTE = "credit_note"; DELIVERY_CHALLAN = "delivery_challan"
    QUOTATION = "quotation"; COMPANY_REGISTRATION = "company_registration"
    CUSTOM = "custom"; UNKNOWN = "unknown"

class ExportFormat(str, enum.Enum):
    PDF = "pdf"; EXCEL = "excel"; JSON = "json"

class FillJobStatus(str, enum.Enum):
    PENDING = "pending"; PROCESSING = "processing"
    COMPLETED = "completed"; FAILED = "failed"
