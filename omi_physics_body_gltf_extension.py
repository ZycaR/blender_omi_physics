# SPDX-License-Identifier: MIT
# bl_info serves as the addon manifest that Blender reads on install.
bl_info = {
    "name": "OMI Physics Body glTF Extension",
    "author": "Z",
    "version": (1, 6, 3),
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
"""

import bpy
import json
import math
import traceback
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup, Panel, Operator


# ============================================================================
# Icon safety helper
# ============================================================================
#
# Blender 5.x removed a large number of legacy icons (BBOX, BOUNDS, GAME,
# ORIENTATION_*, many OUTLINER_OB_* icons, etc.). To make the addon
# bulletproof against further icon-enum removals in future 5.x point
# releases, every UI element that takes an icon=... argument goes through
# this helper. It validates the requested icon name against the live enum
# at runtime and falls back to 'NONE' (always valid) if the name is not
# available.

_VALID_ICONS = None


def _valid_icons():
    """Return the set of icon names valid in the current Blender build.

    On first call, introspects the live UILayout icon enum and caches it.
    If introspection fails (e.g., in headless test mode without bpy), falls
    back to a known-safe set that MUST contain every icon name this addon
    actually references via `_icon(...)`. Keep this set in sync with the
    `_icon(...)` call sites in this file - if you add a new icon to the UI,
    add it here too.

    Icon inventory used by this addon (audit with:
    `rg "_icon\\('[A-Z_]+'\\)" omi_physics_body_gltf_extension.py | sort -u`):
        CHECKMARK, ERROR, EXPORT, INFO, MESH_CUBE, MODIFIER,
        PHYSICS, SCENE_DATA, X
    """
    global _VALID_ICONS
    if _VALID_ICONS is None:
        try:
            props = bpy.types.UILayout.bl_rna.properties
            _VALID_ICONS = set(props["icon"].enum_items.keys())
        except Exception:
            # Fallback: must contain every icon this addon uses (see docstring).
            _VALID_ICONS = {
                "NONE",            # always valid
                "CHECKMARK",
                "ERROR",
                "EXPORT",
                "INFO",
                "MESH_CUBE",
                "MODIFIER",
                "PHYSICS",
                "SCENE_DATA",
                "X",
            }
    return _VALID_ICONS


def _icon(name):
    """Return `name` if it's a valid icon for this Blender version, else 'NONE'."""
    return name if name in _valid_icons() else 'NONE'


# ============================================================================
# Non-uniform scale helpers
# ============================================================================
#
# Godot's CollisionShape3D emits this configuration warning when its local
# scale is non-uniform (collision_shape_3d.cpp:156-158):
#
#     "A non-uniformly scaled CollisionShape3D node will probably not
#      function as expected. Please make its scale uniform (i.e. the same
#      on all axes), and change the size of its shape resource instead."
#
# The glTF importer does NOT bake node scale into shape data - it leaves the
# scale on the CollisionShape3D's transform. So a Blender collision object
# exported with non-uniform scale will, on import into Godot, trip the
# warning AND silently produce wrong physics for primitives.

SCALE_EPSILON = 1e-6  # matches Godot's is_zero_approx default


def _world_scale(obj):
    """Return the cumulative world-scale (Vector3) of `obj`, including all
    ancestors. This is what Godot's CollisionShape3D effectively inherits
    when the glTF node hierarchy is preserved."""
    mw = getattr(obj, "matrix_world", None)
    if mw is not None and hasattr(mw, "to_scale"):
        return mw.to_scale()
    s = obj.scale
    return type("V", (), {"x": float(s.x), "y": float(s.y), "z": float(s.z)})()


def _is_uniform(scale_vec, epsilon=SCALE_EPSILON):
    """True if x ~= y ~= z within `epsilon`. Matches Godot's predicate."""
    x, y, z = float(scale_vec.x), float(scale_vec.y), float(scale_vec.z)
    return (abs(x - y) <= epsilon) and (abs(y - z) <= epsilon)


def _copy_scale(scale_vec):
    """Return a copy of the scale vector, handling both Blender Vector and
    plain attribute containers (for the offline test harness)."""
    if hasattr(scale_vec, "copy"):
        return scale_vec.copy()
    return type("V", (), {"x": float(scale_vec.x),
                          "y": float(scale_vec.y),
                          "z": float(scale_vec.z)})()


def _scale_status(obj):
    """Return (is_local_uniform, is_world_uniform, local_scale, world_scale).

    World-scale uniformity matters because Godot inherits scale through the
    node hierarchy; even if the local scale is uniform, a non-uniformly scaled
    ancestor will still warp the shape on import.
    """
    local = _copy_scale(obj.scale)
    world = _world_scale(obj)
    return _is_uniform(local), _is_uniform(world), local, world


# ============================================================================
# Auto-fit helper (shared by the operator and the is_collision update callback)
# ============================================================================

def _auto_fit_from_object(obj, props):
    """Fill the collision shape's dimensions from the object's bounding box.

    Returns True if the shape was fitted, False if the shape type doesn't
    support auto-fit (convex/trimesh/none) or the object has no usable
    dimensions.
    """
    dims = obj.dimensions
    # Objects without geometry (Empty, Armature, etc.) have zero dimensions.
    # Skip auto-fit for them so we don't write zeros into the shape.
    if dims.x == 0.0 and dims.y == 0.0 and dims.z == 0.0:
        return False

    st = props.shape_type
    if st == 'box':
        props.box_size = (dims.x, dims.y, dims.z)
    elif st == 'sphere':
        props.sphere_radius = max(dims.x, dims.y, dims.z) * 0.5
    elif st == 'cylinder':
        max_axis = max(dims.x, dims.y, dims.z)
        others = sorted([d for d in dims if abs(d - max_axis) > 1e-6])
        props.cylinder_radius = (others[0] if others else max_axis) * 0.5
        props.cylinder_height = max_axis
        if max_axis == dims.x:
            props.cylinder_axis = 'X'
        elif max_axis == dims.y:
            props.cylinder_axis = 'Y'
        else:
            props.cylinder_axis = 'Z'
    elif st == 'capsule':
        max_axis = max(dims.x, dims.y, dims.z)
        others = sorted([d for d in dims if abs(d - max_axis) > 1e-6])
        props.capsule_radius = (others[0] if others else max_axis) * 0.5
        props.capsule_height = max_axis
        if max_axis == dims.x:
            props.capsule_axis = 'X'
        elif max_axis == dims.y:
            props.capsule_axis = 'Y'
        else:
            props.capsule_axis = 'Z'
    else:
        # convex / trimesh / none - no auto-fit
        return False
    return True


def _on_is_collision_toggled(self, context):
    """Update callback for OMIPhysicsProperties.is_collision.

    When the user enables Is Collision, auto-fit the shape's dimensions
    from the object's bounding box (for primitive shapes only). This saves
    a click on the Auto-Fit button in the common workflow.
    """
    if not self.is_collision:
        return
    obj = context.object
    if obj is None:
        return
    _auto_fit_from_object(obj, self)


# ============================================================================
# glTF +Y Up conversion helpers
# ============================================================================
#
# Blender is Z-up; glTF is Y-up. The glTF exporter's "+Y Up" option (ON by
# default) applies a -90 deg rotation around X to convert every node's TRS
# (translation/rotation/scale) from Blender space to glTF space.
#
# However, the exporter does NOT touch extension data - so OMI_physics_shape
# payload (box.size, cylinder/capsule axis labels, etc.) must be converted
# by us when +Y Up is enabled.
#
# The conversion is a -90 deg rotation around X:
#   glTF.x =  Blender.x
#   glTF.y =  Blender.z      (Blender up  -> glTF up)
#   glTF.z = -Blender.y      (Blender forward -> glTF backward)
#
# For SIZE-like quantities (no direction), the negation is dropped:
#   glTF_size = (Blender_size.x, Blender_size.z, Blender_size.y)
#
# For AXIS LABELS (cylinder/capsule), the mapping is:
#   Blender X -> glTF X
#   Blender Y -> glTF Z
#   Blender Z -> glTF Y    <-- this is what Godot assumes by default
#
# For SCALARS (radius, height): no conversion needed.
#
# IMPORTANT: the UI panel keeps using Blender convention. The conversion is
# applied ONLY at export time, inside _build_shape, when self.is_y_up is True.

# Axis label remap: Blender -> glTF (after +Y Up rotation)
_AXIS_YUP_MAP = {'X': 'X', 'Y': 'Z', 'Z': 'Y'}


def _convert_size_yup(vec3):
    """Convert a 3-component size/extents vector from Blender Z-up to
    glTF Y-up. Swaps Y and Z components (no negation since size has no
    direction)."""
    return [vec3[0], vec3[2], vec3[1]]


def _convert_axis_yup(axis):
    """Convert a cylinder/capsule axis label from Blender to glTF.
    Returns 'Y' for unknown values (Godot's default assumption)."""
    return _AXIS_YUP_MAP.get(axis, 'Y')


# ============================================================================
# glTF addon availability probe (UI status indicator only).
# ----------------------------------------------------------------------------
# We probe io_scene_gltf2 purely to drive a UI status indicator in the panel
# so the user knows whether their export will work. The probe failure does
# NOT prevent the extension classes from being defined - the classes are
# always defined at module scope with their canonical names; the lazy
# `Extension` import inside __init__ only fires when the exporter actually
# instantiates us.

_HAS_GLTF = False
try:
    import io_scene_gltf2  # noqa: F401
    _HAS_GLTF = True
except Exception:
    pass


# ============================================================================
# Property Group
# ============================================================================

def _axis_items():
    return [
        ('X', 'X', 'Align along X axis (will be exported; Godot assumes Y, so bake rotation into the node if you pick X/Z)'),
        ('Y', 'Y', 'Align along Y axis (Godot default)'),
        ('Z', 'Z', 'Align along Z axis (will be exported; Godot assumes Y, so bake rotation into the node if you pick X/Z)'),
    ]


class OMIPhysicsProperties(PropertyGroup):
    """Per-object collision properties stored on bpy.types.Object."""

    # ---- Master switch -----------------------------------------------------
    is_collision: BoolProperty(
        name="Is Collision",
        description=(
            "Mark this object as a physics body for glTF export. The object "
            "will be exported with the OMI_physics_body extension and (if a "
            "shape is configured) referenced from the root OMI_physics_shape "
            "shapes array. Godot will automatically import it as a "
            "CollisionObject3D + CollisionShape3D. When enabled, the shape "
            "dimensions are auto-fitted from the object's bounding box."
        ),
        default=False,
        update=_on_is_collision_toggled,
    )

    # ---- Body motion type --------------------------------------------------
    body_type: EnumProperty(
        name="Body Type",
        description=(
            "OMI_physics_body.motion.type. Godot 4.x only honours 'static', "
            "'kinematic' and 'dynamic'. Use the Trigger toggle below for "
            "Area3D-style trigger volumes."
        ),
        items=[
            ('static',    'Static',    'Static body (StaticBody3D in Godot)'),
            ('kinematic', 'Kinematic', 'Kinematic body (AnimatableBody3D in Godot)'),
            ('dynamic',   'Dynamic',   'Dynamic body (RigidBody3D in Godot)'),
        ],
        default='static',
    )

    is_trigger: BoolProperty(
        name="Is Trigger",
        description=(
            "Emit a `trigger` block instead of (or alongside) a `collider` "
            "block. Maps to Godot Area3D. If both a collider and a trigger "
            "shape are enabled, both blocks are emitted (compound)."
        ),
        default=False,
    )

    # ---- Shape -------------------------------------------------------------
    shape_type: EnumProperty(
        name="Shape Type",
        description="OMI_physics_shape.shapes[].type",
        items=[
            ('box',         'Box',         'Box primitive (size in local units)'),
            ('sphere',      'Sphere',      'Sphere primitive (single radius)'),
            ('cylinder',    'Cylinder',    'Cylinder primitive (radius + height)'),
            ('capsule',     'Capsule',     'Capsule primitive (radius + height)'),
            ('convex',      'Convex Hull', 'Convex hull shape derived from the object mesh'),
            ('trimesh',     'Trimesh',     'Triangle-mesh shape derived from the object mesh'),
            ('none',        'None',        'No collider shape (compound/group node only)'),
        ],
        default='box',
    )

    # Box
    box_size: FloatVectorProperty(
        name="Box Size",
        description="Full extents of the box on X/Y/Z (Godot BoxShape3D.size)",
        default=(1.0, 1.0, 1.0),
        size=3,
        subtype='XYZ',
        unit='LENGTH',
    )

    # Sphere
    sphere_radius: FloatProperty(
        name="Sphere Radius",
        description="Sphere radius (Godot SphereShape3D.radius)",
        default=0.5,
        min=0.0,
        unit='LENGTH',
    )

    # Cylinder
    cylinder_radius: FloatProperty(
        name="Cylinder Radius",
        description="Cylinder radius (Godot CylinderShape3D.radius)",
        default=0.5,
        min=0.0,
        unit='LENGTH',
    )
    cylinder_height: FloatProperty(
        name="Cylinder Height",
        description="Total cylinder height (Godot CylinderShape3D.height)",
        default=1.0,
        min=0.0,
        unit='LENGTH',
    )
    cylinder_axis: EnumProperty(
        name="Cylinder Axis",
        description="Stored for spec-compliance; Godot assumes Y so bake rotation if X/Z",
        items=_axis_items(),
        default='Y',
    )

    # Capsule
    capsule_radius: FloatProperty(
        name="Capsule Radius",
        description="Capsule radius (Godot CapsuleShape3D.radius)",
        default=0.5,
        min=0.0,
        unit='LENGTH',
    )
    capsule_height: FloatProperty(
        name="Capsule Height",
        description="Total capsule height incl. caps (Godot CapsuleShape3D.height)",
        default=1.0,
        min=0.0,
        unit='LENGTH',
    )
    capsule_axis: EnumProperty(
        name="Capsule Axis",
        description="Stored for spec-compliance; Godot assumes Y so bake rotation if X/Z",
        items=_axis_items(),
        default='Y',
    )

    # ---- Motion (dynamic body only) ---------------------------------------
    mass: FloatProperty(
        name="Mass",
        description="Body mass in kg (only emitted when body_type=dynamic)",
        default=1.0,
        min=0.0,
    )
    linear_velocity: FloatVectorProperty(
        name="Linear Velocity",
        description="Initial linear velocity (m/s)",
        default=(0.0, 0.0, 0.0),
        size=3,
        subtype='VELOCITY',
    )
    angular_velocity: FloatVectorProperty(
        name="Angular Velocity",
        description="Initial angular velocity (rad/s) on X/Y/Z axes",
        default=(0.0, 0.0, 0.0),
        size=3,
        subtype='XYZ',
    )
    center_of_mass: FloatVectorProperty(
        name="Center of Mass",
        description="Center-of-mass offset in local space",
        default=(0.0, 0.0, 0.0),
        size=3,
        subtype='XYZ',
    )
    inertia_diagonal: FloatVectorProperty(
        name="Inertia Diagonal",
        description="Diagonal of the inertia tensor (kg·m²) on X/Y/Z axes",
        default=(0.0, 0.0, 0.0),
        size=3,
        subtype='XYZ',
    )
    inertia_orientation: FloatVectorProperty(
        name="Inertia Orientation",
        description="Inertia-tensor orientation as quaternion (x, y, z, w)",
        default=(0.0, 0.0, 0.0, 1.0),
        size=4,
        subtype='QUATERNION',
    )


# ============================================================================
# UI Panel
# ============================================================================

# ============================================================================
# UI Panels - Viewport sidebar (View3D > N key > "OMI" tab)
# ============================================================================
#
# Three top-level collapsible panels, vertically stacked:
#   1. Analyze  - scale check + validate button + exports info
#   2. Body     - is_collision toggle + body_type/trigger/mass + 3 sub-panels
#                 (Velocity, Center of Mass, Inertia) - all default closed
#   3. Shape    - shape_type + per-shape fields + Auto-fit + Bake buttons
#
# Sub-panels use bl_parent_id to nest under their parent. They inherit
# bl_category automatically. Setting bl_options={'DEFAULT_CLOSED'} keeps
# them collapsed on first open.

# ----------------------------------------------------------------------------
# Panel 1: ANALYZE (always visible)
# ----------------------------------------------------------------------------
class OBJECT_PT_omi_analyze(Panel):
    bl_label = "Analyze"
    bl_idname = "OBJECT_PT_omi_analyze"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'OMI Physics'
    # bl_order controls vertical stacking order within the tab.
    # Lower numbers appear higher. We want Analyze at the BOTTOM, so it
    # gets the highest number of the three top-level panels.
    bl_order = 3

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        layout = self.layout
        obj = context.object
        layout.use_property_split = True
        layout.use_property_decorate = False

        # ---- Scale validator --------------------------------------------
        # Godot's CollisionShape3D warns when its scale is non-uniform.
        scale_box = layout.box()
        scale_box.label(text="Scale Check (Godot requirement)", icon=_icon('PHYSICS'))
        local_ok, world_ok, local_s, world_s = _scale_status(obj)
        scale_box.label(text=(
            f"Local: ({local_s.x:.3f}, {local_s.y:.3f}, {local_s.z:.3f}) "
            f"{'- OK' if local_ok else '- NON-UNIFORM!'}"
        ), icon=_icon('CHECKMARK' if local_ok else 'ERROR'))
        scale_box.label(text=(
            f"World: ({world_s.x:.3f}, {world_s.y:.3f}, {world_s.z:.3f}) "
            f"{'- OK' if world_ok else '- NON-UNIFORM!'}"
        ), icon=_icon('CHECKMARK' if world_ok else 'ERROR'))
        if not local_ok:
            scale_box.label(text="Godot will warn + physics will be wrong", icon=_icon('ERROR'))
            # Suggest a fix - bake button is on the Shape panel if applicable.
            props = obj.omi_physics_props
            if props.is_collision and props.shape_type in ('box', 'sphere', 'cylinder', 'capsule'):
                scale_box.operator("object.omi_physics_bake_scale",
                                   icon=_icon('MODIFIER'),
                                   text="Bake Scale into Shape")
            elif props.is_collision:
                scale_box.label(text="For convex/trimesh, apply scale via Ctrl+A > Scale",
                                icon=_icon('INFO'))
        elif not world_ok:
            scale_box.label(text="An ancestor has non-uniform scale", icon=_icon('ERROR'))
            scale_box.label(text="Fix the parent's scale, or parent this object to the root",
                            icon=_icon('INFO'))

        # ---- Validate-all button -----------------------------------------
        layout.operator("object.omi_physics_validate_scene",
                        icon=_icon('SCENE_DATA'),
                        text="Validate All Collision Objects")

        # ---- Info ----------------------------------------------------------
        info_box = layout.box()
        info_box.label(text="Exports:", icon=_icon('EXPORT'))
        info_box.label(text="  - OMI_physics_shape (root, deduplicated shapes)")
        info_box.label(text="  - OMI_physics_body  (per-node, motion + collider)")
        if not _HAS_GLTF:
            info_box.label(text="WARNING: glTF addon not enabled!", icon=_icon('ERROR'))


# ----------------------------------------------------------------------------
# Panel 2: BODY (top-level; always visible - contains master toggle)
# ----------------------------------------------------------------------------
class OBJECT_PT_omi_body(Panel):
    bl_label = "Body"
    bl_idname = "OBJECT_PT_omi_body"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'OMI Physics'
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def draw(self, context):
        layout = self.layout
        obj = context.object
        props = obj.omi_physics_props
        layout.use_property_split = True
        layout.use_property_decorate = False

        # Master toggle always visible
        layout.prop(props, "is_collision")

        if not props.is_collision:
            box = layout.box()
            box.label(text="Enable to export as OMI_physics_body", icon=_icon('INFO'))
            box.label(text="Godot will auto-import as CollisionObject3D", icon=_icon('PHYSICS'))
            return

        # Core body fields (always visible when collision is on)
        layout.prop(props, "body_type")
        layout.prop(props, "is_trigger")
        if props.body_type == 'dynamic':
            layout.prop(props, "mass")


# ----------------------------------------------------------------------------
# Panel 2a: BODY > Velocity (sub-panel, default closed)
# ----------------------------------------------------------------------------
class OBJECT_PT_omi_body_velocity(Panel):
    bl_label = "Velocity"
    bl_idname = "OBJECT_PT_omi_body_velocity"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'OMI Physics'
    bl_parent_id = "OBJECT_PT_omi_body"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.omi_physics_props.is_collision

    def draw(self, context):
        layout = self.layout
        props = context.object.omi_physics_props
        # Hybrid pattern: one axis per line, label INSIDE the box, with all
        # axes of a group fused together vertically (no inter-row gaps) via
        # an aligned column. Produces the boxed-group look:
        #   ┌─┬───────┐
        #   │X│ 0 m/s │
        #   ├─┼───────┤
        #   │Y│ 0 m/s │
        #   ├─┼───────┤
        #   │Z│ 0 m/s │
        #   └─┴───────┘
        layout.use_property_split = False
        layout.use_property_decorate = False

        # Linear Velocity (m/s)
        layout.label(text="Linear Velocity (m/s):")
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(props, "linear_velocity", index=0, text="X")
        row = col.row(align=True)
        row.prop(props, "linear_velocity", index=1, text="Y")
        row = col.row(align=True)
        row.prop(props, "linear_velocity", index=2, text="Z")

        # Angular Velocity (rad/s)
        layout.label(text="Angular Velocity (rad/s):")
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(props, "angular_velocity", index=0, text="X")
        row = col.row(align=True)
        row.prop(props, "angular_velocity", index=1, text="Y")
        row = col.row(align=True)
        row.prop(props, "angular_velocity", index=2, text="Z")


# ----------------------------------------------------------------------------
# Panel 2b: BODY > Center of Mass (sub-panel, default closed)
# ----------------------------------------------------------------------------
class OBJECT_PT_omi_body_com(Panel):
    bl_label = "Center of Mass"
    bl_idname = "OBJECT_PT_omi_body_com"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'OMI Physics'
    bl_parent_id = "OBJECT_PT_omi_body"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.omi_physics_props.is_collision

    def draw(self, context):
        layout = self.layout
        props = context.object.omi_physics_props
        # Hybrid pattern: one axis per line, label INSIDE the box, fused
        # vertically via aligned column.
        layout.use_property_split = False
        layout.use_property_decorate = False

        layout.label(text="Center of Mass Offset:")
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(props, "center_of_mass", index=0, text="X")
        row = col.row(align=True)
        row.prop(props, "center_of_mass", index=1, text="Y")
        row = col.row(align=True)
        row.prop(props, "center_of_mass", index=2, text="Z")


# ----------------------------------------------------------------------------
# Panel 2c: BODY > Inertia (sub-panel, default closed)
# ----------------------------------------------------------------------------
class OBJECT_PT_omi_body_inertia(Panel):
    bl_label = "Inertia"
    bl_idname = "OBJECT_PT_omi_body_inertia"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'OMI Physics'
    bl_parent_id = "OBJECT_PT_omi_body"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.omi_physics_props.is_collision

    def draw(self, context):
        layout = self.layout
        props = context.object.omi_physics_props
        # Hybrid pattern: one axis per line, label INSIDE the box, fused
        # vertically via aligned column.
        layout.use_property_split = False
        layout.use_property_decorate = False

        # Inertia Diagonal (kg * m^2)
        layout.label(text="Inertia Diagonal (kg·m²):")
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(props, "inertia_diagonal", index=0, text="X")
        row = col.row(align=True)
        row.prop(props, "inertia_diagonal", index=1, text="Y")
        row = col.row(align=True)
        row.prop(props, "inertia_diagonal", index=2, text="Z")

        # Inertia Orientation (quaternion xyzw)
        layout.label(text="Inertia Orientation (quaternion):")
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(props, "inertia_orientation", index=0, text="X")
        row = col.row(align=True)
        row.prop(props, "inertia_orientation", index=1, text="Y")
        row = col.row(align=True)
        row.prop(props, "inertia_orientation", index=2, text="Z")
        row = col.row(align=True)
        row.prop(props, "inertia_orientation", index=3, text="W")


# ----------------------------------------------------------------------------
# Panel 3: SHAPE (only visible when is_collision is on)
# ----------------------------------------------------------------------------
class OBJECT_PT_omi_shape(Panel):
    bl_label = "Shape"
    bl_idname = "OBJECT_PT_omi_shape"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'OMI Physics'
    bl_order = 2

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj is not None and obj.omi_physics_props.is_collision

    def draw(self, context):
        layout = self.layout
        obj = context.object
        props = obj.omi_physics_props
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.prop(props, "shape_type")

        st = props.shape_type
        if st == 'box':
            # Box Size: fused-box pattern (label + aligned column + 3 rows).
            # Disable use_property_split for this sub-section so the text="X"
            # labels render inside the input boxes instead of in front.
            sub = layout.column()
            sub.use_property_split = False
            sub.label(text="Box Size:")
            col = sub.column(align=True)
            row = col.row(align=True)
            row.prop(props, "box_size", index=0, text="X")
            row = col.row(align=True)
            row.prop(props, "box_size", index=1, text="Y")
            row = col.row(align=True)
            row.prop(props, "box_size", index=2, text="Z")
        elif st == 'sphere':
            layout.prop(props, "sphere_radius")
        elif st == 'cylinder':
            layout.prop(props, "cylinder_radius")
            layout.prop(props, "cylinder_height")
            layout.prop(props, "cylinder_axis")
        elif st == 'capsule':
            layout.prop(props, "capsule_radius")
            layout.prop(props, "capsule_height")
            layout.prop(props, "capsule_axis")
        elif st == 'convex':
            layout.label(text="Uses this object's mesh as convex hull", icon=_icon('INFO'))
            if obj.type != 'MESH':
                layout.label(text="WARNING: object has no mesh!", icon=_icon('ERROR'))
        elif st == 'trimesh':
            layout.label(text="Uses this object's mesh as triangle mesh", icon=_icon('INFO'))
            if obj.type != 'MESH':
                layout.label(text="WARNING: object has no mesh!", icon=_icon('ERROR'))
        elif st == 'none':
            layout.label(text="Compound/group node (no own shape)", icon=_icon('INFO'))

        # ---- Auto-fit + Reset buttons ------------------------------------
        # Vertically stacked (fused via aligned column). The Reset button uses
        # the 'X' icon which has been in Blender since 2.5x and won't be
        # removed - semantically reads as "clear the custom dimensions".
        # Text label is also kept so the button is usable even if the icon
        # somehow fails to render.
        if st in ('box', 'sphere', 'cylinder', 'capsule'):
            col = layout.column(align=True)
            col.operator("object.omi_physics_auto_fit",
                         icon=_icon('MESH_CUBE'),
                         text="Auto-Fit from Object")
            col.operator("object.omi_physics_reset_defaults",
                         icon=_icon('X'),
                         text="Reset to Defaults")


# ============================================================================
# Operator: auto-fit shape from object dimensions
# ============================================================================

class OBJECT_OT_omi_physics_auto_fit(Operator):
    bl_idname = "object.omi_physics_auto_fit"
    bl_label = "Auto-Fit Collision from Object"
    bl_description = "Set the collision shape dimensions from the object's bounding box"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object is not None and context.object.omi_physics_props.is_collision

    def execute(self, context):
        obj = context.object
        props = obj.omi_physics_props
        if _auto_fit_from_object(obj, props):
            self.report({'INFO'}, "Auto-fit collision from object dimensions")
            return {'FINISHED'}
        self.report({'WARNING'}, "Auto-fit not available for this shape type "
                                 "(or object has no dimensions)")
        return {'CANCELLED'}


# ============================================================================
# Operator: reset shape dimensions to defaults
# ============================================================================

class OBJECT_OT_omi_physics_reset_defaults(Operator):
    bl_idname = "object.omi_physics_reset_defaults"
    bl_label = "Reset Shape to Defaults"
    bl_description = (
        "Reset the collision shape's dimensions to sane defaults "
        "(box=1,1,1; sphere=0.5; cylinder=0.5/1.0; capsule=0.5/1.0). "
        "Useful after editing the object's mesh if you don't want to "
        "re-run Auto-Fit."
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        if obj is None or not obj.omi_physics_props.is_collision:
            return False
        return obj.omi_physics_props.shape_type in ('box', 'sphere', 'cylinder', 'capsule')

    def execute(self, context):
        props = context.object.omi_physics_props
        st = props.shape_type
        if st == 'box':
            props.box_size = (1.0, 1.0, 1.0)
        elif st == 'sphere':
            props.sphere_radius = 0.5
        elif st == 'cylinder':
            props.cylinder_radius = 0.5
            props.cylinder_height = 1.0
            props.cylinder_axis = 'Y'
        elif st == 'capsule':
            props.capsule_radius = 0.5
            props.capsule_height = 1.0
            props.capsule_axis = 'Y'
        else:
            self.report({'WARNING'}, "Reset not available for this shape type")
            return {'CANCELLED'}
        self.report({'INFO'}, "Shape reset to defaults")
        return {'FINISHED'}


# ============================================================================
# Operator: bake non-uniform scale into the collision shape
# ============================================================================

class OBJECT_OT_omi_physics_bake_scale(Operator):
    bl_idname = "object.omi_physics_bake_scale"
    bl_label = "Bake Scale into Shape"
    bl_description = (
        "Multiply the collision shape's size/radius/height by the object's "
        "local scale, then reset the object's scale to (1, 1, 1). This "
        "prevents Godot's non-uniform-scale warning on the imported "
        "CollisionShape3D."
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        if obj is None or not obj.omi_physics_props.is_collision:
            return False
        return obj.omi_physics_props.shape_type in ('box', 'sphere', 'cylinder', 'capsule')

    def execute(self, context):
        obj = context.object
        props = obj.omi_physics_props
        sx, sy, sz = float(obj.scale.x), float(obj.scale.y), float(obj.scale.z)

        if props.shape_type == 'box':
            props.box_size = (
                abs(props.box_size[0] * sx),
                abs(props.box_size[1] * sy),
                abs(props.box_size[2] * sz),
            )
        elif props.shape_type == 'sphere':
            new_r = props.sphere_radius * max(sx, sy, sz)
            props.sphere_radius = new_r
            self.report({'WARNING'},
                        "Sphere scaled non-uniformly: used max axis for radius")
        elif props.shape_type == 'cylinder':
            axis = props.cylinder_axis
            if axis == 'X':
                h_comp, r_comp = sx, math.sqrt(sy * sz)
            elif axis == 'Z':
                h_comp, r_comp = sz, math.sqrt(sx * sy)
            else:
                h_comp, r_comp = sy, math.sqrt(sx * sz)
            props.cylinder_height = abs(props.cylinder_height * h_comp)
            props.cylinder_radius = abs(props.cylinder_radius * r_comp)
            if not _is_uniform(obj.scale):
                self.report({'INFO'},
                            "Cylinder radius used geometric mean of non-axis scales")
        elif props.shape_type == 'capsule':
            axis = props.capsule_axis
            if axis == 'X':
                h_comp, r_comp = sx, math.sqrt(sy * sz)
            elif axis == 'Z':
                h_comp, r_comp = sz, math.sqrt(sx * sy)
            else:
                h_comp, r_comp = sy, math.sqrt(sx * sz)
            props.capsule_height = abs(props.capsule_height * h_comp)
            props.capsule_radius = abs(props.capsule_radius * r_comp)
            if not _is_uniform(obj.scale):
                self.report({'INFO'},
                            "Capsule radius used geometric mean of non-axis scales")
        else:
            self.report({'WARNING'}, "Bake not supported for this shape type")
            return {'CANCELLED'}

        obj.scale = (1.0, 1.0, 1.0)
        self.report({'INFO'}, "Baked scale into shape; object scale reset to (1,1,1)")
        return {'FINISHED'}


# ============================================================================
# Operator: validate all collision objects in the scene
# ============================================================================

class OBJECT_OT_omi_physics_validate_scene(Operator):
    bl_idname = "object.omi_physics_validate_scene"
    bl_label = "Validate All Collision Objects"
    bl_description = (
        "Scan all collision-enabled objects in the scene and report any "
        "with non-uniform local or world scale (Godot will warn about them)."
    )

    def execute(self, context):
        bad_local = []
        bad_world = []
        for obj in context.scene.objects:
            if not getattr(obj, "omi_physics_props", None):
                continue
            if not obj.omi_physics_props.is_collision:
                continue
            local_ok, world_ok, local_s, world_s = _scale_status(obj)
            if not local_ok:
                bad_local.append((obj.name, tuple(local_s)))
            if not world_ok:
                bad_world.append((obj.name, tuple(world_s)))

        if not bad_local and not bad_world:
            self.report({'INFO'}, "All collision objects have uniform scale")
            return {'FINISHED'}

        lines = ["Non-uniform scale on collision objects:"]
        for name, s in bad_local:
            lines.append(f"  LOCAL  {name}: ({s[0]:.3f}, {s[1]:.3f}, {s[2]:.3f})")
        for name, s in bad_world:
            lines.append(f"  WORLD  {name}: ({s[0]:.3f}, {s[1]:.3f}, {s[2]:.3f})")
        print("[OMI Physics] " + "\n".join(lines))
        self.report({'WARNING'},
                    f"{len(bad_local)} local + {len(bad_world)} world non-uniform "
                    f"(see console for details)")
        return {'FINISHED'}


# ============================================================================
# glTF Export Extension
# ============================================================================
#
# Canonical name: `glTF2ExportUserExtension`. The glTF exporter discovers
# this class by `hasattr(module, 'glTF2ExportUserExtension')` and calls it
# with zero args. NO subclassing, NO register_class.
#
# The `Extension` helper (used to wrap per-node/per-root payloads) is
# imported lazily inside __init__ so that:
#   - The class can always be defined at module scope (even if io_scene_gltf2
#     is not yet importable, e.g., when the glTF addon is disabled).
#   - The import only fires when the exporter actually instantiates us.

class glTF2ExportUserExtension:
    """
    Per-node hook: builds the inlined OMI_physics_body extension on the
    glTF node and registers the shape (deduplicated) in the root
    OMI_physics_shape.shapes array.

    Root hook: serialises the shapes array onto the glTF root and adds
    the extension names to `extensionsUsed`.
    """

    def __init__(self):
        # Lazy import: io_scene_gltf2 may not be importable at module-load
        # time, but it WILL be importable here (the glTF exporter only
        # instantiates us during an actual glTF export operation).
        from io_scene_gltf2.io.com.gltf2_io_extensions import Extension
        self.extension = Extension
        # shapes list + deduplication map (key -> index)
        self.shapes = []
        self._shape_index = {}
        # +Y Up conversion flag. Set on the first gather_node_hook call
        # from export_settings['gltf_yup']. Defaults to True (the glTF
        # exporter's default) until we get authoritative info.
        self.is_y_up = True

    # ------------------------------------------------------------------
    # Shape construction
    # ------------------------------------------------------------------
    def _build_shape(self, props, gltf2_node):
        """Return a shape dict matching the Godot OMI_physics_shape schema.

        Returns None if no shape should be emitted (shape_type == 'none').

        Applies +Y Up conversion (Blender Z-up -> glTF Y-up) when
        self.is_y_up is True. The UI panel always shows Blender-convention
        values; the conversion happens here, at export time only.
        """
        st = props.shape_type
        if st == 'none':
            return None

        shape = {"type": st}
        yup = getattr(self, 'is_y_up', True)

        if st == 'box':
            size = [float(props.box_size[0]),
                    float(props.box_size[1]),
                    float(props.box_size[2])]
            if yup:
                size = _convert_size_yup(size)
            shape["box"] = {"size": size}
        elif st == 'sphere':
            shape["sphere"] = {"radius": float(props.sphere_radius)}
        elif st == 'cylinder':
            shape["cylinder"] = {
                "radius": float(props.cylinder_radius),
                "height": float(props.cylinder_height),
            }
            # Convert axis label Blender -> glTF (only when +Y Up is on).
            axis_blender = props.cylinder_axis
            axis_gltf = _convert_axis_yup(axis_blender) if yup else axis_blender
            if axis_gltf != 'Y':
                shape["cylinder"]["axis"] = axis_gltf
        elif st == 'capsule':
            shape["capsule"] = {
                "radius": float(props.capsule_radius),
                "height": float(props.capsule_height),
            }
            axis_blender = props.capsule_axis
            axis_gltf = _convert_axis_yup(axis_blender) if yup else axis_blender
            if axis_gltf != 'Y':
                shape["capsule"]["axis"] = axis_gltf
        elif st == 'convex':
            mesh_idx = gltf2_node.mesh if gltf2_node is not None else None
            if mesh_idx is None:
                print("OMI Physics: 'convex' shape requires the object to "
                      "have a mesh that is exported. Skipping shape on "
                      f"node {getattr(gltf2_node, 'name', '?')!r}.")
                return None
            shape["convex"] = {"mesh": int(mesh_idx)}
        elif st == 'trimesh':
            mesh_idx = gltf2_node.mesh if gltf2_node is not None else None
            if mesh_idx is None:
                print("OMI Physics: 'trimesh' shape requires the object to "
                      "have a mesh that is exported. Skipping shape on "
                      f"node {getattr(gltf2_node, 'name', '?')!r}.")
                return None
            shape["trimesh"] = {"mesh": int(mesh_idx)}
        return shape

    @staticmethod
    def _shape_key(shape):
        return json.dumps(shape, sort_keys=True, default=str)

    def _register_shape(self, shape):
        """Deduplicate and return the index into the root shapes array."""
        key = self._shape_key(shape)
        idx = self._shape_index.get(key)
        if idx is None:
            idx = len(self.shapes)
            self.shapes.append(shape)
            self._shape_index[key] = idx
        return idx

    # ------------------------------------------------------------------
    # Body (inlined on the node) construction
    # ------------------------------------------------------------------
    @staticmethod
    def _build_motion(props):
        motion = {"type": props.body_type}
        if props.body_type == 'dynamic':
            if props.mass != 1.0:
                motion["mass"] = float(props.mass)
        if tuple(props.linear_velocity) != (0.0, 0.0, 0.0):
            motion["linearVelocity"] = [float(v) for v in props.linear_velocity]
        if tuple(props.angular_velocity) != (0.0, 0.0, 0.0):
            motion["angularVelocity"] = [float(v) for v in props.angular_velocity]
        if tuple(props.center_of_mass) != (0.0, 0.0, 0.0):
            motion["centerOfMass"] = [float(v) for v in props.center_of_mass]
        if tuple(props.inertia_diagonal) != (0.0, 0.0, 0.0):
            motion["inertiaDiagonal"] = [float(v) for v in props.inertia_diagonal]
        if tuple(props.inertia_orientation) != (0.0, 0.0, 0.0, 1.0):
            motion["inertiaOrientation"] = [float(v) for v in props.inertia_orientation]
        return motion

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------
    def gather_node_hook(self, gltf2_object, blender_object, bl_node, *args, **kwargs):
        """Attach OMI_physics_body extension to the exported node.

        Signature varies by Blender version:
          3.4 - 4.0: (gltf2_object, blender_object, bl_node)
          4.1+ / 5.x: (gltf2_object, blender_object, bl_node, export_settings)
        We accept *args/**kwargs to handle both. The export_settings dict
        (if present) is used to read the `gltf_yup` flag for +Y Up axis
        conversion of the shape payload (Blender Z-up -> glTF Y-up).
        """
        try:
            # Extract export_settings if the glTF exporter passed it.
            export_settings = kwargs.get('export_settings')
            if export_settings is None and args:
                export_settings = args[0]
            if export_settings is not None:
                self.is_y_up = bool(export_settings.get('gltf_yup', False))

            if not blender_object:
                return
            if not hasattr(blender_object, "omi_physics_props"):
                return
            props = blender_object.omi_physics_props
            if not props.is_collision:
                return

            # Export-time scale guard.
            try:
                local_ok, world_ok, local_s, world_s = _scale_status(blender_object)
                if not local_ok:
                    print(
                        f"[OMI Physics] WARNING: object "
                        f"{blender_object.name!r} has non-uniform LOCAL scale "
                        f"({local_s.x:.4f}, {local_s.y:.4f}, {local_s.z:.4f}). "
                        f"Godot will warn and the physics will be wrong. "
                        f"Run 'Bake Scale into Shape' before exporting."
                    )
                if not world_ok:
                    print(
                        f"[OMI Physics] WARNING: object "
                        f"{blender_object.name!r} has non-uniform WORLD scale "
                        f"({world_s.x:.4f}, {world_s.y:.4f}, {world_s.z:.4f}) "
                        f"due to an ancestor's scale. Fix the parent's scale."
                    )
            except Exception:
                pass  # Never block export over a guard failure.

            body_ext = {}

            if not props.is_trigger:
                body_ext["motion"] = self._build_motion(props)

            shape = self._build_shape(props, gltf2_object)
            if shape is not None:
                shape_idx = self._register_shape(shape)
                if props.is_trigger:
                    body_ext["trigger"] = {"shape": shape_idx}
                else:
                    body_ext["collider"] = {"shape": shape_idx}
            else:
                if props.is_trigger:
                    body_ext["trigger"] = {}

            if not body_ext:
                return

            if gltf2_object.extensions is None:
                gltf2_object.extensions = {}
            gltf2_object.extensions["OMI_physics_body"] = self.extension(
                name="OMI_physics_body",
                extension=body_ext,
                required=False,
            )
        except Exception as exc:
            print(f"OMI Physics export: gather_node_hook failed: {exc}")
            traceback.print_exc()

    def gather_gltf_extensions_hook(self, gltf2_object, export_settings):
        """Serialise the root OMI_physics_shape extension."""
        try:
            if not self.shapes:
                return
            if gltf2_object.extensions is None:
                gltf2_object.extensions = {}
            if gltf2_object.extensions_used is None:
                gltf2_object.extensions_used = []
            if gltf2_object.extensions_required is None:
                gltf2_object.extensions_required = []

            gltf2_object.extensions["OMI_physics_shape"] = self.extension(
                name="OMI_physics_shape",
                extension={"shapes": self.shapes},
                required=False,
            )
            for ext_name in ("OMI_physics_shape", "OMI_physics_body"):
                if ext_name not in gltf2_object.extensions_used:
                    gltf2_object.extensions_used.append(ext_name)
        except Exception as exc:
            print(f"OMI Physics export: gather_gltf_extensions_hook failed: {exc}")
            traceback.print_exc()


# ============================================================================
# glTF Import Extension (best-effort round-trip)
# ============================================================================
#
# Canonical name: `glTF2ImportUserExtension`. Same discovery pattern as the
# export side - the glTF importer scans for this module attribute and
# instantiates it. NO subclassing, NO register_class.

class glTF2ImportUserExtension:
    """
    Walks the parsed glTF after import and applies any OMI_physics_body
    extension data back onto the Blender objects that were created for
    each glTF node. Best-effort round-trip.
    """

    def __init__(self):
        pass

    def _get_shapes(self, gltf):
        ext_root = getattr(gltf, "extensions", None) or {}
        shape_ext = ext_root.get("OMI_physics_shape")
        if shape_ext is None:
            return []
        if hasattr(shape_ext, "extension"):
            shape_ext = shape_ext.extension
        return shape_ext.get("shapes", []) or []

    def _node_blender_object(self, gltf, node_idx):
        """Try several known mappings from glTF node index to Blender obj."""
        vnodes = getattr(gltf, "vnodes", None)
        if vnodes:
            vnode = vnodes.get(node_idx)
            if vnode is None:
                vnode = vnodes.get(str(node_idx))
            if vnode is not None:
                bl_obj = getattr(vnode, "blender_object", None)
                if bl_obj:
                    return bl_obj
        n2o = getattr(gltf, "node_to_obj", None)
        if n2o and node_idx in n2o:
            return n2o[node_idx]
        return None

    def _apply_body(self, gltf, node_idx, node):
        ext = getattr(node, "extensions", None) or {}
        body_ext = ext.get("OMI_physics_body")
        if body_ext is None:
            return
        if hasattr(body_ext, "extension"):
            body_ext = body_ext.extension

        bl_obj = self._node_blender_object(gltf, node_idx)
        if bl_obj is None:
            return
        if not hasattr(bl_obj, "omi_physics_props"):
            return

        props = bl_obj.omi_physics_props
        props.is_collision = True

        motion = body_ext.get("motion")
        collider = body_ext.get("collider")
        trigger = body_ext.get("trigger")

        if motion:
            props.body_type = motion.get("type", "static")
            props.mass = motion.get("mass", 1.0)
            if "linearVelocity" in motion:
                props.linear_velocity = motion["linearVelocity"]
            if "angularVelocity" in motion:
                props.angular_velocity = motion["angularVelocity"]
            if "centerOfMass" in motion:
                props.center_of_mass = motion["centerOfMass"]
            if "inertiaDiagonal" in motion:
                props.inertia_diagonal = motion["inertiaDiagonal"]
            if "inertiaOrientation" in motion:
                props.inertia_orientation = motion["inertiaOrientation"]
            props.is_trigger = False
        elif trigger is not None:
            props.is_trigger = True

        shapes = self._get_shapes(gltf)
        shape_idx = None
        if collider and "shape" in collider:
            shape_idx = collider["shape"]
        elif trigger and isinstance(trigger, dict) and "shape" in trigger:
            shape_idx = trigger["shape"]

        if shape_idx is not None and 0 <= shape_idx < len(shapes):
            shape = shapes[shape_idx]
            st = shape.get("type", "box")
            if st == "box":
                props.shape_type = 'box'
                size = shape.get("box", {}).get("size", [1.0, 1.0, 1.0])
                props.box_size = size
            elif st == "sphere":
                props.shape_type = 'sphere'
                props.sphere_radius = shape.get("sphere", {}).get("radius", 0.5)
            elif st == "cylinder":
                props.shape_type = 'cylinder'
                c = shape.get("cylinder", {})
                props.cylinder_radius = c.get("radius", 0.5)
                props.cylinder_height = c.get("height", 1.0)
                props.cylinder_axis = c.get("axis", "Y")
            elif st == "capsule":
                props.shape_type = 'capsule'
                c = shape.get("capsule", {})
                props.capsule_radius = c.get("radius", 0.5)
                props.capsule_height = c.get("height", 1.0)
                props.capsule_axis = c.get("axis", "Y")
            elif st == "convex":
                props.shape_type = 'convex'
            elif st == "trimesh":
                props.shape_type = 'trimesh'

    def gather_import_gltf_after_hook(self, gltf, import_settings):
        try:
            nodes = getattr(gltf, "nodes", None) or []
            for idx, node in enumerate(nodes):
                self._apply_body(gltf, idx, node)
        except Exception as exc:
            print(f"OMI Physics import: gather_import_gltf_after_hook failed: {exc}")
            traceback.print_exc()


# ============================================================================
# Registration
# ============================================================================

_classes = (
    OMIPhysicsProperties,
    # Panels (3 top-level + 3 sub-panels)
    OBJECT_PT_omi_analyze,
    OBJECT_PT_omi_body,
    OBJECT_PT_omi_body_velocity,
    OBJECT_PT_omi_body_com,
    OBJECT_PT_omi_body_inertia,
    OBJECT_PT_omi_shape,
    # Operators
    OBJECT_OT_omi_physics_auto_fit,
    OBJECT_OT_omi_physics_reset_defaults,
    OBJECT_OT_omi_physics_bake_scale,
    OBJECT_OT_omi_physics_validate_scene,
)

# NOTE: glTF2ExportUserExtension and glTF2ImportUserExtension are NOT in
# _classes. The glTF exporter/importer discovers them by module attribute
# name and instantiates them itself. We must NOT register_class them with
# bpy.utils.


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.omi_physics_props = PointerProperty(type=OMIPhysicsProperties)

    if not _HAS_GLTF:
        print("OMI Physics: io_scene_gltf2 not found - glTF export/import "
              "hook disabled. Enable the 'glTF 2.0 format' addon in "
              "Preferences > Add-ons.")


def unregister():
    try:
        del bpy.types.Object.omi_physics_props
    except AttributeError:
        pass
    for cls in reversed(_classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception:
            pass


if __name__ == "__main__":
    register()
