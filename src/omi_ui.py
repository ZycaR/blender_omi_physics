"""
omi_ui.py - viewport sidebar UI: panels and operators (UI layer).

  * Panels: Analyze, Body (+ Velocity / Center of Mass / Inertia
    sub-panels), Shape.
  * Operators: auto-fit, reset-to-defaults, bake-scale-into-shape,
    validate-scene.

No physics or glTF logic lives here - helpers come from omi_core and the
glTF-addon availability flag (_HAS_GLTF) is probed in omi_gltf_ext.
"""

import math
from bpy.types import Panel, Operator

from omi_core import _auto_fit_from_object, _icon, _is_uniform, _scale_status
from omi_gltf_ext import _HAS_GLTF


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

        # ---- Visual grouping box for primitive shapes --------------------
        # Wrap the per-shape dimension inputs + Auto-Fit + Reset buttons in
        # a layout.box() so they read as one visual group. Convex / trimesh /
        # none just show an informational label and stay outside the box.
        if st in ('box', 'sphere', 'cylinder', 'capsule'):
            group = layout.box()

            if st == 'box':
                # Box Size: fused-box pattern (label + aligned column + 3 rows).
                # Disable use_property_split for this sub-section so the text="X"
                # labels render inside the input boxes instead of in front.
                sub = group.column()
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
                group.prop(props, "sphere_radius")
            elif st == 'cylinder':
                group.prop(props, "cylinder_radius")
                group.prop(props, "cylinder_height")
                group.prop(props, "cylinder_axis")
            elif st == 'capsule':
                group.prop(props, "capsule_radius")
                group.prop(props, "capsule_height")
                group.prop(props, "capsule_axis")

            # ---- Auto-fit + Reset buttons (vertically stacked, fused) ----
            # Reset uses 'X' icon (safe since Blender 2.5x) - reads as
            # "clear the custom dimensions". Text label kept for accessibility.
            col = group.column(align=True)
            col.operator("object.omi_physics_auto_fit",
                         icon=_icon('MESH_CUBE'),
                         text="Auto-Fit from Object")
            col.operator("object.omi_physics_reset_defaults",
                         icon=_icon('X'),
                         text="Reset to Defaults")

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
