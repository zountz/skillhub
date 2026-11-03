"""SkillHub —— 统一管理 Skill 与 MCP 的本地中枢.

真源设计:
  Skill 真源:  ~/.agents/skills/<name>/SKILL.md
  MCP  真源:   <project>/data/mcp-servers.json

各 AI 工具通过目录联接 (junction) 指向同一个 skill 真身,
MCP 则由本模块按各家格式分别渲染写入。
"""

__version__ = "0.1.0"
