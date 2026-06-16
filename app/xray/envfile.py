from pathlib import Path


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"invalid line in {path}: {raw_line}")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def read_env_or_file(name: str, default: str = "", env_file_values: dict[str, str] | None = None) -> str:
    import os

    raw = os.environ.get(name)
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip()
    if env_file_values and str(env_file_values.get(name, "")).strip() != "":
        return str(env_file_values[name]).strip()
    return str(default).strip()
