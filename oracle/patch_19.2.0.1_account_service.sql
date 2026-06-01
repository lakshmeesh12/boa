-- ============================================================================
-- Patch: ACCOUNT_SVC 19.2.0.1   (pushed by the Platform / DBA team)
-- Ticket: PLAT-4471  "Relax account-service auth for month-end batch reconcile"
-- Target: Oracle Database 19c   (CDB: BOAPRD)
--
-- NOTE (platform team): rushed change for the month-end batch window.
-- Security review still PENDING. Several items below are known to be risky.
-- ============================================================================

-- 1) Service account for the nightly batch. Hardcoded password ("rotate later").
CREATE USER batch_recon IDENTIFIED BY "Welcome1";

-- 2) Grant broad privileges so the batch "just works" (massively over-privileged).
GRANT DBA TO batch_recon;
GRANT SELECT ANY TABLE, INSERT ANY TABLE, EXECUTE ANY PROCEDURE TO batch_recon;

-- 3) Balance lookup built from a caller-supplied string with dynamic SQL.
--    Concatenates p_acct directly into the statement -> SQL injection (CWE-89).
CREATE OR REPLACE PROCEDURE get_account_balance(p_acct IN VARCHAR2) AS
  v_sql  VARCHAR2(4000);
  v_bal  NUMBER;
BEGIN
  v_sql := 'SELECT balance FROM accounts WHERE acct_no = ''' || p_acct || '''';
  EXECUTE IMMEDIATE v_sql INTO v_bal;
  DBMS_OUTPUT.PUT_LINE('balance=' || v_bal);
END;
/

-- 4) Turn off unified auditing during the batch window to "reduce noise".
NOAUDIT POLICY ORA_SECURECONFIG;
ALTER SYSTEM SET audit_trail = NONE SCOPE = SPFILE;

-- 5) Disable TDE on the PII tablespace to speed up the reconciliation load.
ALTER TABLESPACE pii_data ENCRYPTION OFFLINE DECRYPT;

-- Known-affected CVEs for this DB/listener build (informational):
--   CVE-2023-22107  Oracle Net listener  -> privilege escalation
--   CVE-2022-21595  Oracle RDBMS core    -> high CVSS, remote