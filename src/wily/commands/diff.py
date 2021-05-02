"""
Diff command.

Compares metrics between uncommitted files and indexed files.
"""
import multiprocessing
import os
import typing as T
from json import dumps
from pathlib import Path

import radon.cli.harvest
import tabulate
from wily import format_date, format_delta, format_revision, logger
from wily.archivers import resolve_archiver
from wily.commands import check_output
from wily.commands.build import run_operator
from wily.config import DEFAULT_GRID_STYLE, DEFAULT_PATH, WilyConfig
from wily.helper.custom_enums import ReportFormat
from wily.operators import (
    BAD_COLORS,
    GOOD_COLORS,
    OperatorLevel,
    get_metric,
    resolve_metric,
    resolve_operator,
)
from wily.state import State


def diff(
    config: WilyConfig,
    files: T.Iterable[str],
    metrics: T.Iterable[str],
    changes_only: bool = True,
    detail: bool = True,
    output: Path = None,
    revision: str = None,
    format: ReportFormat = ReportFormat.CONSOLE,
) -> None:
    """
    Show the differences in metrics for each of the files.

    :param config: The wily configuration
    :type  config: :namedtuple:`wily.config.WilyConfig`

    :param files: The files to compare.
    :type  files: ``list`` of ``str``

    :param metrics: The metrics to measure.
    :type  metrics: ``list`` of ``str``

    :param changes_only: Only include changes files in output.
    :type  changes_only: ``bool``

    :param detail: Show details (function-level)
    :type  detail: ``bool``

    :param output: Output path
    :type  output: ``Path``

    :param revision: Compare with specific revision
    :type  revision: ``str``

    :param format: Output format
    :type  format: ``ReportFormat``
    """
    files = list(files)
    config.targets = files
    state = State(config)

    # Resolve target paths when the cli has specified --path
    if config.path != DEFAULT_PATH:
        targets = [str(Path(config.path) / Path(file)) for file in files]
    else:
        targets = files

    # Expand directories to paths
    files = [
        os.path.relpath(fn, config.path)
        for fn in radon.cli.harvest.iter_filenames(targets)
    ]
    logger.debug(f"Targeting - {files}")

    if not revision:
        target_revision = state.index[state.default_archiver].last_revision
    else:
        rev = resolve_archiver(state.default_archiver).cls(config).find(revision)
        logger.debug(f"Resolved {revision} to {rev.key} ({rev.message})")
        try:
            target_revision = state.index[state.default_archiver][rev.key]
        except KeyError:
            logger.error(
                f"Revision {revision} is not in the cache, make sure you have run "
                "wily build."
            )
            exit(1)

    logger.info(
        f"Comparing current with {format_revision(target_revision.revision.key)} by "
        f"{target_revision.revision.author_name} on "
        f"{format_date(target_revision.revision.date)}."
    )

    # Convert the list of metrics to a list of metric instances
    operators = {resolve_operator(metric.split(".")[0]) for metric in metrics}
    metrics = [(metric.split(".")[0], resolve_metric(metric)) for metric in metrics]
    results = []

    # Build a set of operators
    with multiprocessing.Pool(processes=len(operators)) as pool:
        operator_exec_out = pool.starmap(
            run_operator, [(operator, None, config, targets) for operator in operators]
        )
    data = {}
    for operator_name, result in operator_exec_out:
        data[operator_name] = result

    # Write a summary table
    extra = []
    for operator, metric in metrics:
        if detail and resolve_operator(operator).level == OperatorLevel.Object:
            for file in files:
                try:
                    extra.extend(
                        [
                            f"{file}:{k}"
                            for k in data[operator][file]["detailed"].keys()
                            if k != metric.name
                            and isinstance(data[operator][file]["detailed"][k], dict)
                        ]
                    )
                except KeyError:
                    logger.debug(f"File {file} not in cache")
                    logger.debug("Cache follows -- ")
                    logger.debug(data[operator])

    files.extend(extra)
    logger.debug(files)
    for file in files:
        metrics_data = []
        has_changes = False
        for operator, metric in metrics:
            try:
                current = target_revision.get(
                    config, state.default_archiver, operator, file, metric.name
                )
            except KeyError:
                current = "-"

            try:
                new = get_metric(data, operator, file, metric.name)
            except KeyError:
                new = "-"

            if new != current:
                has_changes = True

            _form = lambda x: x if x == "-" else format_delta(x)
            currents = _form(current)
            news = _form(new)
            if new != "-" and current != "-":
                if current == new:
                    metrics_data.append(f"{currents} -> {news}")
                else:
                    color = (
                        BAD_COLORS[metric.measure]
                        if current > new
                        else GOOD_COLORS[metric.measure]
                    )
                    metrics_data.append(f"{currents} -> \u001b[{color}m{news}\u001b[0m")
            elif current == "-" and new == "-":
                metrics_data.append("-")
            else:
                metrics_data.append(f"{currents} -> {news}")

        if has_changes or not changes_only:
            results.append((file, *metrics_data))
        else:
            logger.debug(metrics_data)

    descriptions: T.List[str] = [metric.description for operator, metric in metrics]
    headers = ("File", *descriptions)
    if len(results) > 0:
        if format == ReportFormat.JSON and output is not None:
            generate_json_diff(Path(config.path), output, results, headers)
            return

        print(
            # But it still makes more sense to show the newest at the top, so reverse again
            tabulate.tabulate(
                headers=headers, tabular_data=results, tablefmt=DEFAULT_GRID_STYLE
            )
        )


def generate_json_diff(
    path: Path,
    output: Path,
    data: T.List[T.Tuple[str, ...]],
    headers: T.Tuple[str, ...],
) -> None:
    """
    Make a JSON file of diff of latest commit for codefile/dir found on path.

    :param path: Path to measured file/dir
    :param output: Destination path
    :param data: List of data-tuples
    :param headers: Tuples of names of metrics
    """
    report_path, report_output = check_output(output, ".json")
    files = [t for t in data if ":" not in t[0]]
    metric_data = dict(issues=[])
    for filet in files:
        file = filet[0]

        issue = dict(zip(headers, filet))
        issue["location"] = file
        metric_data["issues"].append(issue)

        funcs = [t for t in data if t[0].startswith(file) and t[0] != file]
        for tup in funcs:
            issue = dict(zip(["Function", *headers[1:]], tup))
            issue["location"] = file
            metric_data["issues"].append(issue)

    report_json_string = dumps(metric_data)
    report_output.write_text(report_json_string)

    logger.info(f"wily report on {str(path)} was saved to {report_path}")
