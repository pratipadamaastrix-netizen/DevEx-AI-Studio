import sqlite3

conn = sqlite3.connect("engine.db")
cursor = conn.cursor()

try:
    cursor.execute("ALTER TABLE fm_tickets ADD COLUMN phone TEXT")
    print("Phone column added")
except:
    print("Phone column already exists")

try:
    cursor.execute("ALTER TABLE fm_tickets ADD COLUMN address TEXT")
    print("Address column added")
except:
    print("Address column already exists")

conn.commit()
conn.close()

print("Database update complete")