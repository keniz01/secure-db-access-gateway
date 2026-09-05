"""Request-time construction of tenant-bound SQL services."""

from __future__ import annotations

from auth import Principal
from punq import Container

from dependencies.dependency_container import setup_container
from services.abstract_sql_query_service import ISqlQueryService
from services.tenant_database_resolver import TenantDatabaseConfig, TenantDatabaseResolver


class TenantServiceProvider:
    """Cache services only by trusted organisation and logical database."""

    def __init__(self, resolver: TenantDatabaseResolver) -> None:
        self._resolver = resolver
        self._containers: dict[tuple[str, str], Container] = {}

    def resolve(
        self,
        principal: Principal,
        database_id: str | None,
    ) -> tuple[TenantDatabaseConfig, ISqlQueryService]:
        """Return the service bound to the principal and logical database."""
        binding = self._resolver.resolve(principal, database_id)
        cache_key = (binding.org_id, binding.database_id)
        container = self._containers.get(cache_key)
        if container is None:
            container = setup_container(
                binding.connection_string,
                data_schema=binding.data_schema,
                metadata_schema=binding.metadata_schema,
                tenant_org_id=binding.org_id,
                tenant_database_id=binding.database_id,
            )
            self._containers[cache_key] = container
        return binding, container.resolve(ISqlQueryService)
