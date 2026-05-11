"""Neo4j graph engine — maps TestCase → Module → Error relationships."""
from __future__ import annotations

from core.logging_config import get_logger
from core.settings import settings

log = get_logger("Neo4jEngine")

_driver = None

# Banking module dependency graph (static knowledge)
_MODULE_DEPS = {
    "Transactions": ["Accounts", "CreditCards", "Deposits"],
    "CreditCards": ["Customers"],
    "Accounts": ["Customers"],
    "Deposits": ["Accounts", "Customers"],
}


def _get_driver():
    global _driver
    if _driver is None:
        from neo4j import GraphDatabase
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
    return _driver


async def ensure_schema() -> bool:
    """Create constraints and seed module dependency graph."""
    try:
        driver = _get_driver()
        with driver.session() as sess:
            # Constraints
            sess.run("CREATE CONSTRAINT IF NOT EXISTS FOR (m:Module) REQUIRE m.name IS UNIQUE")
            sess.run("CREATE CONSTRAINT IF NOT EXISTS FOR (t:TestCase) REQUIRE t.id IS UNIQUE")
            sess.run("CREATE CONSTRAINT IF NOT EXISTS FOR (r:TestRun) REQUIRE r.id IS UNIQUE")

            # Seed modules
            modules = ["Customers", "Accounts", "CreditCards", "Deposits", "Transactions", "UI"]
            for m in modules:
                sess.run("MERGE (:Module {name: $name})", name=m)

            # Seed dependencies
            for module, deps in _MODULE_DEPS.items():
                for dep in deps:
                    sess.run("""
                        MATCH (a:Module {name: $a}), (b:Module {name: $b})
                        MERGE (a)-[:DEPENDS_ON]->(b)
                    """, a=module, b=dep)

        log.info("neo4j.schema_ready")
        return True
    except Exception as exc:
        log.error("neo4j.schema_failed", context={"error": str(exc)})
        return False


async def record_test_run(session_id: str, started_at: str) -> None:
    try:
        driver = _get_driver()
        with driver.session() as sess:
            sess.run(
                "MERGE (r:TestRun {id: $id}) SET r.session_id=$sid, r.started_at=$ts",
                id=session_id, sid=session_id, ts=started_at,
            )
    except Exception as exc:
        log.warning("neo4j.record_run_failed", context={"error": str(exc)})


async def record_test_result(session_id: str, test_id: str, test_name: str, module: str, status: str) -> None:
    try:
        driver = _get_driver()
        with driver.session() as sess:
            sess.run("""
                MERGE (t:TestCase {id: $id})
                SET t.name=$name, t.status=$status
                WITH t
                MATCH (r:TestRun {id: $run_id})
                MERGE (t)-[:PART_OF]->(r)
                WITH t
                MATCH (m:Module {name: $module})
                MERGE (t)-[:TESTS]->(m)
            """, id=test_id, name=test_name, status=status,
               run_id=session_id, module=module)
    except Exception as exc:
        log.warning("neo4j.record_result_failed", context={"error": str(exc)})


async def record_error(session_id: str, test_id: str, trace_id: str, message: str, level: str) -> None:
    try:
        driver = _get_driver()
        with driver.session() as sess:
            sess.run("""
                MERGE (e:Error {trace_id: $tid})
                SET e.message=$msg, e.level=$lvl
                WITH e
                MATCH (t:TestCase {id: $test_id})
                MERGE (t)-[:PRODUCED_ERROR]->(e)
            """, tid=trace_id or test_id, msg=message[:200], lvl=level, test_id=test_id)
    except Exception as exc:
        log.warning("neo4j.record_error_failed", context={"error": str(exc)})


async def run_cypher(cypher: str) -> list[dict]:
    """Execute arbitrary Cypher and return results as a list of dicts."""
    try:
        driver = _get_driver()
        with driver.session() as sess:
            result = sess.run(cypher)
            return [dict(r) for r in result]
    except Exception as exc:
        log.warning("neo4j.cypher_failed", context={"error": str(exc)})
        return []


async def get_blast_radius(module_name: str) -> list[str]:
    """Find all modules that depend on the given module."""
    cypher = """
        MATCH (m:Module)-[:DEPENDS_ON*1..3]->(dep:Module {name: $name})
        RETURN DISTINCT m.name AS affected
    """
    results = await run_cypher(cypher.replace("$name", f"'{module_name}'"))
    return [r.get("affected", "") for r in results]


async def get_graph_data() -> dict:
    """Return nodes + edges for D3 visualisation."""
    try:
        nodes_raw = await run_cypher("MATCH (m:Module) RETURN m.name AS name")
        edges_raw = await run_cypher(
            "MATCH (a:Module)-[r:DEPENDS_ON]->(b:Module) RETURN a.name AS from, b.name AS to"
        )
        return {
            "nodes": [{"id": r["name"], "group": "module"} for r in nodes_raw],
            "links": [{"source": r["from"], "target": r["to"]} for r in edges_raw],
        }
    except Exception as exc:
        return {"nodes": [], "links": [], "error": str(exc)}
