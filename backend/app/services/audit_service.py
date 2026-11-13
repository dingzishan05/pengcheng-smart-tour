from __future__ import annotations

from app.repositories.knowledge_repo import AuditLogRepository


class AuditService:
    def __init__(self, audit_repo: AuditLogRepository) -> None:
        self.audit_repo = audit_repo

    async def log(
        self,
        operator_id: int,
        action: str,
        entity_type: str,
        entity_id: int,
        before_value: dict | None = None,
        after_value: dict | None = None,
    ):
        return await self.audit_repo.append(
            operator_id=operator_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            before_value=before_value,
            after_value=after_value,
        )