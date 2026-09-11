#!/bin/sh
# Aplica as migrations em ordem e depois o seed. Executado uma única vez, na
# primeira inicialização do container do Postgres.
set -e
for f in /docker-entrypoint-initdb.d/migrations/*.sql; do
  echo "==> aplicando $(basename "$f")"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f "$f"
done
echo "==> aplicando seed.sql"
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f /seed.sql
