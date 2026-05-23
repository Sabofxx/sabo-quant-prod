"""
Concrete ``DependencyPolicy`` — AST-based scanner for the declarative rules
in ``governance/dependency_rules.py``.

Implementation philosophy
-------------------------

* **AST only.** No regex on source text, no string matching. A comment or
  a docstring that happens to mention a restricted symbol must not produce
  a false positive.

* **Per-file two-pass.** For each ``.py`` file the scanner first builds a
  symbol table from ``Import`` / ``ImportFrom`` nodes, then walks
  ``Call`` nodes and resolves each call's target against that table.

* **Aliases resolved.** ``from m import Cls as C; C(...)`` is classified
  as a construction of ``m.Cls``, not of ``C``.

* **Re-exports canonicalised.** ``from sabo_lit.core import X`` (the
  canonical form per CONVENTIONS.md §14) is followed back to
  ``sabo_lit.core.<submodule>.X`` so that rules keyed on the definition
  path match calls made through the re-exported path. The re-export map
  is built once per scan by walking every ``__init__.py`` under
  ``root_path``.

* **``model_construct`` escape hatch detected.** ``Cls.model_construct(...)``
  is flagged exactly like ``Cls(...)``.

* **Annotations untouched.** ``x: Cls`` is an ``ast.Name`` in an
  annotation slot — it is never an ``ast.Call``, so the scanner naturally
  ignores it.

* **Fail-closed in CI.** A non-empty return must cause the test suite
  (and therefore the pipeline) to fail. Governance blocks; it does not
  merely report.
"""
from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass, field

from sabo_lit.core import DependencyPolicy, DependencyViolation
from sabo_lit.governance.dependency_rules import (
    CONSTRUCTION_RULES,
    FORBIDDEN_IMPORTS,
    ConstructionRule,
    ForbiddenImportRule,
)

# Directories we never descend into when scanning a tree.
_SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        "__pycache__",
        "venv",
        ".venv",
        "node_modules",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".git",
    }
)


@dataclass
class _SymbolTable:
    """Maps local names (as bound in a file) to fully-qualified symbols.

    Populated from a file's ``Import`` and ``ImportFrom`` nodes.
    """

    names: dict[str, str] = field(default_factory=dict)

    def add_import_from(
        self, module: str, name: str, asname: str | None
    ) -> None:
        """``from M import X [as Y]`` -> local name (``Y`` or ``X``) -> ``M.X``."""
        local = asname or name
        self.names[local] = f"{module}.{name}"

    def add_import(self, module: str, asname: str | None) -> None:
        """``import M`` (or ``import M.sub``, ``import M as N``).

        Without ``as``: the local name is the top-level component of the
        dotted module — ``import sabo_lit.core`` binds ``sabo_lit`` and
        attribute-walks resolve ``sabo_lit.core`` correctly.

        With ``as``: the alias maps directly to the full dotted module.
        """
        if asname is not None:
            self.names[asname] = module
        else:
            top = module.split(".")[0]
            self.names[top] = top

    def resolve(self, name: str) -> str | None:
        return self.names.get(name)


# ----------------------------------------------------------------------------
# Path / module-name helpers
# ----------------------------------------------------------------------------
def _file_module_and_package(
    file_path: pathlib.Path, root: pathlib.Path
) -> tuple[str, str]:
    """Return ``(module_name, package_for_relative_imports)``.

    Examples (root = ``.../sabo-quant/``):

    * ``sabo_lit/core/interfaces.py``
      -> ``module_name="sabo_lit.core.interfaces"``, ``package="sabo_lit.core"``
    * ``sabo_lit/core/__init__.py``
      -> ``module_name="sabo_lit.core"``, ``package="sabo_lit.core"``
      (the file represents the package itself; relative imports anchor here)
    """
    rel = file_path.relative_to(root)
    parts = list(rel.parts)
    is_init = parts[-1] == "__init__.py"
    parts[-1] = parts[-1].removesuffix(".py")
    if is_init:
        parts = parts[:-1]
    module_name = ".".join(parts)
    if is_init:
        package = module_name
    elif len(parts) > 1:
        package = ".".join(parts[:-1])
    else:
        package = ""
    return module_name, package


def _module_in(module_name: str, target_package: str) -> bool:
    """True iff ``module_name`` IS ``target_package`` or a submodule of it."""
    return module_name == target_package or module_name.startswith(
        target_package + "."
    )


def _resolve_relative_module(
    node: ast.ImportFrom, importer_package: str
) -> str | None:
    """Resolve the absolute module imported by an ``ImportFrom`` node.

    Handles relative imports (``level >= 1``). Returns ``None`` if the
    relative path would go above the top-level package (invalid Python).
    """
    if node.level == 0:
        return node.module
    parts = importer_package.split(".") if importer_package else []
    keep = len(parts) - (node.level - 1)
    if keep < 0:
        return None
    base = ".".join(parts[:keep]) if keep > 0 else ""
    if node.module:
        return f"{base}.{node.module}" if base else node.module
    return base


# ----------------------------------------------------------------------------
# Re-export canonicalisation
# ----------------------------------------------------------------------------
def _build_reexport_map(
    files: list[tuple[pathlib.Path, str]],
) -> dict[str, str]:
    """Build a map ``public_path -> defining_path`` from every ``__init__.py``.

    ``files`` is the list of ``(file_path, module_name)`` produced by
    ``_collect_files``. Only ``__init__.py`` files contribute; their
    relative imports become re-export bindings.

    Example: ``sabo_lit/core/__init__.py`` containing
    ``from .schemas import ValidatedStructureState`` yields the binding
    ``sabo_lit.core.ValidatedStructureState ->
    sabo_lit.core.schemas.ValidatedStructureState``.
    """
    reexports: dict[str, str] = {}
    for file_path, module_name in files:
        if file_path.name != "__init__.py":
            continue
        try:
            tree = ast.parse(file_path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        package_name = module_name  # __init__.py represents its package
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            source_module = _resolve_relative_module(node, package_name)
            if source_module is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                reexports[f"{package_name}.{local}"] = (
                    f"{source_module}.{alias.name}"
                )
    return reexports


def _canonicalise(symbol: str, reexports: dict[str, str]) -> str:
    """Follow the re-export chain to find the definition path.

    Cycle-safe (a buggy ``__init__`` re-exporting itself terminates).
    """
    seen: set[str] = set()
    while symbol in reexports and symbol not in seen:
        seen.add(symbol)
        symbol = reexports[symbol]
    return symbol


# ----------------------------------------------------------------------------
# Symbol table + call-target resolution
# ----------------------------------------------------------------------------
def _build_symtab(tree: ast.AST, importer_package: str) -> _SymbolTable:
    """Walk the AST collecting every ``Import`` / ``ImportFrom`` binding."""
    symtab = _SymbolTable()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = _resolve_relative_module(node, importer_package)
            if module is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                symtab.add_import_from(module, alias.name, alias.asname)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                symtab.add_import(alias.name, alias.asname)
    return symtab


def _resolve_attribute_chain(
    node: ast.expr, symtab: _SymbolTable
) -> str | None:
    """Resolve a ``Name`` or ``Attribute`` chain to its fully-qualified name.

    ``Name('Cls')``                       -> ``symtab.resolve('Cls')``
    ``Attribute(Name('m'), 'X')``         -> ``resolve('m') + '.X'``
    ``Attribute(Attribute(...), 'X')``    -> recurse, append ``.X``
    Anything else                          -> ``None``
    """
    if isinstance(node, ast.Name):
        return symtab.resolve(node.id)
    if isinstance(node, ast.Attribute):
        base = _resolve_attribute_chain(node.value, symtab)
        if base is None:
            return None
        return f"{base}.{node.attr}"
    return None


def _identify_called_symbol(
    call: ast.Call, symtab: _SymbolTable
) -> str | None:
    """Return the FQN of the class this call would construct, or ``None``.

    Cases handled:

    * ``Cls(...)``                              -> ``Name`` -> resolve
    * ``module.Cls(...)``                       -> attribute chain
    * ``Cls.model_construct(...)``              -> strip suffix, then resolve
    * ``module.Cls.model_construct(...)``       -> strip suffix, then chain
    """
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "model_construct":
        base: ast.expr = func.value
    else:
        base = func
    return _resolve_attribute_chain(base, symtab)


# ----------------------------------------------------------------------------
# Scanner
# ----------------------------------------------------------------------------
def _collect_files(
    root: pathlib.Path,
) -> list[tuple[pathlib.Path, str]]:
    """Walk ``root`` and yield ``(file_path, module_name)`` for every
    scannable ``.py`` file.

    Skips ``__pycache__``, virtualenvs, build outputs, hidden dirs, etc.
    """
    out: list[tuple[pathlib.Path, str]] = []
    for py_file in root.rglob("*.py"):
        if any(
            part in _SKIP_DIR_NAMES or part.startswith(".")
            for part in py_file.relative_to(root).parts[:-1]
        ):
            continue
        try:
            module_name, _ = _file_module_and_package(py_file, root)
        except ValueError:
            continue
        out.append((py_file, module_name))
    return sorted(out, key=lambda t: t[0])


class RuleBasedDependencyPolicy(DependencyPolicy):
    """Concrete ``DependencyPolicy`` reading ``FORBIDDEN_IMPORTS`` and
    ``CONSTRUCTION_RULES`` from ``governance.dependency_rules``.

    Construction with custom rule tuples is supported for tests:

        policy = RuleBasedDependencyPolicy(
            forbidden_imports=(MyRule, ...),
            construction_rules=(MyOtherRule, ...),
        )
    """

    def __init__(
        self,
        forbidden_imports: tuple[ForbiddenImportRule, ...] = FORBIDDEN_IMPORTS,
        construction_rules: tuple[ConstructionRule, ...] = CONSTRUCTION_RULES,
    ) -> None:
        self._forbidden_imports = forbidden_imports
        self._construction_rules = construction_rules

    def allowed_imports(self) -> dict[str, frozenset[str]]:
        """Inversion of ``FORBIDDEN_IMPORTS``: for each importer module,
        the set of EXPLICITLY FORBIDDEN imported modules.

        The abstract interface speaks of "allowed imports"; in this
        codebase the rule shape is deny-list (everything is allowed
        except the explicitly forbidden), so the returned map names the
        denylist per importer. ``scan`` consumes the underlying rule
        objects directly; this method is provided for introspection.
        """
        forbidden_by_importer: dict[str, set[str]] = {}
        for rule in self._forbidden_imports:
            forbidden_by_importer.setdefault(
                rule.importer_module, set()
            ).add(rule.imported_module)
        return {
            importer: frozenset(forbidden)
            for importer, forbidden in forbidden_by_importer.items()
        }

    def scan(self, root_path: str) -> list[DependencyViolation]:
        root = pathlib.Path(root_path).resolve()
        files = _collect_files(root)
        reexports = _build_reexport_map(files)

        violations: list[DependencyViolation] = []
        for file_path, module_name in files:
            try:
                source = file_path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                tree = ast.parse(source, filename=str(file_path))
            except SyntaxError:
                continue

            _, importer_package = _file_module_and_package(file_path, root)
            symtab = _build_symtab(tree, importer_package)

            self._scan_imports(
                tree=tree,
                file_path=file_path,
                file_module=module_name,
                importer_package=importer_package,
                violations=violations,
            )
            self._scan_constructions(
                tree=tree,
                file_path=file_path,
                file_module=module_name,
                symtab=symtab,
                reexports=reexports,
                violations=violations,
            )
        return violations

    # --- inner scanners -----------------------------------------------------
    def _scan_imports(
        self,
        tree: ast.AST,
        file_path: pathlib.Path,
        file_module: str,
        importer_package: str,
        violations: list[DependencyViolation],
    ) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported = _resolve_relative_module(node, importer_package)
                if imported is None:
                    continue
                self._check_import(
                    file_module=file_module,
                    imported=imported,
                    file_path=file_path,
                    line=node.lineno,
                    violations=violations,
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    self._check_import(
                        file_module=file_module,
                        imported=alias.name,
                        file_path=file_path,
                        line=getattr(alias, "lineno", node.lineno),
                        violations=violations,
                    )

    def _scan_constructions(
        self,
        tree: ast.AST,
        file_path: pathlib.Path,
        file_module: str,
        symtab: _SymbolTable,
        reexports: dict[str, str],
        violations: list[DependencyViolation],
    ) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            resolved = _identify_called_symbol(node, symtab)
            if resolved is None:
                continue
            canonical = _canonicalise(resolved, reexports)
            self._check_construction(
                file_module=file_module,
                symbol=canonical,
                file_path=file_path,
                line=node.lineno,
                violations=violations,
            )

    def _check_import(
        self,
        file_module: str,
        imported: str,
        file_path: pathlib.Path,
        line: int,
        violations: list[DependencyViolation],
    ) -> None:
        for rule in self._forbidden_imports:
            if not _module_in(file_module, rule.importer_module):
                continue
            if not (
                imported == rule.imported_module
                or imported.startswith(rule.imported_module + ".")
            ):
                continue
            violations.append(
                DependencyViolation(
                    importer_module=file_module,
                    imported_module=imported,
                    rule_violated=(
                        f"ForbiddenImportRule: {rule.importer_module} "
                        f"must not import {rule.imported_module} — "
                        f"{rule.rationale}"
                    ),
                    file_path=str(file_path),
                    line_number=line,
                )
            )

    def _check_construction(
        self,
        file_module: str,
        symbol: str,
        file_path: pathlib.Path,
        line: int,
        violations: list[DependencyViolation],
    ) -> None:
        for rule in self._construction_rules:
            if symbol != rule.fully_qualified_symbol:
                continue
            if any(
                _module_in(file_module, allowed)
                for allowed in rule.allowed_constructor_modules
            ):
                continue
            allowed_list = ", ".join(sorted(rule.allowed_constructor_modules))
            violations.append(
                DependencyViolation(
                    importer_module=file_module,
                    imported_module=symbol,
                    rule_violated=(
                        f"ConstructionRule: {symbol} may only be "
                        f"constructed in {{{allowed_list}}} — "
                        f"{rule.rationale}"
                    ),
                    file_path=str(file_path),
                    line_number=line,
                )
            )
