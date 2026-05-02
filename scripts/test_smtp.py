import smtplib, os
user = os.environ.get("SMTP_USER", "")
pwd  = os.environ.get("SMTP_PASSWORD", "")
print(f"SMTP_USER: {user}")
print(f"SMTP_PASSWORD length: {len(pwd)}, repr: {repr(pwd)}")
try:
    smtp = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
    smtp.ehlo()
    smtp.starttls()
    smtp.login(user, pwd)
    print("LOGIN OK")
    smtp.quit()
except Exception as e:
    print(f"FAILED: {type(e).__name__}: {e}")
