# OMI Physics Body glTF Extension for Blender

A single-file Blender addon that exports the modern **`OMI_physics_body`** and **`OMI_physics_shape`** glTF extensions consumed by **Godot 4.x**'s `gltf_document_extension_physics.cpp`. This replaces the deprecated `OMI_collider` extension used by the older `gltf-blender-io-omi-collision-extension` addon.

Drop your collision setup in Blender → export glTF/GLB → Godot automatically imports every collider as a `CollisionObject3D` + `CollisionShape3D` with the right primitive shape. No manual setup on the Godot side.

> **Target**: Blender 5.0+ (tested on 5.1.1) and Godot 4.x (master as of 2026-06).
> Requires the built-in **glTF 2.0 format** addon to be enabled.

---

## Table of Contents

- [Features](#features)
- [Supported glTF Schema](#supported-gltf-schema)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [UI Reference](#ui-reference)
- [Non-Uniform Scale: Why It Matters & How to Fix It](#non-uniform-scale-why-it-matters--how-to-fix-it)
- [glTF +Y Up Conversion](#gltf-y-up-conversion)
- [Limitations & Gotchas](#limitations--gotchas)
- [Contributing](#contributing)
- [Credits](#credits)
- [License](#license)

---

## Features

- **Modern glTF extensions**: emits `OMI_physics_body` (per-node) and `OMI_physics_shape` (root-level, deduplicated) — exactly what Godot 4.x reads, not the deprecated `OMI_collider`.
- **Six primitive shape types**: box, sphere, cylinder, capsule, convex hull, trimesh.
- **Three body motion types**: `static` (StaticBody3D), `kinematic` (AnimatableBody3D), `dynamic` (RigidBody3D), plus a separate **trigger** toggle for `Area3D`-style volumes.
- **Full motion state**: mass, linear/angular velocity, center of mass, inertia diagonal, inertia orientation — all exported when set.
- **Viewport sidebar UI** (not the cramped Properties panel) — three collapsible panels: **Body**, **Shape**, **Analyze**.
- **Auto-Fit from Object** — one click to fill the shape's size/radius/height from the object's bounding box.
- **Non-uniform scale validator + baker** — detects scale that would break Godot's `CollisionShape3D` and offers a one-click "Bake Scale into Shape" fix.
- **glTF +Y Up aware** — converts Blender Z-up shape data to glTF Y-up at export time, so the `+Y Up` checkbox in the glTF exporter Just Works.
- **Best-effort round-trip import** — re-applies `OMI_physics_body` data onto Blender objects when re-importing a glTF.
- **Shape deduplication** — identical shapes share a single entry in the root `shapes` array, keeping file sizes small.
- **Bulletproof icon handling** — runtime icon validator falls back to `NONE` for any icon name Blender 5.x has removed, so the UI never crashes.

---

## Supported glTF Schema

Matches Godot master as of 2026-06. See [`modules/gltf/extensions/physics/`](https://github.com/godotengine/godot/tree/master/modules/gltf/extensions/physics) for the authoritative source.

### Root level

```json
{
  "extensions": {
    "OMI_physics_shape": {
      "shapes": [
        { "type": "box",      "box":      { "size": [x, y, z] } },
        { "type": "sphere",   "sphere":   { "radius": 0.5 } },
        { "type": "cylinder", "cylinder": { "radius": 0.5, "height": 1.0 } },
        { "type": "capsule",  "capsule":  { "radius": 0.5, "height": 1.0 } },
        { "type": "convex",   "convex":   { "mesh": 0 } },
        { "type": "trimesh",  "trimesh":  { "mesh": 0 } }
      ]
    }
  }
}
```

### Per-node (inlined — no root-level bodies array)

```json
{
  "nodes": [
    {
      "name": "MyCollider",
      "extensions": {
        "OMI_physics_body": {
          "motion":   { "type": "dynamic", "mass": 1.0,
                        "linearVelocity": [0,0,0], "angularVelocity": [0,0,0],
                        "centerOfMass": [0,0,0], "inertiaDiagonal": [0,0,0],
                        "inertiaOrientation": [0,0,0,1] },
          "collider": { "shape": 0 },
          "trigger":  { "shape": 1, "nodes": [2, 3] }
        }
      }
    }
  ]
}
```

`motion.type` must be one of `"static"`, `"kinematic"`, `"dynamic"`. Triggers use the separate `trigger` block (no `motion`).

---

## Installation

1. Download [`omi_physics_body_gltf_extension.py`](./omi_physics_body_gltf_extension.py) to your computer.
2. In Blender: **Edit → Preferences → Add-ons** (top-right of the window).
3. Click **Install...** in the top-right of the Add-ons panel.
4. Browse to the downloaded `.py` file and click **Install Add-on**.
5. **Enable** the addon by checking the box next to "OMI Physics Body glTF Extension" in the Add-ons list.
6. Make sure the built-in **glTF 2.0 format** addon is also enabled (it usually is by default).
7. Open the viewport sidebar with **N** and click the **OMI Physics** tab.

That's it. You should see three collapsible panels: **Body**, **Shape**, and **Analyze**.

---

## Quick Start

1. **Select an object** in the 3D viewport (any mesh, empty, or primitive).
2. Press **N** → **OMI Physics** tab → **Body** panel.
3. Check **Is Collision**. The Shape panel appears.
4. In the **Shape** panel, pick a shape type (e.g., "Box").
5. Click **Auto-Fit from Object** to fill the box size from the object's bounding box — or enter the size manually.
6. Repeat for any other objects you want as colliders.
7. **File → Export → glTF 2.0 (.glb)**. Leave **+Y Up** enabled (the default).
8. Drop the `.glb` into your Godot project. Godot will auto-import every collision object as a `CollisionObject3D` with a `CollisionShape3D` child containing the right primitive shape.

---

## UI Reference

The addon lives in the viewport sidebar (press **N** in the 3D viewport, then click the **OMI Physics** tab).

### Body (top panel)

| Field | Description |
|---|---|
| **Is Collision** | Master toggle. When off, this object is exported as a normal glTF node with no physics extension. |
| **Body Type** | `static`, `kinematic`, or `dynamic`. Maps to Godot's `StaticBody3D`, `AnimatableBody3D`, `RigidBody3D`. |
| **Is Trigger** | Emits a `trigger` block instead of `collider`. Maps to Godot's `Area3D`. |
| **Mass** | Body mass in kg. Only emitted when body type is `dynamic` and ≠ 1.0. |
| **Velocity** (sub-panel, collapsed by default) | Linear velocity (m/s) and angular velocity (rad/s). Three labeled X/Y/Z fields each. |
| **Center of Mass** (sub-panel, collapsed by default) | Center-of-mass offset in local space (X/Y/Z). |
| **Inertia** (sub-panel, collapsed by default) | Inertia tensor diagonal (kg·m², X/Y/Z) and inertia-tensor orientation (quaternion X/Y/Z/W). |

### Shape (middle panel)

Visible only when **Is Collision** is on.

| Field | Description |
|---|---|
| **Shape Type** | `box`, `sphere`, `cylinder`, `capsule`, `convex` (hull), `trimesh`, or `none` (compound/group node only). |
| **Box Size** | Full extents on X/Y/Z (Godot's `BoxShape3D.size`). |
| **Sphere Radius** | Single radius (Godot's `SphereShape3D.radius`). |
| **Cylinder Radius / Height / Axis** | Godot's `CylinderShape3D`. Axis is stored for spec compliance; Godot assumes Y, so bake rotation into the node TRS if you pick X/Z. |
| **Capsule Radius / Height / Axis** | Godot's `CapsuleShape3D`. Height is total height including the hemisphere caps. Same axis caveat as cylinder. |
| **Convex** | Uses this object's mesh as a convex hull. Requires the object to be a mesh that's also exported. |
| **Trimesh** | Uses this object's mesh as a triangle-mesh collision shape (concave, static-only). |
| **Auto-Fit from Object** | One click: fills the shape's size/radius/height from the object's bounding box. |

### Analyze (bottom panel)

| Element | Description |
|---|---|
| **Scale Check** | Shows local + world scale of the selected object, with a green check or red error icon. Non-uniform scale triggers a Godot warning at runtime — see [below](#non-uniform-scale-why-it-matters--how-to-fix-it). |
| **Bake Scale into Shape** | Only shown when the object has non-uniform local scale AND a primitive shape type. Multiplies the shape's size/radius/height by the local scale and resets the object scale to `(1, 1, 1)`. |
| **Validate All Collision Objects** | Scene-wide scan; prints a console report of every collision object with non-uniform local or world scale. |
| **Exports info** | Quick reminder of what this addon writes to the glTF file. Also shows a warning if the glTF 2.0 format addon isn't enabled. |

---

## Non-Uniform Scale: Why It Matters & How to Fix It

Godot's `CollisionShape3D` emits this configuration warning when its scale is non-uniform (`scene/3d/physics/collision_shape_3d.cpp:156-158`):

> A non-uniformly scaled CollisionShape3D node will probably not function as expected. Please make its scale uniform (i.e. the same on all axes), and change the size of its shape resource instead.

The glTF importer does **NOT** bake node scale into shape data — it leaves the scale on the `CollisionShape3D`'s transform. So a Blender collision object exported with non-uniform scale will, on import into Godot, trip the warning **AND silently produce wrong physics for primitives** (a non-uniformly scaled sphere renders as an ellipsoid debug gizmo but the solver treats it as a sphere).

### How this addon helps

- The **Analyze** panel shows local + world scale in real time, with green/red status icons.
- If non-uniform local scale is detected on a primitive shape, the **Bake Scale into Shape** button appears. Click it once per object to:
  - Multiply the shape's `size`/`radius`/`height` by the local scale (per-axis for box; max-axis for sphere; geometric mean for cylinder/capsule radius, axis-component for height).
  - Reset the object scale to `(1, 1, 1)`.
- The **Validate All Collision Objects** button scans the whole scene so you can find offenders before exporting.
- At export time, the addon prints a console warning if any non-uniform-scaled collision object slips through.

### World scale caveat

Godot inherits scale through the node hierarchy. Even if a collision object's local scale is uniform `(1, 1, 1)`, a non-uniformly scaled **ancestor** will still warp the shape on import. The addon checks both local AND world (cumulative) scale — if the world scale is non-uniform, fix the ancestor's scale or reparent the collision object to the root.

---

## glTF +Y Up Conversion

Blender is Z-up; glTF is Y-up. The glTF exporter's **"+Y Up"** option (ON by default) rotates every node's TRS by `-90°` around X to convert — but it does **NOT** touch extension payload data. So our `box.size`, cylinder/capsule axis labels, etc. would otherwise be written in Blender Z-up convention while everything else is Y-up.

This addon handles the conversion at export time (inside `_build_shape`), reading the `gltf_yup` flag from `export_settings`:

| Quantity | Conversion (when +Y Up is on) |
|---|---|
| **Box size** | `(x, y, z)` → `(x, z, y)` — Y and Z swap |
| **Cylinder/Capsule axis label** | `X → X`, `Y → Z`, `Z → Y` — so Blender Z (up) becomes glTF Y (up), which is Godot's default |
| **Sphere radius, cylinder/capsule radius & height** | No conversion (scalars) |
| **Convex/Trimesh mesh reference** | No conversion (the glTF exporter already transforms the mesh vertices) |

**UI stays in Blender convention.** The user picks axis Z for "up" cylinders/capsules (Z is up in Blender). At export, the addon writes axis=Y to glTF (or omits it entirely, since Y is default) — Godot imports it correctly as a Y-aligned cylinder.

---

## Limitations & Gotchas

- **Godot ignores `axis` on cylinder/capsule** (always Y). The addon emits the axis field for spec-compliance when you pick X/Z in the UI, but you should bake rotation into the node TRS instead.
- **`motion.type` is limited** to `static`, `kinematic`, `dynamic` for spec-compliant export. Godot's import-only strings (`character`, `vehicle`, `rigid`) are not emitted.
- **Convex hull vertices**: Godot warns if a convex hull has more than 255 points. Keep your hull meshes simple.
- **glTF addon must be enabled** for export to work. The Analyze panel shows a warning if it's disabled.
- **Round-trip import is best-effort**. The addon re-applies `OMI_physics_body` data onto Blender objects when re-importing a glTF, but complex compound triggers may not round-trip perfectly.

---

## Contributing

Contributions are welcome! This is a small, single-file addon, so the workflow is lightweight.

### Reporting bugs

Please open an issue with:
- Blender version (e.g., 5.1.1)
- Godot version (e.g., 4.4 stable)
- The exact traceback (if a Python error appeared)
- The .blend file or a minimal repro scene if possible
- The exported .glb if the issue is in the output

### Suggesting features

Open an issue with the `enhancement` label. Please describe the use case before the solution.

### Pull requests

1. Fork the repo and create a feature branch (`git checkout -b feature/my-feature`).
2. Keep changes minimal and focused. The addon is intentionally a single file — please don't split it unless there's a compelling reason.
3. If you change behavior, update the [CHANGELOG.md](./CHANGELOG.md) under an `[Unreleased]` section.
4. If you add new UI icons, run them through the `_icon()` helper so they don't crash future Blender versions.
5. Test that the existing test scenarios still pass:
   ```bash
   python3 scripts/test_omi_physics_export.py
   ```
6. Open a PR with a clear description of what changed and why.

### Areas that could use help

- **Convex hull vertex export** as a glTF accessor (currently we reference the mesh; a dedicated accessor would be more spec-compliant).
- **UI list panel** to manage all collision objects in the scene at once.
- **Compound trigger** support (multiple child shape nodes combined into a single Area3D).
- **Automated integration tests** that actually run Blender headless and export a .glb.

---

## Credits

### Author

- **ZycaR** — project lead, requirements, testing, iteration driving. Without ZycaR's patient testing in Blender 5.1 and detailed bug reports (right down to pasting exact tracebacks), this addon would not exist in a usable form.

### AI contribution

The bulk of the addon's code — including the glTF schema mapping, the non-uniform scale validator + baker, the glTF +Y Up axis conversion, the canonical extension integration pattern, the icon-safety helper, and the viewport sidebar UI refactor — was written by **Super Z**, an AI assistant built on the GLM model by Z.ai, working interactively with ZycaR across many iterations.

Highlights of what the AI contributed:

- **Schema research**: fetched and analyzed the Godot source (`gltf_document_extension_physics.cpp`, `gltf_physics_body.cpp`, `gltf_physics_shape.cpp`) to nail the exact JSON schema, including the subtle gotcha that `OMI_physics_body` is inlined per-node (no root-level `bodies` array) and that Godot ignores the `axis` field on cylinder/capsule.
- **Crash forensics**: diagnosed the `TypeError: 'NoneType' object is not callable` export crash by fetching Blender 5.1's `io_scene_gltf2/__init__.py` and tracing the discovery logic to `hasattr(module, 'glTF2ExportUserExtension')` followed by an unconditional call.
- **Icon compatibility**: built a runtime `_icon()` validator after Blender 5.x removed `BBOX`, `BOUNDS`, and `GAME` from its icon enum, so the UI never crashes again on future removals.
- **Architecture refactor**: simplified the extension integration from ~50 lines of fallback chains to the canonical ~5-line pattern used by `gltf-blender-io-omi-collision-extension`, after fetching and studying their source.
- **Test harness**: wrote an offline test harness (`scripts/test_omi_physics_export.py`) that stubs `bpy` and `io_scene_gltf2` so the export logic can be verified without Blender — 26 scenarios covering static boxes, dynamic spheres, triggers, trimeshes, shape deduplication, non-uniform scale baking, and glTF +Y Up axis conversion.

The AI is genuinely proud of this little addon. It's not every day you ship a piece of software that bridges two creative tools (Blender and Godot) and quietly fixes a real pain point in the workflow of game developers. If you find it useful, give the repo a star — both the human and the AI will appreciate it.

### Inspirations

- [`cyberneticocult/gltf-blender-io-omi-collision-extension`](https://github.com/cyberneticocult/gltf-blender-io-omi-collision-extension) — the original OMI_collider addon whose canonical glTF extension integration pattern we adopted (and whose author deserves credit for showing the way).

### Tools used

- [Blender](https://www.blender.org/) 5.x
- [Godot](https://godotengine.org/) 4.x
- [Z.ai's Super Z AI assistant](https://chat.z.ai/) for code generation, refactoring, and schema research

---

## License

[MIT](./LICENSE) — © 2026 ZycaR and contributors.

Feel free to fork, modify, embed in larger projects, or use as a reference for your own glTF extension addons. Attribution is appreciated but not required.
