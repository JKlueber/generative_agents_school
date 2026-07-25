import sys
import pexpect

forked_sim = sys.argv[1]
new_sim = sys.argv[2]
history_file = sys.argv[3]
steps = sys.argv[4]

# Set timeout=None to allow long-running simulations
child = pexpect.spawn("python3 reverie.py", encoding="utf-8", timeout=None)
child.logfile = sys.stdout 

# 1. Answer initial simulation setup prompts
child.expect_exact("Enter the name of the forked simulation:")
child.sendline(forked_sim)

child.expect_exact("Enter the name of the new simulation:")
child.sendline(new_sim)

# 2. Enable headless mode so the backend doesn't wait on a frontend
child.expect_exact("Enter option:")
child.sendline("headless on")

# 3. Wait for main reverie prompt, then load history
child.expect_exact("Enter option:")
child.sendline(f"call -- load history {history_file}")

# 4. Step 1 (warmup)
child.expect_exact("Enter option:")
child.sendline("run 1")

# 5. Step N (main run)
child.expect_exact("Enter option:")
child.sendline(f"run {steps}")

# 6. Exit cleanly
child.expect_exact("Enter option:")
child.sendline("fin")
child.expect(pexpect.EOF)