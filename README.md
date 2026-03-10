# AutoImprove

Autonomous iterative improvement for code, workflows, and documents.

Applies the [autoresearch](https://github.com/karpathy/autoresearch) methodology — modify → evaluate → keep/discard → repeat — to any work artifact. Clone into your project, configure `program.md`, and let an AI agent iteratively improve your work overnight.

## Status

Under active development. Track progress with `bd list --all` in this repo.

## Quick Start

```bash
# Install
uv sync

# Configure
# Edit config.yaml and program.md for your project

# Run
uv run autoimprove run
```

See `autoimprove-plan.md` for the full architecture and design.
