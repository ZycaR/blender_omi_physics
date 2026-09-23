#!/usr/bin/env python3
"""AST-equivalence check between two single-file addon versions.

Used to prove that the src/ split (and the build.mjs bundling) is a pure
refactor: the bundle must contain exactly the same module-level statements
as the reference file.

Usage:
    python3 scripts/check_equivalence.py REFERENCE.py BUNDLE.py

Normalizations (all behavior-neutral, applied to BOTH files):
  * module-level docstring / string-expression statements are dropped
    (the split adds per-module docstrings; the meta docstring was edited)
  * the bl_info dict literal is replaced with a marker constant, because
    build.mjs syncs version/author/doc_url from blender_manifest.toml
  * import alias order is sorted (`from x import a, b` == `from x import b, a`)
  * module-level statements are sorted by (kind, name, dump) so that moving
    a top-level definition between bundle sections is not reported as a
    change (order of top-level defs has no behavioral effect here - nothing
    is read at module level before it is defined in either layout)
"""
import ast
import sys


def kind_name(node):
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        mod = getattr(node, 'module', None) or ','.join(
            a.name for a in node.names)
        return (f'IMP:{mod}', mod)
    if isinstance(node, ast.ClassDef):
        return (f'CLS:{node.name}', node.name)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return (f'FUNC:{node.name}', node.name)
    if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
        return (f'VAR:{node.targets[0].id}', node.targets[0].id)
    return ('OTHER', ast.dump(node)[:60])


def normalize(path):
    tree = ast.parse(open(path, encoding='utf-8').read())
    out = []
    for node in tree.body:
        # 1) drop module-level string expressions (docstrings)
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue
        # 2) bl_info is synced from blender_manifest.toml by the build
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == 'bl_info':
            node.value = ast.Constant(value='__BL_INFO_SYNCED_FROM_MANIFEST__')
        # 3) sort import aliases
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            node.names = sorted(node.names, key=lambda a: a.name)
            if isinstance(node, ast.ImportFrom):
                node.module = node.module or ''
        out.append(node)
    # 4) order-insensitive module body
    out.sort(key=lambda n: (kind_name(n)[0], ast.dump(n)))
    return ast.dump(ast.Module(body=out, type_ignores=[]))


def top_level_names(path):
    tree = ast.parse(open(path, encoding='utf-8').read())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    ref, bundle = sys.argv[1], sys.argv[2]
    d_ref, d_bundle = normalize(ref), normalize(bundle)
    names_ref, names_bundle = top_level_names(ref), top_level_names(bundle)

    ok = True
    if d_ref == d_bundle:
        print('[equivalence] PASS - normalized ASTs are identical')
    else:
        ok = False
        print('[equivalence] FAIL - normalized ASTs differ')
        # Diagnostics: top-level name diff is the most common cause.
        missing = names_ref - names_bundle
        extra = names_bundle - names_ref
        if missing:
            print(f'  missing from bundle: {sorted(missing)}')
        if extra:
            print(f'  extra in bundle    : {sorted(extra)}')
        print('  (inspect by diffing: python3 -c "import ast,sys; ..." or an IDE)')
    print(f'[equivalence] top-level names: ref={len(names_ref)} bundle={len(names_bundle)}')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
