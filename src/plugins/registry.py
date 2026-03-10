"""Plugin registry — discovers, registers, and retrieves evaluator plugins."""

from __future__ import annotations

from src.plugins.base import EvaluatorPlugin


class PluginRegistry:
    """Singleton-style registry for evaluator plugins."""

    def __init__(self) -> None:
        self._plugins: dict[str, EvaluatorPlugin] = {}

    def register(self, plugin: EvaluatorPlugin) -> None:
        """Register a plugin instance.  Raises if name already registered."""
        if plugin.name in self._plugins:
            raise ValueError(f"Plugin '{plugin.name}' is already registered")
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

    def discover_and_register_defaults(self) -> None:
        """Import and register all built-in plugins."""
        from src.plugins.code_plugin import CodePlugin
        from src.plugins.document_plugin import DocumentPlugin
        from src.plugins.workflow_plugin import WorkflowPlugin

        for plugin_cls in (CodePlugin, DocumentPlugin, WorkflowPlugin):
            try:
                plugin = plugin_cls()
                self.register(plugin)
            except NotImplementedError:
                pass  # stub plugins are skipped gracefully

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
