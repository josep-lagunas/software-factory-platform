"""Local Execution Engine — HOST subprocess operations for the Workspace Worker.

The Local Execution Engine (MAS §9.6) is an internal Workspace Worker component
(ID-034) that owns the build/test/lint operations executed over a repository
working directory. This subpackage holds that surface.

This is the **Phase A HOST execution** rig (ID-060): operations run ``uv``
directly on the host with ``cwd`` set to a per-job worktree (SFP-56 / ID-033) of
the token-free clone (SFP-55). No credentials traverse this subpackage — the
clone's ``origin`` is already token-free — and there is no container boundary.

Container isolation (docker/podman) is explicitly **out of scope** here: it is
SFP-65 / Phase B. This subpackage must never introduce a container code path.

* ``build.py`` implements the shared :data:`~workspace_worker.exec.build.Runner`
  abstraction (reused by SFP-63 test + SFP-64 lint) and the ``build`` operation
  (``uv sync --all-packages``, SFP-62).
* ``test.py`` (SFP-63) and ``lint.py`` (SFP-64) land in follow-on tickets, each
  importing the shared ``Runner`` from :mod:`workspace_worker.exec.build`.
"""
