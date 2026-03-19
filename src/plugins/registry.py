"""Plugin registry — discovers, registers, and retrieves evaluator plugins.

Supports three discovery mechanisms (checked in order):
1. Built-in plugins — always loaded (code, document, workflow)
2. Entry points — ``autoimprove.plugins`` group in installed packages
3. Config directories — extra_plugin_dirs in config.yaml
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
import sys
from pathlib import Path

from src.plugins.base import EvaluatorPlugin

logger = logging.getLogger(__name__)


class PluginRegistry:
    """Registry for evaluator plugins with dynamic discovery."""

    def __init__(self) -> None:
        self._plugins: dict[str, EvaluatorPlugin] = {}

    def register(self, plugin: EvaluatorPlugin) -> None:
        """Register a plugin instance.  Warns on duplicate (last one wins)."""
        if plugin.name in self._plugins:
            logger.warning(
                "Plugin '%s' already registered (from %s), replacing with %s",
                plugin.name,
                type(self._plugins[plugin.name]).__module__,
                type(plugin).__module__,
            )
        self._plugins[plugin.name] = plugin

    def get(self, name: str) -> EvaluatorPlugin:
        """Get a plugin by name.  Raises ``KeyError`` if not found."""
        if name not in self._plugins:
            available = ", ".join(self._plugins) or "(none)"
            raise KeyError(f"Plugin '{name}' not found. Available: {available}")
        return self._plugins[name]

    def list_plugins(self) -> list[str]:
        """Return names of all registered plugins."""
        return list(self._plugins)

    def all_plugins(self) -> dict[str, EvaluatorPlugin]:
        """Return all registered plugins."""
        return dict(self._plugins)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover_and_register_defaults(self) -> None:
        """Import and register all built-in plugins."""
        from src.plugins.agent_plugin import AgentPlugin
        from src.plugins.code_plugin import CodePlugin
        from src.plugins.document_plugin import DocumentPlugin
        from src.plugins.workflow_plugin import WorkflowPlugin

        for plugin_cls in (CodePlugin, DocumentPlugin, WorkflowPlugin, AgentPlugin):
            try:
                plugin = plugin_cls()
                self.register(plugin)
            except NotImplementedError:
                pass  # stub plugins are skipped gracefully

    def discover_entrypoints(self) -> None:
        """Discover plugins registered via ``autoimprove.plugins`` entry points.

        Third-party packages can register plugins in their ``pyproject.toml``::

            [project.entry-points."autoimprove.plugins"]
            myplugin = "my_package:MyPlugin"
        """
        try:
            eps = importlib.metadata.entry_points()
            # Python 3.12+ returns a SelectableGroups; 3.10+ has .select()
            if hasattr(eps, "select"):
                plugin_eps = eps.select(group="autoimprove.plugins")
            else:
                plugin_eps = eps.get("autoimprove.plugins", [])
        except Exception:
            return

        for ep in plugin_eps:
            try:
                plugin_cls = ep.load()
                if isinstance(plugin_cls, type) and issubclass(plugin_cls, EvaluatorPlugin):
                    self.register(plugin_cls())
                    logger.info("Loaded entry-point plugin '%s' from %s", ep.name, ep.value)
                elif isinstance(plugin_cls, EvaluatorPlugin):
                    # Entry point returned an instance, not a class
                    self.register(plugin_cls)
                    logger.info("Loaded entry-point plugin '%s' from %s", ep.name, ep.value)
                else:
                    logger.warning(
                        "Entry point '%s' (%s) is not an EvaluatorPlugin subclass, skipping",
                        ep.name, ep.value,
                    )
            except Exception as e:
                logger.warning("Failed to load entry-point plugin '%s': %s", ep.name, e)

    def discover_from_dirs(self, dirs: list[str]) -> None:
        """Discover plugins from extra directories (e.g. ``extra_plugin_dirs`` in config).

        Scans each directory for ``*_plugin.py`` files and imports any
        ``EvaluatorPlugin`` subclasses found.
        """
        for d in dirs:
            dir_path = Path(d)
            if not dir_path.is_dir():
                logger.warning("Plugin directory '%s' does not exist, skipping", d)
                continue

            for py_file in sorted(dir_path.glob("*_plugin.py")):
                try:
                    module_name = f"autoimprove_extra_plugins.{py_file.stem}"
                    spec = importlib.util.spec_from_file_location(module_name, py_file)
                    if spec is None or spec.loader is None:
                        continue
                    mod = importlib.util.module_from_spec(spec)
                    sys.modules[module_name] = mod
                    spec.loader.exec_module(mod)

                    # Find all EvaluatorPlugin subclasses in the module
                    for attr_name in dir(mod):
                        attr = getattr(mod, attr_name)
                        if (
                            isinstance(attr, type)
                            and issubclass(attr, EvaluatorPlugin)
                            and attr is not EvaluatorPlugin
                        ):
                            try:
                                self.register(attr())
                                logger.info("Loaded directory plugin '%s' from %s", attr_name, py_file)
                            except Exception as e:
                                logger.warning("Failed to instantiate plugin '%s' from %s: %s", attr_name, py_file, e)
                except Exception as e:
                    logger.warning("Failed to import plugin from %s: %s", py_file, e)

    def discover_all(self, extra_plugin_dirs: list[str] | None = None) -> None:
        """Run full discovery: builtins → entry points → config directories."""
        self.discover_and_register_defaults()
        self.discover_entrypoints()
        if extra_plugin_dirs:
            self.discover_from_dirs(extra_plugin_dirs)

    # ------------------------------------------------------------------
    # Target detection
    # ------------------------------------------------------------------

    def detect_plugins_for_paths(
        self, paths: list[str], exclude: list[str]
    ) -> dict[str, list[str]]:
        """Auto-detect which plugins apply to which paths.

        Returns ``{plugin_name: [target_files]}`` for plugins that found
        at least one target.
        """
        result: dict[str, list[str]] = {}
        for name, plugin in self._plugins.items():
            targets = plugin.discover_targets(paths, exclude)
            if targets:
                result[name] = targets
        return result
