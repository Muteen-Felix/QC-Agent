from qc_agent.selector.rules import ChangedFile, decide, matches


POLICY = {"floor_workers": ["gitleaks", "semgrep"], "full_set_paths": ["Dockerfile", "**/Dockerfile*", "requirements*.txt"],
          "docs_paths": ["docs/**", "**/*.md"]}
SUITES = {"pytest": ["gt-functional"], "schemathesis": ["api-contract"], "semgrep": ["sast"]}


def test_globs_follow_posix_paths():
    assert matches("a/b/Dockerfile.dev", ["**/Dockerfile*"])
    assert matches("README.md", ["**/*.md"])
    assert matches(".github/README.md", ["**/*.md"])
    assert not matches("Readme.md", ["README.md"])
    assert not matches("a/b/c.py", ["a/*.py"])


def test_rule_priority_and_module_hints():
    assert decide([], POLICY, None, SUITES).floor_only
    assert decide([ChangedFile("docs/a.md", "M")], POLICY, None, SUITES).floor_only
    assert decide([ChangedFile("docs/a.md", "M"), ChangedFile("Dockerfile", "M")], POLICY, None, SUITES).full_set
    module_map = {"status": "draft", "modules": [{"name": "notes", "paths": ["toyapp/**"], "suites": ["gt-functional"]}]}
    result = decide([ChangedFile("toyapp/app.py", "M"), ChangedFile("other.py", "M")], POLICY, module_map, SUITES)
    assert result.hint_workers == {"pytest": ("module-map: notes",)}
    assert result.unmapped == ("other.py",)
    assert result.module_map_status == "draft"
