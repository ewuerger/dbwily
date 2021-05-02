"""Command implementations for CLI."""
import typing as T
from pathlib import Path


def check_output(output: Path, file_ending: str = ".html") -> T.Tuple[Path, Path]:
    """Check if destination output path suffix equals file_ending"""
    if output.is_file and output.suffix == file_ending:
        report_path = output.parents[0]
        report_output = output
    else:
        report_path = output
        report_output = output.joinpath("index" + file_ending)
    report_path.mkdir(exist_ok=True, parents=True)
    return report_path, report_output
