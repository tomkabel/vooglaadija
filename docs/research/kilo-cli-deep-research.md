# Kilo Code CLI Documentation: Deep Research Report

## Generated: 2026-03-25 | Sources: 12 | Confidence: High

## Executive Summary

The Kilo Code CLI is a powerful terminal-based AI coding assistant built on the OpenCode foundation. It offers extensive customization through skills, workflows, agents, modes, and rules. Current implementation supports skills (`.kilocode/skills/`), workflows (`.kilocode/workflows/`), custom subagents, custom modes, and custom rules. This report identifies gaps, potential improvements, and advanced features that can be instilled based on the current documentation and emerging patterns from the Agent Skills ecosystem.

---

## 1. Current Kilo Code CLI Architecture

### 1.1 Core Components

| Component | Location | Purpose |
|-----------|----------|---------|
| **Skills** | `.kilocode/skills/`, `~/.kilocode/skills/` | Portable instruction packages that auto-activate based on description matching |
| **Workflows** | `.kilocode/workflows/`, `~/.kilocode/workflows/` | Markdown files with step-by-step instructions, invoked via `/filename.md` |
| **Custom Subagents** | `kilo.json` or `.kilo/agents/*.md` | Specialized agents with isolated contexts |
| **Custom Modes** | `custom_modes.yaml`, `.kilocodemodes` | Specialized primary agents with tool restrictions |
| **Custom Rules** | `.kilocode/rules/` | Project/global behavioral rules |
| **Config** | `~/.config/kilo/opencode.json` | Provider, model, permission, MCP settings |

### 1.2 Existing CLI Commands

```text
kilo [project]          # Start TUI
kilo run [message..]    # Non-interactive mode
kilo attach <url>       # Attach to running server
kilo serve              # Headless server
kilo web                # Web interface
kilo auth               # Credential management
kilo agent              # Agent management (create, list)
kilo mcp                # MCP server management
kilo models [provider]  # List available models
kilo stats              # Token usage statistics
kilo session            # Session management
kilo export [sessionID] # Export session as JSON
kilo pr <number>        # Fetch/checkout GitHub PR
kilo github             # GitHub agent
kilo debug              # Debugging tools
kilo completion         # Shell completion
```

### 1.3 Built-in Agents/Modes

| Mode | Type | Description |
|------|------|-------------|
| Code | primary | Default coding mode, full tool access |
| Ask | primary | Read-only Q&A mode |
| Debug | primary | Debugging assistance |
| Architect | primary | Planning/design mode |
| Orchestrator | primary | Workflow coordination via new_task() |
| general | subagent | Research and multi-step tasks |
| explore | subagent | Fast read-only codebase exploration |

---

## 2. Identified Gaps and Improvement Opportunities

### 2.1 Skills System Improvements

**Current State:**

- Skills auto-activate based on description matching
- No slash command invocation (being developed per Issue #6731)
- No built-in skill discovery/install mechanism (Issue #7285 proposes `/learn`)

**Recommended Improvements:**

| Feature | Priority | Description |
|---------|----------|-------------|
| **Slash Command Skills** | High | Implement `/skill-name` invocation as per Issue #6731 |
| **Built-in `/learn` Command** | High | Add agentskill.sh integration for 110K+ skill discovery |
| **Skill Versioning** | Medium | Add version field to SKILL.md for update tracking |
| **Skill Dependencies** | Medium | Support referencing other skills via `uses:` field |
| **Skill Metrics** | Low | Track which skills get used and their success rate |

**Example Enhanced SKILL.md:**

```yaml
---
name: youtube-downloader
description: Download YouTube videos using yt-dlp. Use when downloading media, extracting audio, or processing YouTube URLs.
version: 1.0.0
uses:
  - file-management
  - cli-commands
---
```

### 2.2 Workflow System Enhancements

**Current State:**

- Workflows invoked via `/filename.md`
- Limited to markdown instructions
- No conditional branching
- No variable/parameter passing

**Recommended Improvements:**

| Feature | Priority | Description |
|---------|----------|-------------|
| **Workflow Variables** | High | Allow `{{variable}}` substitution in workflows |
| **Conditional Steps** | Medium | Support `{{#if condition}}...{{/if}}` branching |
| **Workflow Chaining** | Medium | Allow workflows to call other workflows |
| **Workflow History** | Medium | Track execution history and success/failure |
| **Async Workflows** | Low | Support background execution with notifications |

**Example Enhanced Workflow:**

```markdown
# Deploy Workflow

{{#if environment == "production"}}

1. Run `{{validator_command}}` to verify
2. Require explicit approval before proceeding
{{else}}
1. Deploy directly to {{environment}}
{{/if}}
3. Notify team via Slack: "Deployed {{version}} to {{environment}}"
```

### 2.3 Agent System Enhancements

**Current State:**

- Custom subagents via JSON or markdown files
- Limited to `mode: primary|subagent|all`
- Basic permission system

**Recommended Improvements:**

| Feature | Priority | Description |
|---------|----------|-------------|
| **Hierarchical Agents** | High | Support agent teams with parent-child relationships |
| **Agent Communication Protocol** | High | Enable agents to exchange structured data |
| **Dynamic Agent Creation** | Medium | Create agents on-the-fly based on task requirements |
| **Agent Templates** | Medium | Pre-defined agent templates (Code Reviewer, Security Auditor, etc.) |
| **Agent Pool Management** | Low | Pool of idle agents for parallel task execution |

**Example Enhanced Agent Config:**

```yaml
---
name: security-auditor
description: Performs security audits and identifies vulnerabilities
mode: subagent
model: anthropic/claude-sonnet-4-20250514
temperature: 0.1
permission:
  edit: deny
  bash: deny
tools:
  - read
  - grep
  - glob
maxConcurrency: 3
timeout: 300
---
```

### 2.4 Custom Modes System Enhancements

**Current State:**

- YAML/JSON configuration
- Tool groups with fileRegex restrictions
- Sticky models per mode
- Import/export capability

**Recommended Improvements:**

| Feature | Priority | Description |
|---------|----------|-------------|
| **Mode Composability** | Medium | Allow modes to inherit from base modes |
| **Mode Transitions** | Medium | Define smooth transitions between modes |
| **Context Carry-over** | Medium | Preserve context when switching modes |
| **Mode Analytics** | Low | Track which modes are used and when |
| **Mode Suggestions** | Low | AI-powered mode recommendations |

### 2.5 Rules System Enhancements

**Current State:**

- Plain text/Markdown rules in `.kilocode/rules/`
- Project and global scopes
- Mode-specific rules via `.kilocode/rules-${mode}/`

**Recommended Improvements:**

| Feature | Priority | Description |
|---------|----------|-------------|
| **Rule Dependencies** | Medium | Allow rules to include other rules |
| **Rule Priority/Weighting** | Medium | Explicit priority values for conflict resolution |
| **Rule Testing** | Medium | Test rules against sample inputs |
| **Rule Versioning** | Low | Track rule changes over time |
| **Rule Templates** | Low | Pre-built rule sets for common patterns |

### 2.6 CLI Core Improvements

**Current State:**

- Basic TUI, run mode, attach mode
- Session management
- Limited shell integration

**Recommended Improvements:**

| Feature | Priority | Description |
|---------|----------|-------------|
| **Background Jobs** | High | `kilo job` command for background task management |
| **Task Queue** | High | Persistent queue for sequential task processing |
| **WebSocket Streaming** | Medium | Real-time output streaming to web UI |
| **Plugin System** | Medium | Extensible CLI via plugins |
| **Interactive Tutorial** | Medium | Built-in interactive onboarding |
| **Voice Commands** | Low | Voice input support for CLI |

---

## 3. Advanced Feature Recommendations

### 3.1 Multi-Agent Orchestration Enhancements

Based on patterns from superpowers (46k stars), agents (28k stars), and claude-flow (12.6k stars):

| Feature | Description |
|---------|-------------|
| **Agent SWARM Protocol** | Implement swarm-based task distribution |
| **Consensus Voting** | Multiple agents vote on decisions |
| **Debate Mode** | Agents argue different sides of an issue |
| **Chain of Thought** | Agents document reasoning chains |

### 3.2 Persistent Memory Enhancements

Based on patterns from claude-mem (24k stars):

| Feature | Description |
|---------|-------------|
| **Semantic Memory** | Store and retrieve learnings semantically |
| **Project Memory** | Persistent context per project |
| **Cross-Session Context** | Retain context across sessions |
| **Memory Search** | Search through past sessions |

### 3.3 Skills Ecosystem Integration

| Feature | Description |
|---------|-------------|
| **agentskill.sh Integration** | Native `/learn` command for 110K+ skills |
| **Tessl Registry** | Support for Tessl's evaluated skill registry |
| **Skill Marketplace** | Browse and install skills from marketplace |
| **Skill Ratings** | Community ratings and reviews |

### 3.4 CI/CD Integration Enhancements

| Feature | Description |
|---------|-------------|
| **GitHub Actions Native** | `kilo github` improved integration |
| **GitLab CI Support** | Native GitLab CI/CD integration |
| **Jenkins Plugin** | Enterprise Jenkins integration |
| **Container Deploy** | Deploy kilo agents in containers |

### 3.5 IDE Integration Improvements

| Feature | Description |
|---------|-------------|
| **VS Code Web** | Browser-based VS Code extension |
| **Neovim Plugin** | First-class Neovim integration |
| **Emacs Mode** | Emacs major mode for Kilo |
| **JetBrains Fleet** | Support for JetBrains Fleet |

---

## 4. Implementation Priorities

### Priority 1 (Immediate)

1. **Slash Command Skills** - Issue #6731 implementation
1. **Background Jobs** - `kilo job` command
1. **Hierarchical Agents** - Agent teams
1. **agentskill.sh `/learn`** - Issue #7285 implementation

### Priority 2 (Short-term)

1. **Workflow Variables** - Parameterized workflows
1. **Dynamic Agent Creation** - On-the-fly agents
1. **Rule Dependencies** - Composable rules
1. **Mode Composability** - Mode inheritance

### Priority 3 (Medium-term)

1. **Agent Communication Protocol** - Structured agent messaging
1. **Task Queue** - Persistent queue management
1. **Skill Versioning** - Version tracking
1. **Interactive Tutorial** - Onboarding

---

## 5. Key Takeaways

1. **Skills System is Foundation** - The shift toward portable, shareable skills (Agent Skills spec) is the most significant architectural decision. Invest in skill tooling.

1. **Slash Commands Unify UX** - Having both workflows (`/workflow.md`) and skills (proposed `/skill-name`) creates a unified command interface.

1. **Multi-Agent is Emerging** - The ecosystem is moving toward agent swarms and orchestration. Kilo's Orchestrator mode is a good start but needs enhancement.

1. **Memory Persistence is Lacking** - Current sessions are ephemeral. Persistent memory across sessions would significantly improve productivity.

1. **Skill Discovery is Critical** - Without a built-in marketplace/discovery mechanism, skills remain fragmented. The agentskill.sh integration addresses this.

1. **Enterprise Features Needed** - Role-based access, audit logs, compliance modes for regulated industries.

---

## 6. Sources

1. [Kilo Code CLI Documentation](https://kilo.ai/docs/cli) — Official CLI reference
1. [Agent Skills - Kiro](https://kiro.dev/docs/cli/skills/) — Skills specification
1. [Custom Subagents](https://kilo.ai/docs/customize/custom-subagents) — Subagent configuration
1. [Custom Modes](https://kilo.ai/docs/customize/custom-modes) — Mode customization
1. [Custom Rules](https://kilo.ai/docs/customize/custom-rules) — Rules system
1. [Workflows](https://kilo.ai/docs/customize/workflows) — Workflow automation
1. [Skills Features](https://kilo.ai/features/skills) — Skills overview
1. [Issue #6731](https://github.com/Kilo-Org/kilocode/issues/6731) — Slash command skills
1. [Issue #7285](https://github.com/Kilo-Org/kilocode/issues/7285) — agentskill.sh integration
1. [Agent Skills Deep Dive](https://addozhang.medium.com/agent-skills-deep-dive-building-a-reusable-skills-ecosystem-for-ai-agents-ccb1507b2c0f) — Ecosystem analysis
1. [Mintlify Supported Agents](https://mintlify.com/vercel-labs/skills/guides/supported-agents) — Cross-platform skills
1. [Reddit: Kilo CLI Agent Modes](https://www.reddit.com/r/kilocode/comments/1r2e03a/kilo_cli_documentation_on_agent_modes/) — Community feedback

---

## 7. Methodology

Searched 15+ queries across web and news. Analyzed official documentation, GitHub issues, community discussions, and cross-agent ecosystem patterns. Key focus areas: CLI commands, skills system, workflow automation, agent configuration, and emerging patterns from top GitHub repositories.
