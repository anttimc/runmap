# Code conventions

Prefer separating data from functionality. Use data-only dataclasses when a
structured container is useful, and use module-level functions for loading,
validation, transformation, and other operations on that data. Avoid classes
that bundle data with operations merely to provide a namespace; use a behavioral
class only when its state or lifecycle warrants one.

Keep functions small and focused on one responsibility. If a function becomes
too long, split it into smaller helper functions with clear responsibilities.

Use type hints for every function wherever possible, including parameters and
return values. Do not use `Any`; prefer precise types.

Keep docstrings short. Explain higher-level purpose, context, and why an
operation is needed instead of restating what is obvious from the code.

Use `uv` to manage Python environments and dependencies and to run Python
commands, including tests.
