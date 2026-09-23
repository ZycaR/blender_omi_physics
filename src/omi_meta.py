# SPDX-License-Identifier: MIT
# bl_info serves as the addon manifest that Blender reads on install.
bl_info = {
    "name": "OMI Physics Body glTF Extension",
    "author": "Z",
    "version": (1, 6, 4),
    "blender": (5, 0, 0),
    "location": "View3D > Sidebar (N) > OMI Physics tab",
    "description": (
        "Export OMI_physics_body and OMI_physics_shape glTF extensions for "
        "Godot 4.x (replaces deprecated OMI_collider). Supports primitive "
        "collision shapes: box, sphere, cylinder, capsule, convex, trimesh. "
        "Includes a non-uniform-scale validator + baker because Godot's "
        "CollisionShape3D warns and misbehaves on non-uniform scale. "
        "Viewport sidebar UI with collapsible Analyze/Body/Shape panels."
    ),
    "warning": "Requires Blender 5.0+ and the built-in glTF 2.0 format addon enabled.",
    "doc_url": "",
    "category": "Import-Export",
}

"""
==========================================================================
OMI Physics Body glTF Extension for Blender
==========================================================================

Single-file Blender addon that exports the modern OMI physics glTF
extensions consumed by Godot 4.x's `gltf_document_extension_physics.cpp`:

  * OMI_physics_shape  (root-level: array of shape definitions)
  * OMI_physics_body   (per-node: motion / collider / trigger blocks)

This replaces the deprecated `OMI_collider` extension used by the older
`gltf-blender-io-omi-collision-extension` addon.

-------------------------------------------------------------------------
SCHEMA (matches Godot master as of 2026-06)
-------------------------------------------------------------------------

Root level:
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

Per-node (inlined - NO root-level bodies array):
{
  "nodes": [
    {
      "name": "MyCollider",
      "extensions": {
        "OMI_physics_body": {
          "motion":   { "type": "dynamic", "mass": 1.0,
                        "linearVelocity": [...], "angularVelocity": [...],
                        "centerOfMass": [...], "inertiaDiagonal": [...],
                        "inertiaOrientation": [x,y,z,w] },
          "collider": { "shape": 0 },
          "trigger":  { "shape": 1, "nodes": [2, 3] }
        }
      }
    }
  ]
}

motion.type: "static" | "kinematic" | "dynamic"   (trigger is via the
                                                    separate `trigger` block)

=========================================================================

INTEGRATION PATTERN
-------------------
This addon follows the canonical glTF user-extension pattern (same one
used by `gltf-blender-io-omi-collision-extension`):

  1. The export/import extension classes are defined at module scope with
     the canonical names `glTF2ExportUserExtension` and
     `glTF2ImportUserExtension`. The glTF exporter discovers user
     extensions by `hasattr(module, 'glTF2ExportUserExtension')` and
     instantiates them with zero args. NO subclassing, NO register_class.
  2. The `Extension` helper class (used to wrap per-node/per-root
     payloads) is imported LAZILY inside `__init__` of the export
     extension. This avoids `ImportError` at addon-load time if the
     glTF addon is disabled; the import only fires when the exporter
     actually instantiates us.
  3. We do NOT subclass anything. The glTF exporter duck-types hook
     methods via getattr at call time.

=========================================================================
SOURCE LAYOUT & BUILD (dev)
=========================================================================
This file is the DISTRIBUTED single-file addon. It is GENERATED - do not
edit it directly. The source lives in src/ modules which scripts/build.mjs
(Node, zero npm dependencies) concatenates in a fixed order:

    src/omi_meta.py        bl_info + this docstring (bundle header)
    src/omi_core.py        business logic + data model (PropertyGroup)
    src/omi_ui.py          viewport panels + operators
    src/omi_gltf_ext.py    glTF export/import user extensions
    src/omi_register.py    _classes + register()/unregister()

Rebuild with:  node scripts/build.mjs [--verify]

The build also syncs bl_info (version/author/doc_url) from
blender_manifest.toml and produces the Blender extension .zip in dist/.
Behavioral equivalence with the pre-split file can be checked with
scripts/check_equivalence.py (AST comparison).
=========================================================================
"""
