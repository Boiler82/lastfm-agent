"""Check that the Snowflake settings in .env work, without using the Claude API."""
from dotenv import load_dotenv
load_dotenv()
import agent

cur = agent.get_connection().cursor()

# 1. Who am I logged in as?
cur.execute("SELECT CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE(), CURRENT_WAREHOUSE()")
account, user, role, warehouse = cur.fetchone()
print(f"Account:   {account}\nUser:      {user}\nRole:      {role}\nWarehouse: {warehouse}")

# 2. Can I read the Last.fm data?
cur.execute("SELECT COUNT(*), MAX(loaded_at) FROM LASTFM.STAGING.STG_TOP_TRACKS")
rows, latest = cur.fetchone()
print(f"\nRead test: {rows} rows, latest snapshot {latest}")

# 3. What is this role allowed to do? (should be only reading)
cur.execute(f"SHOW GRANTS TO ROLE {role}")
privileges = sorted({row[1] for row in cur.fetchall()})
print(f"Privileges of {role}: {', '.join(privileges)}")