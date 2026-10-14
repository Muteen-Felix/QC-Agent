"""Resolve static FastAPI router mounts and string settings without importing SUT code."""
import ast
from pathlib import Path


def inspect(root, files, health_sources=None):
    modules, module_files = {}, {}
    for rel in files:
        try:
            tree = ast.parse((Path(root) / rel).read_text(encoding='utf-8'))
        except (OSError, UnicodeError, SyntaxError):
            continue
        name = str(rel).replace('\\', '/').removesuffix('.py').replace('/', '.')
        if name.endswith('.__init__'): name = name[:-9]
        modules[name] = tree
        module_files[name] = str(rel).replace('\\', '/')
    imports, values, objects, edges, health, openapis = {}, {}, {}, [], [], []
    def module(name):
        matches = [m for m in modules if m == name or m.endswith('.' + name)]
        return matches[0] if len(matches) == 1 else None
    def dotted(node):
        if isinstance(node, ast.Name): return node.id
        if isinstance(node, ast.Attribute): return dotted(node.value) + '.' + node.attr
        return ''
    for m, tree in modules.items():
        aliases = imports[m] = {}
        for n in tree.body:
            if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                for alias in n.names:
                    child = module(n.module + '.' + alias.name)
                    parent = module(n.module)
                    if child: aliases[alias.asname or alias.name] = (child, '')
                    elif parent: aliases[alias.asname or alias.name] = (parent, alias.name)
            elif isinstance(n, ast.Import):
                for alias in n.names:
                    target = module(alias.name)
                    if target: aliases[alias.asname or alias.name] = (target, '')
        for n in ast.walk(tree):
            if isinstance(n, ast.ClassDef):
                for field in n.body:
                    if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                        values[m, n.name + '.' + field.target.id] = field.value
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                values[m, n.targets[0].id] = n.value
                if isinstance(n.value, ast.Call):
                    kind = dotted(n.value.func).split('.')[-1]
                    if kind in ('FastAPI', 'APIRouter'):
                        kwargs = {k.arg:k.value for k in n.value.keywords}
                        objects[m, n.targets[0].id] = (kind, kwargs.get('prefix', ast.Constant('')))
                        if kind == 'FastAPI': openapis.append((m, kwargs.get('openapi_url', ast.Constant('/openapi.json'))))
    def resolve(m, name, depth=0):
        if depth > 15: return m, name
        first, _, suffix = name.partition('.')
        if first in imports[m]:
            target, export = imports[m][first]
            return resolve(target, '.'.join(p for p in (export, suffix) if p), depth + 1)
        # settings = Settings(): resolve string class fields, never execute the constructor.
        value = values.get((m, first))
        if suffix and isinstance(value, ast.Call):
            return resolve(m, dotted(value.func) + '.' + suffix, depth + 1)
        return m, name
    def string(m, node, seen=()):
        if isinstance(node, ast.Constant) and isinstance(node.value, str): return node.value
        if isinstance(node, ast.JoinedStr):
            parts = [string(m, v.value if isinstance(v, ast.FormattedValue) else v, seen) for v in node.values]
            return ''.join(parts) if all(p is not None for p in parts) else None
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            a, b = string(m, node.left, seen), string(m, node.right, seen)
            return a + b if a is not None and b is not None else None
        key = resolve(m, dotted(node))
        if key in seen or key not in values: return None
        return string(key[0], values[key], (*seen, key))
    for m, tree in modules.items():
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'include_router' and n.args:
                parent = resolve(m, dotted(n.func.value))
                child = resolve(m, dotted(n.args[0]))
                prefix = next((k.value for k in n.keywords if k.arg == 'prefix'), ast.Constant(''))
                edges.append((parent, child, string(m, prefix)))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in n.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == 'get' and dec.args:
                        path = string(m, dec.args[0])
                        if (path and any(word in path.lower() for word in ('health', 'ready'))
                                and (health_sources is None or module_files[m] in health_sources)):
                            health.append((resolve(m, dotted(dec.func.value)), path))
    mounts = {}
    def visit(key, prefix, seen):
        if key in seen or key not in objects: return
        own = string(key[0], objects[key][1])
        if own is None: return
        full = prefix + own
        mounts.setdefault(key, set()).add(full)
        for parent, child, addition in edges:
            if parent == key and addition is not None: visit(child, full + addition, {*seen, key})
    for key, (kind, _) in objects.items():
        if kind == 'FastAPI': visit(key, '', set())
    paths = sorted({prefix + path for key, path in health for prefix in mounts.get(key, ())})
    schemas = sorted({value for m, n in openapis if (value := string(m, n)) is not None})
    unresolved_schema = any(string(m, n) is None for m, n in openapis)
    return paths, schemas, unresolved_schema
