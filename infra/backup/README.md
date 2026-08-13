# Database Backup Guide

## Automated Backups (backup profile)

The `backup` profile in `docker-compose.yml` runs daily PostgreSQL dumps (`pg_dump -Fc`, gzip) at
02:00 with a 7-day retention (`BACKUP_RETENTION_DAYS`). Dumps land in `infra/backup/backup-data/`.

```bash
# Local stack
docker compose -f docker-compose.yml -f docker-compose.local.yml --profile backup up -d

# Coolify deployment: append --profile backup to the application's
# Docker Compose start command (Coolify UI → vooglaadija → Advanced)
```

## Quick Backup (Manual)

```bash
# Via docker
docker exec -T ytprocessor-db pg_dump -U postgres -d ytprocessor -Fc -Z 6 > backup_$(date +%Y%m%d).sql.gz
```

## Restore from Backup

```bash
# Custom-format dump (pg_dump -Fc)
docker exec -i ytprocessor-db pg_restore -U postgres -d ytprocessor --clean --if-exists < backup_file.dump

# Plain SQL dump
docker exec -i ytprocessor-db psql -U postgres -d ytprocessor < backup_file.sql
```

## Backup Verification

Always verify your backups work — a backup that has never been restored is only a theory:

```bash
# List backups
ls -la infra/backup/backup-data/

# Test restore into a scratch database
docker exec -i ytprocessor-db pg_restore -U postgres -d postgres --create < backup_file.dump
```

## Production Notes

- Copy dumps off the server (rsync to another host or object storage) — a backup on the same disk as
  the data is not a real backup.
- The Coolify-managed deployment stores its database in the `ytprocessor-postgres-data` volume;
  backups via the profile above are the supported restore path.
