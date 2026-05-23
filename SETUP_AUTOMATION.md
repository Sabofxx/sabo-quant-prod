# Setup automation 100% gratuit, zero-touch

GitHub Actions cron + Capital.com demo + Telegram bot = full pipeline 24/7, $0/mois.
Une fois setup, tu ne touches plus rien. Le système :
- Trade Dimanche-Jeudi à 22:05 UTC (5 min après FX close, skip Fri+Sat = marché fermé)
- Fetch fresh daily candles from Capital.com (cached entre runs)
- Compute signals (ADAPTIVE_75_50)
- Execute delta orders via Capital.com REST API (skip si marché fermé)
- Génère dashboard HTML statique
- Telegram notif daily summary + erreurs
- Commit state back to repo

## 1. Capital.com demo account (5 min, GRATUIT)

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

## 2. Telegram bot (3 min, GRATUIT)

```
1. Telegram → search @BotFather → /start
2. /newbot → choose name (e.g. "Sabo Quant Daily") → choose username (must end _bot)
3. Copy bot TOKEN (format 123456:ABC-DEF...)
4. Telegram → search @userinfobot → /start
5. Copy your CHAT_ID (numeric)
6. Open your bot DM → send "/start" or any message → bot can now send to you
```

## 3. Push project to GitHub (5 min)

```bash
cd /Users/oscarmischler/sabo-quant
git init
git add .
git commit -m "Initial sabo-quant automated trading system"

# Private repo via gh CLI
gh repo create sabo-quant-prod --private --source=. --push

# OR manually : create repo on github.com, then:
# git remote add origin git@github.com:USER/sabo-quant-prod.git
# git branch -M main && git push -u origin main
```

## 4. Configure GitHub secrets (5 min)

```bash
# Capital.com (paste value when prompted, Enter, Ctrl+D to finish):
gh secret set CAPITAL_API_KEY
gh secret set CAPITAL_IDENTIFIER
gh secret set CAPITAL_API_PASSWORD
gh secret set CAPITAL_ENVIRONMENT --body "demo"

# Telegram notifications:
gh secret set TELEGRAM_BOT_TOKEN
gh secret set TELEGRAM_CHAT_ID
```

**ATTENTION** : la valeur du secret = stdin de `gh secret set`. Pipeline :
1. Type `gh secret set NAME` + Enter
2. Paste la valeur
3. Enter pour newline (optional)
4. Ctrl+D pour soumettre stdin

**JAMAIS** `gh secret set <valeur>` — la valeur deviendrait le NOM du secret et fuirait dans `~/.zsh_history`.

OR via GitHub UI : Repo > Settings > Secrets and variables > Actions > New repository secret

## 5. Test Telegram locally (1 min)

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."
export TELEGRAM_CHAT_ID="123456789"
cd sabo_lit/sandbox
python telegram_notifier.py --test
# → "🤖 Sabo Quant Test ..." doit arriver dans le DM bot
```

## 6. Vérifier setup complet (2 min)

```bash
# Trigger workflow manuel (don't wait until tomorrow)
gh workflow run "Daily Prop Firm Signal"

# Watch progress
gh run watch

# Or view in browser
gh repo view --web   # → Actions tab
```

Si green ✅ : Telegram daily summary arrive. Dimanche 22:05 UTC = auto run. Forever.

## 7. Capital.com local test (optionnel, 2 min)

```bash
export CAPITAL_API_KEY="ton-api-key"
export CAPITAL_IDENTIFIER="ton-email-login"
export CAPITAL_API_PASSWORD="ton-api-password-custom"
export CAPITAL_ENVIRONMENT="demo"

.venv/bin/pip install requests pandas

# Account access
.venv/bin/python sabo_lit/sandbox/capital_connector.py --account-info

# Positions
.venv/bin/python sabo_lit/sandbox/capital_connector.py --positions

# Dry-run (no orders sent)
.venv/bin/python sabo_lit/sandbox/capital_connector.py \
  --execute sabo_lit/sandbox/live/prop_delta_orders_latest.csv --dry-run
```

## 8. Daily cycle (tu ne touches rien)

```
22:05 UTC Dim-Jeu :
  GitHub Actions wakes up
  Runs sabo_lit/sandbox/automated_runner.py:
    a. Login Capital.com (session token)
    b. Snapshot balance (before)
    c. Fetch 200 daily candles per pair (cached après 1er run)
    d. Append fresh bars
    e. Signal generator --adaptive (ADAPTIVE_75_50)
    f. Market hours check (skip si FX closed)
    g. capital_connector --execute --reset (close all + open fresh)
    h. Log fills to live/capital_executions.jsonl
    i. Snapshot balance (after)
    j. Generate live/dashboard.html
    k. Telegram daily summary
  Commit state files back to repo
  Si fail → Telegram alert
```

## 9. Weekly review (5 min/sem)

```bash
git pull   # sync state

# Latest signals + executions
cat sabo_lit/sandbox/live/prop_signals_latest.md
tail -50 sabo_lit/sandbox/live/capital_executions.jsonl

# P&L trajectory
tail -7 sabo_lit/sandbox/live/automated_daily_pnl.jsonl

# Dashboard (web)
open https://htmlpreview.github.io/?https://github.com/USER/sabo-quant-prod/blob/main/sabo_lit/sandbox/live/dashboard.html

# Live tracker
python sabo_lit/sandbox/live_tracker.py
cat sabo_lit/sandbox/live/tracker_dashboard.md
```

## 10. Cost check

```
GitHub Actions free tier : 2000 min/month private repo
Daily run usage          : ~1.5 min × 22 jours/mois = ~33 min/mois
Safety margin            : 60× sous la limite
Capital.com demo         : free forever
Telegram bot             : free forever
Disk on GitHub           : <50 MB (data cached, pas committed)
─────────────────────────────────────────────────
TOTAL MONTHLY COST       : $0
```

## 11. Stop / pause

```bash
gh workflow disable "Daily Prop Firm Signal"   # pause
gh workflow enable  "Daily Prop Firm Signal"   # resume

# Permanent remove:
rm .github/workflows/daily_propfirm.yml
git commit -am "remove automation" && git push
```

## 12. Monitoring sans Mac

- **Telegram** : daily summary + erreurs push direct mobile
- **GitHub Actions UI** : github.com/USER/sabo-quant-prod/actions
- **State files** : `sabo_lit/sandbox/live/` (commit history visible)
- **Dashboard web** : htmlpreview link Telegram message

## TROUBLESHOOTING

### Workflow ne se déclenche pas
- GitHub désactive auto les workflows si pas d'activité 60 jours
- Solution : push commit dummy mensuel, OU `gh workflow run` manuel

### Capital.com orders rejected
- Vérifier demo account ACTIVE (relogin web si dormant)
- Epic naming : EURUSD, GBPUSD (pas EUR_USD)
- Margin disponible
- "INVALID_SIZE" → check `dealingRules.minDealSize` via /markets/{epic}
- Marché fermé : runner check market_execution_allowed() → skip propre

### Telegram silence
- `python telegram_notifier.py --test` localement
- Vérifier bot DM ouvert (envoie au moins 1 message au bot)
- Vérifier TELEGRAM_CHAT_ID numérique (pas username)

### Données pas à jour
- Capital daily bars closent à 21:00 UTC (FX close)
- Workflow tourne à 22:05 UTC pour capter close
- Fri 20:59 → Sun 21:00 UTC : marché fermé, skip propre

### State file conflicts
- Si commit échoue (rare), workflow continue mais state pas pushed
- Manuel : `git pull origin main` puis re-run workflow

## CHECKLIST FINAL DEPLOY

```
[ ] Capital.com demo : API key + custom password
[ ] Telegram bot : TOKEN + CHAT_ID
[ ] Repo pushed GitHub (private)
[ ] gh secret set : CAPITAL_API_KEY, CAPITAL_IDENTIFIER, CAPITAL_API_PASSWORD,
                    CAPITAL_ENVIRONMENT, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
[ ] python telegram_notifier.py --test → message reçu
[ ] capital_connector.py --account-info → balance OK
[ ] gh workflow run "Daily Prop Firm Signal" → green
[ ] Telegram daily summary reçu après run
[ ] Dimanche 22:05 UTC auto run, verify 7 jours
```

After 30 days : compare realized P&L vs backtest expectation, decide go-real avec FTMO/FundingPips challenge.
