-- Read-only checks to run against a copy of the database.
SELECT name FROM sys.foreign_keys fk WHERE NOT EXISTS (
  SELECT 1 FROM sys.index_columns ic WHERE ic.object_id = fk.parent_object_id);
