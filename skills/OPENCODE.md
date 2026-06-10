# Using the volltreffer skills from OpenCode

The skills in this folder are plain [Agent Skills](https://opencode.ai/docs/skills/)
(a `SKILL.md` + a stdlib Python client). OpenCode auto-discovers any
`SKILL.md` under `.opencode/skills/`, `.claude/skills/`, or `.agents/skills/`
(walking up to the git root) and globally under `~/.config/opencode/skills/`.
You don't need the rest of this repo — just a running volltreffer instance, your
token, and the one skill folder. Here's the empty-directory setup.

## Prerequisites

- [OpenCode](https://opencode.ai) installed (`opencode` on your PATH) and a model configured.
- A reachable volltreffer instance (e.g. `http://localhost:8000`).
- **Personal API token** for the `my-predictions` skill — generate it on volltreffer's *Settings* tab. (For the admin/automation `update-predictions` skill, use the server's `AUTOMATION_TOKEN` instead.)

## 1 · Lay out the skill in an empty directory

```bash
mkdir volltreffer-agent && cd volltreffer-agent
git init -q                                   # OpenCode scopes project skills to the git worktree
mkdir -p .opencode/skills/my-predictions

# pull the skill from GitHub (or copy the folder from this repo)
BASE=https://raw.githubusercontent.com/ChristianKniep/volltreffer/main/skills/my-predictions
curl -fsSL "$BASE/SKILL.md"          -o .opencode/skills/my-predictions/SKILL.md
curl -fsSL "$BASE/my_predictions.py" -o .opencode/skills/my-predictions/my_predictions.py
```

Layout:

```
volltreffer-agent/
├─ .opencode/skills/my-predictions/
│  ├─ SKILL.md
│  └─ my_predictions.py
└─ AGENTS.md           # created in step 2
```

## 2 · Tell OpenCode about it (AGENTS.md)

OpenCode reads project rules from `AGENTS.md`. Create one so the agent knows the
skill exists and how the script is configured:

```bash
cat > AGENTS.md <<'EOF'
# volltreffer prediction agent

Use the **my-predictions** skill to review and update *my own* World Cup match
predictions in volltreffer. The skill's client is at
`.opencode/skills/my-predictions/my_predictions.py` and reads `VOLLTREFFER_URL`
and `VOLLTREFFER_TOKEN` from the environment.

- Always read my current view first (`my_predictions.py upcoming`) before proposing changes.
- Propose a revised scoreline per match with a one-line rationale; only write after I agree.
- Never invent news — reason only from what I provide.
EOF
```

## 3 · Configure the connection

The client reads two environment variables; export them in the shell **before**
launching OpenCode so its bash tool inherits them:

```bash
export VOLLTREFFER_URL="http://localhost:8000"
export VOLLTREFFER_TOKEN="vt_…"     # from volltreffer → Settings → Personal API token
```

## 4 · Run it

```bash
opencode
```

OpenCode loads the skill on demand. A first prompt to try:

```
Use the my-predictions skill. Let's review Group C after matchday 2.
Here's the coverage I gathered: <paste articles / notes>.

Go match by match through my upcoming fixtures: show the current prediction,
weigh the news, and propose a revised scoreline with a one-line rationale.
When I say go, update my prediction for that match, then show me the diff.
```

The agent will run `my_predictions.py upcoming` to read your view, discuss each
match, and on your OK call `my_predictions.py set <id> --home H --away A
--rationale "…"`. `clear <id>` reverts a match to the model.

## Notes

- The same SKILL.md works in Claude Code / the Claude Agent SDK — drop it under `.claude/skills/` instead, or install globally at `~/.config/opencode/skills/my-predictions/`.
- Admin/automation tuning of the **shared** model uses the sibling `update-predictions` skill the same way, with `AUTOMATION_TOKEN` instead of a personal token.
- Skill not showing up? Ensure the file is named `SKILL.md` (all caps) with `name` + `description` frontmatter, and that you're inside the git repo you created.
