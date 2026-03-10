#!/usr/bin/env python3
"""Mock agent for E2E testing.

Reads a prompt file, makes a small deterministic improvement to the
sample project, and prints a response. Used instead of a real agent
so tests can run without API keys.
"""

import sys
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Mock agent: no prompt file provided")
        sys.exit(1)

    prompt_file = sys.argv[1]
    prompt = Path(prompt_file).read_text()

    # Detect mode from prompt content
    if "Grounding Phase" in prompt:
        # Return criteria JSON
        print("""{
  "criteria": [
    {"name": "test_pass_rate", "description": "All tests pass", "weight": 0.0, "is_hard_gate": true, "metric_type": "deterministic"},
    {"name": "lint_score", "description": "Fewer lint issues", "weight": 0.35, "is_hard_gate": false, "metric_type": "deterministic"},
    {"name": "readability", "description": "Code is readable", "weight": 0.35, "is_hard_gate": false, "metric_type": "judgment"},
    {"name": "error_handling", "description": "Proper error handling", "weight": 0.30, "is_hard_gate": false, "metric_type": "judgment"}
  ],
  "hypotheses": [
    {"description": "Add error handling to file operations", "expected_impact": "high", "files_affected": ["main.py"], "risk": "low"},
    {"description": "Remove duplicated code in utils.py", "expected_impact": "medium", "files_affected": ["utils.py"], "risk": "low"}
  ]
}""")
    elif "Criteria Review" in prompt:
        print('{"changes": [], "rationale": "Criteria are working well."}')
    else:
        # Improvement iteration — make a small change
        # Find utils.py in the working directory and fix the bare except
        cwd = Path(".")
        utils = cwd / "utils.py"
        if utils.exists():
            content = utils.read_text()
            if "except:" in content:
                content = content.replace("except:", "except Exception:")
                utils.write_text(content)
                print("Hypothesis: Replace bare except clauses with explicit Exception catches in utils.py")
                print("Changed bare `except:` to `except Exception:` for better error handling.")
            elif "unused_var = 42" in content:
                content = content.replace("    unused_var = 42\n", "")
                utils.write_text(content)
                print("Hypothesis: Remove unused variable in utils.py fmt function")
                print("Removed unused_var assignment.")
            else:
                print("Hypothesis: No obvious improvements remaining")
                print("All known issues already fixed.")
        else:
            print("Hypothesis: No utils.py found to improve")


if __name__ == "__main__":
    main()
