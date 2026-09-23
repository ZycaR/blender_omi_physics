"""
omi_modifier.py - OMI Physics modifier-stack integration.

Blender Python add-ons CANNOT register a new real Modifier type
(that requires C changes + a custom Blender build). The supported
equivalent implemented here:

  * Adds a top-level "OMI Physics" submenu to Add Modifier with an
    "OMI Collider" entry (Add Modifier > OMI Physics > OMI Collider).
  * That entry adds a Geometry Nodes (NODES) modifier named
    "OMI Collider" wired to a pass-through "[OMI Physics] Collider"
    node-group tree (geometry passes through untouched -
    the modifier is a stack anchor). Future modifiers (e.g. OMI
    Physics > OMI Physics Body) use the same "[OMI Physics] ..."
    tag prefix so their trees group together in pickers.
  * A custom Properties > Modifiers panel (bl_context="modifier")
    draws the SAME Shape UI as the sidebar Shape panel (Shape Type +
    per-shape dimensions + Auto-Fit + Reset to Defaults).

Authoritative data always lives on Object.omi_physics_props (which the
glTF exporter already reads) - the modifier panel edits object
properties directly via layout.prop(props, ...). The NODES modifier
itself stores no physics state.

Headless-safe: all bpy.data / menu access happens inside functions
guarded with try/except so scripts/verify_bundle.py (stubbed bpy)
still passes.
"""

import bpy
from bpy.types import Menu, Operator, Panel

from omi_core import _icon


# Top-level Add Modifier submenu owned by this extension.
OMI_PHYSICS_MENU_LABEL = "OMI Physics"
# Names shown in the modifier stack / node editor. The node-group tree
# uses the "[OMI Physics] Collider" tag-style prefix so related trees
# group together alphabetically and future modifiers (e.g. "[OMI
# Physics] Physics Body") sort next to it. NOTE: ">" must NOT be used
# in datablock names - it is a reserved library path separator, and a
# tree named "OMI Physics > OMI Collider" is hidden from the Geometry
# Nodes "Browse Node Tree" picker (visible only via New).
OMI_COLLIDER_MOD_NAME = "OMI Collider"
OMI_COLLIDER_GROUP_NAME = "[OMI Physics] Collider"
# Tree names from older builds that must still be recognised.
OMI_COLLIDER_LEGACY_GROUPS = frozenset({
    OMI_COLLIDER_MOD_NAME,          # 1.7.0 initial build
    "OMI Physics > OMI Collider",   # 1.7.0 submenu build
})


def _is_omi_collider_modifier(md):
    """True if `md` is one of our anchor NODES modifiers."""
    try:
        if getattr(md, "type", "") != 'NODES':
            return False
        ng = getattr(md, "node_group", None)
        ng_name = getattr(ng, "name", "") or ""
        # Current tree name + legacy tree names from older builds.
        if ng_name == OMI_COLLIDER_GROUP_NAME:
            return True
        if ng_name in OMI_COLLIDER_LEGACY_GROUPS:
            return True
        return (getattr(md, "name", "") or "").startswith(OMI_COLLIDER_MOD_NAME)
    except Exception:
        return False


def _find_collider_modifiers(obj):
    """Return the list of OMI Collider anchor modifiers on `obj`."""
    try:
        mods = getattr(obj, "modifiers", []) or []
    except Exception:
        return []
    return [m for m in mods if _is_omi_collider_modifier(m)]

def _get_or_create_collider_node_group():
    """Return the shared pass-through "[OMI Physics] Collider" tree.

    Group Input.geometry -> Group Output.geometry (no-op). Socket setup
    is best-effort across Blender 3.x/4.x/5.x APIs; a bare group is still
    a valid no-op so failures never raise. Returns None only if even the
    group itself cannot be created.

    A stale "OMI Physics > OMI Collider" tree (">" is a reserved library
    path separator, invisible in the Browse picker) is renamed in place
    to the new name so old scenes heal automatically.
    """
    try:
        groups = bpy.data.node_groups
    except Exception:
        return None
    try:
        existing = groups.get(OMI_COLLIDER_GROUP_NAME)
        if existing is not None:
            return existing
    except Exception:
        pass
    # Heal: rename the ">"-named tree from the previous build (it is
    # hidden from the Browse Node Tree picker) instead of leaving a
    # duplicate behind.
    try:
        stale = groups.get("OMI Physics > OMI Collider")
        if stale is not None:
            stale.name = OMI_COLLIDER_GROUP_NAME
            return stale
    except Exception:
        pass
    try:
        ng = groups.new(OMI_COLLIDER_GROUP_NAME, 'GeometryNodeTree')
    except Exception:
        return None
    try:
        in_node = ng.nodes.new('NodeGroupInput')
        out_node = ng.nodes.new('NodeGroupOutput')
        in_node.location = (-200, 0)
        out_node.location = (200, 0)
        try:
            # Blender 4.0+ interface API.
            iface = ng.interface
            have_in = False
            have_out = False
            for s in iface.items_tree:
                if getattr(s, "socket_type", "") == 'NodeSocketGeometry':
                    if getattr(s, "in_out", "") == 'INPUT':
                        have_in = True
                    if getattr(s, "in_out", "") == 'OUTPUT':
                        have_out = True
            if not have_in:
                iface.new_socket("Geometry", socket_type='NodeSocketGeometry',
                                 in_out='INPUT')
            if not have_out:
                iface.new_socket("Geometry", socket_type='NodeSocketGeometry',
                                 in_out='OUTPUT')
        except Exception:
            # Pre-4.0 API.
            try:
                if not getattr(ng, "inputs", []):
                    ng.inputs.new('NodeSocketGeometry', "Geometry")
                if not getattr(ng, "outputs", []):
                    ng.outputs.new('NodeSocketGeometry', "Geometry")
            except Exception:
                pass
        try:
            ng.links.new(in_node.outputs[0], out_node.inputs[0])
        except Exception:
            pass
    except Exception:
        pass
    return ng


# ============================================================================
# Shared Shape UI (mirrors OBJECT_PT_omi_shape in omi_ui.py)
# ============================================================================

def draw_omi_shape_ui(layout, obj):
    """Draw Shape Type + dimensions + Auto-Fit/Reset for `obj`.

    Shared by the sidebar Shape panel and the modifier-stack panel so
    both stay identical. Edits obj.omi_physics_props (the exporter
    source of truth) directly - the NODES modifier stores no state.
    """
    props = obj.omi_physics_props
    layout.use_property_split = True
    layout.use_property_decorate = False

    if not props.is_collision:
        box = layout.box()
        box.label(text="Enable Is Collision to export", icon=_icon('INFO'))

    layout.prop(props, "shape_type")
    st = props.shape_type

    if st in ('box', 'sphere', 'cylinder', 'capsule'):
        group = layout.box()

        if st == 'box':
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

        col = group.column(align=True)
        col.operator("object.omi_physics_auto_fit",
                     icon=_icon('MESH_CUBE'),
                     text="Auto-Fit from Object")
        col.operator("object.omi_physics_reset_defaults",
                     icon=_icon('X'),
                     text="Reset to Defaults")

    elif st == 'convex':
        layout.label(text="Uses this object's mesh as convex hull", icon=_icon('INFO'))
        if getattr(obj, "type", 'MESH') != 'MESH':
            layout.label(text="WARNING: object has no mesh!", icon=_icon('ERROR'))
    elif st == 'trimesh':
        layout.label(text="Uses this object's mesh as triangle mesh", icon=_icon('INFO'))
        if getattr(obj, "type", 'MESH') != 'MESH':
            layout.label(text="WARNING: object has no mesh!", icon=_icon('ERROR'))
    elif st == 'none':
        layout.label(text="Compound/group node (no own shape)", icon=_icon('INFO'))


# ============================================================================
# Operator: Add Modifier > Physics > OMI Collider
# ============================================================================

class OBJECT_OT_omi_collider_add_modifier(Operator):
    bl_idname = "object.omi_collider_add_modifier"
    bl_label = "Add OMI Collider"
    bl_description = (
        "Add an 'OMI Collider' Geometry Nodes modifier as a physics "
        "collider anchor. Its modifier-stack panel exposes the same Shape "
        "settings and enables Is Collision so the object exports."
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return getattr(context, "object", None) is not None

    def execute(self, context):
        obj = context.object
        if obj is None:
            self.report({'WARNING'}, "No active object")
            return {'CANCELLED'}

        if _find_collider_modifiers(obj):
            self.report({'INFO'}, "Object already has an OMI Collider modifier")
            return {'FINISHED'}

        ng = _get_or_create_collider_node_group()
        try:
            md = obj.modifiers.new(name=OMI_COLLIDER_MOD_NAME, type='NODES')
        except Exception as exc:
            self.report({'WARNING'}, "Could not add modifier: %s" % exc)
            return {'CANCELLED'}
        if ng is not None:
            try:
                md.node_group = ng
            except Exception:
                pass

        # Same as the sidebar toggle: enable -> auto-fit + bounds display
        # via the is_collision update callback in omi_core.
        try:
            props = obj.omi_physics_props
            if not props.is_collision:
                props.is_collision = True
        except Exception:
            pass

        self.report({'INFO'}, "Added OMI Collider modifier")
        return {'FINISHED'}


# ============================================================================
# Panel: Properties > Modifiers > OMI Collider
# ============================================================================

class OBJECT_PT_omi_collider_modifier(Panel):
    bl_label = "OMI Collider"
    bl_idname = "OBJECT_PT_omi_collider_modifier"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "modifier"

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "object", None)
        if obj is None:
            return False
        return len(_find_collider_modifiers(obj)) > 0

    def draw(self, context):
        layout = self.layout
        obj = context.object
        for md in _find_collider_modifiers(obj):
            box = layout.box()
            header = box.row()
            header.label(text=getattr(md, "name", OMI_COLLIDER_MOD_NAME),
                         icon=_icon('PHYSICS'))
            try:
                op = header.operator("object.modifier_remove", text="",
                                     icon=_icon('X'))
                op.modifier = md.name
            except Exception:
                pass
            try:
                draw_omi_shape_ui(box, obj)
            except Exception:
                pass


# ============================================================================
# Add Modifier > OMI Physics submenu (extensible for future modifiers)
# ============================================================================
#
# The root Add Modifier menu (OBJECT_MT_modifier_add) draws one
# layout.menu(...) line per category (Edit / Generate / Deform /
# Physics / ...). We append our own top-level category line for
# "OMI Physics" so the tree reads:
#
#     Add Modifier > OMI Physics > OMI Collider
#
# Future modifiers (e.g. OMI Physics Body) add their operator line to
# OBJECT_MT_omi_physics_modifier_add.draw() - no other menu plumbing
# needed.


class OBJECT_MT_omi_physics_modifier_add(Menu):
    bl_label = OMI_PHYSICS_MENU_LABEL
    bl_idname = "OBJECT_MT_omi_physics_modifier_add"

    def draw(self, context):
        layout = self.layout
        try:
            layout.operator("object.omi_collider_add_modifier",
                            text="OMI Collider",
                            icon=_icon('PHYSICS'))
        except Exception:
            try:
                layout.operator("object.omi_collider_add_modifier",
                                text="OMI Collider")
            except Exception:
                pass
        # Future entries go here, e.g.:
        #   layout.operator("object.omi_body_add_modifier",
        #                   text="OMI Physics Body", icon=_icon('PHYSICS'))


def omi_physics_root_menu_entry(self, context):
    """Append the "OMI Physics" category line to Add Modifier root menu."""
    try:
        obj = getattr(context, "object", None)
        if obj is None:
            return
        # Same object types Geometry Nodes supports as anchors.
        if getattr(obj, "type", 'MESH') not in {
                'EMPTY', 'MESH', 'CURVE', 'CURVES', 'FONT', 'VOLUME',
                'POINTCLOUD', 'GREASEPENCIL'}:
            return
        self.layout.menu("OBJECT_MT_omi_physics_modifier_add",
                         icon=_icon('PHYSICS'))
    except Exception:
        try:
            self.layout.menu("OBJECT_MT_omi_physics_modifier_add")
        except Exception:
            pass


def register_omi_collider_menu():
    """Append the OMI Physics submenu to the Add Modifier root menu."""
    try:
        root_menu = getattr(bpy.types, "OBJECT_MT_modifier_add", None)
        if root_menu is not None:
            root_menu.append(omi_physics_root_menu_entry)
    except Exception:
        pass


def unregister_omi_collider_menu():
    """Remove the OMI Physics submenu from the Add Modifier root menu."""
    try:
        root_menu = getattr(bpy.types, "OBJECT_MT_modifier_add", None)
        if root_menu is not None:
            root_menu.remove(omi_physics_root_menu_entry)
    except Exception:
        pass


