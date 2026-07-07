import os
import resend
from datetime import datetime

current_dir = os.path.dirname(os.path.abspath(__file__))
resend.api_key = os.getenv("RESEND_API_KEY")


def send_withdraw_otp_email(receiver_email: str, user_name: str, otp: str, amount: float, bank_summary: str) -> bool:
    """
    Sends the 6-digit OTP that the user must enter to confirm a withdraw request.
    Returns True on success, False on failure.
    """
    sender = os.getenv("RESEND_SENDER", "onboarding@resend.dev")
    amount_str = f"\u20a6{amount:,.2f}"

    html_content = f"""
    <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; background-color: #f4f4f4; color: #333; text-align: center; padding: 20px; }}
                .container {{ background-color: #ffffff; padding: 30px; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); display: inline-block; max-width: 480px; }}
                h1 {{ color: #ff6900; margin-top: 0; }}
                .otp {{ font-size: 36px; font-weight: bold; letter-spacing: 8px; color: #ff6900; margin: 24px 0; }}
                .summary {{ background: #FFF5ED; border-radius: 8px; padding: 16px; margin: 20px 0; text-align: left; }}
                .summary-row {{ display: flex; justify-content: space-between; padding: 4px 0; font-size: 14px; }}
                .summary-row .label {{ color: #6B7280; }}
                .summary-row .value {{ font-weight: bold; color: #111827; }}
                p {{ font-size: 15px; line-height: 1.5; }}
                .warning {{ color: #B91C1C; font-size: 13px; margin-top: 16px; }}
                .footer {{ margin-top: 30px; font-size: 12px; color: #888; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>Confirm Your Withdraw Request</h1>
                <p>Hi {user_name},</p>
                <p>Use the code below to confirm your withdraw request. The code is valid for 10 minutes.</p>
                <div class="otp">{otp}</div>
                <div class="summary">
                    <div class="summary-row"><span class="label">Amount</span><span class="value">{amount_str}</span></div>
                    <div class="summary-row"><span class="label">To</span><span class="value">{bank_summary}</span></div>
                </div>
                <p class="warning">If you did not initiate this withdraw, please ignore this email and contact support.</p>
                <div class="footer">\u00a9 {datetime.now().year} JardX. All rights reserved.</div>
            </div>
        </body>
    </html>
    """

    try:
        resend.Emails.send({
            "from": sender,
            "to": [receiver_email],
            "subject": "Confirm your JardX withdraw request",
            "html": html_content,
        })
        print("SUCCESS: Withdraw OTP email sent.")
        return True
    except Exception as e:
        print("ERROR sending withdraw OTP email:", e)
        return False
