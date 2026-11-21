import sys
from importlib.abc import MetaPathFinder
from importlib.machinery import PathFinder

_TARGET = "tui_gateway.server"


class _Finder(MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname != _TARGET:
            return None
        spec = PathFinder.find_spec(fullname, path, target)
        if spec is None or spec.loader is None:
            return spec
        loader = spec.loader
        orig_exec = loader.exec_module

        def exec_module(module, _orig=orig_exec):
            _orig(module)
            try:
                import ods_talk_resume

                ods_talk_resume.install(module)
            except Exception as exc:
                sys.stderr.write(f"ods_talk_resume: install failed: {exc}\n")

        loader.exec_module = exec_module
        return spec


if not any(isinstance(f, _Finder) for f in sys.meta_path):
    sys.meta_path.insert(0, _Finder())
