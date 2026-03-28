import logging
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, To, From, Subject, HtmlContent

from app.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self) -> None:
        self.client = SendGridAPIClient(api_key=settings.SENDGRID_API_KEY)
        self.from_email = settings.SENDGRID_FROM_EMAIL

    def _send(self, to_email: str, subject: str, html_content: str) -> None:
        message = Mail(
            from_email=From(self.from_email, "Baseball Rules App"),
            to_emails=To(to_email),
            subject=Subject(subject),
            html_content=HtmlContent(html_content),
        )
        try:
            response = self.client.send(message)
            logger.info("Email sent to %s — status %s", to_email, response.status_code)
        except Exception as exc:
            logger.error("Failed to send email to %s: %s", to_email, exc)

    def send_welcome_email(self, email: str, name: str) -> None:
        subject = "Welcome to Baseball Rules App!"
        html_content = f"""
        <html>
          <body style="font-family: Arial, sans-serif; color: #1a1a1a;">
            <h2>Welcome, {name}!</h2>
            <p>Your account has been created successfully.</p>
            <p>You now have access to the official baseball rules, searchable and always up to date.</p>
            <p>
              <a href="{settings.FRONTEND_URL}" style="background:#003087;color:#fff;padding:10px 20px;
                 text-decoration:none;border-radius:4px;">Open the App</a>
            </p>
            <p style="font-size:12px;color:#888;">If you did not create this account, please ignore this email.</p>
          </body>
        </html>
        """
        self._send(email, subject, html_content)

    def send_password_reset_email(self, email: str, reset_url: str) -> None:
        subject = "Reset Your Baseball Rules App Password"
        html_content = f"""
        <html>
          <body style="font-family: Arial, sans-serif; color: #1a1a1a;">
            <h2>Password Reset Request</h2>
            <p>We received a request to reset the password for your account.</p>
            <p>Click the button below to set a new password. This link expires in 1 hour.</p>
            <p>
              <a href="{reset_url}" style="background:#003087;color:#fff;padding:10px 20px;
                 text-decoration:none;border-radius:4px;">Reset Password</a>
            </p>
            <p>If you did not request a password reset, you can safely ignore this email.</p>
            <p style="font-size:12px;color:#888;">Link: {reset_url}</p>
          </body>
        </html>
        """
        self._send(email, subject, html_content)


email_service = EmailService()
