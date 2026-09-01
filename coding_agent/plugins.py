from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


_IDENTIFIER = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
    r"(?::[A-Za-z0-9][A-Za-z0-9_-]{0,63})?$"
)
# Keep room for first-party workflow files such as the documents skill while
# still bounding prompt expansion from accidentally huge local files.
MAX_SKILL_BYTES = 64_000
MAX_SELECTED_SKILLS = 8

# These URLs are used when a plugin does not package an icon, or as a network
# fallback. A packaged icon is always preferred because it is the plugin's
# actual artwork. The fallback uses a distinct, recognizable icon from
# Iconify rather than a shared placeholder so a failed local load still keeps
# the plugin visually identifiable.
REMOTE_ICON_URLS = {
    "documents": "https://api.iconify.design/mdi:microsoft-word.svg?color=%232563EB",
    "pdf": "https://api.iconify.design/mdi:file-pdf-box.svg?color=%23DC2626",
    "presentations": "https://api.iconify.design/mdi:microsoft-powerpoint.svg?color=%23C43E1C",
    "spreadsheets": "https://api.iconify.design/mdi:microsoft-excel.svg?color=%23107C41",
    "template-creator": "https://api.iconify.design/mdi:card-text-outline.svg?color=%2310A37F",
    "visualize": "https://api.iconify.design/mdi:chart-timeline-variant-shimmer.svg?color=%230169CC",
    "browser": "https://api.iconify.design/mdi:web.svg?color=%23013B7B",
    "frank-gstack-superpowers": "https://api.iconify.design/mdi:source-branch.svg?color=%2378D5CA",
}


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    keywords: tuple[str, ...]
    path: Path
    plugin_name: str
    content: str


@dataclass
class Plugin:
    name: str
    version: str
    root: Path
    manifest: dict[str, Any]
    skills: list[Skill] = field(default_factory=list)
    tool_paths: list[Path] = field(default_factory=list)
    source: str = "local"


class PluginManager:
    """Discover local Codex-style plugins and index their skills."""

    def __init__(
        self,
        workspace: Path,
        plugin_dirs: Iterable[Path] | None = None,
        *,
        include_defaults: bool = True,
    ) -> None:
        self.workspace = workspace.resolve()
        self.warnings: list[str] = []
        self.plugins: list[Plugin] = []
        self._by_name: dict[str, Plugin] = {}
        self._discover(plugin_dirs, include_defaults=include_defaults)

    @property
    def skills(self) -> list[Skill]:
        return [skill for plugin in self.plugins for skill in plugin.skills]

    @property
    def metadata(self) -> dict[str, Any]:
        return {
            "plugins": [
                self._plugin_metadata(plugin)
                for plugin in self.plugins
            ],
            "skills": [
                {"name": skill.name, "plugin": skill.plugin_name, "path": str(skill.path)}
                for skill in self.skills
            ],
            "warnings": list(self.warnings),
        }

    def _plugin_metadata(self, plugin: Plugin) -> dict[str, Any]:
        interface = plugin.manifest.get("interface")
        interface = interface if isinstance(interface, dict) else {}
        description = str(plugin.manifest.get("description") or "").strip()
        short_description = str(interface.get("shortDescription") or description).strip()
        icon_url, icon_fallback_url = self._plugin_icon_urls(plugin, interface)
        return {
            "name": plugin.name,
            "display_name": str(interface.get("displayName") or plugin.name).strip() or plugin.name,
            "description": description,
            "short_description": short_description,
            "version": plugin.version,
            "path": str(plugin.root),
            "skill_count": len(plugin.skills),
            "brand_color": str(interface.get("brandColor") or "").strip(),
            "icon_url": icon_url,
            "icon_fallback_url": icon_fallback_url,
        }

    @staticmethod
    def _github_raw_url(repository: Any, asset: Any) -> str:
        if not isinstance(repository, str) or not isinstance(asset, str):
            return ""
        parsed = urlparse(repository.strip())
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"github.com", "www.github.com"}:
            return ""
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            return ""
        owner, repo = parts[0], parts[1].removesuffix(".git")
        asset_path = asset.strip().replace("\\", "/").lstrip("./")
        if not owner or not repo or not asset_path or ".." in asset_path.split("/"):
            return ""
        return f"https://raw.githubusercontent.com/{owner}/{repo}/main/{asset_path}"

    @staticmethod
    def _manifest_icon_value(interface: dict[str, Any], manifest: dict[str, Any]) -> str:
        for key in ("iconURL", "iconUrl", "icon_url", "logoURL", "logoUrl", "logo_url"):
            value = interface.get(key, manifest.get(key))
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def icon_path(self, plugin_name: str) -> Path | None:
        """Return a plugin-declared local icon, constrained to its root."""
        plugin = self._by_name.get(str(plugin_name))
        if plugin is None:
            return None
        interface = plugin.manifest.get("interface")
        interface = interface if isinstance(interface, dict) else {}
        values: list[str] = []
        for key in ("composerIcon", "logo", "logoDark", "icon"):
            value = interface.get(key, plugin.manifest.get(key))
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
        for value in values:
            parsed = urlparse(value)
            if parsed.scheme or parsed.netloc:
                continue
            candidate = (plugin.root / value).resolve()
            try:
                candidate.relative_to(plugin.root.resolve())
            except ValueError:
                continue
            if candidate.is_file() and candidate.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"}:
                return candidate
        return None

    def _plugin_icon_urls(self, plugin: Plugin, interface: dict[str, Any]) -> tuple[str, str]:
        local = f"/api/plugin-icon?plugin={plugin.name}" if self.icon_path(plugin.name) else ""
        explicit = self._manifest_icon_value(interface, plugin.manifest)
        if explicit.startswith(("https://", "http://")):
            return explicit, local
        composer = interface.get("composerIcon")
        repository_icon = self._github_raw_url(plugin.manifest.get("repository"), composer)
        # First-party assets are distributed in the local plugin cache. Their
        # openai/openai repository paths are not public asset URLs, so Iconify
        # is the useful web fallback for those plugins.
        remote = REMOTE_ICON_URLS.get(plugin.name.lower(), "") or repository_icon
        if local:
            return local, remote
        return remote, ""

    def _candidate_dirs(self, explicit: Iterable[Path] | None, *, include_defaults: bool) -> list[tuple[Path, str]]:
        candidates: list[tuple[Path, str]] = []
        for path in explicit or ():
            candidates.append((Path(path).expanduser(), "explicit"))
        if include_defaults:
            candidates.append((self.workspace / ".codex" / "plugins", "project"))
            user_root = Path.home() / ".codex" / "plugins"
            candidates.append((user_root, "user"))
            candidates.append((Path.home() / ".codex" / "skills", "user-skills"))
            # Codex's local cache is where first-party and personal plugins are
            # commonly installed. It is optional and silently ignored if absent.
            for cache_root in (
                user_root / "cache" / "personal",
                user_root / "cache" / "openai-primary-runtime",
                user_root / "cache" / "openai-bundled",
            ):
                if cache_root.is_dir():
                    candidates.append((cache_root, "cache"))
            candidates.append((Path(__file__).with_name("builtin_plugins"), "builtin"))
        return candidates

    def _discover(self, explicit: Iterable[Path] | None, *, include_defaults: bool) -> None:
        seen_roots: set[Path] = set()
        seen_plugin_roots: set[Path] = set()
        for directory, source in self._candidate_dirs(explicit, include_defaults=include_defaults):
            directory = directory.resolve()
            if directory in seen_roots or not directory.exists():
                continue
            seen_roots.add(directory)
            if source == "user-skills":
                self._load_plugin(
                    directory,
                    source,
                    manifest={
                        "name": "user-skills",
                        "version": "0",
                        "description": "User-installed local skills",
                        "skills": ".",
                    },
                )
                continue
            roots = [directory] if (directory / ".codex-plugin" / "plugin.json").is_file() else []
            if directory.is_dir() and not roots:
                try:
                    if source == "cache":
                        for manifest_path in sorted(
                            directory.rglob(".codex-plugin/plugin.json"),
                            key=lambda item: str(item).lower(),
                        ):
                            root = manifest_path.parent.parent.resolve()
                            try:
                                root.relative_to(directory)
                            except ValueError:
                                continue
                            roots.append(root)
                    else:
                        roots.extend(
                            child
                            for child in sorted(directory.iterdir(), key=lambda item: item.name.lower())
                            if child.is_dir() and (child / ".codex-plugin" / "plugin.json").is_file()
                        )
                except OSError as exc:
                    self.warnings.append(f"插件目录不可读 {directory}: {exc}")
                    continue
            for root in roots:
                root = root.resolve()
                if root in seen_plugin_roots:
                    continue
                try:
                    root.relative_to(directory)
                except ValueError:
                    continue
                seen_plugin_roots.add(root)
                self._load_plugin(root, source)

    def _load_plugin(
        self,
        root: Path,
        source: str,
        *,
        manifest: dict[str, Any] | None = None,
    ) -> None:
        manifest_path = root / ".codex-plugin" / "plugin.json"
        try:
            if manifest is None:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest must be a JSON object")
            name = manifest.get("name")
            if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
                raise ValueError("manifest name is not a safe identifier")
            version = str(manifest.get("version") or "0")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self.warnings.append(f"跳过无效插件 {manifest_path}: {exc}")
            return
        if name in self._by_name:
            existing = self._by_name[name]
            # The bundled PDF adapter supplies a callable tool while the
            # first-party cached PDF plugin supplies richer skill guidance.
            # Keep the higher-priority metadata and augment missing adapters.
            if source == "builtin" and not existing.tool_paths:
                bundled = Plugin(name=name, version=version, root=root.resolve(), manifest=manifest)
                existing.tool_paths.extend(self._tool_paths(bundled))
                self.warnings.append(f"插件名称重复，已为 {name} 补充内置工具适配器")
            else:
                self.warnings.append(f"插件名称冲突，保留优先来源 {name}: {root}")
            return
        plugin = Plugin(name=name, version=version, root=root.resolve(), manifest=manifest, source=source)
        plugin.skills = self._load_skills(plugin)
        plugin.tool_paths = self._tool_paths(plugin)
        self._by_name[name] = plugin
        self.plugins.append(plugin)

    def _tool_paths(self, plugin: Plugin) -> list[Path]:
        raw = plugin.manifest.get("tools", [])
        if isinstance(raw, str):
            raw = [raw]
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            self.warnings.append(f"插件 {plugin.name} 的 tools 必须是字符串或数组")
            return []
        paths: list[Path] = []
        for item in raw:
            if not isinstance(item, str):
                self.warnings.append(f"插件 {plugin.name} 忽略非法工具路径")
                continue
            path = (plugin.root / item).resolve()
            try:
                path.relative_to(plugin.root.resolve())
            except ValueError:
                self.warnings.append(f"插件 {plugin.name} 的工具路径越界: {item}")
                continue
            if path.suffix != ".py" or not path.is_file():
                self.warnings.append(f"插件 {plugin.name} 的工具模块不存在或不是 Python 文件: {item}")
                continue
            paths.append(path)
        return paths

    def _load_skills(self, plugin: Plugin) -> list[Skill]:
        raw = plugin.manifest.get("skills", "skills")
        if isinstance(raw, str):
            skill_root = (plugin.root / raw).resolve()
            roots = [skill_root]
        elif isinstance(raw, list):
            roots = [(plugin.root / item).resolve() for item in raw if isinstance(item, str)]
        else:
            self.warnings.append(f"插件 {plugin.name} 的 skills 配置无效")
            return []
        result: list[Skill] = []
        for skill_root in roots:
            try:
                skill_root.relative_to(plugin.root.resolve())
            except ValueError:
                self.warnings.append(f"插件 {plugin.name} 的技能目录越界")
                continue
            if not skill_root.is_dir():
                continue
            try:
                skill_files = sorted(skill_root.glob("*/SKILL.md"), key=lambda item: str(item).lower())
            except OSError as exc:
                self.warnings.append(f"读取插件 {plugin.name} 技能目录失败: {exc}")
                continue
            for path in skill_files:
                try:
                    path.resolve().relative_to(skill_root.resolve())
                except ValueError:
                    self.warnings.append(f"插件 {plugin.name} 的技能文件越界: {path}")
                    continue
                name, description, keywords, content = self._read_skill(path, plugin.name)
                if name:
                    result.append(Skill(name, description, keywords, path, plugin.name, content))
        return result

    def _read_skill(self, path: Path, plugin_name: str) -> tuple[str | None, str, tuple[str, ...], str]:
        try:
            if path.stat().st_size > MAX_SKILL_BYTES:
                raise ValueError(f"技能文件超过 {MAX_SKILL_BYTES} 字节")
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self.warnings.append(f"跳过技能 {path}: {exc}")
            return None, "", (), ""
        front: dict[str, str] = {}
        body = content
        lines = content.splitlines()
        if lines and lines[0].strip() == "---":
            end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
            if end is not None:
                for line in lines[1:end]:
                    if ":" in line:
                        key, value = line.split(":", 1)
                        front[key.strip().lower()] = value.strip().strip('"\'')
                body = "\n".join(lines[end + 1:])
        name = front.get("name") or path.parent.name
        if not _IDENTIFIER.fullmatch(name):
            self.warnings.append(f"跳过技能 {path}: 名称不是安全标识符")
            return None, "", (), ""
        description = front.get("description", "")
        keywords_raw = front.get("keywords", "")
        keywords = tuple(item.strip() for item in keywords_raw.split(",") if item.strip())
        if not description:
            description = next((line.strip().lstrip("#").strip() for line in body.splitlines() if line.strip()), "")
        return name, description, keywords, content

    def select_skills(self, task: str, explicit: Iterable[str] | None = None) -> list[Skill]:
        text = task or ""
        requested = [str(item).lstrip("@/") for item in (explicit or ()) if str(item).strip()]
        if not requested:
            requested = re.findall(
                r"(?:@|/)([A-Za-z0-9][A-Za-z0-9_-]{0,63}"
                r"(?::[A-Za-z0-9][A-Za-z0-9_-]{0,63})?)",
                text,
            )
        by_key = {skill.name.lower(): skill for skill in self.skills}
        by_key.update({f"{skill.plugin_name.lower()}:{skill.name.lower()}": skill for skill in self.skills})
        plugins_by_key = {plugin.name.lower(): plugin for plugin in self.plugins}
        selected: list[Skill] = []
        matched_plugin = False
        for key in requested:
            plugin = plugins_by_key.get(key.lower())
            if plugin:
                matched_plugin = True
                for skill in plugin.skills:
                    if skill not in selected:
                        selected.append(skill)
                    if len(selected) >= MAX_SELECTED_SKILLS:
                        break
                if len(selected) >= MAX_SELECTED_SKILLS:
                    break
                continue
            skill = by_key.get(key.lower())
            if skill and skill not in selected:
                selected.append(skill)
            if len(selected) >= MAX_SELECTED_SKILLS:
                break
        if selected or matched_plugin:
            return selected[:MAX_SELECTED_SKILLS]
        lowered = text.lower()
        for skill in self.skills:
            haystack = " ".join((skill.name, skill.description, *skill.keywords)).lower()
            if any(token and token in lowered for token in haystack.split()):
                selected.append(skill)
            if len(selected) >= MAX_SELECTED_SKILLS:
                break
        return selected

    def system_prompt_addendum(self, task: str, explicit: Iterable[str] | None = None) -> str:
        selected = self.select_skills(task, explicit)
        if not selected:
            return ""
        sections = ["\n\nLoaded local skills (workflow guidance only; task and safety policy take precedence):"]
        for skill in selected:
            sections.append(f"\n--- skill {skill.plugin_name}/{skill.name} ({skill.path}) ---\n{skill.content}\n--- end skill ---")
        return "".join(sections)

    def load_tools(self, registry: Any) -> None:
        from .plugin_tools import load_plugin_tools

        load_plugin_tools(self, registry)
