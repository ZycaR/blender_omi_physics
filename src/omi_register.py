"""
omi_register.py - composition root: _classes + register()/unregister().

NOTE: glTF2ExportUserExtension and glTF2ImportUserExtension are NOT in
_classes - the glTF exporter/importer discovers them by module attribute
name and instantiates them itself. We must NOT register_class them with
bpy.utils.
"""

import bpy
from bpy.props import PointerProperty

from omi_core import OMIPhysicsProperties
from omi_gltf_ext import _HAS_GLTF
from omi_modifier import (
    OBJECT_MT_omi_physics_modifier_add,
    OBJECT_OT_omi_collider_add_modifier,
    OBJECT_PT_omi_collider_modifier,
    register_omi_collider_menu,
    unregister_omi_collider_menu,
)
from omi_ui import (
    OBJECT_OT_omi_physics_auto_fit,
    OBJECT_OT_omi_physics_bake_scale,
    OBJECT_OT_omi_physics_reset_defaults,
    OBJECT_OT_omi_physics_validate_scene,
    OBJECT_PT_omi_analyze,
    OBJECT_PT_omi_body,
    OBJECT_PT_omi_body_com,
    OBJECT_PT_omi_body_inertia,
    OBJECT_PT_omi_body_velocity,
    OBJECT_PT_omi_shape,
)


# ============================================================================
# Registration
# ============================================================================

_classes = (
    OMIPhysicsProperties,
    # Add Modifier > OMI Physics submenu (extensible category menu)
    OBJECT_MT_omi_physics_modifier_add,
    # Panels (3 top-level + 3 sub-panels + modifier-stack panel)
    OBJECT_PT_omi_analyze,
    OBJECT_PT_omi_body,
    OBJECT_PT_omi_body_velocity,
    OBJECT_PT_omi_body_com,
    OBJECT_PT_omi_body_inertia,
    OBJECT_PT_omi_shape,
    OBJECT_PT_omi_collider_modifier,
    # Operators
    OBJECT_OT_omi_physics_auto_fit,
    OBJECT_OT_omi_physics_reset_defaults,
    OBJECT_OT_omi_physics_bake_scale,
    OBJECT_OT_omi_physics_validate_scene,
    OBJECT_OT_omi_collider_add_modifier,
)

# NOTE: glTF2ExportUserExtension and glTF2ImportUserExtension are NOT in
# _classes. The glTF exporter/importer discovers them by module attribute
# name and instantiates them itself. We must NOT register_class them with
# bpy.utils.


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.omi_physics_props = PointerProperty(type=OMIPhysicsProperties)
    register_omi_collider_menu()

    if not _HAS_GLTF:
        print("OMI Physics: io_scene_gltf2 not found - glTF export/import "
              "hook disabled. Enable the 'glTF 2.0 format' addon in "
              "Preferences > Add-ons.")


def unregister():
    unregister_omi_collider_menu()
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
