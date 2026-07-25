import sys
import time
import pexpect
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.service import Service

# Force real-time terminal output
sys.stdout.reconfigure(line_buffering=True)

forked_sim = sys.argv[1]
new_sim = sys.argv[2]
history_file = sys.argv[3]
steps = sys.argv[4]

# --- 1. Launch Headless Browser ---
print("Starting Headless Browser...")
chrome_options = Options()
chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()), 
    options=chrome_options
)

driver.set_page_load_timeout(30)

try:
    driver.get("http://127.0.0.1:8000/simulator_home")
    print("Frontend page loaded successfully in background.")
except Exception as e:
    print(f"Browser navigation warning (continuing): {e}")

# --- 2. Start Reverie Simulation ---
print("Starting Reverie Process...")
child = pexpect.spawn("python3 reverie.py", encoding="utf-8", timeout=None)
child.logfile = sys.stdout 

# Send the initial setup inputs reverie.py requests at startup
child.expect(".*") 
child.sendline(forked_sim)

child.expect(".*")
child.sendline(new_sim)

# Now wait for the command option prompt
# child.expect_exact("Enter option:")
# child.sendline(f"call -- load history {history_file}")

child.expect_exact("Enter option:")
child.sendline("run 1")

child.expect_exact("Enter option:")
child.sendline(f"run {steps}")

child.expect_exact("Enter option:")
child.sendline("fin")
child.expect(pexpect.EOF)

# --- 3. Close Browser ---
driver.quit()