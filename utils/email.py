import resend
from config import config
from logging_config import get_logger
from datetime import datetime

logger = get_logger("email")

# Set the API key for the resend SDK
if config.RESEND_API_KEY:
    resend.api_key = config.RESEND_API_KEY

def send_email(to_email: str, subject: str, html_content: str):
    """
    Utility function to send an email using Resend.
    Does nothing if RESEND_API_KEY is not configured.
    """
    if not config.RESEND_API_KEY or config.RESEND_API_KEY == "your_resend_api_key_here":
        logger.warning(f"Resend API key not configured. Mock sending email to {to_email} with subject '{subject}'")
        return None

    try:
        params = {
            "from": config.MAIL_FROM,
            "to": [to_email],
            "subject": subject,
            "html": html_content,
        }
        response = resend.Emails.send(params)
        logger.info(f"Email sent successfully to {to_email}", extra={"data": {"email_id": response.get("id")}})
        return response
    except Exception as e:
        logger.error(f"Failed to send email to {to_email}: {e}", exc_info=True)
        return None


def base_email_template(title: str, preheader: str, content: str, cta_url: str = None, cta_text: str = None, footer_text: str = "") -> str:
    """
    Generates a professional, responsive HTML skeleton for all Yugen Hub emails.
    """
    cta_html = f"""
    <div style="text-align: center; margin: 32px 0;">
        <a href="{cta_url}" style="background-color: #ef4444; color: #ffffff; padding: 14px 28px; text-decoration: none; border-radius: 8px; font-weight: 600; font-size: 16px; display: inline-block;">
            {cta_text}
        </a>
    </div>
    """ if cta_url and cta_text else ""

    return f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{title}</title>
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f3f4f6; margin: 0; padding: 0; -webkit-font-smoothing: antialiased; line-height: 1.6;">
        <!-- Preheader text (hidden in the email body, visible in inbox preview) -->
        <div style="display: none; max-height: 0px; overflow: hidden;">
            {preheader}
        </div>
        
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f3f4f6; margin: 0; padding: 40px 20px;">
            <tr>
                <td align="center">
                    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width: 600px; background-color: #ffffff; border-radius: 12px; overflow: hidden; box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);">
                        <!-- Header -->
                        <tr>
                            <td style="background-color: #111827; padding: 24px; text-align: center;">
                                <h1 style="color: #ffffff; font-size: 24px; margin: 0; font-weight: 700; letter-spacing: -0.5px;">Yugen Hub</h1>
                            </td>
                        </tr>
                        
                        <!-- Body Content -->
                        <tr>
                            <td style="padding: 40px 32px; color: #374151;">
                                {content}
                                {cta_html}
                            </td>
                        </tr>
                        
                        <!-- Footer -->
                        <tr>
                            <td style="background-color: #f9fafb; padding: 24px 32px; text-align: center; border-top: 1px solid #e5e7eb;">
                                <p style="color: #6b7280; font-size: 13px; margin: 0; line-height: 1.5;">
                                    {footer_text}<br>
                                    © {config.ENV == "production" and "2026" or "2024"} Yugen Hub. All rights reserved.
                                </p>
                            </td>
                        </tr>
                    </table>
                </td>
            </tr>
        </table>
    </body>
    </html>
    """

def send_invite_email(to_email: str, org_name: str, frontend_url: str, role: str):
    """
    Sends an invitation email to a new user.
    """
    subject = f"You've been invited to join {org_name} on Yugen Hub"
    
    content = f"""
        <h2 style="color: #111827; font-size: 20px; font-weight: 600; margin-top: 0; margin-bottom: 16px;">Welcome to Yugen Hub!</h2>
        <p style="margin: 0 0 16px 0;">You have been invited to join <strong>{org_name}</strong> as a <strong>{role.title()}</strong>.</p>
        <p style="margin: 0 0 16px 0;">Yugen Hub helps creative teams manage their projects, tasks, and deliverables all in one place. To accept this invitation and get started, simply log in using your Google account.</p>
    """
    
    html_content = base_email_template(
        title="Invitation to Yugen Hub",
        preheader=f"Join {org_name} on Yugen Hub",
        content=content,
        cta_url=f"{frontend_url}/login",
        cta_text="Accept Invitation",
        footer_text="If you didn't expect this invitation, you can safely ignore this email."
    )
    
    return send_email(to_email, subject, html_content)


def send_role_change_email(to_email: str, org_name: str, new_role: str, frontend_url: str):
    """
    Sends a notification that a user's role has been updated.
    """
    subject = f"Your role at {org_name} has been updated"
    
    content = f"""
        <h2 style="color: #111827; font-size: 20px; font-weight: 600; margin-top: 0; margin-bottom: 16px;">Role Update</h2>
        <p style="margin: 0 0 16px 0;">Your access level at <strong>{org_name}</strong> has been updated. You are now designated as a <strong>{new_role.title()}</strong>.</p>
        <p style="margin: 0 0 16px 0;">These changes are effective immediately. If you are currently logged in, you may need to refresh your browser to see the new dashboard options available to your role.</p>
    """

    html_content = base_email_template(
        title="Role Update",
        preheader=f"Your role is now {new_role}",
        content=content,
        cta_url=frontend_url,
        cta_text="Go to Dashboard",
    )
    
    return send_email(to_email, subject, html_content)

def send_task_assignment_email(to_email: str, org_name: str, task_title: str, assigner_name: str, project_title: str, due_date: datetime, frontend_url: str):
    """
    Sends a notification when a user is assigned a new task.
    """
    subject = f"New Task Assigned: {task_title}"
    
    # Format metadata if present
    project_html = f'<p style="margin: 0 0 8px 0; font-size: 15px;"><strong>Project:</strong> {project_title}</p>' if project_title else ""
    due_date_html = f'<p style="margin: 0 0 8px 0; font-size: 15px;"><strong>Due Date:</strong> {due_date.strftime("%B %d, %Y")}</p>' if due_date else ""
    
    content = f"""
        <h2 style="color: #111827; font-size: 20px; font-weight: 600; margin-top: 0; margin-bottom: 16px;">New Task Assignment</h2>
        <p style="margin: 0 0 20px 0;"><strong>{assigner_name}</strong> has assigned you a new task in <strong>{org_name}</strong>.</p>
        
        <div style="background-color: #f9fafb; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; margin-bottom: 24px;">
            <p style="margin: 0 0 8px 0; font-size: 16px; font-weight: 600; color: #111827;">{task_title}</p>
            {project_html}
            {due_date_html}
        </div>
    """

    html_content = base_email_template(
        title="Task Assigned",
        preheader=f"{assigner_name} assigned you: {task_title}",
        content=content,
        cta_url=f"{frontend_url}/tasks",
        cta_text="View Tasks",
    )
    
    return send_email(to_email, subject, html_content)

def send_event_assignment_email(to_email: str, org_name: str, associate_name: str, project_code: str, event_type: str, event_date: datetime, frontend_url: str):
    """
    Sends a notification when an associate is assigned to a project event.
    """
    subject = f"You've been assigned to {event_type} on {project_code}"
    
    date_str = event_date.strftime("%B %d, %Y") if event_date else "Date TBD"
    
    content = f"""
        <h2 style="color: #111827; font-size: 20px; font-weight: 600; margin-top: 0; margin-bottom: 16px;">Event Assignment</h2>
        <p style="margin: 0 0 16px 0;">Hello {associate_name},</p>
        <p style="margin: 0 0 20px 0;">You have been scheduled for an upcoming event by <strong>{org_name}</strong>.</p>
        
        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-bottom: 24px;">
            <tr>
                <td style="padding: 12px; border-bottom: 1px solid #e5e7eb; color: #6b7280; width: 120px;">Project Code</td>
                <td style="padding: 12px; border-bottom: 1px solid #e5e7eb; font-weight: 600; color: #111827;">{project_code}</td>
            </tr>
            <tr>
                <td style="padding: 12px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Event Type</td>
                <td style="padding: 12px; border-bottom: 1px solid #e5e7eb; font-weight: 600; color: #111827;">{event_type}</td>
            </tr>
            <tr>
                <td style="padding: 12px; border-bottom: 1px solid #e5e7eb; color: #6b7280;">Date</td>
                <td style="padding: 12px; border-bottom: 1px solid #e5e7eb; font-weight: 600; color: #111827;">{date_str}</td>
            </tr>
        </table>
    """

    html_content = base_email_template(
        title="Event Assignment",
        preheader=f"Scheduled: {event_type} on {date_str}",
        content=content,
        cta_url=f"{frontend_url}/projects/{project_code}", # Optional deep link logic
        cta_text="View Project",
    )
    
    return send_email(to_email, subject, html_content)
