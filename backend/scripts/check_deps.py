import ast
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "app"

LAYER = {
    "app.api": 1,
    "app.agent.nodes": 1,
    "app.agent.graph": 1,
    "app.scheduler": 1,
    "app.services": 2,
    "app.tools": 2,
    "app.rag": 2,
    "app.optimizer": 2,
    "app.repositories": 3,
    "app.clients": 3,
    "app.cache": 3,
    "app.models": 4,
    "app.core": 4,
}

SHARED_CONSTANT_PREFIXES = ("app.agent.prompts",)

OPTIMIZER_ALLOWED_PREFIXES = ("app.optimizer", "app.core.config")
EXTERNAL_SDK_MODULES = {"openai", "ortools"}


def module_layer(module: str) -> int | None:
    if any(module == p or module.startswith(p + ".") for p in SHARED_CONSTANT_PREFIXES):
        return None
    for prefix, layer in LAYER.items():
        if module == prefix or module.startswith(prefix + "."):
            return layer
    return None


def iter_imports(tree: ast.AST) -> list[str]:
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.append(node.module)
    return modules


def to_module(py: Path) -> str:
    rel = py.relative_to(BACKEND.parent)
    module = str(rel.with_suffix("")).replace("\\", ".")
    if module.endswith(".__init__"):
        module = module[: -len(".__init__")]
    return module


def check() -> list[str]:
    violations: list[str] = []
    for py in BACKEND.rglob("*.py"):
        module = to_module(py)
        tree = ast.parse(py.read_text(encoding="utf-8"))
        imports = [m for m in iter_imports(tree) if m.startswith("app.")]

        own_layer = module_layer(module)
        if own_layer is not None:
            for imp in imports:
                target_layer = module_layer(imp)
                if target_layer is None:
                    continue
                if target_layer < own_layer:
                    violations.append(
                        f"dependency rule violated: {module} (layer {own_layer}) imports {imp} (layer {target_layer})"
                    )

        if module.startswith("app.optimizer"):
            for imp in imports:
                if not imp.startswith(OPTIMIZER_ALLOWED_PREFIXES):
                    violations.append(
                        f"optimizer must be pure (only {OPTIMIZER_ALLOWED_PREFIXES}): {module} imports {imp}"
                    )

        if not module.startswith("app.clients"):
            for imp in imports:
                root = imp.split(".")[0]
                if root in EXTERNAL_SDK_MODULES:
                    violations.append(f"external SDK only allowed in app.clients: {module} imports {imp}")
    return violations


def main() -> int:
    violations = check()
    for v in violations:
        print(v)
    print(f"checked, {len(violations)} violation(s)")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())