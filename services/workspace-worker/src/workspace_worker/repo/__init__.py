"""Repository Manager — local repository lifecycle for the Workspace Worker.

The Repository Manager is an internal Workspace Worker component (ID-034) that
owns clone/worktree/sync/cleanup of the target repository. This subpackage
holds that surface; ``manager.py`` implements the *clone* slice (SFP-38) and
``worktree.py`` implements the *worktree* lifecycle (SFP-56 / ID-033).
"""
