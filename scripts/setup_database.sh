#!/bin/bash
# setup_database.sh - Ensure all required database objects exist
# This script can be run on existing databases to apply missing migrations

set -e

CONTAINER_NAME="${1:-survival_postgres}"
DB_USER="${2:-postgres}"
DB_NAME="${3:-postgres}"

echo "=== Database Setup Verification ==="
echo "Container: $CONTAINER_NAME"
echo "Database: $DB_NAME"
echo ""

# Function to run SQL in the container
run_sql() {
    docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -c "$1"
}

# 1. Check and create street_embeddings table
echo "1. Checking street_embeddings table..."
TABLE_EXISTS=$(run_sql "SELECT EXISTS (SELECT FROM pg_tables WHERE tablename = 'street_embeddings');" 2>/dev/null | grep -o 't\|f' || echo 'f')
if [ "$TABLE_EXISTS" = "f" ]; then
    echo "   Creating street_embeddings table..."
    docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" < postgres/init-scripts/10-street-embeddings-table.sql
    echo "   ✅ street_embeddings table created"
else
    echo "   ✅ street_embeddings table exists"
fi

# 2. Check process_location_smart function
echo ""
echo "2. Checking process_location_smart function..."
FUNC_EXISTS=$(run_sql "SELECT EXISTS (SELECT 1 FROM pg_proc WHERE proname = 'process_location_smart');" 2>/dev/null | grep -o 't\|f' || echo 'f')
if [ "$FUNC_EXISTS" = "f" ]; then
    echo "   Creating process_location_smart function..."
    docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" < postgres/init-scripts/07-process-location-smart.sql
    echo "   ✅ process_location_smart function created"
else
    echo "   ✅ process_location_smart function exists"
fi

# 3. Check streets table has data
echo ""
echo "3. Checking streets table..."
STREET_COUNT=$(run_sql "SELECT COUNT(*) FROM streets;" 2>/dev/null | tail -1 | tr -d ' ' || echo '0')
# Extract just the number
STREET_COUNT=$(echo "$STREET_COUNT" | grep -o '[0-9]*' | head -1)
if [ -z "$STREET_COUNT" ] || [ "$STREET_COUNT" = "0" ]; then
    echo "   ⚠️  Streets table is empty!"
    echo "   Loading streets from CSV..."
    
    # Copy CSV to container
    docker cp postgres/data/streets.csv "$CONTAINER_NAME":/tmp/streets.csv
    
    # Load streets
    docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" << 'EOSQL'
        CREATE TEMP TABLE streets_temp (
            name TEXT,
            wkt_geom TEXT
        );
        
        COPY streets_temp(name, wkt_geom) FROM '/tmp/streets.csv' WITH (FORMAT csv, HEADER true);
        
        INSERT INTO streets (names, geom)
        SELECT ARRAY[name], ST_GeomFromText(wkt_geom, 4326)
        FROM streets_temp
        WHERE wkt_geom IS NOT NULL AND wkt_geom != '' AND wkt_geom NOT LIKE '%NaN%';
        
        DROP TABLE streets_temp;
EOSQL
    
    NEW_COUNT=$(run_sql "SELECT COUNT(*) FROM streets;" 2>/dev/null | tail -1 | tr -d ' ' | grep -o '[0-9]*' | head -1)
    echo "   ✅ Loaded $NEW_COUNT streets"
    
    # Clean up
    docker exec "$CONTAINER_NAME" rm -f /tmp/streets.csv
else
    echo "   ✅ Streets table has $STREET_COUNT streets"
fi

# 4. Check embeddings
echo ""
echo "4. Checking street_embeddings..."
EMBED_COUNT=$(run_sql "SELECT COUNT(*) FROM street_embeddings;" 2>/dev/null | tail -1 | tr -d ' ' | grep -o '[0-9]*' | head -1)
if [ -z "$EMBED_COUNT" ]; then
    EMBED_COUNT=0
fi

if [ "$EMBED_COUNT" = "0" ]; then
    echo "   ⚠️  No embeddings found"
    echo "   💡 Embeddings will be generated automatically when parser starts"
else
    echo "   ✅ $EMBED_COUNT embeddings exist"
fi

# 5. Summary
echo ""
echo "=== Database Setup Complete ==="
run_sql "SELECT 'streets' as table_name, COUNT(*) as count FROM streets UNION ALL SELECT 'street_embeddings', COUNT(*) FROM street_embeddings UNION ALL SELECT 'events', COUNT(*) FROM events;"

echo ""
echo "Next steps:"
echo "1. Restart parser to generate embeddings (if needed): docker compose restart parser"
echo "2. Monitor parser logs: docker logs -f survival_parser"
