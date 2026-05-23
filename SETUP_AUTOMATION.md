# Setup automation 100% gratuit, zero-touch

GitHub Actions cron + Capital.com demo = full pipeline 24/7, $0/mois.
Une fois setup, tu ne touches plus rien. Le système :
- Trade tous les jours à 22:05 UTC (5 min après FX close)
- Fetch fresh data from Capital.com
- Compute signals (ADAPTIVE_75_50)
- Execute delta orders via Capital.com REST API
- Log P&L + alerts
- Commit state back to repo
- Email/Discord notif si échec

## 1. Setup Capital.com demo account (5 min, GRATUIT)

```
1. https://capital.com → Sign up (no card needed, EU friendly)
2. Email confirm → login web platform
3. Switch to Demo account (top bar account selector)
4. Settings → API integration → Generate API key
   - Set a CUSTOM API PASSWORD (separate from login pwd)
   - Copy API key (X-CAP-API-KEY)
5. Note 3 values :
   - API key      (40+ chars)
   - Identifier   (your login email)
   - API password (the custom one you just set, NOT login pwd)
```

## 2. Push project to GitHub (5 min)

```bash
cd /Users/oscarmischler/sabo-quant

# Add gitignore to skip large files
cat > .gitignore <<'EOF'
__pycache__/
*.pyc
.venv/
.ruff_cache/
.pytest_cache/
sandbox/data/dukascopy_*/
sandbox/data/*.csv
sandbox/reports/*.html
EOF

# Init repo
git init
git add .
git commit -m "Initial sabo-quant automated trading system"

# Create private GitHub repo (use gh CLI)
gh repo create sabo-quant-prod --private --source=. --push

# Or manually : create repo on github.com, then:
# git remote add origin git@github.com:USER/sabo-quant-prod.git
# git branch -M main
# git push -u origin main
```

## 3. Configure secrets in GitHub (5 min)

```bash
# Using gh CLI (fastest) — paste values when prompted, never on command line:
gh secret set CAPITAL_API_KEY
gh secret set CAPITAL_IDENTIFIER
gh secret set CAPITAL_API_PASSWORD
gh secret set CAPITAL_ENVIRONMENT --body "demo"

# Optional Discord notification on failure:
gh secret set DISCORD_WEBHOOK --body "https://discord.com/api/webhooks/..."
```

Discord webhook = optional. Create one :
- Discord server > Channel settings > Integrations > Webhooks > New > Copy URL

OR via GitHub UI :
```
Repo > Settings > Secrets and variables > Actions > New repository secret
```

## 4. Verify setup (2 min)

```bash
# Test workflow manually (don't wait until tomorrow)
gh workflow run "Daily Prop Firm Signal"

# Watch progress
gh run watch

# Or view in browser
gh repo view --web
# → Actions tab → Daily Prop Firm Signal
```

If green ✅ = system live. Tomorrow 22:05 UTC will run automatically. Forever.

## 5. Verify Capital.com test (optional, 2 min)

Local test before relying on GitHub Actions :

```bash
export CAPITAL_API_KEY="ton-api-key"
export CAPITAL_IDENTIFIER="ton-email-login"
export CAPITAL_API_PASSWORD="ton-api-password-custom"
export CAPITAL_ENVIRONMENT="demo"

/Users/oscarmischler/sabo-quant/.venv/bin/pip install requests pandas

# Test account access
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/capital_connector.py --account-info

# Should output JSON with balance, available, currency etc.
# Then check positions:
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/capital_connector.py --positions

# Dry-run a fake execution (no orders sent):
/Users/oscarmischler/sabo-quant/.venv/bin/python sabo_lit/sandbox/capital_connector.py \
  --execute sabo_lit/sandbox/live/prop_delta_orders_latest.csv --dry-run
```

## 6. Daily cycle (you do nothing)

```
22:05 UTC every day:
  GitHub Actions wakes up
  Runs sabo_lit/sandbox/automated_runner.py:
    a. Login Capital.com (session token)
    b. Snapshot Capital account balance (before)
    c. Fetch last 30 days daily candles from Capital for 6 FX pairs
    d. Append fresh bars to local CSV history
    e. Run signal generator with --adaptive (ADAPTIVE_75_50)
    f. Read current Capital positions
    g. Compute delta orders = target - current
    h. Submit BUY/SELL orders for each non-zero delta
    i. Log fills to live/capital_executions.jsonl
    j. Refresh dashboard
    k. Snapshot Capital balance (after)
  Commit state files back to repo
  If fail → Discord notif + email
```

## 7. Weekly review (5 min/semaine)

```bash
git pull   # local sync of state

# View latest signals + executions
cat sabo_lit/sandbox/live/prop_signals_latest.md
cat sabo_lit/sandbox/live/capital_executions.jsonl | tail -50

# View P&L trajectory
cat sabo_lit/sandbox/live/automated_daily_pnl.jsonl | tail -7

# Run live tracker dashboard
python sabo_lit/sandbox/live_tracker.py
cat sabo_lit/sandbox/live/tracker_dashboard.md
```

## 8. Cost check

```
GitHub Actions free tier : 2000 minutes/month for private repos
Daily run usage          : ~3 min/day = ~90 min/month
Safety margin            : 22× under limit
OANDA practice           : free forever
Disk usage on GitHub     : <100 MB (data CSVs gitignored)
Discord webhook          : free
─────────────────────────────────────────────────
TOTAL MONTHLY COST       : $0
```

## 9. Stop / pause system

Pause anytime :
```bash
# Disable workflow without deleting:
gh workflow disable "Daily Prop Firm Signal"

# Re-enable:
gh workflow enable "Daily Prop Firm Signal"

# Permanently remove:
rm .github/workflows/daily_propfirm.yml
git commit -am "remove automation"
git push
```

## 10. Monitoring sans Mac

Tout est on GitHub :
- **Logs runs** : github.com/USER/sabo-quant-prod/actions
- **State files** : github.com/USER/sabo-quant-prod/tree/main/sabo_lit/sandbox/live
- **P&L history** : `automated_daily_pnl.jsonl` (commit history visible)
- **Failure alerts** : email auto + Discord webhook (si configuré)

Mobile :
- GitHub mobile app → notifications workflow failures
- Discord mobile → push notif si Discord webhook configuré

## TROUBLESHOOTING

### Workflow ne se déclenche pas
- GitHub Actions désactive auto les workflows si pas d'activité 60 jours sur repo
- Solution : push un commit dummy mensuel, OU `gh workflow run` manuellement

### Capital.com orders rejected
- Vérifier que demo account est ACTIVE (relogin web platform si dormant)
- Vérifier epic naming (EURUSD, GBPUSD — pas EUR_USD)
- Vérifier balance/margin disponible
- Si "INVALID_SIZE" → check `dealingRules.minDealSize` via /markets/{epic}
- Account hedging vs netting : non-issue, le runner appelle --reset (close all)
  avant d'ouvrir nouvelles positions, donc rebalance idempotent dans les deux modes

### Données pas à jour
- Capital.com daily bars closent à 21:00 UTC (FX close)
- Workflow tourne à 22:05 UTC pour capter close du jour
- Si weekend (Vendredi 21:00 UTC à Dimanche 22:00 UTC) : pas de nouvelles bars, system idle

### State file conflicts
- Si commit échoue (rare), workflow continue mais state pas pushed
- Manuel : `git pull origin main` puis re-run workflow

## CHECKLIST FINAL DEPLOY

```
[ ] Capital.com demo créé, API key + custom password générés
[ ] Repo pushed sur GitHub (private)
[ ] GitHub Secrets : CAPITAL_API_KEY + CAPITAL_IDENTIFIER + CAPITAL_API_PASSWORD
[ ] Discord webhook créé (optionnel)
[ ] Local test : capital_connector.py --account-info → balance OK
[ ] Local test : capital_connector.py --execute ... --dry-run → orders simulés OK
[ ] gh workflow run "Daily Prop Firm Signal" → workflow vert
[ ] Logs lisibles dans GitHub Actions UI
[ ] State files committed dans repo après 1er run
[ ] Tomorrow 22:05 UTC = auto run, verify pendant 7 jours
```

After 30 days : compare realized P&L vs backtest expectation, decide go-real with FTMO/FundingPips challenge.
