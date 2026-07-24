import sys
import pexpect

forked_sim = sys.argv[1]
new_sim = sys.argv[2]
history_file = sys.argv[3]
steps = sys.argv[4]

# Start the interactive script
child = pexpect.spawn("python3 reverie.py", encoding="utf-8", timeout=3600)
child.logfile = sys.stdout  # See output in GitHub Actions logs

# 1. Answer initial simulation prompts
child.expect(".*")  # Wait for first prompt
child.sendline(forked_sim)

child.expect(".*")
child.sendline(new_sim)

# 2. Wait for main reverie prompt, then load history
child.expect_exact("Enter option:")
child.sendline(f"call -- load history {history_file}")

# 3. Wait for loading to finish, then run 1 step
child.expect_exact("Enter option:")
child.sendline(f"run 1")

# 4. Wait for loading to finish, then run steps
child.expect_exact("Enter option:")
child.sendline(f"run {steps}")

# 5. Wait for run to finish and exit cleanly
child.expect_exact("Enter option:")
child.sendline("fin")
child.expect(pexpect.EOF)