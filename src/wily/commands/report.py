"""
Report command.

The report command gives a table of metrics for a specified list of files.
Will compare the values between revisions and highlight changes in green/red.
"""
from typing import Any, Dict

import tabulate

from pathlib import Path
from shutil import copytree
from string import Template

from wily import logger, format_date, format_revision, MAX_MESSAGE_WIDTH
from wily.config import WilyConfig
from wily.helper.custom_enums import ReportFormat
from wily.operators import resolve_metric_as_tuple, MetricType
from wily.state import State


def report(
    config: WilyConfig,
    path: str,
    metrics: str,
    n: int,
    output: Path,
    include_message: bool = False,
    format: ReportFormat = ReportFormat.CONSOLE,
    console_format: str = None,
):
    """
    Show information about the cache and runtime.

    :param config: The configuration
    :param path: The path to the file
    :param metrics: Name of the metric to report on
    :param n: Number of items to list
    :param output: Output path
    :param include_message: Include revision messages
    :param format: Output format
    :param console_format: Grid format style for tabulate
    """
    logger.debug("Running report command")
    logger.info(f"-----------History for {metrics}------------")

    data = []
    metric_metas = []

    for metric_name in metrics:
        _operator, metric = resolve_metric_as_tuple(metric_name)
        key = metric.name
        operator_name = _operator.name
        # Set the delta colors depending on the metric type
        if metric.measure == MetricType.AimHigh:
            good_color = 32
            bad_color = 31
        elif metric.measure == MetricType.AimLow:
            good_color = 31
            bad_color = 32
        elif metric.measure == MetricType.Informational:
            good_color = 33
            bad_color = 33
        metric_meta = {
            "key": key,
            "operator": operator_name,
            "good_color": good_color,
            "bad_color": bad_color,
            "title": metric.description,
            "type": metric.type,
        }
        metric_metas.append(metric_meta)

    state = State(config)
    for archiver in state.archivers:
        history = state.get_index(archiver).revisions[:n][::-1]
        last: Dict[str, Any] = {}
        for rev in history:
            vals = []
            for meta in metric_metas:
                try:
                    logger.debug(
                        f"Fetching metric {meta['key']} for {meta['operator']} in {path}"
                    )
                    val = rev.get(config, archiver, meta["operator"], path, meta["key"])

                    last_val = last.get(meta["key"], None)
                    # Measure the difference between this value and the last
                    if meta["type"] in (int, float):
                        if last_val:
                            delta = val - last_val
                        else:
                            delta = 0
                        last[meta["key"]] = val
                    else:
                        # TODO : Measure ranking increases/decreases for str types?
                        delta = 0

                    if delta == 0:
                        delta_col = delta
                    elif delta < 0:
                        delta_col = f"\u001b[{meta['good_color']}m{delta:n}\u001b[0m"
                    else:
                        delta_col = f"\u001b[{meta['bad_color']}m+{delta:n}\u001b[0m"

                    if meta["type"] in (int, float):
                        k = f"{val:n} ({delta_col})"
                    else:
                        k = f"{val}"
                except KeyError as e:
                    k = f"Not found {e}"
                vals.append(k)
            if include_message:
                data.append(
                    (
                        format_revision(rev.revision.key),
                        rev.revision.message[:MAX_MESSAGE_WIDTH],
                        rev.revision.author_name,
                        format_date(rev.revision.date),
                        *vals,
                    )
                )
            else:
                data.append(
                    (
                        format_revision(rev.revision.key),
                        rev.revision.author_name,
                        format_date(rev.revision.date),
                        *vals,
                    )
                )
    descriptions = [meta["title"] for meta in metric_metas]
    if include_message:
        headers = ("Revision", "Message", "Author", "Date", *descriptions)
    else:
        headers = ("Revision", "Author", "Date", *descriptions)

    if format == ReportFormat.HTML:
        if output.is_file and output.suffix == ".html":
            report_path = output.parents[0]
            report_output = output
        else:
            report_path = output
            report_output = output.joinpath("index.html")

        report_path.mkdir(exist_ok=True, parents=True)

        templates_dir = (Path(__file__).parents[1] / "templates").resolve()
        report_template = Template((templates_dir / "report_template.html").read_text())

        table_headers = "".join([f"<th>{header}</th>" for header in headers])
        table_content = ""
        for line in data[::-1]:
            table_content += "<tr>"
            for element in line:
                element = element.replace("[32m", "<span class='green-color'>")
                element = element.replace("[31m", "<span class='red-color'>")
                element = element.replace("[33m", "<span class='orange-color'>")
                element = element.replace("[0m", "</span>")
                table_content += f"<td>{element}</td>"
            table_content += "</tr>"

        with report_output.open("w") as output_f:
            output_f.write(
                report_template.safe_substitute(
                    headers=table_headers, content=table_content
                )
            )

        try:
            copytree(str(templates_dir / "css"), str(report_path / "css"))
        except FileExistsError:
            pass

        logger.info(f"wily report was saved to {report_path}")
    else:
        print(
            tabulate.tabulate(
                headers=headers, tabular_data=data[::-1], tablefmt=console_format
            )
        )
