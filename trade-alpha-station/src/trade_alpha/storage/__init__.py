"""M04 storage regions, real Parquet snapshots, and point-in-time queries."""

from .boundary import OperationalBoundaryError, assert_operational_table, scan_gold_dependencies
from .engines import StorageDependencyError
from .lake import PublicationFault, SnapshotIntegrityError, StorageLake, observations_to_rows
from .models import REGION_CONTRACTS, BitemporalObservation, MoneyObservation, SnapshotManifest

__all__ = [
    "BitemporalObservation",
    "MoneyObservation",
    "OperationalBoundaryError",
    "PublicationFault",
    "REGION_CONTRACTS",
    "SnapshotIntegrityError",
    "SnapshotManifest",
    "StorageDependencyError",
    "StorageLake",
    "assert_operational_table",
    "observations_to_rows",
    "scan_gold_dependencies",
]
