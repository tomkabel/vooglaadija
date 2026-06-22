#!/usr/bin/env bash
set -euo pipefail
DB="${HOME}/.local/share/kilo/kilo.db"
DIR="${HOME}/Documents/team21-vooglaadija"
OUTDIR="${HOME}/kilo-exports-$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTDIR"
echo "Fetching sessions for: $DIR"

# Use named parameters to avoid SQL injection from DIR
sessions=$(sqlite3 -list "$DB" \
  -cmd ".param set :dir ${DIR}" \
  -cmd ".param set :pattern ${DIR}/%" \
  "SELECT id || '|' || title || '|' || time_created
   FROM session
   WHERE directory = :dir OR directory LIKE :pattern
   ORDER BY time_created")
count=$(echo "$sessions" | wc -l)
echo "Found $count sessions"
echo "---"
idx=0
exported=0
echo "$sessions" | while IFS='|' read -r sid stitle _; do
  idx=$((idx+1))

  safe_title=$(printf '%s' "$stitle" | tr -cd 'a-zA-Z0-9 ._-' | head -c 80 | tr ' ' '_')
  [ -z "$safe_title" ] && safe_title="session"

  output="${OUTDIR}/${sid}.json"

  session_meta=$(sqlite3 "$DB" -cmd ".param set :sid ${sid}" \
    "SELECT json_object('session_id', id, 'title', title, 'time_created', time_created)
     FROM session WHERE id = :sid;")

  msgs=$(sqlite3 "$DB" -cmd ".param set :sid ${sid}" \
    "SELECT json_group_array(json_object(
      'role', json_extract(data, '$.role'),
      'agent', json_extract(data, '$.agent'),
      'summary', json_extract(data, '$.summary')
    )) FROM message WHERE session_id = :sid ORDER BY time_created;")

  printf '%s\n' "{\"session\":${session_meta},\"messages\":${msgs}}" > "$output"

  size=$(wc -c < "$output")
  if [ "$size" -gt 50 ]; then
    echo "[$idx/$count] OK ($size bytes): $stitle"
    exported=$((exported+1))
  else
    rm -f "$output"
    echo "[$idx/$count] EMPTY: $stitle"
  fi
done
echo "---"
echo "Done. Exported $exported/$count sessions to $OUTDIR"
