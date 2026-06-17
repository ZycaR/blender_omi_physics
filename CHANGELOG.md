# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.6.3] - 2026-06-17

### Added
- **Auto-fit on Is Collision toggle**: when the user enables `Is Collision`
  on an object, the shape's dimensions are now automatically fitted from
  the object's bounding box (for box/sphere/cylinder/capsule). Saves a
  click on the Auto-Fit button in the common workflow.
- **Reset Shape to Defaults** operator (next to Auto-Fit in the Shape panel,
  with a refresh icon). Resets dimensions to sane defaults:
  - Box: size = (1, 1, 1)
  - Sphere: radius = 0.5
  - Cylinder: radius = 0.5, height = 1.0, axis = 'Y'
  - Capsule: radius = 0.5, height = 1.0, axis = 'Y'

### Changed
- Refactored the auto-fit logic into a shared `_auto_fit_from_object()`
  helper so it can be called from both the operator and the property
  update callback without duplication.
- The Auto-Fit operator now gracefully skips objects with zero dimensions
  (Empty, Armature, etc.) instead of writing zeros into the shape.
- Shape panel: Auto-Fit and Reset buttons are now in an aligned row,
  making the Reset button a compact icon-only button next to Auto-Fit.

## [1.6.2] - 2026-06-17

### Added
- Per-axis labels (X / Y / Z, W) for `Linear Velocity`, `Angular Velocity`,
  `Center of Mass`, `Inertia Diagonal`, and `Inertia Orientation` fields in
  the Body sub-panels. Previously `use_property_split` was hiding the axis
  labels for vector properties without `TRANSLATION`/`EULER` subtype.
- Unit hints in field labels (m/s, rad/s, kg·m²).

### Changed
- Sidebar tab renamed from `OMI` → `OMI Physics` (clearer).
- Panel order changed: Body (top) → Shape (middle) → Analyze (bottom),
  controlled via `bl_order`. Analyze now sits at the bottom because it's
  the diagnostic panel and the user typically interacts with Body/Shape
  first when setting up a collider.

## [1.6.0] - 2026-06-17

### Changed
- **Major UI refactor.** Moved from the Object Properties tab to a
  viewport sidebar panel (View3D → Sidebar (N) → `OMI Physics` tab).
- Split the single big panel into three vertically-stacked collapsible
  panels: **Analyze**, **Body**, **Shape**.
- Made `Velocity`, `Center of Mass`, and `Inertia` into collapsible
  sub-panels nested under **Body** (`bl_parent_id` + `DEFAULT_CLOSED`).
- Shape panel only renders when `is_collision` is enabled.

## [1.5.0] - 2026-06-17

### Added
- glTF **+Y Up axis conversion** for the extension payload. The glTF
  exporter's "+Y Up" option (ON by default) rotates node TRS from Blender
  Z-up to glTF Y-up but does NOT touch extension data. We now perform the
  same conversion on the shape payload:
  - **Box**: `size = (x, y, z)` → `(x, z, y)` (Y/Z swap).
  - **Cylinder/Capsule axis labels**: `X→X`, `Y→Z`, `Z→Y` (so a Blender
    Z-up cylinder becomes a glTF Y-up cylinder, which is Godot's default).
  - **Sphere radius, cylinder/capsule radius & height**: scalars, no
    conversion needed.
- The `gather_node_hook` signature now accepts `*args, **kwargs` so it can
  receive the `export_settings` dict that Blender 5.x passes, while staying
  backward-compatible with older Blender versions.

## [1.4.0] - 2026-06-17

### Changed
- **Refactored glTF extension integration** to match the canonical pattern
  used by `gltf-blender-io-omi-collision-extension`:
  - Renamed `OMIPhysicsExportExtension` → `glTF2ExportUserExtension`
    (canonical name; the `class` statement itself is the discovery binding).
  - Renamed `OMIPhysicsImportExtension` → `glTF2ImportUserExtension`.
  - Moved the `Extension` helper import from module-top (with a 3-path
    fallback chain + local class definition) to a lazy import inside
    `__init__` (single line, no fallback needed).
  - Removed the `if _HAS_GLTF_EXPORT:` conditional class definition —
    classes are now always defined at module scope.
- Removed ~50 lines of integration glue; the addon is now ~5 lines of
  integration code, matching the canonical pattern.

## [1.3.0] - 2026-06-17

### Fixed
- **Critical crash on glTF export**: `TypeError: 'NoneType' object is not
  callable` at `io_scene_gltf2/__init__.py:1336`. The glTF exporter
  discovers user extensions by `hasattr(module, 'glTF2ExportUserExtension')`
  and then calls it. Our previous code left the attribute as `None` when
  the glTF addon wasn't importable, which made `hasattr` return `True` and
  the subsequent `None()` call crash. Now the attribute is only set when
  the extension class is actually defined.

## [1.2.x] - 2026-06-17

### Fixed
- **Blender 5.x icon compatibility.** Replaced removed icons (`BBOX`,
  `BOUNDS`, `GAME`) with 5.x-safe equivalents (`MESH_CUBE`, `PHYSICS`,
  `MODIFIER`, etc.). Added a runtime `_icon()` helper that validates icon
  names against the live Blender enum and falls back to `NONE` — making
  the addon bulletproof against future icon-enum removals.

## [1.1.0] - 2026-06-17

### Added
- **Non-uniform scale validator + baker.** Godot's `CollisionShape3D`
  emits a configuration warning when its scale is non-uniform
  (`collision_shape_3d.cpp:156-158`), and the physics solver silently
  misbehaves for primitives. The glTF importer does NOT bake node scale
  into shape data — so a Blender collision object exported with
  non-uniform scale will trip the warning AND produce wrong physics.
  - New **"Scale Check"** panel section showing local + world scale
    status with green check / red error icons.
  - New **"Bake Scale into Shape"** operator: multiplies the shape's
    size/radius/height by the object's local scale and resets the object
    scale to `(1, 1, 1)`. Handles box, sphere, cylinder, capsule.
  - New **"Validate All Collision Objects"** operator: scene-wide scan
    that reports all non-uniformly-scaled collision objects.
  - Export-time guard prints a console warning if a non-uniform-scaled
    collision object is being exported.

## [1.0.0] - 2026-06-17

### Added
- Initial release.
- Single-file Blender addon exporting `OMI_physics_body` and
  `OMI_physics_shape` glTF extensions (the modern replacements for the
  deprecated `OMI_collider`), targeting Godot 4.x's
  `gltf_document_extension_physics.cpp`.
- Supports primitive collision shapes: **box, sphere, cylinder, capsule,
  convex hull, trimesh**.
- Body motion types: **static, kinematic, dynamic**, plus a separate
  **trigger** toggle for `Area3D`-style volumes.
- Per-object UI panel with: master "Is Collision" toggle, body type,
  shape type + per-shape inputs (size, radius & height, axis), motion
  properties (mass, linear/angular velocity, center of mass, inertia).
- **Auto-Fit from Object** button auto-fills the shape from the object's
  bounding box.
- Shape deduplication: identical shapes share a single entry in the
  root `OMI_physics_shape.shapes` array.
- Best-effort round-trip import: re-applies `OMI_physics_body` data onto
  the Blender objects created for each glTF node.

[1.6.3]: https://github.com/ZycaR/omi-physics-body-gltf-extension/releases/tag/v1.6.3
[1.6.2]: https://github.com/ZycaR/omi-physics-body-gltf-extension/releases/tag/v1.6.2
[1.6.0]: https://github.com/ZycaR/omi-physics-body-gltf-extension/releases/tag/v1.6.0
[1.5.0]: https://github.com/ZycaR/omi-physics-body-gltf-extension/releases/tag/v1.5.0
[1.4.0]: https://github.com/ZycaR/omi-physics-body-gltf-extension/releases/tag/v1.4.0
[1.3.0]: https://github.com/ZycaR/omi-physics-body-gltf-extension/releases/tag/v1.3.0
[1.2.x]: https://github.com/ZycaR/omi-physics-body-gltf-extension/releases/tag/v1.2.1
[1.1.0]: https://github.com/ZycaR/omi-physics-body-gltf-extension/releases/tag/v1.1.0
[1.0.0]: https://github.com/ZycaR/omi-physics-body-gltf-extension/releases/tag/v1.0.0
