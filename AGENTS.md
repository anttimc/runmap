# Code conventions

Prefer separating data from functionality. Use data-only dataclasses when a
structured container is useful, and use module-level functions for loading,
validation, transformation, and other operations on that data. Avoid classes
that bundle data with operations merely to provide a namespace; use a behavioral
class only when its state or lifecycle warrants one.
