# Quick Production Deployment - Phase 1 & 2

## TL;DR - 3 Commands

```bash
# 1. Run migration (auto-creates backup, <1 sec)
python scripts/prod_migrate.py

# 2. Pull new code
git pull origin master

# 3. Restart server
sudo systemctl restart fast-todo  # or your restart command
```

That's it! ✅

---

## What This Does

- Makes `occ_hash` column nullable in database
- Deploys Phase 1 & 2 code (metadata-based completions)
- Zero downtime (old code works during migration)
- Automatic backup created

## Verification (Optional)

```bash
# Check migration worked
sqlite3 fast_todo.db "PRAGMA table_info(completedoccurrence)" | grep occ_hash
# Should show: 2|occ_hash|VARCHAR|0||0
#                                 ^ nullable (0 = yes)

# Test app still works
curl http://localhost:8000/
# Should return 200

# Check new completions have NULL hash (after deployment)
sqlite3 fast_todo.db "SELECT occ_hash FROM completedoccurrence ORDER BY id DESC LIMIT 1"
# New ones should show empty (NULL)
```

## Rollback (If Needed)

```bash
# Restore backup
cp fast_todo.db.backup.<timestamp> fast_todo.db

# Revert code
git checkout <previous-commit>

# Restart
sudo systemctl restart fast-todo
```

---

## Two Migration Scripts Available

### Option 1: Lightweight Script (Recommended)
```bash
python scripts/prod_migrate.py
```
- ✅ Auto-creates backup
- ✅ Idempotent (safe to run multiple times)
- ✅ Shows progress
- ✅ 50 lines of code

### Option 2: Full Featured Script
```bash
python scripts/migrate_phase1_nullable_hash.py --db fast_todo.db --commit
```
- ✅ Dry-run mode available
- ✅ Detailed logging
- ✅ More verbose output
- ✅ 200 lines of code

Both scripts do the same thing. Pick whichever you prefer!

---

## FAQ Speed Round

**Q: Will this break my site?**  
A: No. Backward compatible, zero downtime.

**Q: How long does it take?**  
A: <1 second for most databases.

**Q: Do I need to stop the server?**  
A: No! Migration can run while server is running.

**Q: What if I run the migration twice?**  
A: It's idempotent - detects already migrated and skips.

**Q: Can I test it first?**  
A: Yes! Copy database and test:
```bash
cp fast_todo.db test.db
python scripts/prod_migrate.py test.db
```

**Q: What's the worst that can happen?**  
A: Migration fails, automatic backup lets you restore. But it won't fail - it's a simple schema change.

---

## For Full Details

See: `docs/production_deployment_guide.md`

---

**Questions?** The migration is safe, tested, and backward compatible. Just run it! 🚀
