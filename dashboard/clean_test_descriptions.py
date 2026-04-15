#!/usr/bin/env python3
"""
Clean test descriptions in database - remove leading hyphens and separators
"""

import sys
import re
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from storage.database import DashboardDatabase


def clean_description(description: str) -> str:
    """
    Clean test description by removing leading separators

    Args:
        description: Original description

    Returns:
        Cleaned description
    """
    if not description:
        return description

    # Remove Windows_Containers prefix with : - or space separators
    cleaned = re.sub(r'^Windows_Containers[:\-\s]+', '', description)

    # Remove any remaining leading separators (: - or spaces)
    cleaned = re.sub(r'^[:\-\s]+', '', cleaned)

    return cleaned.strip()


def main():
    db_path = '/data/dashboard.db'

    # Check if database exists
    if not Path(db_path).exists():
        print(f"Database not found at {db_path}")
        print("Using local path: ./dashboard.db")
        db_path = './dashboard.db'

        if not Path(db_path).exists():
            print(f"Database not found at {db_path}")
            sys.exit(1)

    print(f"Connecting to database: {db_path}")
    db = DashboardDatabase(db_path)

    # Get all test results with descriptions
    cursor = db.conn.cursor()
    cursor.execute("""
        SELECT id, test_name, test_description
        FROM test_results
        WHERE test_description IS NOT NULL
        AND test_description != ''
    """)

    rows = cursor.fetchall()
    print(f"Found {len(rows)} test results with descriptions")

    updated = 0
    cleaned_count = 0

    for row in rows:
        test_id = row[0]
        test_name = row[1]
        original_desc = row[2]

        cleaned_desc = clean_description(original_desc)

        if cleaned_desc != original_desc:
            cleaned_count += 1
            print(f"Cleaning: {test_name}")
            print(f"  Before: {original_desc}")
            print(f"  After:  {cleaned_desc}")

            cursor.execute("""
                UPDATE test_results
                SET test_description = ?
                WHERE id = ?
            """, (cleaned_desc, test_id))

            updated += 1

    db.conn.commit()

    print(f"\nResults:")
    print(f"  Total rows checked: {len(rows)}")
    print(f"  Rows with changes: {cleaned_count}")
    print(f"  Rows updated: {updated}")
    print("Done!")


if __name__ == '__main__':
    main()
