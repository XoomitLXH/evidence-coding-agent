from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from coding_agent.plugins import MAX_SKILL_BYTES, PluginManager


class PluginManagerTests(unittest.TestCase):
    def make_plugin(
        self,
        root: Path,
        name: str,
        *,
        version: str = "1.0.0",
        skill: str | None = None,
        interface: dict[str, object] | None = None,
        repository: str | None = None,
    ) -> Path:
        plugin = root / name
        (plugin / ".codex-plugin").mkdir(parents=True)
        manifest: dict[str, object] = {"name": name, "version": version}
        if interface is not None:
            manifest["interface"] = interface
        if repository is not None:
            manifest["repository"] = repository
        if skill is not None:
            (plugin / "skills" / "demo").mkdir(parents=True)
            (plugin / "skills" / "demo" / "SKILL.md").write_text(skill, encoding="utf-8")
        (plugin / ".codex-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        return plugin

    def test_discovers_manifest_and_indexes_skill_metadata(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_plugin(root, "quality", skill="---\nname: linting\ndescription: run checks\nkeywords: lint, test\n---\n# Linting\n")

            manager = PluginManager(root, [root], include_defaults=False)

            self.assertEqual([plugin.name for plugin in manager.plugins], ["quality"])
            self.assertEqual(manager.skills[0].name, "linting")
            self.assertEqual(manager.skills[0].keywords, ("lint", "test"))
            self.assertEqual(manager.metadata["skills"][0]["plugin"], "quality")

    def test_project_plugin_wins_over_user_plugin_with_same_name(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / ".codex" / "plugins"
            user = root / "user-plugins"
            self.make_plugin(project, "shared", version="project")
            self.make_plugin(user, "shared", version="user")

            manager = PluginManager(root, [project, user], include_defaults=False)

            self.assertEqual(len(manager.plugins), 1)
            self.assertEqual(manager.plugins[0].version, "project")
            self.assertTrue(any("名称冲突" in warning for warning in manager.warnings))

    def test_invalid_manifest_and_oversized_skill_are_reported(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = root / "invalid" / ".codex-plugin"
            invalid.mkdir(parents=True)
            (invalid / "plugin.json").write_text("{broken", encoding="utf-8")
            huge = self.make_plugin(root, "huge", skill="x" * (MAX_SKILL_BYTES + 1))

            manager = PluginManager(root, [root], include_defaults=False)

            self.assertEqual([plugin.name for plugin in manager.plugins], ["huge"])
            self.assertEqual(manager.plugins[0].skills, [])
            self.assertGreaterEqual(len(manager.warnings), 2)

    def test_loads_official_skill_slightly_over_40_kb(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            content = "# Documents\n" + ("x" * 42_000)
            self.make_plugin(root, "documents", skill=content)

            manager = PluginManager(root, [root], include_defaults=False)

            self.assertEqual([skill.name for skill in manager.skills], ["demo"])

    def test_explicit_and_automatic_skill_selection(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_plugin(root, "workflow", skill="---\nname: review\ndescription: review code changes\nkeywords: audit\n---\nReview instructions\n")
            manager = PluginManager(root, [root], include_defaults=False)

            self.assertEqual(manager.select_skills("anything", ["@review"])[0].name, "review")
            self.assertEqual(manager.select_skills("please audit this change")[0].name, "review")
            self.assertIn("Review instructions", manager.system_prompt_addendum("/review"))

    def test_plugin_metadata_prefers_declared_local_icon_over_repository_guess(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = self.make_plugin(
                root,
                "quality",
                repository="https://github.com/acme/quality",
                interface={"composerIcon": "./assets/icon.svg"},
            )
            (plugin / "assets").mkdir()
            (plugin / "assets" / "icon.svg").write_text("<svg/>", encoding="utf-8")

            manager = PluginManager(root, [root], include_defaults=False)

            metadata = manager.metadata["plugins"][0]
            self.assertEqual(metadata["icon_url"], "/api/plugin-icon?plugin=quality")
            self.assertEqual(
                metadata["icon_fallback_url"],
                "https://raw.githubusercontent.com/acme/quality/main/assets/icon.svg",
            )

    def test_plugin_icon_path_only_exposes_discovered_plugin_assets(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            plugin = self.make_plugin(root, "quality", interface={"composerIcon": "assets/icon.svg"})
            (plugin / "assets").mkdir()
            icon = plugin / "assets" / "icon.svg"
            icon.write_text("<svg/>", encoding="utf-8")
            manager = PluginManager(root, [root], include_defaults=False)

            self.assertEqual(manager.icon_path("quality"), icon.resolve())
            self.assertIsNone(manager.icon_path("missing"))

    def test_builtin_plugin_network_icons_are_distinct(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("documents", "pdf", "presentations", "spreadsheets", "template-creator", "frank-gstack-superpowers"):
                self.make_plugin(root, name)

            manager = PluginManager(root, [root], include_defaults=False)
            icons = {plugin.name: manager.metadata["plugins"][index]["icon_url"] for index, plugin in enumerate(manager.plugins)}

            self.assertEqual(len(set(icons.values())), len(icons))
            self.assertTrue(all(str(url).startswith("https://api.iconify.design/") for url in icons.values()))
            self.assertTrue(all("lucide:" not in str(url) for url in icons.values()))


if __name__ == "__main__":
    unittest.main()
