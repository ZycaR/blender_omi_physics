#!/usr/bin/env python3
"""Offline smoke test for the BUILT single-file bundle.

Stubs bpy and io_scene_gltf2, imports the bundle as a module, and exercises
the main code paths so a broken build never ships:

  * symbol presence (bl_info, PropertyGroup, 6 panels, 4 operators,
    both glTF user-extension classes, register/unregister)
  * bl_info version synced from blender_manifest.toml
  * pure helpers (icon fallback, scale uniformity, +Y Up conversion)
  * auto-fit logic
  * a full export round: gather_node_hook + gather_gltf_extensions_hook
    (shape dedup, extensionsUsed, Y-up box-size swap)
  * the import (round-trip) hook
  * register() / unregister()

Usage:
    python3 scripts/verify_bundle.py [path-to-bundle]

Exit code 0 = OK, 1 = failures. Complements the deep scenario suite in
scripts/test_omi_physics_export.py (which should point at dist/ as well).
"""
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUNDLE = Path(sys.argv[1]) if len(sys.argv) > 1 else \
    ROOT / 'dist' / 'omi_physics_body_gltf_extension.py'
MANIFEST = ROOT / 'blender_manifest.toml'

failures = []


def check(label, cond):
    tag = 'ok  ' if cond else 'FAIL'
    print(f'  [{tag}] {label}')
    if not cond:
        failures.append(label)


# ------------------------------------------------------------- bpy stub
def _prop_stub(*args, **kwargs):
    return ('PROP_STUB', args, kwargs)


bpy = types.ModuleType('bpy')
bpy_props = types.ModuleType('bpy.props')
for n in ('BoolProperty', 'EnumProperty', 'FloatProperty',
          'FloatVectorProperty', 'PointerProperty', 'StringProperty'):
    setattr(bpy_props, n, _prop_stub)
bpy_types = types.ModuleType('bpy.types')
for n in ('PropertyGroup', 'Panel', 'Operator', 'Object', 'UILayout'):
    setattr(bpy_types, n, type(n, (), {}))
bpy_utils = types.ModuleType('bpy.utils')
bpy_utils.register_class = lambda cls: None
bpy_utils.unregister_class = lambda cls: None
bpy.props, bpy.types, bpy.utils = bpy_props, bpy_types, bpy_utils
sys.modules.update({'bpy': bpy, 'bpy.props': bpy_props,
                    'bpy.types': bpy_types, 'bpy.utils': bpy_utils})

# ------------------------------------------------ io_scene_gltf2 stub
class _FakeExtension:
    def __init__(self, name=None, extension=None, required=False):
        self.name, self.extension, self.required = name, extension, required


gltf_exts_mod = types.ModuleType('io_scene_gltf2.io.com.gltf2_io_extensions')
gltf_exts_mod.Extension = _FakeExtension
for dotted in ('io_scene_gltf2', 'io_scene_gltf2.io', 'io_scene_gltf2.io.com'):
    m = types.ModuleType(dotted)
    m.__path__ = []
    sys.modules[dotted] = m
sys.modules['io_scene_gltf2.io.com.gltf2_io_extensions'] = gltf_exts_mod
sys.modules['io_scene_gltf2'].io = sys.modules['io_scene_gltf2.io']
sys.modules['io_scene_gltf2.io'].com = sys.modules['io_scene_gltf2.io.com']
sys.modules['io_scene_gltf2.io.com'].gltf2_io_extensions = gltf_exts_mod

# ------------------------------------------------------------ load it
if not BUNDLE.exists():
    print(f'verify_bundle: bundle not found: {BUNDLE}')
    sys.exit(1)

spec = importlib.util.spec_from_file_location('omi_physics_bundle', BUNDLE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
print(f'loaded bundle: {BUNDLE}')

# --------------------------------------------------------- fake scene
class NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


DEFAULT_PROPS = dict(
    is_collision=True, body_type='dynamic', is_trigger=False,
    shape_type='box', box_size=(1.0, 2.0, 3.0),
    sphere_radius=0.5,
    cylinder_radius=0.5, cylinder_height=1.0, cylinder_axis='Z',
    capsule_radius=0.5, capsule_height=1.0, capsule_axis='Y',
    mass=2.0,
    linear_velocity=(0.0, 0.0, 0.0), angular_velocity=(0.0, 0.0, 0.0),
    center_of_mass=(0.0, 0.0, 0.0), inertia_diagonal=(0.0, 0.0, 0.0),
    inertia_orientation=(0.0, 0.0, 0.0, 1.0),
)


def fake_props(**over):
    d = dict(DEFAULT_PROPS)
    d.update(over)
    return NS(**d)


def fake_obj(**over):
    d = dict(name='TestObj', type='MESH',
             scale=NS(x=1.0, y=1.0, z=1.0), matrix_world=None,
             dimensions=NS(x=2.0, y=4.0, z=4.0),
             display_type='TEXTURED', display_bounds_type='BOX',
             omi_physics_props=fake_props())
    d.update(over)
    return NS(**d)


# ------------------------------------------------------------- tests
print('symbols:')
for name in ('bl_info', 'OMIPhysicsProperties', 'register', 'unregister',
             'OBJECT_PT_omi_analyze', 'OBJECT_PT_omi_body',
             'OBJECT_PT_omi_body_velocity', 'OBJECT_PT_omi_body_com',
             'OBJECT_PT_omi_body_inertia', 'OBJECT_PT_omi_shape',
             'OBJECT_OT_omi_physics_auto_fit',
             'OBJECT_OT_omi_physics_reset_defaults',
             'OBJECT_OT_omi_physics_bake_scale',
             'OBJECT_OT_omi_physics_validate_scene',
             'glTF2ExportUserExtension', 'glTF2ImportUserExtension',
             '_HAS_GLTF'):
    check(f'module defines {name}', hasattr(mod, name))

print('bl_info sync:')
manifest_version = None
if MANIFEST.exists():
    m = MANIFEST.read_text(encoding='utf-8').splitlines()
    for line in m:
        if line.strip().startswith('version = '):
            manifest_version = line.split('=')[1].strip().strip('"')
            break
if manifest_version:
    want = tuple(int(p) for p in manifest_version.split('.'))
    check(f'bl_info version == manifest version {want}',
          tuple(mod.bl_info['version']) == want)
    check('bl_info author non-empty', bool(mod.bl_info.get('author')))

print('pure helpers:')
check("_icon('MESH_CUBE') returns a valid icon",
      mod._icon('MESH_CUBE') in ('MESH_CUBE', 'NONE'))
check("_icon('NOT_A_REAL_ICON') falls back to 'NONE'",
      mod._icon('NOT_A_REAL_ICON') == 'NONE')
check('_is_uniform (1,1,1) is True', mod._is_uniform(NS(x=1.0, y=1.0, z=1.0)))
check('_is_uniform (1,2,1) is False', not mod._is_uniform(NS(x=1.0, y=2.0, z=1.0)))
check('_convert_size_yup swaps Y/Z', mod._convert_size_yup([1.0, 2.0, 3.0]) == [1.0, 3.0, 2.0])
check("_convert_axis_yup('Z') == 'Y'", mod._convert_axis_yup('Z') == 'Y')
check("_convert_axis_yup('Y') == 'Z'", mod._convert_axis_yup('Y') == 'Z')

print('auto-fit:')
props = fake_props(shape_type='sphere')
check('auto-fit sphere radius from dimensions (2,4,4) -> 2.0',
      mod._auto_fit_from_object(fake_obj(dimensions=NS(x=2.0, y=4.0, z=4.0)), props)
      and props.sphere_radius == 2.0)
props = fake_props(shape_type='box')
mod._auto_fit_from_object(fake_obj(), props)
check('auto-fit box size from dimensions (2,4,4)',
      tuple(props.box_size) == (2.0, 4.0, 4.0))

print('export round (Y-up):')
exporter = mod.glTF2ExportUserExtension()
node = NS(name='TestNode', mesh=0, extensions=None,
          extensions_used=None, extensions_required=None)
bl_obj = fake_obj()
exporter.gather_node_hook(node, bl_obj, None, {'gltf_yup': True})
check('node got OMI_physics_body extension',
      node.extensions is not None and 'OMI_physics_body' in node.extensions)
if node.extensions:
    body = node.extensions['OMI_physics_body'].extension
    check("motion.type == 'dynamic'", body.get('motion', {}).get('type') == 'dynamic')
    check('motion.mass == 2.0 (non-default emitted)', body.get('motion', {}).get('mass') == 2.0)
    check('collider.shape == 0', body.get('collider', {}).get('shape') == 0)
root = NS(extensions=None, extensions_used=None, extensions_required=None)
exporter.gather_gltf_extensions_hook(root, {})
check('root got OMI_physics_shape extension',
      root.extensions is not None and 'OMI_physics_shape' in root.extensions)
if root.extensions:
    shapes = root.extensions['OMI_physics_shape'].extension.get('shapes', [])
    check('one deduplicated shape', len(shapes) == 1 and shapes[0]['type'] == 'box')
    check('box size Y-up swapped (1,2,3)->(1,3,2)',
          shapes[0].get('box', {}).get('size') == [1.0, 3.0, 2.0])
    check('extensionsUsed has both extension names',
          {'OMI_physics_shape', 'OMI_physics_body'} <= set(root.extensions_used or []))

print('export round (Y-up off):')
exporter2 = mod.glTF2ExportUserExtension()
node2 = NS(name='N2', mesh=0, extensions=None,
           extensions_used=None, extensions_required=None)
exporter2.gather_node_hook(node2, fake_obj(), None, {})
root2 = NS(extensions=None, extensions_used=None, extensions_required=None)
exporter2.gather_gltf_extensions_hook(root2, {})
if root2.extensions:
    shapes2 = root2.extensions['OMI_physics_shape'].extension.get('shapes', [])
    check('box size NOT swapped when gltf_yup is off/absent',
          shapes2 and shapes2[0].get('box', {}).get('size') == [1.0, 2.0, 3.0])
else:
    check('root2 extensions present (y-up off)', False)

print('import round-trip:')
importer = mod.glTF2ImportUserExtension()
target = fake_obj()
target.omi_physics_props = fake_props(is_collision=False, body_type='static')
gltf = NS(
    extensions={'OMI_physics_shape': {'shapes': [
        {'type': 'box', 'box': {'size': [1.0, 3.0, 2.0]}}]}},
    vnodes={0: NS(blender_object=target)},
    nodes=[NS(name='TestNode', extensions={'OMI_physics_body': {
        'motion': {'type': 'dynamic', 'mass': 2.0},
        'collider': {'shape': 0}}})],
)
importer.gather_import_gltf_after_hook(gltf, {})
p = target.omi_physics_props
check('import set is_collision True', p.is_collision is True)
check("import set body_type 'dynamic'", p.body_type == 'dynamic')
check('import set mass 2.0', p.mass == 2.0)
check('import set box size', tuple(p.box_size) == (1.0, 3.0, 2.0))

print('register / unregister:')
try:
    mod.register()
    mod.unregister()
    check('register()+unregister() ran without exceptions', True)
except Exception as exc:  # noqa: BLE001
    check(f'register()/unregister() raised: {exc!r}', False)

print()
if failures:
    print(f'verify_bundle: {len(failures)} FAILURE(S)')
    for f in failures:
        print(f'  - {f}')
    sys.exit(1)
print('verify_bundle: ALL CHECKS PASSED')
