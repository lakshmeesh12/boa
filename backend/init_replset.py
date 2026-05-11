"""Initialise the single-node Mongo replica set, idempotently.

Runs *from the api container* (not from the mongo entrypoint) so that
when `rs.initiate` validates "is `mongo:27017` reachable from me?", the
real daemon is already listening on the docker network — avoiding the
`NodeNotFound` race that hits the /docker-entrypoint-initdb.d hook.
"""
from __future__ import annotations

import os
import sys
import time

from pymongo import MongoClient
from pymongo.errors import OperationFailure, ServerSelectionTimeoutError

MONGO_HOST = os.getenv("MONGO_INIT_HOST", "mongo:27017")
RS_NAME = os.getenv("MONGO_REPLSET_NAME", "rs0")
TIMEOUT_SECONDS = int(os.getenv("MONGO_INIT_TIMEOUT", "60"))


def _log(msg: str) -> None:
    print(f"[init-replset] {msg}", flush=True)


def _connect() -> MongoClient:
    """Connect WITHOUT replicaSet=, since the set may not exist yet."""
    return MongoClient(
        f"mongodb://{MONGO_HOST}/?directConnection=true",
        serverSelectionTimeoutMS=5_000,
    )


def _wait_for_mongod(client: MongoClient) -> None:
    deadline = time.time() + TIMEOUT_SECONDS
    while time.time() < deadline:
        try:
            client.admin.command("ping")
            _log("mongod is responding to ping.")
            return
        except ServerSelectionTimeoutError:
            time.sleep(1)
    _log("ERROR: timed out waiting for mongod.")
    sys.exit(1)


def _is_initialised(client: MongoClient) -> bool:
    try:
        status = client.admin.command("replSetGetStatus")
        return bool(status.get("ok"))
    except OperationFailure as e:
        # 94 = NotYetInitialized, 93 = InvalidReplicaSetConfig
        if getattr(e, "code", None) in (94, 93):
            return False
        # 76 = NoReplicationEnabled (mongod started without --replSet) — fatal
        raise


def _initiate(client: MongoClient) -> None:
    config = {
        "_id": RS_NAME,
        "members": [{"_id": 0, "host": MONGO_HOST}],
    }
    try:
        client.admin.command("replSetInitiate", config)
        _log(f"replSetInitiate sent for {RS_NAME} ({MONGO_HOST}).")
    except OperationFailure as e:
        # 23 = AlreadyInitialized — fine, treat as success
        if getattr(e, "code", None) == 23:
            _log("replica set already initialised.")
            return
        raise


def _wait_for_primary(client: MongoClient) -> None:
    """Block until this node has elected itself PRIMARY (myState == 1)."""
    deadline = time.time() + TIMEOUT_SECONDS
    while time.time() < deadline:
        try:
            status = client.admin.command("replSetGetStatus")
            my_state = status.get("myState")
            if my_state == 1:
                _log("node is PRIMARY — replica set is ready.")
                return
            _log(f"waiting for PRIMARY (current myState={my_state})...")
        except OperationFailure as e:
            _log(f"replSetGetStatus error (will retry): {e}")
        time.sleep(1)
    _log("ERROR: timed out waiting for PRIMARY.")
    sys.exit(1)


def main() -> None:
    _log(f"target = {MONGO_HOST}, replSet = {RS_NAME}")
    client = _connect()
    _wait_for_mongod(client)

    if _is_initialised(client):
        _log("already initialised — skipping rs.initiate.")
    else:
        _initiate(client)

    _wait_for_primary(client)
    _log("done.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[init-replset] FATAL: {e}", flush=True)
        sys.exit(1)
