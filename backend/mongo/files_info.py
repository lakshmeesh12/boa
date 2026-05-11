"""
MongoDB management endpoints — mirrors the Milvus and Qdrant files_info.py pattern.

Provides a REST endpoint to clean up MongoDB documents by workspace_id
across ALL known agent collections in the AI service database.
"""

from fastapi import APIRouter, Depends, Query, BackgroundTasks
from typing import Optional, List, Dict, Any
import sys
import os
import asyncio
import traceback
import logging

from common.utils.validating_token import token_required
from engine.db.mongo.config import db, mongo_client

logger = logging.getLogger("default")

app = APIRouter()

# ── Database handles ──────────────────────────────────────────────────────────
# Some backend collections live in a separate database (the Node.js backend DB).
# AI-layer collections all live in db (the main AI database).
_backend_db_name = os.environ.get("BACKEND_DATABASE_NAME")
backend_db = mongo_client[_backend_db_name] if _backend_db_name else db

# ── Collection names ──────────────────────────────────────────────────────────
# These MUST match what each agent's ingester actually writes to.

# Tally agent (from ingestor.py env-var defaults)
TALLY_MASTER_COL  = os.getenv("MONGO_MASTER_COLLECTION", "master_data")

# Excel / Document agent (ExcelIngester hardcodes these with en_ prefix)
EXCEL_DOCS_COL    = "en_excel_documents"
EXCEL_CHUNKS_COL  = "en_semantic_chunks"
EXCEL_PARTS_COL   = "en_excel_documents_parts"

# Databricks agent
# IMPORTANT: The agents (structured_ingester.py, schema_monitor.py) read
# os.getenv("DATABRICKS_COLLECTION", "en_databricks") — that is the actual
# collection where all table registration documents are written.
# A legacy env-var alias MONGO_DATABRICKS_COLLECTION pointed to a different
# default ("databricks_documents") which was registered here instead, causing
# workspace deletion to clean the WRONG collection.
# Both collection names are now registered so data is cleaned regardless of
# which env-var was set at ingestion time.
DATABRICKS_DOCS_COL        = os.getenv("DATABRICKS_COLLECTION", "en_databricks")
DATABRICKS_DOCS_COL_LEGACY = os.getenv("MONGO_DATABRICKS_COLLECTION", "databricks_documents")

# Salesforce agent collections (flat workspace_id field)
SALESFORCE_CONNECTIONS_COL       = "salesforce_connections"
SALESFORCE_PKCE_COL              = "salesforce_pkce_store"
SALESFORCE_SCHEMA_COL            = "salesforce_schema"
SALESFORCE_SYNC_CHECKPOINTS_COL  = "salesforce_sync_checkpoints"

# Databricks connection config
DATABRICKS_CONNECTIONS_COL = "databricks_connections"

# Guardrail rules — stored in backend DB with NESTED workspace field:
#   { "workspace": { "workspace_id": "...", ... }, "scope": "workspace", ... }
GUARDRAIL_RULES_COL = "guardrailrules"


# ── Collection registry ───────────────────────────────────────────────────────
# Each entry describes which database and filter key to use.
# filter_key: dot-notation MongoDB field for workspace matching.
# db_handle:  "ai" = AI service db, "backend" = backend (Node.js) db.
# IMPORTANT: Every collection listed here will be swept during workspace/session
# deletion.  If you add a new agent with its own MongoDB collection, register it
# here so data is not left behind on deletion.
COLLECTION_REGISTRY: List[Dict[str, Any]] = [
    # ── Tally ──────────────────────────────────────────────────────────────
    {"name": TALLY_MASTER_COL,  "db": "ai",      "filter_key": "workspace_id",          "label": "Tally master data"},

    # ── Excel / Document ───────────────────────────────────────────────────
    {"name": EXCEL_DOCS_COL,    "db": "ai",      "filter_key": "workspace_id",          "label": "Excel documents"},
    {"name": EXCEL_CHUNKS_COL,  "db": "ai",      "filter_key": "workspace_id",          "label": "Excel semantic chunks"},
    {"name": EXCEL_PARTS_COL,   "db": "ai",      "filter_key": "workspace_id",          "label": "Excel partitioned parts"},

    # ── Databricks ─────────────────────────────────────────────────────────
    # Primary collection — written by structured_ingester.py and schema_monitor.py
    # using os.getenv("DATABRICKS_COLLECTION", "en_databricks").
    {"name": DATABRICKS_DOCS_COL,         "db": "ai", "filter_key": "workspace_id", "label": "Databricks table registrations (en_databricks)"},
    # Legacy alias — written when MONGO_DATABRICKS_COLLECTION was set differently;
    # kept to catch any data written under the old default "databricks_documents".
    {"name": DATABRICKS_DOCS_COL_LEGACY,  "db": "ai", "filter_key": "workspace_id", "label": "Databricks documents (legacy alias)"},
    {"name": DATABRICKS_CONNECTIONS_COL,  "db": "ai", "filter_key": "workspace_id", "label": "Databricks connections (credentials)"},

    # ── Salesforce ─────────────────────────────────────────────────────────
    {"name": SALESFORCE_CONNECTIONS_COL,      "db": "ai", "filter_key": "workspace_id",  "label": "Salesforce connections"},
    {"name": SALESFORCE_PKCE_COL,             "db": "ai", "filter_key": "workspace_id",  "label": "Salesforce PKCE store"},
    {"name": SALESFORCE_SCHEMA_COL,           "db": "ai", "filter_key": "workspace_id",  "label": "Salesforce schema"},
    {"name": SALESFORCE_SYNC_CHECKPOINTS_COL, "db": "ai", "filter_key": "workspace_id",  "label": "Salesforce sync checkpoints"},

    # ── Guardrails ─────────────────────────────────────────────────────────
    # IMPORTANT: guardrailrules stores workspace_id nested inside a 'workspace' object:
    #   { "workspace": { "workspace_id": "<id>" }, "scope": "workspace", ... }
    # Only workspace-scoped rules should be deleted (tenant-scoped rules survive).
    {
        "name": GUARDRAIL_RULES_COL,
        "db":   "backend",
        "filter_key": "workspace.workspace_id",   # nested path
        "extra_filter": {"scope": "workspace"},   # only delete workspace-scoped rules
        "label": "Guardrail rules (workspace-scoped)",
    },

    # ── Playgrounds / Dashboards ───────────────────────────────────────────
    {
        "name": "playground_dashboards",
        "db": "ai",
        "filter_key": "workspace_id",
        "ignore_session_delete": True,  # Dashboards are workspace-scoped; do not delete on session delete
        "label": "Playground Dashboards"
    },
    {
        "name": "playground_dashboard_runs",
        "db": "ai",
        "filter_key": "workspace_id",
        "label": "Playground Dashboard Runs"
    },
]


def _get_db(db_handle: str):
    return backend_db if db_handle == "backend" else db


async def _delete_postgres_vouchers(
    workspace_id: str,
    session_id: Optional[str] = None,
    delete_session: bool = False,
) -> Dict[str, Any]:
    """
    Delete Tally voucher rows from PostgreSQL for a workspace or session.

    The `vouchers` table stores workspace_id and session_id columns that
    directly map to the same identifiers used everywhere else in the system.

    - delete_session=True:  DELETE FROM vouchers WHERE workspace_id=$1 AND session_id=$2
    - delete_session=False: DELETE FROM vouchers WHERE workspace_id=$1
    """
    try:
        from engine.db.postgres.config import get_postgres_conn

        def _sync_delete():
            conn = get_postgres_conn()
            try:
                with conn.cursor() as cur:
                    if delete_session and session_id:
                        cur.execute(
                            "DELETE FROM vouchers WHERE workspace_id = %s AND session_id = %s",
                            (workspace_id, session_id),
                        )
                    else:
                        cur.execute(
                            "DELETE FROM vouchers WHERE workspace_id = %s",
                            (workspace_id,),
                        )
                    deleted = cur.rowcount
                conn.commit()
                return deleted
            finally:
                conn.close()

        deleted_count = await asyncio.to_thread(_sync_delete)
        logger.info(
            f"✓ Deleted {deleted_count} rows from PostgreSQL vouchers "
            f"(workspace_id={workspace_id}, session_id={session_id if delete_session else 'all'})"
        )
        return {"status": "deleted", "label": "PostgreSQL Tally vouchers", "deleted": deleted_count}
    except Exception as pg_err:
        logger.error(f"✗ Failed to delete PostgreSQL vouchers: {pg_err}")
        return {"status": "error", "label": "PostgreSQL Tally vouchers", "message": str(pg_err)}


@app.delete("/delete_session_or_workspace")
async def delete_session_or_workspace(
    workspace_id: str,
    background_tasks: BackgroundTasks,
    tenant_id: Optional[str] = Query(default=""),
    session_id: Optional[str] = Query(default=""),
    delete_session: bool = Query(default=False),
    decoded_token: dict = Depends(token_required),
):
    """
    Delete MongoDB documents across ALL agent collections for a given workspace.
    Also deletes Tally voucher rows from PostgreSQL.

    Mirrors `DELETE /ai/v1/milvus/delete_session_or_workspace`
    and    `DELETE /ai/v1/qdrant/delete_session_or_workspace`.

    Collections swept (AI DB):
        master_data, en_excel_documents, en_semantic_chunks,
        en_excel_documents_parts, databricks_documents,
        databricks_connections, salesforce_connections,
        salesforce_pkce_store, salesforce_schema,
        salesforce_sync_checkpoints

    Collections swept (Backend DB):
        guardrailrules  (workspace-scoped rules only)

    PostgreSQL tables swept:
        vouchers  (Tally agent financial transactions — workspace_id + session_id filtered)
    """
    try:
        current_user = decoded_token.get("sub", "").split("|")[-1]
        logger.info(f"{current_user} entered delete_session_or_workspace (mongodb)")

        # ── Validation ──────────────────────────────────────────────────────
        if not workspace_id:
            return {
                "status_code": 400,
                "status_message": "workspace_id is required",
            }

        if delete_session and not session_id:
            return {
                "status_code": 400,
                "status_message": "session_id is required when delete_session=true",
            }

        scope_label = (
            f"session_id: {session_id}" if delete_session
            else f"workspace_id: {workspace_id}"
        )
        logger.info(f"Deleting MongoDB documents for {scope_label} (tenant hint: {tenant_id})")

        # ── Iterate over all collections ─────────────────────────────────────
        results = {}

        for entry in COLLECTION_REGISTRY:
            col_name   = entry["name"]
            filter_key = entry["filter_key"]
            label      = entry.get("label", col_name)
            extra      = entry.get("extra_filter", {})
            handle     = _get_db(entry["db"])

            try:
                collection = handle[col_name]

                # Build filter — always filter by workspace_id (via filter_key)
                mongo_filter = {filter_key: workspace_id}

                # Apply any extra filters (e.g. { "scope": "workspace" } for guardrails)
                if extra:
                    mongo_filter.update(extra)

                # For session-level deletion, additionally filter by session_id
                if delete_session and session_id:
                    if entry.get("ignore_session_delete"):
                        logger.info(f"Skipping '{col_name}' because it ignores session-level deletions.")
                        continue
                    mongo_filter["session_id"] = session_id

                # Count before deletion for transparent logging
                matched_count = await collection.count_documents(mongo_filter)
                logger.info(
                    f"Collection '{col_name}' ({label}): "
                    f"{matched_count} matching documents for {scope_label}"
                )

                if matched_count > 0:
                    delete_result = await collection.delete_many(mongo_filter)
                    deleted = delete_result.deleted_count
                else:
                    deleted = 0

                results[col_name] = {
                    "status":  "deleted",
                    "label":   label,
                    "matched": matched_count,
                    "deleted": deleted,
                }
                logger.info(f"✓ Deleted {deleted} documents from '{col_name}' ({label})")

            except Exception as col_err:
                logger.error(f"✗ Failed to delete from '{col_name}': {col_err}")
                results[col_name] = {
                    "status":  "error",
                    "label":   label,
                    "message": str(col_err),
                }

        # ── PostgreSQL: delete Tally vouchers ────────────────────────────────
        # The Tally agent writes financial voucher rows to the PostgreSQL
        # `vouchers` table with workspace_id and session_id columns.
        # This is the ONLY place that cleans up Postgres — it was missing
        # before and would leave orphaned Tally data behind forever.
        pg_result = await _delete_postgres_vouchers(
            workspace_id=workspace_id,
            session_id=session_id or None,
            delete_session=delete_session,
        )
        results["_postgres_vouchers"] = pg_result
        # ───────────────────────────────────────────────────────────────────

        # ── Trigger concurrent background AI context cleanup ─────────────────
        from engine.context_manager.context_manager import get_context_manager
        
        async def _run_context_cleanup():
            try:
                logger.info(f"[CONTEXT] Starting _run_context_cleanup background task in Mongo. workspace={workspace_id}, session={session_id}, delete_session={delete_session}")
                ctx_manager = get_context_manager()
                if delete_session:
                    logger.info("[CONTEXT] Calling ctx_manager.delete_session_context...")
                    await ctx_manager.delete_session_context(
                        session_id=session_id,
                        tenant_id=tenant_id or None,
                        workspace_id=workspace_id
                    )
                    logger.info("[CONTEXT] Finished ctx_manager.delete_session_context")
                else:
                    logger.info("[CONTEXT] Calling ctx_manager.delete_workspace_context...")
                    await ctx_manager.delete_workspace_context(
                        tenant_id=tenant_id or None,
                        workspace_id=workspace_id
                    )
                    logger.info("[CONTEXT] Finished ctx_manager.delete_workspace_context")
            except Exception as e:
                logger.error(f"[CONTEXT] Failed to run background context cleanup from MongoDB: {e}")
                
        logger.info("[CONTEXT] Adding _run_context_cleanup to FastAPI BackgroundTasks...")
        background_tasks.add_task(_run_context_cleanup)
        # ───────────────────────────────────────────────────────────────────

        # ── Summary ──────────────────────────────────────────────────────────
        successful    = [c for c, r in results.items() if r.get("status") == "deleted"]
        failed        = [c for c, r in results.items() if r.get("status") == "error"]
        total_deleted = sum(r.get("deleted", 0) for r in results.values() if r.get("status") == "deleted")

        return {
            "status_code":    200,
            "status_message": f"Deleted MongoDB documents for {scope_label}",
            "summary": {
                "scope":                   scope_label,
                "total_documents_deleted": total_deleted,
                "collections_swept":       len(results),
                "successful_deletions":    len(successful),
                "failed_deletions":        len(failed),
            },
            "details": {
                "deleted":        successful,
                "failed":         failed,
                "per_collection": results,
            },

        }

    except Exception as err:
        exc_type, exc_obj, exc_tb = sys.exc_info()
        fname = os.path.split(exc_tb.tb_frame.f_code.co_filename)[1]
        message = f"{fname} : Line no {exc_tb.tb_lineno} - {exc_type} : {err}"
        logger.error(f"Error in mongodb delete_session_or_workspace: {message}")

        traceback_details = traceback.extract_tb(exc_tb)
        for filename, lineno, function, text in traceback_details:
            logger.error(f"File: {filename}, line {lineno}, in {function}")
            logger.error(f"Error message: {text}")

        return {
            "status_code":    500,
            "status_message": f"Error deleting MongoDB documents: {str(err)}",
        }
