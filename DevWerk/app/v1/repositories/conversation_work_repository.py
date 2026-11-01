"""Immutable, Job-scoped conversation work revisions."""
import json

from app.v1.storage_support import utcnow
from app.v1.protocols.conversation.conversation_work_contracts import ConversationWorkSnapshot


class ConversationWorkRepository:
    def __init__(self, store):
        self.store = store

    def init_schema(self):
        with self.store.tx(immediate=True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS v1_conversation_work_revisions (
                project_id TEXT NOT NULL REFERENCES v1_projects(id) ON DELETE CASCADE,
                job_id TEXT NOT NULL REFERENCES v1_conversation_jobs(id) ON DELETE CASCADE,
                revision INTEGER NOT NULL,
                operation_key TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY(job_id,revision), UNIQUE(project_id,operation_key))''')

    def latest(self, project_id, job_id, *, db=None):
        query = '''SELECT snapshot_json FROM v1_conversation_work_revisions
                   WHERE project_id=? AND job_id=? ORDER BY revision DESC LIMIT 1'''
        if db is None:
            with self.store.connect() as connection:
                row = connection.execute(query, (project_id, job_id)).fetchone()
        else:
            row = db.execute(query, (project_id, job_id)).fetchone()
        return ConversationWorkSnapshot.model_validate_json(row['snapshot_json']) if row else None

    def get_revision(self, project_id, job_id, revision):
        with self.store.connect() as db:
            row = db.execute('''SELECT snapshot_json FROM v1_conversation_work_revisions
                WHERE project_id=? AND job_id=? AND revision=?''',
                (project_id, job_id, revision)).fetchone()
        return ConversationWorkSnapshot.model_validate_json(row['snapshot_json']) if row else None

    def latest_prior(self, project_id, current_job_id):
        with self.store.connect() as db:
            row = db.execute('''SELECT snapshot_json FROM v1_conversation_work_revisions
                WHERE project_id=? AND job_id!=? ORDER BY created_at DESC,revision DESC LIMIT 1''',
                (project_id, current_job_id)).fetchone()
        return ConversationWorkSnapshot.model_validate_json(row['snapshot_json']) if row else None

    def by_operation(self, project_id, operation_key, *, db):
        row = db.execute('''SELECT snapshot_json FROM v1_conversation_work_revisions
            WHERE project_id=? AND operation_key=?''', (project_id, operation_key)).fetchone()
        return ConversationWorkSnapshot.model_validate_json(row['snapshot_json']) if row else None

    def append(self, snapshot: ConversationWorkSnapshot, base_revision: int, operation_key: str, *, db):
        prior = db.execute('''SELECT snapshot_json FROM v1_conversation_work_revisions
            WHERE project_id=? AND operation_key=?''', (snapshot.project_id, operation_key)).fetchone()
        if prior:
            previous = ConversationWorkSnapshot.model_validate_json(prior['snapshot_json'])
            if previous != snapshot:
                raise ValueError('WorkOperationConflict: operation key has a different snapshot')
            return previous
        current = self.latest(snapshot.project_id, snapshot.job_id, db=db)
        if (current.revision if current else 0) != base_revision:
            raise ValueError('WorkRevisionConflict: read the latest work revision')
        active = db.execute('SELECT status FROM v1_conversation_jobs WHERE id=? AND project_id=?',
                            (snapshot.job_id, snapshot.project_id)).fetchone()
        if not active or active['status'] != 'running':
            raise ValueError('ConversationTurnInactive: work update requires a running Job')
        db.execute('''INSERT INTO v1_conversation_work_revisions
            (project_id,job_id,revision,operation_key,snapshot_json,created_at)
            VALUES(?,?,?,?,?,?)''', (snapshot.project_id,snapshot.job_id,snapshot.revision,
                                     operation_key,snapshot.model_dump_json(),utcnow()))
        return snapshot
