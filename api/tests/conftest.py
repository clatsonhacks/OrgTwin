import os

# Tests run in demo mode: don't pick up real keys from the repo-root .env.
os.environ["ORGTWIN_NO_DOTENV"] = "1"
