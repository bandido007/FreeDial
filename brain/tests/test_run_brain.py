"""Only one brain per machine: two on one ARI app split the commands between them."""

import subprocess
import sys
import time

from brain.management.commands.run_brain import another_brain

# Fixed commands, no outside input: a stand-in brain, and a shell that only mentions one.
STAND_IN_BRAIN = [sys.executable, "-c", "import time; time.sleep(30)", "manage.py", "run_brain"]
SHELL_THAT_MENTIONS_ONE = ["/bin/bash", "-c", "sleep 30 # .venv/bin/python manage.py run_brain &"]


def test_a_running_brain_is_found_but_a_shell_that_mentions_one_is_not():
    brain = subprocess.Popen(STAND_IN_BRAIN)  # noqa: S603
    shell = subprocess.Popen(SHELL_THAT_MENTIONS_ONE)  # noqa: S603
    try:
        time.sleep(0.3)
        found = another_brain()
        assert str(brain.pid) in found
        assert str(shell.pid) not in found, "make dev's own shell must not stop the brain from starting"
    finally:
        brain.kill()
        shell.kill()
