"""Workbench core: the shared base that every skill depends on.

Dependency direction is one-way and enforced by review:
``workstation.core`` never imports a skill, and a skill never reaches into
another skill. Both sides may import ``workstation_contracts``.
"""
