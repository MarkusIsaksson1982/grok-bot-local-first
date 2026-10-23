---
name: sql-review-checklist
description: Reviews a SQL Server migration or query for locking, missing indexes and unsafe data changes. Use when a pull request touches .sql files.
---

# SQL review checklist

- Does an UPDATE or DELETE have a WHERE clause and run in a transaction?
- Are new foreign keys indexed?
- Is the migration reversible? Write the rollback next to it.
- Run the queries in [examples/queries.sql](examples/queries.sql) against a copy, never production.
- Report findings as a list; do not edit the migration yourself.
