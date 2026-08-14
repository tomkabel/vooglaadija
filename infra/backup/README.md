# Database Backup Guide

## Automated Backups (backup profile)

The `backup` profile in `docker-compose.yml` runs daily PostgreSQL dumps (`pg_dump -Fc`,
gzip-compressed custom-format `.dump` archives) at 02:00 with a 7-day retention
(`BACKUP_RETENTION_DAYS`). Dumps land in `infra/backup/backup-data/`.

```bash
# Local stack
docker compose -f docker-compose.yml -f docker-compose.local.yml --profile backup up -d

# Coolify deployment: prepend the profile flag to the application's
# Docker Compose start command (Coolify UI → vooglaadija → Advanced):
#   --profile backup up -d --pull always --remove-orphans
```

A one-off manual backup (no scheduler):

```bash
docker compose --profile backup run --rm backup
```

## Quick Backup (Manual)

```bash
# Via docker (custom-format archive, gzip-compressed)
docker exec -T ytprocessor-db pg_dump -U postgres -d ytprocessor -Fc -Z 6 > backup_$(date +%Y%m%d).dump
```

## Restore from Backup

Restore a custom-format `.dump` archive into the running database:

```bash
# Custom-format archive
docker exec -i ytprocessor-db pg_restore -U postgres -d ytprocessor --clean --if-exists < backup_file.dump

# Plain SQL dump
docker exec -i ytprocessor-db psql -U postgres -d ytprocessor < backup_file.sql
```

> `pg_dump -Fc` archives are **not** gzip files despite the `-Z 6` flag — the compression is built
> into the archive format, so they must be restored with `pg_restore`, not `gunzip`.

## Backup Verification

Always verify your backups work — a backup that has never been restored is only a theory:

```bash
# List backups
ls -la infra/backup/backup-data/

# Restore into a uniquely named scratch database (no --create; it only exists
# inside the container), verify, then drop it:
docker exec -i ytprocessor-db pg_restore -U postgres -d postgres -v < backup_file.dump 2>&1 | tail -5

docker exec ytprocessor-db psql -U postgres -d postgres \
  -c 'CREATE DATABASE scratch_restore_test;'
docker exec -i ytprocessor-db pg_restore -U postgres -d scratch_restore_test < backup_file.dump
docker exec ytprocessor-db psql -U postgres -d scratch_restore_test \
  -c '\dt' \
  -c 'SELECT count(*) FROM users;'
# Verification complete — drop the scratch database:
docker exec ytprocessor-db psql -U postgres -d postgres \
  -c 'DROP DATABASE scratch_restore_test;'
```

## Production Notes

- Copy dumps off the server (rsync to another host or object storage) — a backup on the same disk as
  the data is not a real backup.
- The Coolify-managed deployment stores its database in the `ytprocessor-postgres-data` volume;
  backups via the profile above are the supported restore path.
