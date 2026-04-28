import logging
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

logger = logging.getLogger(__name__)

SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "remediation@example.com")


def _send_email(to_addresses: list, subject: str, body_html: str) -> bool:
    """Send email via SMTP."""
    if not SMTP_HOST:
        logger.warning(f"SMTP not configured, skipping email: {subject}")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM
        msg["To"] = ", ".join(to_addresses)

        msg.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            if os.environ.get("SMTP_TLS", "true").lower() == "true":
                server.starttls()
            if SMTP_USER and SMTP_PASSWORD:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)

        logger.info(f"Email sent to {to_addresses}: {subject}")
        return True

    except Exception as e:
        logger.error(f"Error sending email: {e}", exc_info=True)
        return False


def send_failure_email(
    recipients: list,
    platform: str,
    instance_name: str,
    failure_signature: str,
    remediation_id: str,
) -> bool:
    """Send email when remediation was attempted but issue persists."""
    subject = f"⚠️ Remediation Failed: {platform}/{instance_name} - {failure_signature}"

    body_html = f"""
    <html>
        <body style="font-family: Arial, sans-serif;">
            <h2>Remediation Attempt Failed</h2>
            <p>A remediation was automatically attempted but the issue persists.</p>

            <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                <p><strong>Platform:</strong> {platform}</p>
                <p><strong>Instance:</strong> {instance_name}</p>
                <p><strong>Failure Type:</strong> {failure_signature}</p>
                <p><strong>Remediation ID:</strong> {remediation_id}</p>
                <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
            </div>

            <p>Please investigate and take manual action if needed.</p>
            <p>
                <a href="http://localhost:8501/reporting" style="background-color: #007bff; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
                    View Reporting Dashboard
                </a>
            </p>

            <hr>
            <p style="font-size: 12px; color: #666;">
                This is an automated notification from the Unified Self-Healing Platform.
            </p>
        </body>
    </html>
    """

    return _send_email(recipients, subject, body_html)


def send_pending_email(
    recipients: list,
    platform: str,
    instance_name: str,
    remediation_id: str,
) -> bool:
    """Send email when remediation plan requires manual approval."""
    subject = f"⏳ Manual Approval Required: {platform}/{instance_name}"

    body_html = f"""
    <html>
        <body style="font-family: Arial, sans-serif;">
            <h2>Manual Approval Required</h2>
            <p>A remediation plan was generated but requires manual approval (HIGH/MED risk or not auto-executable).</p>

            <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                <p><strong>Platform:</strong> {platform}</p>
                <p><strong>Instance:</strong> {instance_name}</p>
                <p><strong>Remediation ID:</strong> {remediation_id}</p>
                <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
            </div>

            <p>Please review the remediation plan and approve or reject it.</p>
            <p>
                <a href="http://localhost:8501" style="background-color: #28a745; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px;">
                    Review & Approve Plan
                </a>
            </p>

            <hr>
            <p style="font-size: 12px; color: #666;">
                This is an automated notification from the Unified Self-Healing Platform.
            </p>
        </body>
    </html>
    """

    return _send_email(recipients, subject, body_html)


def send_success_email(
    recipients: list,
    platform: str,
    instance_name: str,
    failure_signature: str,
    remediation_id: str,
) -> bool:
    """Send email when remediation succeeds (optional, not used in failures-only mode)."""
    subject = f"✅ Remediation Successful: {platform}/{instance_name} - {failure_signature}"

    body_html = f"""
    <html>
        <body style="font-family: Arial, sans-serif;">
            <h2>Remediation Successful</h2>
            <p>The issue has been automatically remediated and resolved.</p>

            <div style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 20px 0;">
                <p><strong>Platform:</strong> {platform}</p>
                <p><strong>Instance:</strong> {instance_name}</p>
                <p><strong>Issue Type:</strong> {failure_signature}</p>
                <p><strong>Remediation ID:</strong> {remediation_id}</p>
                <p><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}</p>
            </div>

            <p>No further action required.</p>

            <hr>
            <p style="font-size: 12px; color: #666;">
                This is an automated notification from the Unified Self-Healing Platform.
            </p>
        </body>
    </html>
    """

    return _send_email(recipients, subject, body_html)
