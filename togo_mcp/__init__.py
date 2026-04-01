import togo_mcp.api_tools as api_tools  # noqa: F401 (registers @mcp.tool handlers)
import togo_mcp.rdf_portal as rdf_portal  # noqa: F401 (registers @mcp.tool handlers)

from .server import mcp as mcp
from .togoid import (
    convertId as convertId,
)
from .togoid import (
    countId as countId,
)
from .togoid import (
    getAllDataset as getAllDataset,
)
from .togoid import (
    getAllRelation as getAllRelation,
)
from .togoid import (
    getDataset as getDataset,
)
from .togoid import (
    getDescription as getDescription,
)
from .togoid import (
    getRelation as getRelation,
)
