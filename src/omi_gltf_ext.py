"""
omi_gltf_ext.py - glTF 2.0 export / import user extension classes.

  * _HAS_GLTF probe (io_scene_gltf2 availability for the UI indicator)
  * glTF2ExportUserExtension (per-node OMI_physics_body + root
    OMI_physics_shape with deduplication)
  * glTF2ImportUserExtension (best-effort round-trip import)

Canonical glTF user-extension integration: the exporter/importer discovers
the module attributes glTF2ExportUserExtension / glTF2ImportUserExtension
by name and instantiates them - NO subclassing, NO register_class. The
`Extension` helper is imported lazily inside __init__ so the module can
load even when io_scene_gltf2 is not importable.
"""

import json
import traceback

from omi_core import _convert_axis_yup, _convert_size_yup, _scale_status


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
