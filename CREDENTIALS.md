# Credentials

## Where passwords are stored

Credentials are stored **outside the repository** at:

```
~/.credentials/ai-server.txt
```

This file is `chmod 600` — readable only by your user. It is never inside the repo and cannot be committed to git.

To view:

```bash
cat ~/.credentials/ai-server.txt
```

## Accounts

| Service | Email | Notes |
|---------|-------|-------|
| Open WebUI | YOUR_EMAIL | admin role |
| Docmost | YOUR_EMAIL | owner role |

---

## Resetting the Open WebUI password

Open WebUI stores passwords in a SQLite database inside the `open-webui` container.

**1. Generate a bcrypt hash of the new password:**

```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_NEW_PASSWORD', bcrypt.gensalt()).decode())"
```

**2. Update the database:**

```bash
docker exec open-webui python3 -c "
import bcrypt, sqlite3
pw = b'YOUR_NEW_PASSWORD'
hashed = bcrypt.hashpw(pw, bcrypt.gensalt()).decode()
conn = sqlite3.connect('/app/backend/data/webui.db')
conn.execute(\"UPDATE auth SET password=? WHERE email='YOUR_EMAIL'\", (hashed,))
conn.commit()
print('rows updated:', conn.total_changes)
"
```

**3. Update `~/.credentials/ai-server.txt` with the new password.**

### Notes

- The password column is in the `auth` table, not the `user` table
- bcrypt generates a new salt each run — the hash in the terminal and the hash in the DB will differ; this is correct
- The hash is never written to any file — it only exists in memory during the reset

---

## Resetting the Docmost password

Docmost stores passwords in PostgreSQL inside the `docmost-postgresql` container.

**1. Generate a bcrypt hash on the host:**

```bash
python3 -c "import bcrypt; print(bcrypt.hashpw(b'YOUR_NEW_PASSWORD', bcrypt.gensalt()).decode())"
```

**2. Apply it to the database** (replace the hash with your output):

```bash
docker exec docmost-postgresql psql -U docmost -d docmost -c \
  "UPDATE users SET password='\$2b\$12\$...' WHERE email='YOUR_EMAIL';"
```

**3. Update `~/.credentials/ai-server.txt` with the new password.**

### Notes

- The relevant column is `password` in the `users` table
- `has_generated_password` column exists but does not need to be changed for a manual reset
- Dollar signs in the hash must be escaped as `\$` inside shell strings

---

## Why no mail-based password reset?

Docmost supports a "forgot password" email flow, but this stack has no mail server configured. The database method above works without any email setup.

---

## Security reminders

- Never commit `~/.credentials/ai-server.txt` or `.env` to git
- Rotate passwords if the machine is ever accessed by someone else or exposed to the internet
- The bcrypt hash stored in the DB is safe to have in a database — it cannot be reversed to the original password
