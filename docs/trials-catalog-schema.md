# Read-only trial catalog schema contract (#659 / #373)

EXACT's current concrete `trials` models must be a **subset** of the database
resolved by its read router. Extra CB tables/columns are allowed. Missing or
incompatible modeled storage blocks startup, even with OMOP flags disabled.
Reserved JSONB storage is not evidence of materialization or a reader contract.

## Preflight

```bash
python manage.py check_trials_schema --configuration-only
python manage.py check_trials_schema
```

The first command does not connect to a database. With a split `trials` alias,
matching startup refuses `TRIALS_DB_MIGRATE`,
`TRIALS_DATABASE_INIT_FROM_BACKUP`, and
`TRIALS_DB_TOLERATE_MISSING_COLUMNS`. Initialization/migration of a genuinely
EXACT-owned copy is a separate administrative operation, never an automatic
repair of CB-owned storage. Turn these flags off before serving requests.
The existing opt-in administrative commands do not establish ownership or grant
permission to change a CB catalog.

The second command follows the same router as ORM reads and checks every current
concrete trials model, including automatic through models. It requires one routed
catalog alias and PostgreSQL/PostGIS. It compares the resolved relation/column
identity, PostgreSQL type OIDs and relevant typmods, nullability, schema USAGE and
column SELECT privileges. Geometry/geography type, subtype, dimensions and SRID
must match. Domains are not silently flattened. Strict certification currently
requires ordinary/partitioned tables: views cannot prove the same null/default
contract from their metadata.

An explicitly declared model `db_default=[]` requires the literal empty JSONB
default. The checker does not execute default expressions. Older fields without
a declared database default do not acquire a new default requirement.

Inspection uses a dedicated connection and one repeatable-read, read-only
transaction, with statement/lock timeouts. It reads metadata only, not Trial or
patient rows, and always closes its connection (including before Gunicorn forks).
Errors are not cached and no schema, data, migration recorder or criteria are
modified. There is no autofix, deferral, or empty-data fallback.

Output records the contract SHA256, timestamp, alias, database/server identity,
effective role/search_path, resolved table schema/OIDs, compared-column count,
errors and compatibility result. Connection failures and drift exit nonzero.
Store full evidence privately; identifiers/connection topology may be sensitive.

## Before traffic

The Docker entrypoint checks configuration **before** migrations or opt-in
bootstrap scripts, then runs the complete metadata preflight before Gunicorn.
`exact/wsgi.py` repeats the same preflight before exposing `application`, covering
direct Gunicorn/runserver startup that bypasses Docker. No DB introspection is
added to `AppConfig.ready()` or ordinary management-command initialization.

## Model alignment in this change

- The 43 fields from CB `0449_trial_omop_criterion_columns` are modeled as non-null
  JSONB with independent application `[]` values and database `[]` defaults.
  Together with the 12 existing counterparts, all 55 configured source lists
  have storage counterparts. No new reader/config criterion is activated.
- Unused retired `Marker`/`MarkerCategory`/through and ETL-owned `RawDataItem`
  declarations leave runtime and migration state, not physical storage.
  Migration 0028 retains all their tables/rows; migration 0027 still uses its
  historical apps registry for standalone copying. This is not legacy Trial
  column cleanup or ETL ownership transfer.
- RBC threshold fields mirror CB's existing `numeric(12,2)` instead of declaring
  integer storage. Their units, matcher and queryset rules are unchanged.
- Standalone EXACT migrations create the new fields and align local RBC types.
  Default split-DB routing prohibits these migrations on the CB catalog. Any
  needed physical external schema work belongs to CB, not this checker.

## Evidence and deployment boundary

A successful check on an isolated October snapshot proves that snapshot only.
Before any eventual deployment, capture evidence using the **candidate revision
and its actual target database, read role and search_path**. Record the commit
alongside the checker output and the CB schema migration identity. A merged CB
migration or a local pass is not target readiness. The startup check also cannot
prevent an operator changing schema after it succeeds; schema changes require
coordinated deployment and subsequent revalidation.

This work authorizes no deployment, backfill, patient/reference write, reader
activation or production repair. #660–#663 and CB generation publication #5616
remain separate gates.
