import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from scripts.generation.db import get_connection

# Read from user's DB path if provided, else default
db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
if db_path is None:
    print("Usage: python check_london.py /path/to/travelbench.db")
    sys.exit(1)

conn = get_connection(db_path)

# Wrong info distribution
print("=== WRONG INFO DISTRIBUTION ===")
wi_rows = conn.execute("""
    SELECT v.name, v.category, v.traffic_tier, wi.affected_field, 
           wi.wrong_info_category, wi.incorrect_value, wi.correct_value, wi.source_type
    FROM wrong_info wi JOIN venues v ON wi.venue_id = v.venue_id
    WHERE v.city = 'london'
    ORDER BY v.traffic_tier, v.category
""").fetchall()
print(f"Total wrong info entries: {len(wi_rows)}")
print()
for row in wi_rows:
    print(f"  [{row['traffic_tier']:4s}] {row['name'][:35]:35s} | {row['affected_field']:15s} | {row['incorrect_value']} → {row['correct_value']} | via {row['source_type']}")

# Pace distribution
print("\n=== PACE DISTRIBUTION ===")
pace_rows = conn.execute("""
    SELECT recommended_pace, COUNT(*) as n FROM venues WHERE city='london' GROUP BY recommended_pace
""").fetchall()
for row in pace_rows:
    print(f"  {row['recommended_pace']}: {row['n']}")

# Source doc count per venue
print("\n=== SOURCE DOCS PER VENUE ===")
doc_counts = conn.execute("""
    SELECT v.name, v.traffic_tier, COUNT(r.doc_id) as n
    FROM venues v LEFT JOIN doc_venue_refs r ON v.venue_id = r.venue_id
    WHERE v.city = 'london'
    GROUP BY v.venue_id ORDER BY n DESC
""").fetchall()
for row in doc_counts:
    print(f"  {row['n']:2d} docs | [{row['traffic_tier']:4s}] {row['name']}")

# The failed venue
print("\n=== FAILED VENUE (has_official_site=1 but no doc) ===")
missing = conn.execute("""
    SELECT v.venue_id, v.name, v.category, v.district
    FROM venues v WHERE v.city='london' AND v.has_official_site=1
    AND v.venue_id NOT IN (SELECT venue_id FROM official_site_docs)
""").fetchall()
for row in missing:
    print(f"  {row['venue_id']} | {row['name']} | {row['category']} | {row['district']}")

conn.close()
