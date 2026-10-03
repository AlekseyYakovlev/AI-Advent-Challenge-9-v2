"""Knowledge base limits and chunking constants defined in one place."""

MAX_FILE_BYTES: int = 50 * 1024 * 1024
MAX_FILES: int = 10
MAX_TOTAL_BYTES: int = 100 * 1024 * 1024
MAX_REQUEST_BYTES: int = MAX_TOTAL_BYTES + 1024 * 1024
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".txt", ".md"})
MAX_KB_NAME_CHARS: int = 200

MIN_CHUNK_SIZE: int = 100
MAX_EMBED_CHARS: int = 2000
DEFAULT_CHUNK_SIZE: int = 1000
DEFAULT_CHUNK_OVERLAP: int = 150
STRUCT_SUBSPLIT_OVERLAP: int = 100

EMBED_BATCH_SIZE: int = 32
PROGRESS_THROTTLE_SECONDS: float = 0.5

SEARCH_DEFAULT_TOP_K: int = 5
SEARCH_MAX_TOP_K: int = 20
MAX_QUERY_CHARS: int = 2000
