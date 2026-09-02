# Copyright 2026 EPAM Systems, Inc. (“EPAM”)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import html
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Optional

import aiosmtplib

from codemie.configs import config
from codemie.configs.logger import logger


@dataclass
class BudgetCategorySnapshot:
    """One category's budget info included in a soft-limit notification email."""

    category: str
    budget_name: str
    soft_budget: float
    max_budget: float
    triggered: bool = False


def _build_category_row_html(b: BudgetCategorySnapshot) -> str:
    """Render one category's budget row for the soft-limit email breakdown table."""

    def _fmt(v: float) -> str:
        return f"${v:,.4f}".rstrip("0").rstrip(".")

    cat_safe = html.escape(b.category.capitalize())
    name_safe = html.escape(b.budget_name)
    soft_v = _fmt(b.soft_budget)
    max_v = _fmt(b.max_budget)
    if b.triggered:
        row_bg = "#2a1a10"
        badge = (
            '<span style="background:#ef4444;color:#fff;font-size:10px;'
            'font-weight:700;padding:2px 7px;border-radius:4px;margin-left:6px'
            '">SOFT LIMIT</span>'
        )
        cat_color = "#f87171"
        fw = "700"
    else:
        row_bg = "#1a1030"
        badge = ""
        cat_color = "#e0e0ff"
        fw = "400"
    return f"""
          <tr>
            <td style="background:{row_bg};padding:10px 14px;border-radius:6px;
                       font-weight:{fw};color:{cat_color}">
              {cat_safe}{badge}<br>
              <span style="font-size:11px;color:#9090b0;font-weight:400">{name_safe}</span>
            </td>
            <td style="background:{row_bg};padding:10px 14px;border-radius:6px;
                       color:{cat_color};font-weight:{fw}">
              {soft_v}
            </td>
            <td style="background:{row_bg};padding:10px 14px;border-radius:6px;color:#9090b0">
              {max_v}
            </td>
          </tr>
          <tr><td colspan="3" style="height:4px"></td></tr>"""


def _build_project_section_html(
    sibling_budgets: list[BudgetCategorySnapshot],
    safe_proj: Optional[str],
) -> str:
    """Build the project overview + category breakdown HTML block for the email."""
    total_budget = sum(b.max_budget for b in sibling_budgets)
    total_fmt = f"${total_budget:,.2f}"
    rows_html = "".join(_build_category_row_html(b) for b in sibling_budgets)
    return f"""
        <!-- Project overview label -->
        <tr>
          <td colspan="5" style="font-size: 16px; font-weight: bold; padding: 32px 20px 16px 20px">
            &#128202; Project Budget Overview
          </td>
        </tr>

        <!-- Project + total -->
        <tr>
          <td colspan="5" style="padding: 0 20px 16px 20px">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
              <tr>
                <td width="50%" style="background:#1a1030;border-radius:8px;padding:14px 16px">
                  <div style="font-size:11px;color:#9090b0;margin-bottom:4px">Project</div>
                  <div style="font-size:17px;font-weight:700;color:#c084fc">{safe_proj}</div>
                </td>
                <td width="4%">&nbsp;</td>
                <td width="46%" style="background:#1a1030;border-radius:8px;padding:14px 16px">
                  <div style="font-size:11px;color:#9090b0;margin-bottom:4px">Total budget</div>
                  <div style="font-size:17px;font-weight:700;color:#ffffff">{total_fmt}</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Category breakdown header -->
        <tr>
          <td colspan="5" style="padding: 0 20px 8px 20px">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
              <tr>
                <td style="font-size:11px;font-weight:600;letter-spacing:0.06em;
                           text-transform:uppercase;color:#9090b0;padding:0 14px 6px 14px">
                  Category
                </td>
                <td style="font-size:11px;font-weight:600;letter-spacing:0.06em;
                           text-transform:uppercase;color:#9090b0;padding:0 14px 6px 14px">
                  Soft limit
                </td>
                <td style="font-size:11px;font-weight:600;letter-spacing:0.06em;
                           text-transform:uppercase;color:#9090b0;padding:0 14px 6px 14px">
                  Max budget
                </td>
              </tr>
              {rows_html}
            </table>
          </td>
        </tr>"""


class EmailService:
    """Email service for sending verification and password reset emails"""

    async def send_email(self, to: str, subject: str, html_body: str) -> bool:
        """Send email via SMTP with TLS

        Args:
            to: Recipient email address
            subject: Email subject
            html_body: HTML email body

        Returns:
            True if email sent successfully

        Raises:
            Exception: On SMTP failure (caller should handle)
        """
        message = EmailMessage()
        message["From"] = f"{config.EMAIL_FROM_NAME} <{config.EMAIL_FROM_ADDRESS}>"
        message["To"] = to
        message["Subject"] = subject
        message.set_content(html_body, subtype="html")

        try:
            await aiosmtplib.send(
                message,
                hostname=config.EMAIL_SMTP_HOST,
                port=config.EMAIL_SMTP_PORT,
                username=config.EMAIL_SMTP_USERNAME,
                password=config.EMAIL_SMTP_PASSWORD,
                start_tls=config.EMAIL_USE_TLS,  # STARTTLS for port 587
                timeout=10,  # 10 second timeout
            )
            logger.info("Email sent successfully to recipient")  # Don't log email address
            return True
        except Exception as e:
            logger.error(f"Failed to send email: {e}", exc_info=True)
            raise  # Let caller handle failure

    async def send_verification_email(self, email: str, token: str) -> bool:
        """Send email verification link

        Args:
            email: Recipient email address
            token: Verification token (raw, not hashed)

        Returns:
            True if sent successfully

        Raises:
            Exception: On SMTP failure (fail-closed for registration)
        """
        verification_url = f"{config.FRONTEND_URL}/verify-email?token={token}"

        subject = "Verify your CodeMie account"
        html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #4F46E5; color: white; padding: 20px; text-align: center; }}
        .content {{ padding: 20px; background-color: #f9f9f9; }}
        .button {{
            display: inline-block;
            background-color: #4F46E5;
            color: white;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 4px;
            margin: 20px 0;
        }}
        .footer {{ font-size: 12px; color: #666; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Welcome to CodeMie!</h1>
        </div>
        <div class="content">
            <p>Thank you for registering. Please verify your email address by clicking the button below:</p>

            <p style="text-align: center;">
                <a href="{verification_url}" class="button">Verify Email Address</a>
            </p>

            <p>Or copy and paste this URL into your browser:</p>
            <p style="word-break: break-all; background: #eee; padding: 10px; font-size: 12px;">
                {verification_url}
            </p>

            <p><strong>This link will expire in 24 hours.</strong></p>

            <p class="footer">
                If you didn't create this account, please ignore this email.
            </p>
        </div>
    </div>
</body>
</html>
"""

        return await self.send_email(email, subject, html_body)

    async def send_password_reset_email(self, email: str, token: str) -> None:
        """Send password reset link (fail-safe for privacy)

        Args:
            email: Recipient email address
            token: Reset token (raw, not hashed)

        Note:
            Never raises exceptions to prevent email enumeration.
            Failures are caught and logged silently.
        """
        try:
            reset_url = f"{config.FRONTEND_URL}/reset-password?token={token}"

            subject = "Reset your CodeMie password"
            html_body = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #4F46E5; color: white; padding: 20px; text-align: center; }}
        .content {{ padding: 20px; background-color: #f9f9f9; }}
        .button {{
            display: inline-block;
            background-color: #4F46E5;
            color: white;
            padding: 12px 24px;
            text-decoration: none;
            border-radius: 4px;
            margin: 20px 0;
        }}
        .warning {{ background-color: #FEF3C7; padding: 10px; border-left: 4px solid #F59E0B; margin: 20px 0; }}
        .footer {{ font-size: 12px; color: #666; margin-top: 20px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Password Reset Request</h1>
        </div>
        <div class="content">
            <p>You requested to reset your CodeMie password. Click the button below to create a new password:</p>

            <p style="text-align: center;">
                <a href="{reset_url}" class="button">Reset Password</a>
            </p>

            <p>Or copy and paste this URL into your browser:</p>
            <p style="word-break: break-all; background: #eee; padding: 10px; font-size: 12px;">
                {reset_url}
            </p>

            <div class="warning">
                <strong>Security Notice:</strong> This link will expire in 24 hours.
            </div>

            <p class="footer">
                If you didn't request this password reset, please ignore this email.
                Your password will remain unchanged.
            </p>
        </div>
    </div>
</body>
</html>
"""

            await self.send_email(email, subject, html_body)
        except Exception as e:
            # Fail-safe: don't reveal whether email exists
            logger.warning(f"Failed to send password reset email: {e}", exc_info=True)

    @staticmethod
    def _build_admin_link(project_name: Optional[str]) -> str:
        """Build the CTA URL: project page if known, otherwise generic budgets list."""
        if project_name:
            from urllib.parse import quote

            return f"{config.FRONTEND_URL}/settings/administration/projects/{quote(project_name, safe='')}"
        return f"{config.FRONTEND_URL}/settings/administration/budgets"

    @staticmethod
    def _build_subject_and_safe_names(
        budget_name: str,
        project_name: Optional[str],
        budget_category: Optional[str],
    ) -> tuple[str, Optional[str], Optional[str]]:
        """Return (email subject, safe_proj, safe_cat) with header injection stripped."""
        subject_name = budget_name.replace("\r", " ").replace("\n", " ")
        if project_name and budget_category:
            subj_proj = project_name.replace("\r", " ").replace("\n", " ")
            subj_cat = budget_category.replace("\r", " ").replace("\n", " ")
            return (
                f"CodeMie budget soft limit reached: {subj_proj} / {subj_cat}",
                html.escape(project_name),
                html.escape(budget_category),
            )
        return f"CodeMie budget soft limit reached: {subject_name}", None, None

    async def send_budget_soft_limit_notification(
        self,
        email: str,
        budget_name: str,
        budget_id: str,
        current_spend: float,
        soft_limit: float,
        project_name: Optional[str] = None,
        budget_category: Optional[str] = None,
        sibling_budgets: Optional[list[BudgetCategorySnapshot]] = None,
    ) -> bool:
        """Send soft-limit reached notification (EPMCDME-13959).

        Caller is expected to catch exceptions; see budget_notification_service.

        Admin-controlled fields (``budget_name``, ``budget_id``) are HTML-escaped
        before insertion into the email body (CR-003) and stripped of CR/LF
        before insertion into the Subject header (CR-004).
        """
        safe_name = html.escape(budget_name)
        safe_id = html.escape(budget_id)
        admin_link = self._build_admin_link(project_name)
        spend_fmt = f"${current_spend:,.4f}".rstrip("0").rstrip(".")
        limit_fmt = f"${soft_limit:,.4f}".rstrip("0").rstrip(".")
        subject, safe_proj, safe_cat = self._build_subject_and_safe_names(budget_name, project_name, budget_category)

        project_section = _build_project_section_html(sibling_budgets, safe_proj) if sibling_budgets else ""

        if safe_proj and safe_cat:
            intro_text = f"The <b>{safe_cat}</b> budget for project <b>{safe_proj}</b> has exceeded its soft limit."
            card_label = "Project / Category"
        else:
            intro_text = "The soft limit for one of your CodeMie budgets has been reached."
            card_label = "Budget"

        html_body = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<title>CodeMie Budget Alert</title>
<style>
  body {{
    margin: 0;
    background: #1c1c1c;
    color: #ffffff;
    font-family: Arial, sans-serif;
    font-size: 14px;
    line-height: 22px;
    -webkit-font-smoothing: antialiased;
  }}
  table {{ border-collapse: collapse; width: 100%; max-width: 650px; margin: 0 auto; }}
  .panel {{ background: #2e2138; }}
</style>
</head>
<body>
<table align="center" width="650" cellpadding="0" cellspacing="0" border="0" role="presentation">
  <tr>
    <td align="center" valign="top" style="
          background-image: url('https://github.com/epam-gen-ai-run/ai-run-install/blob/main/docs/assets/ai/email_bg.png?raw=true');
          background-repeat: no-repeat;
          background-position: top right;">
      <table width="650" cellpadding="0" cellspacing="0" border="0" role="presentation">

        <!-- Logo -->
        <tr>
          <td colspan="5" style="padding: 0 20px">
            <img src="https://github.com/epam-gen-ai-run/ai-run-install/blob/main/docs/assets/ai/email-logo-dark.png?raw=true"
                 alt="EPAM AI/RUN CodeMie" width="156"
                 style="padding-top: 50px; background-color: transparent; display: block">
          </td>
        </tr>

        <!-- Title -->
        <tr>
          <td colspan="5" style="padding: 32px 20px 0 20px; font-size: 32px; line-height: 34px; font-weight: bold">
            Budget Soft Limit Reached
          </td>
        </tr>

        <!-- Intro -->
        <tr>
          <td colspan="5" style="padding: 24px 20px 0 20px">
            {intro_text}
            Review the details below and take action if needed.
          </td>
        </tr>

        <!-- Section label -->
        <tr>
          <td colspan="5" style="font-size: 16px; font-weight: bold; padding: 32px 20px 16px 20px">
            &#9888;&#65039; Triggered Budget
          </td>
        </tr>

        <!-- Alert card -->
        <tr>
          <td colspan="5" style="padding: 0 20px">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
              <tr>
                <td class="panel" style="padding: 24px; border-radius: 8px; border-left: 4px solid #ef4444">
                  <div style="font-size: 11px; font-weight: 600; letter-spacing: 0.08em;
                       text-transform: uppercase; color: #c084fc; margin-bottom: 10px;
                       display: block">{card_label}</div>
                  <b style="font-size: 17px; display: block; margin-bottom: 4px">{safe_name}</b>
                  <div style="font-size: 12px; color: #9090b0; font-family: monospace;
                       margin-bottom: 20px">ID: {safe_id}</div>
                  <!-- Stats row -->
                  <table width="100%" cellpadding="0" cellspacing="0" border="0" role="presentation">
                    <tr>
                      <td width="48%" style="background: #1a1030; border-radius: 8px; padding: 14px 16px">
                        <div style="font-size: 11px; color: #9090b0; margin-bottom: 4px">Current spend</div>
                        <div style="font-size: 22px; font-weight: 700; color: #f87171">{spend_fmt}</div>
                      </td>
                      <td width="4%">&nbsp;</td>
                      <td width="48%" style="background: #1a1030; border-radius: 8px; padding: 14px 16px">
                        <div style="font-size: 11px; color: #9090b0; margin-bottom: 4px">Soft limit</div>
                        <div style="font-size: 22px; font-weight: 700; color: #fbbf24">{limit_fmt}</div>
                      </td>
                    </tr>
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>
{project_section}
        <!-- CTA -->
        <tr>
          <td colspan="5" style="text-align: center; padding: 32px 20px">
            <a href="{admin_link}"
               style="display: inline-block; background: linear-gradient(135deg, #7c3aed, #9333ea);
                      color: #ffffff; text-decoration: none; padding: 13px 32px;
                      border-radius: 8px; font-size: 14px; font-weight: 600; letter-spacing: 0.02em">
              Open Budgets in CodeMie
            </a>
          </td>
        </tr>

        <!-- Footer logo -->
        <tr>
          <td colspan="5" style="text-align: center; padding: 20px 20px 2px 20px">
            <img src="https://github.com/epam-gen-ai-run/ai-run-install/blob/main/docs/assets/ai/email-logo-dark.png?raw=true"
                 alt="EPAM AI/RUN CodeMie" height="25" style="background-color: transparent">
          </td>
        </tr>

        <!-- Sincerely -->
        <tr>
          <td colspan="5" style="padding: 8px 20px 0 20px; text-align: center">
            Sincerely,<br>
            EPAM AI/Run CodeMie Team
          </td>
        </tr>

        <!-- Footer note -->
        <tr>
          <td colspan="5" style="padding: 16px 20px 64px 20px; text-align: center; color: #999; font-size: 12px">
            You are receiving this because you are the notification owner for this budget.<br>
            Update the owner in the CodeMie admin console to reroute these alerts.
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>
</body>
</html>
"""
        return await self.send_email(email, subject, html_body)

    def is_configured(self) -> bool:
        """Check if email service is configured

        Returns:
            True if SMTP settings are configured
        """
        return bool(config.EMAIL_SMTP_HOST and config.EMAIL_FROM_ADDRESS)


# Singleton instance
email_service = EmailService()
