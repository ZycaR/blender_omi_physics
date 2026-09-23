"""
omi_core.py - business logic & data model (no UI, no glTF coupling).

Sections (bundled in this order):
  * Icon safety helper (_icon / _valid_icons)
  * Non-uniform scale helpers (Godot CollisionShape3D semantics)
  * Auto-fit helper (object bounding box -> shape dimensions)
  * Viewport display apply/restore (used by the update callbacks)
  * glTF +Y Up conversion helpers
  * OMIPhysicsProperties (per-object PropertyGroup + update callbacks)

Everything here is headless-testable with a stubbed bpy. UI panels and
operators live in omi_ui.py; the glTF exporter/importer integration lives
in omi_gltf_ext.py.
"""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import PropertyGroup


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

    When the user enables Is Collision:
      - Auto-fit the shape's dimensions from the object's bounding box
        (for primitive shapes only).
      - Switch the object's viewport display to BOUNDS with a
        display_bounds_type that matches the collision shape type:
          box      -> BOX
          sphere   -> SPHERE
          cylinder -> CYLINDER
          capsule  -> CAPSULE
          none     -> BOX (just show the AABB as a hint)
          convex / trimesh -> WIRE (the mesh IS the collision shape)
      - Save the previous display_type AND display_bounds_type so we can
        restore both on disable.

    When the user disables Is Collision:
      - Restore the object's previous display_type and display_bounds_type,
        but ONLY if they're still what we set them to (i.e., the user
        didn't manually change them after enabling). If they manually
        switched to other values, respect their choice and don't clobber.
    """
    obj = context.object
    if obj is None:
        return

    if self.is_collision:
        # Enable: auto-fit + apply viewport display
        _auto_fit_from_object(obj, self)
        _apply_viewport_display(obj, self, save_original=True)
    else:
        # Disable: restore previous display settings (only if user hasn't
        # manually changed them since we applied ours).
        _restore_viewport_display(obj, self)


# Mapping from collision shape_type to Blender display_bounds_type.
# Only primitive shapes are mapped; convex/trimesh/none are handled
# separately (see _apply_viewport_display).
_SHAPE_TO_BOUNDS_TYPE = {
    'box':      'BOX',
    'sphere':   'SPHERE',
    'cylinder': 'CYLINDER',
    'capsule':  'CAPSULE',
    'none':     'BOX',         # No shape -> just show the AABB
}


def _apply_viewport_display(obj, props, save_original=True):
    """Set obj.display_type and obj.display_bounds_type based on the
    collision shape_type. If save_original is True, record the previous
    values on `props` so they can be restored later.
    """
    if not hasattr(obj, "display_type"):
        return  # Armature/Camera/etc. - no display_type attr.

    # Save originals (only if not already saved - this guards against
    # on->off->on without a clear in between).
    if save_original:
        if not props.saved_display_type:
            props.saved_display_type = obj.display_type
        if not props.saved_display_bounds_type and hasattr(obj, "display_bounds_type"):
            props.saved_display_bounds_type = obj.display_bounds_type

    st = props.shape_type
    if st in ('convex', 'trimesh'):
        # The mesh IS the collision shape -> show it as wireframe.
        obj.display_type = 'WIRE'
    else:
        # Primitive shapes (box/sphere/cylinder/capsule) and 'none':
        # display as bounds with a matching shape.
        obj.display_type = 'BOUNDS'
        if hasattr(obj, "display_bounds_type"):
            obj.display_bounds_type = _SHAPE_TO_BOUNDS_TYPE.get(st, 'BOX')


def _restore_viewport_display(obj, props):
    """Inverse of _apply_viewport_display: restore the saved display_type
    and display_bounds_type, but only if the user hasn't manually changed
    them since we applied ours. Then clear the saved values.
    """
    if not hasattr(obj, "display_type"):
        return

    # Restore display_type if it's still what we set it to.
    if props.saved_display_type:
        # We set it to either 'BOUNDS' (primitive/none) or 'WIRE' (convex/trimesh).
        # If the user manually changed it to something else, respect their choice.
        current = obj.display_type
        if current in ('BOUNDS', 'WIRE'):
            obj.display_type = props.saved_display_type
        props.saved_display_type = ""

    # Restore display_bounds_type similarly.
    if props.saved_display_bounds_type and hasattr(obj, "display_bounds_type"):
        current_bounds = obj.display_bounds_type
        # We set it to one of BOX/SPHERE/CYLINDER/CAPSULE.
        if current_bounds in _SHAPE_TO_BOUNDS_TYPE.values():
            obj.display_bounds_type = props.saved_display_bounds_type
        props.saved_display_bounds_type = ""


def _on_shape_type_changed(self, context):
    """Update callback for OMIPhysicsProperties.shape_type.

    When the user picks a new primitive shape type:
      - Auto-fit its dimensions from the object's bounding box so the new
        shape immediately matches the object's size. Skipped for
        convex/trimesh/none (no dimensions to fit).
      - Re-apply the viewport display so the gizmo matches the new shape
        type (e.g., switching from Box to Cylinder updates the viewport
        bounds shape from BOX to CYLINDER). save_original=False because
        we already saved on Is Collision enable.
    Skipped entirely if Is Collision is off.
    """
    if not self.is_collision:
        return
    obj = context.object
    if obj is None:
        return
    _auto_fit_from_object(obj, self)
    _apply_viewport_display(obj, self, save_original=False)


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
        description=(
            "OMI_physics_shape.shapes[].type. When changed, the new shape's "
            "dimensions are auto-fitted from the object's bounding box "
            "(for primitive shapes only)."
        ),
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
        update=_on_shape_type_changed,
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

    # ---- Internal (hidden) state ------------------------------------------
    # Records the object's display_type and display_bounds_type before we
    # forced them on Is Collision enable, so we can restore both on disable.
    # Empty string means "no saved value" (i.e., Is Collision is currently
    # off or we never forced the display).
    #
    # NOTE: Blender disallows PropertyGroup attribute names starting with
    # '_', so these are named without the underscore prefix. They're still
    # effectively hidden because no panel ever draws them.
    saved_display_type: StringProperty(
        name="Saved Display Type",
        description="Internal: stores the object's display_type before Is Collision forced it.",
        default="",
    )
    saved_display_bounds_type: StringProperty(
        name="Saved Display Bounds Type",
        description="Internal: stores the object's display_bounds_type before Is Collision forced it.",
        default="",
    )
