#!/bin/bash
# Initialize a single-node replica set so the API can use multi-document transactions.
# Runs once on first container start (entrypoint hook in mongo image).
set -e

echo "[init-replset] waiting for mongod to accept connections..."
until mongosh --quiet --eval "db.adminCommand('ping').ok" >/dev/null 2>&1; do
  sleep 1
done

echo "[init-replset] initiating replica set rs0..."
mongosh --quiet --eval '
try {
  rs.status();
  print("[init-replset] replica set already initialised");
} catch (e) {
  rs.initiate({
    _id: "rs0",
    members: [{ _id: 0, host: "mongo:27017" }]
  });
  print("[init-replset] rs.initiate() called");
}
'

echo "[init-replset] done."
