from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.v1.repositories.base import StoreHost
from app.v1.states import MAILBOX_STATE_MACHINE, MailboxStatus
from app.v1.storage_support import new_id, utcnow


class MailboxService:
    """Durable Project communication and delivery lifecycle.

    This service does not schedule Tasks or invoke an Agent. Callers bind a pending
    message to one consumer, then record receipt and one explicit terminal result.
    """

    def __init__(self, store: StoreHost):
        self.store = store

    def append(
        self,
        db: sqlite3.Connection,
        project_id: str,
        event_type: str,
        task_id: str | None,
        run_id: str | None,
        payload: dict[str, Any],
    ) -> int:
        event = db.execute(
            "SELECT id FROM v1_events WHERE project_id=? AND type=? AND task_id IS ? AND run_id IS ? "
            "ORDER BY id DESC LIMIT 1",
            (project_id, event_type, task_id, run_id),
        ).fetchone()
        cursor = db.execute(
            "INSERT INTO v1_project_mailbox("
            "project_id,event_id,event_type,task_id,run_id,payload_json,state,created_at,delivery_count"
            ") VALUES(?,?,?,?,?,?,'pending',?,0)",
            (
                project_id,
                event[0] if event else None,
                event_type,
                task_id,
                run_id,
                json.dumps(payload, ensure_ascii=False, default=str),
                utcnow(),
            ),
        )
        return int(cursor.lastrowid)

    def list(
        self,
        project_id: str,
        *,
        state: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        MailboxStatus(state)
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM v1_project_mailbox WHERE project_id=? AND state=? ORDER BY id LIMIT ?",
                (project_id, state, limit),
            ).fetchall()
        return [self.store._decode(dict(row), "payload_json") for row in rows]  # type: ignore[misc]

    def deliver_pending(
        self,
        db: sqlite3.Connection,
        project_id: str,
        conversation_job_id: str,
        *,
        limit: int,
        mailbox_ids: list[int] | None = None,
        consumer_kind: str = "conversation_job",
    ) -> list[int]:
        if mailbox_ids is None:
            rows = db.execute(
                "SELECT id,delivery_count FROM v1_project_mailbox "
                "WHERE project_id=? AND state='pending' ORDER BY id LIMIT ?",
                (project_id, limit),
            ).fetchall()
        elif mailbox_ids:
            placeholders = ",".join("?" for _ in mailbox_ids)
            rows = db.execute(
                f"SELECT id,delivery_count FROM v1_project_mailbox "
                f"WHERE project_id=? AND state='pending' AND id IN ({placeholders}) ORDER BY id",
                [project_id, *mailbox_ids],
            ).fetchall()
        else:
            rows = []
        now = utcnow()
        delivered: list[int] = []
        for row in rows:
            message_id, current_count = int(row[0]), int(row[1] or 0)
            MAILBOX_STATE_MACHINE.require(MailboxStatus.PENDING, MailboxStatus.DELIVERED)
            attempt_no = current_count + 1
            changed = db.execute(
                "UPDATE v1_project_mailbox SET state='delivered',delivery_count=?,"
                "last_delivery_job_id=?,delivered_at=?,received_at=NULL,failed_at=NULL,last_error=NULL,"
                "claim_owner=NULL,claim_expires_at=NULL WHERE id=? AND project_id=? AND state='pending'",
                (attempt_no, conversation_job_id, now, message_id, project_id),
            ).rowcount
            if changed != 1:
                continue
            db.execute(
                "INSERT INTO v1_mailbox_deliveries("
                "project_id,mailbox_id,attempt_no,conversation_job_id,consumer_kind,state,delivered_at"
                ") VALUES(?,?,?,?,?,'delivered',?)",
                (
                    project_id,
                    message_id,
                    attempt_no,
                    conversation_job_id,
                    consumer_kind,
                    now,
                ),
            )
            delivered.append(message_id)
        return delivered

    def receive_for_job(
        self,
        db: sqlite3.Connection,
        project_id: str,
        conversation_job_id: str,
        mailbox_ids: list[int],
        claim_owner: str,
        claim_expires_at: str,
    ) -> list[int]:
        if not mailbox_ids:
            return []
        now = utcnow()
        received: list[int] = []
        for message_id in mailbox_ids:
            row = db.execute(
                "SELECT state FROM v1_project_mailbox WHERE id=? AND project_id=? "
                "AND last_delivery_job_id=?",
                (message_id, project_id, conversation_job_id),
            ).fetchone()
            if not row:
                continue
            MAILBOX_STATE_MACHINE.require(row[0], MailboxStatus.RECEIVED)
            changed = db.execute(
                "UPDATE v1_project_mailbox SET state='received',received_at=?,claim_owner=?,claim_expires_at=? "
                "WHERE id=? AND project_id=? AND last_delivery_job_id=? AND state='delivered'",
                (
                    now,
                    claim_owner,
                    claim_expires_at,
                    message_id,
                    project_id,
                    conversation_job_id,
                ),
            ).rowcount
            if changed != 1:
                continue
            db.execute(
                "UPDATE v1_mailbox_deliveries SET state='received',received_at=? "
                "WHERE project_id=? AND mailbox_id=? AND conversation_job_id=? AND state='delivered'",
                (now, project_id, message_id, conversation_job_id),
            )
            received.append(message_id)
        return received

    def acknowledge_for_job(
        self,
        db: sqlite3.Connection,
        project_id: str,
        conversation_job_id: str,
        mailbox_ids: list[int],
        *,
        claim_owner: str,
        governance_decision_id: str,
        reported_message_id: int | None,
    ) -> list[int]:
        if not mailbox_ids:
            return []
        now = utcnow()
        acknowledged: list[int] = []
        for message_id in mailbox_ids:
            row = db.execute(
                "SELECT state FROM v1_project_mailbox WHERE id=? AND project_id=? "
                "AND last_delivery_job_id=? AND claim_owner=?",
                (message_id, project_id, conversation_job_id, claim_owner),
            ).fetchone()
            if not row:
                continue
            MAILBOX_STATE_MACHINE.require(row[0], MailboxStatus.ACKNOWLEDGED)
            changed = db.execute(
                "UPDATE v1_project_mailbox SET state='acknowledged',observed_at=?,acknowledged_at=?,"
                "governance_decision_id=?,reported_message_id=COALESCE(reported_message_id,?),"
                "reported_at=CASE WHEN ? IS NULL THEN reported_at ELSE COALESCE(reported_at,?) END,"
                "claim_owner=NULL,claim_expires_at=NULL,last_error=NULL "
                "WHERE id=? AND project_id=? AND last_delivery_job_id=? AND state='received' AND claim_owner=?",
                (
                    now,
                    now,
                    governance_decision_id,
                    reported_message_id,
                    reported_message_id,
                    now,
                    message_id,
                    project_id,
                    conversation_job_id,
                    claim_owner,
                ),
            ).rowcount
            if changed != 1:
                continue
            db.execute(
                "UPDATE v1_mailbox_deliveries SET state='acknowledged',finished_at=?,error=NULL "
                "WHERE project_id=? AND mailbox_id=? AND conversation_job_id=? AND state='received'",
                (now, project_id, message_id, conversation_job_id),
            )
            acknowledged.append(message_id)
        return acknowledged

    def fail_for_job(
        self,
        db: sqlite3.Connection,
        project_id: str,
        conversation_job_id: str,
        mailbox_ids: list[int],
        error: str,
        *,
        attention: bool = False,
    ) -> list[int]:
        now = utcnow()
        failed: list[int] = []
        for message_id in mailbox_ids:
            row = db.execute(
                "SELECT state FROM v1_project_mailbox WHERE id=? AND project_id=? "
                "AND last_delivery_job_id=?",
                (message_id, project_id, conversation_job_id),
            ).fetchone()
            if not row or row[0] not in {
                MailboxStatus.DELIVERED.value,
                MailboxStatus.RECEIVED.value,
                "claimed",
            }:
                continue
            source = MailboxStatus.RECEIVED if row[0] == "claimed" else MailboxStatus(row[0])
            target = (
                MailboxStatus.ATTENTION
                if attention and source == MailboxStatus.RECEIVED
                else MailboxStatus.FAILED
            )
            MAILBOX_STATE_MACHINE.require(source, target)
            changed = db.execute(
                "UPDATE v1_project_mailbox SET state=?,failed_at=?,last_error=?,"
                "claim_owner=NULL,claim_expires_at=NULL "
                "WHERE id=? AND project_id=? AND last_delivery_job_id=? "
                "AND state IN ('delivered','received','claimed')",
                (target.value, now, error, message_id, project_id, conversation_job_id),
            ).rowcount
            if changed != 1:
                continue
            db.execute(
                "UPDATE v1_mailbox_deliveries SET state=?,finished_at=?,error=? "
                "WHERE project_id=? AND mailbox_id=? AND conversation_job_id=? "
                "AND state IN ('delivered','received')",
                (target.value, now, error, project_id, message_id, conversation_job_id),
            )
            failed.append(message_id)
        return failed

    def redeliver(self, project_id: str, message_id: int, reason: str) -> dict[str, Any]:
        now = utcnow()
        with self.store.tx(immediate=True) as db:
            row = db.execute(
                "SELECT state FROM v1_project_mailbox WHERE id=? AND project_id=?",
                (message_id, project_id),
            ).fetchone()
            if not row:
                raise KeyError(message_id)
            MAILBOX_STATE_MACHINE.require(row[0], MailboxStatus.PENDING)
            db.execute(
                "UPDATE v1_project_mailbox SET state='pending',redelivered_at=?,last_error=NULL,"
                "last_delivery_job_id=NULL,delivered_at=NULL,received_at=NULL,failed_at=NULL,"
                "claim_owner=NULL,claim_expires_at=NULL WHERE id=? AND project_id=?",
                (now, message_id, project_id),
            )
            self.store._event(
                db,
                project_id,
                None,
                None,
                "mailbox.redelivery_requested",
                {"mailbox_id": message_id, "reason": reason},
            )
        with self.store.connect() as db:
            result = db.execute(
                "SELECT * FROM v1_project_mailbox WHERE id=? AND project_id=?",
                (message_id, project_id),
            ).fetchone()
        assert result is not None
        return self.store._decode(dict(result), "payload_json")  # type: ignore[return-value]

    def deliveries(self, project_id: str, message_id: int) -> list[dict[str, Any]]:
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT * FROM v1_mailbox_deliveries WHERE project_id=? AND mailbox_id=? "
                "ORDER BY attempt_no",
                (project_id, message_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def acknowledge_pending_noop(self, project_id: str, message_id: int) -> bool:
        """Compatibility path for an explicit audited no-op observer."""
        now = utcnow()
        decision_id = new_id("gdec")
        consumer_id = f"audited-noop:{decision_id}"
        with self.store.tx(immediate=True) as db:
            delivered = self.deliver_pending(
                db,
                project_id,
                consumer_id,
                limit=1,
                mailbox_ids=[message_id],
                consumer_kind="audited_noop",
            )
            if delivered != [message_id]:
                return False
            received = self.receive_for_job(
                db,
                project_id,
                consumer_id,
                delivered,
                consumer_id,
                now,
            )
            if received != [message_id]:
                raise RuntimeError("Mailbox audited no-op delivery was not received")
            db.execute(
                "INSERT INTO v1_governance_decisions(id,project_id,kind,subject_id,decision,data_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    decision_id,
                    project_id,
                    "mailbox_ack",
                    str(message_id),
                    "observed_no_intervention",
                    "{}",
                    now,
                ),
            )
            acknowledged = self.acknowledge_for_job(
                db,
                project_id,
                consumer_id,
                received,
                claim_owner=consumer_id,
                governance_decision_id=decision_id,
                reported_message_id=None,
            )
            return acknowledged == [message_id]
