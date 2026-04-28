# Quick Start Guide

## Prerequisites
- Python 3.9+
- Virtual environment activated
- Dependencies installed: `pip install -r requirements.txt`

## 1. Initialize System

```bash
# Set encryption key
export SELF_HEALING_MASTER_KEY='<your-secure-key>'

# Set admin API key
export ADMIN_API_KEY='your-admin-key'

# (Optional) Configure email
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=your-email@gmail.com
export SMTP_PASSWORD=your-app-password
export SMTP_FROM=remediation@example.com
```

## 2. Start Backend

```bash
uvicorn backend.api.main:app --reload
```

Backend starts at: http://localhost:8000
API docs at: http://localhost:8000/docs

On startup, it will:
- Initialize SQLite database
- Start polling orchestrator
- Load all enabled polling configs

## 3. Start Frontend

In another terminal:

```bash
streamlit run frontend/reporting.py
```

Frontend available at: http://localhost:8501

## 4. Configure Polling (Two Options)

### Option A: Via Streamlit UI (Recommended for admins)

1. Open http://localhost:8501
2. Click "Admin Configuration" tab
3. Enter your admin API key
4. **Polling Configuration**:
   - Select platform (e.g., aws_glue)
   - Enter instance name (e.g., prod-glue)
   - Set poll interval (e.g., 300 seconds = 5 minutes)
   - Toggle "Auto-execute LOW-risk plans?" if desired
   - Click "Save Polling Config"
5. **Notification Configuration**:
   - Select same platform/instance
   - Enter email recipients (comma-separated)
   - Click "Save Notification Config"

### Option B: Via API (For automation)

```bash
# Enable polling for an instance
curl -X POST http://localhost:8000/admin/polling/aws_glue/prod-glue \
  -H "x-token: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "poll_interval_seconds": 300,
    "auto_execute_low_risk": false,
    "log_filter": null
  }'

# Configure email recipients
curl -X POST http://localhost:8000/admin/notifications/aws_glue/prod-glue \
  -H "x-token: your-admin-key" \
  -H "Content-Type: application/json" \
  -d '{
    "recipients": ["ops@example.com", "alerts@example.com"]
  }'

# Verify config
curl -X GET http://localhost:8000/admin/polling/aws_glue/prod-glue \
  -H "x-token: your-admin-key"
```

## 5. View Reporting Dashboard

1. Open http://localhost:8501
2. Click "Reporting" tab
3. View metrics for last 24h, 7d, or 30d
4. Filter by platform if needed
5. See remediation history with status

Metrics shown:
- **Total Attempted**: All remediation attempts
- **Success Rate**: % of successful remediations
- **Successful**: Count of fixed issues
- **Still Failing**: Count of attempted but unresolved issues

## 6. Monitor via API

```bash
# Get metrics for last 24h
curl "http://localhost:8000/reporting/metrics?period=24h"

# Get metrics for specific platform
curl "http://localhost:8000/reporting/metrics?period=7d&platform=aws_glue"

# Get history
curl "http://localhost:8000/reporting/history?limit=50"

# Get summary across all periods
curl "http://localhost:8000/reporting/summary"
```

## Workflow Overview

### Polling → Detection → Auto-Remediation

```
┌─────────────────────┐
│ Polling Orchestrator │  Every 5 min (configurable)
└──────────┬──────────┘
           │
           ├─→ AWS CloudWatch Logs
           ├─→ Azure Log Analytics
           ├─→ GCP Cloud Logging
           ├─→ Airflow REST API
           └─→ More...
           │
           ├─→ Failure detected?
           │   └─→ extract: {failure_signature, run_id}
           │
           └─→ Invoke healing workflow
                │
                ├─→ Classify error
                ├─→ Match SOPs
                ├─→ Generate plan
                ├─→ Evaluate safety
                │
                ├─→ LOW risk + allowlisted?
                │   ├─→ YES: Auto-execute
                │   └─→ NO: Store as PENDING
                │
                └─→ Store result in database
                    └─→ Email if still failing
```

## Use Cases

### Case 1: Auto-Healing (Low-Risk Issues)

1. Failure detected: "OutOfMemory"
2. Plan generated: "Increase cluster memory"
3. Allowlisted action: ✅ Yes
4. Risk level: ✅ LOW
5. Auto-execute: ✅ YES (if enabled)
6. Result: ✅ SUCCESS → No email
7. Result: ❌ FAILED → Email sent with details

### Case 2: Manual Review (High-Risk Issues)

1. Failure detected: "AccessDenied"
2. Plan generated: "Grant IAM permissions"
3. Allowlisted action: ❌ No
4. Result: Stored as PENDING
5. Email: ⏳ Manual approval needed
6. Admin reviews plan and clicks "Execute"
7. Plan executed with explicit approval

### Case 3: Monitoring & Metrics

1. Open Reporting dashboard
2. View metrics: 100 attempts, 87% success rate, 8 still failing
3. Filter by platform: "aws_glue" → 45 attempts, 2 still failing
4. Click on failed remediation → see full details
5. Investigation: Check logs, security groups, credentials
6. Adjust allowlist or SOPs as needed

## Configuration Files

### Database
Location: `data/remediation.db` (SQLite)
Auto-created on first startup.

### Encrypted Integration Config
Location: `config/integrations.enc` (encrypted YAML)
Contains: Auth credentials for all platforms/instances

See `config/integrations.auth_examples.yaml` for template.

### Polling Config
Location: SQLite table `polling_config`
Managed via Streamlit UI or API `/admin/polling`

### Notification Config
Location: SQLite table `notification_config`
Managed via Streamlit UI or API `/admin/notifications`

## Troubleshooting

### Polling not starting
```bash
# Check logs
grep "Polling" /tmp/backend.log

# Verify config exists
sqlite3 data/remediation.db "SELECT * FROM polling_config;"

# Ensure backend restarted after config change
```

### Emails not sending
```bash
# Check SMTP config
echo $SMTP_HOST
echo $SMTP_PORT

# Verify recipients configured
sqlite3 data/remediation.db "SELECT * FROM notification_config;"

# Test SMTP manually
python -c "
import smtplib
from email.mime.text import MIMEText
msg = MIMEText('test')
msg['Subject'] = 'Test'
msg['From'] = 'remediation@example.com'
msg['To'] = 'test@example.com'
with smtplib.SMTP('$SMTP_HOST', $SMTP_PORT) as s:
    s.starttls()
    s.login('$SMTP_USER', '$SMTP_PASSWORD')
    s.send_message(msg)
print('Email sent!')
"
```

### Low metrics or no activity
```bash
# Check if polling is running
ps aux | grep uvicorn

# Check database for remediations
sqlite3 data/remediation.db "SELECT COUNT(*) FROM remediations;"

# Check API health
curl http://localhost:8000/health

# Verify instance logs exist
# (e.g., CloudWatch, Log Analytics, Cloud Logging for your instances)
```

## Environment Variables Summary

```bash
# Required
export SELF_HEALING_MASTER_KEY='<your-key>'

# Admin
export ADMIN_API_KEY='<your-admin-key>'

# Database (optional)
export REMEDIATION_DB_PATH='data/remediation.db'

# Email (optional, but recommended)
export SMTP_HOST='smtp.gmail.com'
export SMTP_PORT='587'
export SMTP_USER='your-email@gmail.com'
export SMTP_PASSWORD='your-app-password'
export SMTP_FROM='remediation@example.com'
export SMTP_TLS='true'

# API (optional)
export SELF_HEALING_API='http://localhost:8000'
```

## Next Steps

1. ✅ Start backend and frontend
2. ✅ Configure polling for at least one instance
3. ✅ Set email recipients
4. ✅ Monitor dashboard for incoming metrics
5. ✅ Test with manual failure event (via "Generate Plan" tab)
6. ✅ Verify auto-remediation works (check logs, emails, dashboard)
7. ✅ Adjust polling intervals and auto-execute flags as needed
8. ✅ Set up monitoring alerts on failure count

## Support

- API Documentation: http://localhost:8000/docs
- Frontend: http://localhost:8501
- Logs: Check uvicorn and streamlit output
- Database: `sqlite3 data/remediation.db`

---

**Ready to go!** 🚀
