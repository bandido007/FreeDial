# Updating a project from the starter

A project created with `init_project.py` diverges from the starter the moment its
first business app lands. The starter keeps improving (a security fix in
`authentication`, a new export format, a better deploy script). This is how to bring
those improvements over without losing your work.

## 1. Keep the starter reachable

Once, in the project:

```bash
git remote add starter <starter-repo-url>
git fetch starter
```

If the project was created with `--reset-git` there is no shared history; the
first merge is a one-off (see §4). Without `--reset-git` the histories are shared
and `git merge starter/main` just works.

## 2. What is safe to take

The starter is layered so that updates land in files you do not edit:

| Area | Files | Your edits there |
|------|-------|------------------|
| Foundation apps | `apps/core`, `apps/users`, `apps/rbac`, `apps/authentication`, `apps/notifications`, `apps/audit`, `apps/attachments`, `apps/configuration`, `apps/workflows`, `apps/sso` | **None, by design.** Extend them from your own apps (signals, hooks, registries) rather than editing them, and merges stay clean |
| Tooling | `Makefile`, `docker-compose.yml`, `.gitlab-ci.yml`, `.pre-commit-config.yaml`, `scripts/`, `deploy/`, `requirements/*.in` | Rarely; keep project-specific additions below a `# ── project ──` line so a merge shows them apart |
| Configuration | `config/settings/*.py`, `config/urls.py`, `config/modules.py`, `pyproject.toml` | Only at the `# <scaffold:…>` markers (the generator) and in obviously project-specific blocks |
| Docs | `docs/*.md` | `ARCHITECTURE`, `SECURITY`, `ADDING_AN_APP` are the starter's; `API.md` grows with your modules |
| Generated | `docs/schema.graphql`, `docs/WORKFLOW_CATALOGUE.md`, `requirements/*.txt` | Never merged by hand — regenerate after the merge |

If you find yourself needing to change a foundation app, prefer contributing the
change to the starter and taking it back through an update.

## 3. The update procedure

```bash
git fetch starter
git merge starter/main            # or: git merge starter/v1.2.0
```

Resolve conflicts (they should only be in the config files above). Then:

```bash
make deps                          # only if requirements/*.in changed
make venv                          # install what changed
make migrate                       # the starter may ship migrations
make seed                          # catalogue changes (new permissions, templates, settings)
make schema && make workflow-docs  # regenerate the snapshots
make ci                            # lint, types, checks, tests
```

Read the starter's changelog for the release: it names the settings it added
(add them to your `.env` / deployment), the permissions it introduced (assign them
to roles), and anything a client must know (API.md changes).

## 4. First merge after `--reset-git`

The histories are unrelated, so tell git to line them up on the starter commit the
project was created from (the tag or SHA is in the initial commit message):

```bash
git fetch starter
git merge --allow-unrelated-histories -s ours <starter-commit-you-started-from>   # record the baseline, change nothing
git merge starter/main                                                            # now a normal merge
```

From then on §3 applies.

## 5. Modules removed at init

If the project was created `--without sso` or `--without workflows`, a starter
update will try to bring the module back (its folder and marked lines). Either:

- run the removal again after the merge —
  `python scripts/init_project.py --name … --slug … --without sso --keep-script`
  removes only what is marked and touches nothing of yours; or
- keep the module: it arrives switched off (`ENABLE_SSO_PROVIDER=False`) and costs
  nothing until enabled.

The example app is removed the same way (`init_project.py` without
`--keep-example`).

## 6. Versioning

The starter is tagged `vMAJOR.MINOR.PATCH`. A **patch** changes no interface; a
**minor** adds settings, permissions, operations or files; a **major** renames or
removes something a project could depend on (a status code, an operation, a
service signature, a marker). Pin what you merge (`starter/v1.3.0`, not
`starter/main`) and take majors deliberately.
